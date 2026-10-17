"""選定期間で案を1つ決め、未使用期間が足りて有意なときだけ日次を更新する。"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Any

from skabu.config import Settings
from skabu.errors import SkabuError
from skabu.experiments import activate, get_experiment, record_result, upsert_experiment
from skabu.gates import gate_catalog, sanitize_gates
from skabu.indicators.registry import catalog
from skabu.logging_setup import dump_debug_event, get_logger
from skabu.store import connect
from skabu.strategy import _atomic_json, _side_metrics, active_path, load_active_strategy
from skabu.verify import run_compare

SEARCH_LAST_NAME = "search_last.json"
SEARCH_HORIZON = "20"
SEARCH_NOTE = "選定で一番でも、確認で有意になるまで採用しない。"
MIN_NEED_N = 8
ALPHA = 0.05
Z_ALPHA = 1.6448536269514722
Z_BETA = 0.8416212335729143
TOP_SCORES = 3
TOP_GATES = 3
MAX_PRESETS = 120
SKIP_GATE_FEATURES = frozenset({"sales_growth3"})


def search_last_path(settings: Settings) -> Path:
    return settings.skabu_data_dir / SEARCH_LAST_NAME


def load_last_search(settings: Settings) -> dict[str, Any] | None:
    path = search_last_path(settings)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        get_logger().exception("search last read failed")
        return None
    return raw if isinstance(raw, dict) else None


def _betacf(a: float, b: float, x: float) -> float:
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < 1e-30:
        d = 1e-30
    d = 1.0 / d
    h = d
    for step in range(1, 201):
        m2 = 2 * step
        aa = step * (b - step) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-30:
            d = 1e-30
        c = 1.0 + aa / c
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1.0 / d
        h *= d * c
        aa = -(a + step) * (qab + step) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-30:
            d = 1e-30
        c = 1.0 + aa / c
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 3e-7:
            return h
    return h


def _betai(a: float, b: float, x: float) -> float:
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    ln = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log(1.0 - x)
    front = math.exp(ln)
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def student_t_cdf(t: float, df: float) -> float:
    if df <= 0:
        return float("nan")
    if t == 0:
        return 0.5
    x = df / (df + t * t)
    ib = _betai(df / 2.0, 0.5, x)
    if t > 0:
        return 1.0 - 0.5 * ib
    return 0.5 * ib


def t_ppf(p: float, df: float) -> float:
    lo, hi = -40.0, 40.0
    for _ in range(80):
        mid = (lo + hi) / 2.0
        if student_t_cdf(mid, df) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def one_sample_ttest(values: list[float], *, alpha: float = ALPHA) -> dict[str, Any]:
    n = len(values)
    if n < 2:
        return {"n": n, "mean": None, "sd": None, "t": None, "p": None, "ci_low": None}
    mean = sum(values) / n
    var = sum((item - mean) ** 2 for item in values) / (n - 1)
    sd = math.sqrt(var) if var > 0 else 0.0
    if sd == 0:
        p = 0.0 if mean > 0 else 1.0 if mean < 0 else 0.5
        return {"n": n, "mean": mean, "sd": 0.0, "t": None, "p": p, "ci_low": mean}
    se = sd / math.sqrt(n)
    t_stat = mean / se
    p = max(0.0, min(1.0, 1.0 - student_t_cdf(t_stat, n - 1)))
    ci_low = mean - t_ppf(1.0 - alpha, n - 1) * se
    return {"n": n, "mean": mean, "sd": sd, "t": t_stat, "p": p, "ci_low": ci_low}


def paired_ttest(left: list[float], right: list[float], *, alpha: float = ALPHA) -> dict[str, Any]:
    n = min(len(left), len(right))
    diffs = [left[i] - right[i] for i in range(n)]
    return one_sample_ttest(diffs, alpha=alpha)


def need_n(sd: float | None, effect: float | None, *, min_n: int = MIN_NEED_N) -> int:
    if sd is None or effect is None or effect <= 0 or sd <= 0:
        return min_n
    raw = ((Z_ALPHA + Z_BETA) * float(sd) / float(effect)) ** 2
    return max(min_n, int(math.ceil(raw)))


def _period_returns(periods: list[dict[str, Any]]) -> list[float]:
    return [float(item["period_return"]) for item in periods if item.get("period_return") is not None]


def split_select_confirm(
    periods: list[dict[str, Any]],
    *,
    after: str | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    ordered = sorted(periods, key=lambda item: str(item.get("as_of") or ""))
    if not after:
        return ordered, []
    day = str(after)[:10]
    select = [item for item in ordered if str(item.get("as_of") or "") <= day]
    confirm = [item for item in ordered if str(item.get("as_of") or "") > day]
    if not select:
        return ordered, []
    return select, confirm


def walk_forward_folds(periods: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(periods, key=lambda item: str(item.get("as_of") or ""))
    n = len(ordered)
    if n < 4:
        return [
            {
                "id": 0,
                "train": _side_metrics(ordered),
                "confirm": _side_metrics([]),
                "train_n": n,
                "confirm_n": 0,
            }
        ]
    cuts = [(0.4, 0.6), (0.6, 0.8), (0.8, 1.0)] if n >= 10 else [(0.6, 0.8), (0.8, 1.0)]
    folds: list[dict[str, Any]] = []
    for index, (train_end, confirm_end) in enumerate(cuts):
        train_cut = max(2, int(n * train_end))
        confirm_cut = n if index == len(cuts) - 1 else max(train_cut + 1, int(n * confirm_end))
        train_rows = ordered[:train_cut]
        confirm_rows = ordered[train_cut:confirm_cut]
        if not confirm_rows:
            continue
        folds.append(
            {
                "id": index,
                "train": _side_metrics(train_rows),
                "confirm": _side_metrics(confirm_rows),
                "train_n": len(train_rows),
                "confirm_n": len(confirm_rows),
            }
        )
    return folds or [
        {
            "id": 0,
            "train": _side_metrics(ordered[: max(2, n * 2 // 3)]),
            "confirm": _side_metrics(ordered[max(2, n * 2 // 3) :]),
            "train_n": max(2, n * 2 // 3),
            "confirm_n": n - max(2, n * 2 // 3),
        }
    ]


def _select_key(row: dict[str, Any]) -> tuple[float, float, int]:
    select = row.get("select") or {}
    ret = select.get("avg_return")
    excess = select.get("avg_excess")
    n = int(select.get("periods") or 0)
    return (
        float("-inf") if ret is None else float(ret),
        float("-inf") if excess is None else float(excess),
        n,
    )


def decide_gate(
    candidate: dict[str, Any],
    *,
    previous: dict[str, Any] | None = None,
    previous_confirm: list[dict[str, Any]] | None = None,
    alpha: float = ALPHA,
    min_n: int = MIN_NEED_N,
) -> dict[str, Any]:
    need = int(candidate.get("need_n") or min_n)
    confirm_rows = list(candidate.get("confirm_periods") or [])
    values = _period_returns(confirm_rows)
    zero = one_sample_ttest(values, alpha=alpha)
    n = len(values)
    gate: dict[str, Any] = {
        "ok": False,
        "candidate_id": candidate.get("id"),
        "n": n,
        "need_n": need,
        "p_gt_zero": zero.get("p"),
        "p_gt_active": None,
        "ci_low": None if zero.get("ci_low") is None else round(float(zero["ci_low"]), 6),
        "reason": "n_short",
        "winner_id": candidate.get("id"),
    }
    if n < need or n < min_n:
        gate["reason"] = "n_short"
        get_logger().info("search gate n_short n=%s need=%s id=%s", n, need, candidate.get("id"))
        return gate
    p_zero = zero.get("p")
    if p_zero is None or float(p_zero) >= alpha:
        gate["reason"] = "p_ge_05"
        get_logger().info("search gate p_ge_05 p=%s id=%s", p_zero, candidate.get("id"))
        return gate
    if previous and previous.get("id") and previous.get("id") != candidate.get("id"):
        left, right = _align_returns(confirm_rows, previous_confirm or [])
        paired = paired_ttest(left, right, alpha=alpha)
        gate["p_gt_active"] = paired.get("p")
        if paired.get("p") is None or float(paired["p"]) >= alpha:
            gate["reason"] = "not_better_than_active"
            get_logger().info("search gate not_better_than_active p=%s", paired.get("p"))
            return gate
    gate["ok"] = True
    gate["reason"] = "significant"
    return gate


def _align_returns(
    left_periods: list[dict[str, Any]],
    right_periods: list[dict[str, Any]],
) -> tuple[list[float], list[float]]:
    right = {str(item.get("as_of")): item.get("period_return") for item in right_periods}
    a: list[float] = []
    b: list[float] = []
    for item in left_periods:
        key = str(item.get("as_of"))
        if key not in right or item.get("period_return") is None or right[key] is None:
            continue
        a.append(float(item["period_return"]))
        b.append(float(right[key]))
    return a, b


def _cutoff_day(previous: dict[str, Any] | None, last: dict[str, Any] | None) -> str | None:
    for payload in (previous, last):
        if not payload:
            continue
        raw = payload.get("adopted_at") or payload.get("finished_at") or payload.get("started_at")
        if raw:
            return str(raw)[:10]
    return None


def _locked_pending(last: dict[str, Any] | None) -> tuple[str | None, int | None]:
    if not last or last.get("adopted"):
        return None, None
    gate = last.get("gate") or {}
    cand = last.get("candidate_id") or gate.get("candidate_id")
    need = gate.get("need_n")
    if cand and need:
        try:
            return str(cand), int(need)
        except (TypeError, ValueError):
            return str(cand), None
    return (str(cand) if cand else None), None


def _score_item(
    item: dict[str, Any],
    select_rows: list[dict[str, Any]],
    confirm_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    select_rets = _period_returns(select_rows)
    select = _side_metrics(select_rows)
    confirm = _side_metrics(confirm_rows)
    stats = one_sample_ttest(select_rets)
    effect = None if stats.get("mean") is None or float(stats["mean"]) <= 0 else 0.5 * float(stats["mean"])
    return {
        "id": item.get("id"),
        "title": item.get("title"),
        "selections": list(item.get("selections") or []),
        "filters": list(item.get("filters") or []),
        "select": select,
        "confirm": confirm,
        "train": select,
        "select_periods": select_rows,
        "confirm_periods": confirm_rows,
        "need_n": need_n(stats.get("sd"), effect),
        "official_confirm": bool(confirm_rows),
    }


def _merge_verify(existing: dict[str, Any] | None, row: dict[str, Any], *, official: bool) -> dict[str, Any]:
    current = dict(existing or {})
    current["search"] = {
        "horizon": 20,
        "select": row.get("select"),
        "confirm": row.get("confirm"),
        "need_n": row.get("need_n"),
        "note": SEARCH_NOTE if not official else "採用日以降の新しい足で確認した。",
        "source": "search",
        "adopt": False,
    }
    current["note"] = current["search"]["note"]
    if current.get("holdout") is None:
        current["holdout"] = row.get("confirm")
    if current.get("train") is None:
        current["train"] = row.get("select")
    return current


def _item_periods(item: dict[str, Any]) -> list[dict[str, Any]]:
    body = (item.get("horizons") or {}).get(SEARCH_HORIZON) or {}
    return list(body.get("periods") or [])


def score_atoms() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for item in catalog():
        if "score" not in (item.get("surface") or []):
            continue
        plugin_id = str(item.get("id") or "")
        if not plugin_id:
            continue
        key = f"score_{plugin_id}"
        out[key] = {
            "title": str(item.get("title") or plugin_id),
            "selections": [{"id": plugin_id, "params": {}}],
            "filters": [],
        }
    return out


def gate_atoms() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for item in gate_catalog():
        feature = str(item.get("feature") or "")
        if feature in SKIP_GATE_FEATURES:
            continue
        plugin_id = str(item.get("id") or "")
        if not plugin_id or not feature:
            continue
        key = f"gate_{plugin_id}.{feature}"
        filters = sanitize_gates(
            [
                {
                    "id": plugin_id,
                    "feature": feature,
                    "op": item.get("default_op"),
                    "value": item.get("default_value"),
                }
            ]
        )
        if not filters:
            continue
        out[key] = {
            "title": str(item.get("title") or key),
            "selections": [],
            "filters": filters,
        }
    return out


def stage1_presets() -> dict[str, dict[str, Any]]:
    return {**score_atoms(), **gate_atoms()}


def combo_id(selections: list[dict[str, Any]], filters: list[dict[str, Any]]) -> str:
    scores = "+".join(sorted(str(item.get("id") or "") for item in selections)) or "none"
    gates = "+".join(sorted(f"{item.get('id')}.{item.get('feature')}" for item in filters)) or "none"
    return f"s_{scores}__g_{gates}"


def combo_presets(
    top_scores: list[dict[str, Any]],
    top_gates: list[dict[str, Any]],
    *,
    limit: int = MAX_PRESETS,
) -> dict[str, dict[str, Any]]:
    score_sets: list[tuple[list[dict[str, Any]], str]] = []
    for count in range(1, len(top_scores) + 1):
        for combo in combinations(top_scores, count):
            selections: list[dict[str, Any]] = []
            titles: list[str] = []
            for atom in combo:
                selections.extend(list(atom.get("selections") or []))
                titles.append(str(atom.get("title") or atom.get("id") or ""))
            score_sets.append((selections, " + ".join(titles)))
    gate_sets: list[tuple[list[dict[str, Any]], str]] = [([], "門なし")]
    for count in range(1, len(top_gates) + 1):
        for combo in combinations(top_gates, count):
            filters: list[dict[str, Any]] = []
            titles: list[str] = []
            for atom in combo:
                filters.extend(list(atom.get("filters") or []))
                titles.append(str(atom.get("title") or atom.get("id") or ""))
            gate_sets.append((filters, " + ".join(titles)))
    out: dict[str, dict[str, Any]] = {}
    for selections, score_title in score_sets:
        for filters, gate_title in gate_sets:
            if len(selections) == 1 and not filters:
                continue
            key = combo_id(selections, filters)
            if key in out:
                continue
            out[key] = {
                "title": f"{score_title} / {gate_title}",
                "selections": selections,
                "filters": filters,
            }
            if len(out) >= limit:
                get_logger().warning("search combo cap hit limit=%s", limit)
                return out
    return out


def _active_preset(previous: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not previous or not previous.get("id"):
        return {}
    ident = str(previous["id"])
    return {
        ident: {
            "title": str(previous.get("title") or ident),
            "selections": list(previous.get("selections") or []),
            "filters": list(previous.get("filters") or []),
        }
    }


def _locked_preset(last: dict[str, Any] | None, locked_id: str | None) -> dict[str, dict[str, Any]]:
    if not last or not locked_id:
        return {}
    row = next((item for item in (last.get("candidates") or []) if item.get("id") == locked_id), None)
    if not row:
        return {}
    return {
        locked_id: {
            "title": str(row.get("title") or locked_id),
            "selections": list(row.get("selections") or []),
            "filters": list(row.get("filters") or []),
        }
    }


def _atom_specs(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        out.append(
            {
                "id": row.get("id"),
                "title": row.get("title") or row.get("id"),
                "selections": list(row.get("selections") or []),
                "filters": list(row.get("filters") or []),
            }
        )
    return out


def _score_items(
    raw_items: list[dict[str, Any]],
    select_keys: set[str],
    confirm_keys: set[str],
) -> list[dict[str, Any]]:
    ranked = []
    for item in raw_items:
        periods = _item_periods(item)
        select_rows = [row for row in periods if str(row.get("as_of")) in select_keys]
        confirm_rows = [row for row in periods if str(row.get("as_of")) in confirm_keys]
        ranked.append(_score_item(item, select_rows, confirm_rows))
    ranked.sort(key=_select_key, reverse=True)
    return ranked


def _run_compare(
    settings: Settings,
    specs: dict[str, dict[str, Any]],
    *,
    market: str,
    min_avg_volume: float,
    min_bars: int,
    top: int,
    stride: int,
    min_history: int,
    refresh: bool,
) -> dict[str, Any]:
    if not specs:
        return {"items": [], "rules": {}}
    with connect(settings.db_path) as conn:
        return run_compare(
            conn,
            presets=specs,
            include_periods=True,
            market=market,
            min_avg_volume=min_avg_volume,
            min_bars=min_bars,
            top=top,
            stride=stride,
            min_history=min_history,
            refresh=refresh,
        )


def _build_two_stage_items(
    settings: Settings,
    *,
    previous: dict[str, Any] | None,
    last: dict[str, Any] | None,
    locked_id: str | None,
    cutoff: str | None,
    market: str,
    min_avg_volume: float,
    min_bars: int,
    top: int,
    stride: int,
    min_history: int,
    refresh: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    log = get_logger()
    stage1 = {**stage1_presets(), **_active_preset(previous), **_locked_preset(last, locked_id)}
    if len(stage1) > MAX_PRESETS:
        log.warning("search stage1 truncated n=%s cap=%s", len(stage1), MAX_PRESETS)
        stage1 = dict(list(stage1.items())[:MAX_PRESETS])
    compared1 = _run_compare(
        settings,
        stage1,
        market=market,
        min_avg_volume=min_avg_volume,
        min_bars=min_bars,
        top=top,
        stride=stride,
        min_history=min_history,
        refresh=refresh,
    )
    raw1 = list(compared1.get("items") or [])
    if not raw1:
        raise SkabuError("探索できる組み合わせがありません。")
    dates = _item_periods(raw1[0])
    select_template, confirm_template = split_select_confirm(dates, after=cutoff)
    select_keys = {str(item.get("as_of")) for item in select_template}
    confirm_keys = {str(item.get("as_of")) for item in confirm_template}
    ranked1 = _score_items(raw1, select_keys, confirm_keys)
    top_scores = _atom_specs([row for row in ranked1 if row.get("selections") and not row.get("filters")][:TOP_SCORES])
    top_gates = _atom_specs([row for row in ranked1 if row.get("filters") and not row.get("selections")][:TOP_GATES])
    log.info(
        "search stage1 n=%s top_scores=%s top_gates=%s",
        len(ranked1),
        [row.get("id") for row in top_scores],
        [row.get("id") for row in top_gates],
    )
    stage2 = combo_presets(top_scores, top_gates)
    stage2.update(_active_preset(previous))
    stage2.update(_locked_preset(last, locked_id))
    compared2 = _run_compare(
        settings,
        stage2,
        market=market,
        min_avg_volume=min_avg_volume,
        min_bars=min_bars,
        top=top,
        stride=stride,
        min_history=min_history,
        refresh=refresh,
    )
    merged: dict[str, dict[str, Any]] = {}
    for item in raw1 + list(compared2.get("items") or []):
        ident = str(item.get("id") or "")
        if ident:
            merged[ident] = item
    return list(merged.values()), compared2 if compared2.get("items") else compared1


def run_search(
    settings: Settings,
    *,
    market: str = "プライム",
    min_avg_volume: float = 50_000,
    min_bars: int = 60,
    top: int = 20,
    stride: int = 20,
    min_history: int = 80,
    refresh: bool = False,
    min_confirm_periods: int = MIN_NEED_N,
    presets: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    settings.ensure_dirs()
    log = get_logger()
    previous = load_active_strategy(settings)
    last = load_last_search(settings)
    cutoff = _cutoff_day(previous, last)
    locked_id, locked_need = _locked_pending(last)
    started = datetime.now(timezone.utc).isoformat()
    try:
        if presets is not None:
            compared = _run_compare(
                settings,
                presets,
                market=market,
                min_avg_volume=min_avg_volume,
                min_bars=min_bars,
                top=top,
                stride=stride,
                min_history=min_history,
                refresh=refresh,
            )
            raw_items = list(compared.get("items") or [])
        else:
            raw_items, compared = _build_two_stage_items(
                settings,
                previous=previous,
                last=last,
                locked_id=locked_id,
                cutoff=cutoff,
                market=market,
                min_avg_volume=min_avg_volume,
                min_bars=min_bars,
                top=top,
                stride=stride,
                min_history=min_history,
                refresh=refresh,
            )
        if not raw_items:
            raise SkabuError("探索できる組み合わせがありません。")
        dates = _item_periods(raw_items[0])
        select_template, confirm_template = split_select_confirm(dates, after=cutoff)
        select_keys = {str(item.get("as_of")) for item in select_template}
        confirm_keys = {str(item.get("as_of")) for item in confirm_template}
        ranked = _score_items(raw_items, select_keys, confirm_keys)
        picked = ranked[0]
        if locked_id:
            locked = next((item for item in ranked if item.get("id") == locked_id), None)
            if locked is not None:
                picked = locked
                if locked_need is not None:
                    picked["need_n"] = locked_need
                    log.info("search keep locked candidate=%s need_n=%s", locked_id, locked_need)
        if locked_need is None:
            picked["need_n"] = max(int(min_confirm_periods), int(picked.get("need_n") or MIN_NEED_N))
        else:
            picked["need_n"] = max(int(min_confirm_periods), int(locked_need))

        previous_confirm = []
        if previous and previous.get("id"):
            current_row = next((item for item in ranked if item.get("id") == previous.get("id")), None)
            previous_confirm = list((current_row or {}).get("confirm_periods") or [])
        gate = decide_gate(
            picked,
            previous=previous,
            previous_confirm=previous_confirm,
            min_n=int(min_confirm_periods),
        )
        official = bool(picked.get("official_confirm")) and int(gate.get("n") or 0) >= int(gate.get("need_n") or MIN_NEED_N)
        remain = max(0, int(gate.get("need_n") or 0) - int(gate.get("n") or 0))
        note = SEARCH_NOTE
        if gate.get("reason") == "n_short":
            note = f"{SEARCH_NOTE} 確認は {gate.get('n')} / {gate.get('need_n')} 期間。あと {remain} 回。"
        elif official and gate.get("ok"):
            note = "採用日以降の新しい足で確認し、有意だった。"

        for row in ranked:
            try:
                upsert_experiment(
                    settings,
                    {
                        "id": row["id"],
                        "title": row.get("title") or row["id"],
                        "selections": row.get("selections") or [],
                        "filters": row.get("filters") or [],
                    },
                )
                current = None
                try:
                    current = get_experiment(settings, str(row["id"])).get("verify")
                except SkabuError:
                    current = None
                record_result(settings, str(row["id"]), verify=_merge_verify(current, row, official=official))
            except Exception:
                log.exception("search record failed id=%s", row.get("id"))

        adopted = False
        active = previous
        if gate.get("ok"):
            try:
                active = activate(settings, str(picked["id"]))
                active["warning"] = note
                active["search"] = {"gate": gate, "confirm": picked.get("confirm"), "select": picked.get("select")}
                _atomic_json(active_path(settings), active)
                adopted = True
                log.info("search adopted id=%s p=%s n=%s", picked.get("id"), gate.get("p_gt_zero"), gate.get("n"))
            except Exception:
                log.exception("search activate failed id=%s", picked.get("id"))
                gate = {**gate, "ok": False, "reason": "activate_failed"}
                active = previous
        else:
            log.info("search keep previous reason=%s candidate=%s", gate.get("reason"), picked.get("id"))

        display = []
        for row in ranked:
            slim = {key: value for key, value in row.items() if key not in {"select_periods", "confirm_periods"}}
            display.append(slim)
        report = {
            "ok": True,
            "started_at": started,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "note": note,
            "horizon": 20,
            "gate": gate,
            "adopted": adopted,
            "candidate_id": picked.get("id"),
            "winner_id": picked.get("id"),
            "active": active,
            "previous_id": None if previous is None else previous.get("id"),
            "candidates": display,
            "rules": compared.get("rules"),
            "min_confirm_periods": min_confirm_periods,
            "cutoff": cutoff,
        }
        _atomic_json(search_last_path(settings), report)
        log.info(
            "search done candidate=%s adopted=%s reason=%s n=%s need=%s",
            picked.get("id"),
            adopted,
            gate.get("reason"),
            gate.get("n"),
            gate.get("need_n"),
        )
        return report
    except SkabuError:
        raise
    except Exception as exc:
        log.exception("search failed")
        dump_debug_event(settings.debug_dir, {"kind": "search", "error": str(exc)})
        raise SkabuError(f"自動探索に失敗しました: {exc}") from exc
