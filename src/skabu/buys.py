"""ランキングは出すが、条件を満たす銘柄だけ買う。0件は現金。"""

from __future__ import annotations

from typing import Any

from skabu.levels import STOP_MULT, TARGET_MULT
from skabu.logging_setup import get_logger

MAX_STOP_PCT = 0.08
MIN_EV_PCT = 0.0
DEFAULT_RISK_PCT = 0.01
MAX_PER_SECTOR = 2
UNKNOWN_SECTOR = "不明"
BUY_SPEC: dict[str, Any] = {
    "max_stop_pct": MAX_STOP_PCT,
    "min_ev_pct": MIN_EV_PCT,
    "max_per_sector": MAX_PER_SECTOR,
    "gates": ["strategy_holdout", "stop_wide", "ev_nonpositive", "score_nonpositive", "sector_cap"],
}

SKIP_LABELS = {
    "strategy_holdout": "確認期間がプラスでないため見送り",
    "stop_wide": "損切幅が広すぎる",
    "ev_nonpositive": "期待値がプラスでない",
    "score_nonpositive": "合計点がプラスでない",
    "sector_cap": "同じ業種が上限",
    "size_too_small": "1株分の資金がない",
    "already_held": "すでに保有",
    "kabu_offline": "kabuステーション未接続",
    "unapproved": "承認されず見送り",
    "over_limit": "1注文の上限超過",
    "order_dead": "注文が失効または取消",
}


def skip_label(reason: str | None) -> str | None:
    if not reason:
        return None
    return SKIP_LABELS.get(reason, reason)


def ref_price(row: dict[str, Any]) -> float | None:
    for key in ("entry_ref", "close"):
        value = row.get(key)
        if value not in (None, 0):
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
    return None


def stop_pct(row: dict[str, Any], *, stop_mult: float = STOP_MULT) -> float | None:
    price = ref_price(row)
    atr = row.get("atr")
    if price is None or atr is None:
        return None
    try:
        atr_value = float(atr)
    except (TypeError, ValueError):
        return None
    if atr_value <= 0:
        return None
    return float(stop_mult) * atr_value / price


def ev_pct(
    row: dict[str, Any],
    *,
    stop_mult: float = STOP_MULT,
    target_mult: float = TARGET_MULT,
) -> float | None:
    proba = row.get("proba")
    width = stop_pct(row, stop_mult=stop_mult)
    if proba is None or width is None or float(stop_mult) <= 0:
        return None
    try:
        p = float(proba)
    except (TypeError, ValueError):
        return None
    reward_per_risk = float(target_mult) / float(stop_mult)
    return width * (reward_per_risk * p - (1.0 - p))


def buy_skip_reason(
    row: dict[str, Any],
    *,
    holdout_ok: bool = True,
    stop_mult: float = STOP_MULT,
    target_mult: float = TARGET_MULT,
) -> str | None:
    # 確認期間の成績では買いを止めない。holdout_ok は記録用に残す。
    del holdout_ok
    total = row.get("total")
    if total is None:
        return "score_nonpositive"
    try:
        if float(total) <= 0:
            return "score_nonpositive"
    except (TypeError, ValueError):
        return "score_nonpositive"
    width = stop_pct(row, stop_mult=stop_mult)
    if width is not None and width > MAX_STOP_PCT:
        return "stop_wide"
    value = ev_pct(row, stop_mult=stop_mult, target_mult=target_mult)
    if value is not None and value <= MIN_EV_PCT:
        return "ev_nonpositive"
    return None


def annotate_buys(
    rows: list[dict[str, Any]],
    *,
    holdout_ok: bool = True,
    stop_mult: float = STOP_MULT,
    target_mult: float = TARGET_MULT,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        reason = buy_skip_reason(item, holdout_ok=holdout_ok, stop_mult=stop_mult, target_mult=target_mult)
        item["skip_reason"] = reason
        item["skip_label"] = skip_label(reason)
        item["buyable"] = reason is None
        width = stop_pct(item, stop_mult=stop_mult)
        item["stop_pct"] = None if width is None else round(width, 6)
        item["s33_nm"] = sector_name(item)
        out.append(item)
    return out


def sector_name(row: dict[str, Any]) -> str:
    raw = row.get("s33_nm")
    if raw is None:
        return UNKNOWN_SECTOR
    name = str(raw).strip()
    return name or UNKNOWN_SECTOR


def mark_already_held(rows: list[dict[str, Any]], held_codes: list[str] | None) -> list[dict[str, Any]]:
    from skabu.store import normalize_code

    held = {normalize_code(str(code)) for code in (held_codes or []) if str(code).strip()}
    if not held:
        return rows
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        code = normalize_code(str(item.get("code") or ""))
        if item.get("buyable") and code in held:
            item["buyable"] = False
            item["skip_reason"] = "already_held"
            item["skip_label"] = skip_label("already_held")
        out.append(item)
    return out


def apply_sector_cap(
    rows: list[dict[str, Any]],
    *,
    max_per_sector: int = MAX_PER_SECTOR,
    held_sectors: list[str] | None = None,
) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for name in held_sectors or []:
        sector = str(name).strip() or UNKNOWN_SECTOR
        counts[sector] = counts.get(sector, 0) + 1
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["s33_nm"] = sector_name(item)
        if item.get("buyable"):
            sector = item["s33_nm"]
            used = counts.get(sector, 0)
            if max_per_sector >= 1 and used >= max_per_sector:
                item["buyable"] = False
                item["skip_reason"] = "sector_cap"
                item["skip_label"] = skip_label("sector_cap")
            else:
                counts[sector] = used + 1
        out.append(item)
    return out


DEFAULT_RISK_POINTS = 1.0


def parse_risk_pct(value: Any, default_points: float = DEFAULT_RISK_POINTS) -> float:
    """画面・CLIの「リスク%」を割合にする。1 → 1%。"""
    fallback = default_points / 100.0 if default_points > 0 else DEFAULT_RISK_PCT
    if value in (None, ""):
        return fallback
    try:
        points = float(value)
    except (TypeError, ValueError):
        get_logger().warning("risk_pct parse failed value=%s", value)
        return fallback
    if points <= 0:
        get_logger().warning("risk_pct nonpositive value=%s fallback=%s", value, fallback)
        return fallback
    return points / 100.0


def size_position(
    *,
    cash: float,
    risk_pct: float,
    stop_width: float | None,
    price: float | None,
    leftover: float,
) -> dict[str, Any] | None:
    """金額 = (資金 × リスク%) / 損切幅%。1株未満は買わない。"""
    if cash <= 0 or leftover <= 0:
        return None
    if stop_width is None or stop_width <= 0:
        return None
    if price is None or price <= 0:
        return None
    risk_yen = cash * risk_pct
    alloc = min(risk_yen / stop_width, leftover)
    qty = int(alloc // price)
    if qty < 1:
        return None
    spent = qty * price
    sized = {
        "alloc": round(spent, 2),
        "qty": qty,
        "risk_yen": round(risk_yen, 2),
        "stop_pct": round(stop_width, 6),
    }
    get_logger().debug(
        "size_position cash=%.2f risk=%.4f stop=%.6f price=%.4f leftover=%.2f alloc=%.2f qty=%s",
        cash,
        risk_pct,
        stop_width,
        price,
        leftover,
        sized["alloc"],
        qty,
    )
    return sized


def stop_width_at(
    row: dict[str, Any],
    price: float | None = None,
    *,
    stop_mult: float = STOP_MULT,
) -> float | None:
    atr = row.get("atr")
    px = price if price not in (None, 0) else ref_price(row)
    if px is None or atr is None:
        return None
    try:
        atr_value = float(atr)
        px_value = float(px)
    except (TypeError, ValueError):
        return None
    if atr_value <= 0 or px_value <= 0:
        return None
    return float(stop_mult) * atr_value / px_value


def size_rows(
    rows: list[dict[str, Any]],
    *,
    cash: float,
    risk_pct: float = DEFAULT_RISK_PCT,
    leftover: float | None = None,
    prices: dict[str, float] | None = None,
    stop_mult: float = STOP_MULT,
) -> list[dict[str, Any]]:
    remain = cash if leftover is None else leftover
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        code = str(item.get("code") or "")
        price = None
        if prices and code in prices:
            price = prices[code]
        else:
            price = ref_price(item)
        width = stop_width_at(item, price, stop_mult=stop_mult)
        sized = size_position(
            cash=cash,
            risk_pct=risk_pct,
            stop_width=width,
            price=price,
            leftover=remain,
        )
        if sized is None:
            item["alloc"] = None
            item["qty"] = None
            item["risk_yen"] = None
        else:
            item["alloc"] = sized["alloc"]
            item["qty"] = sized["qty"]
            item["risk_yen"] = sized["risk_yen"]
            item["stop_pct"] = sized["stop_pct"]
            remain -= sized["alloc"]
        out.append(item)
    return out


def select_buys(
    rows: list[dict[str, Any]],
    *,
    top: int = 20,
    holdout_ok: bool = True,
    max_per_sector: int = MAX_PER_SECTOR,
    held_codes: list[str] | None = None,
    held_sectors: list[str] | None = None,
    stop_mult: float = STOP_MULT,
    target_mult: float = TARGET_MULT,
) -> dict[str, Any]:
    annotated = apply_sector_cap(
        mark_already_held(
            annotate_buys(rows, holdout_ok=holdout_ok, stop_mult=stop_mult, target_mult=target_mult),
            held_codes,
        ),
        max_per_sector=max_per_sector,
        held_sectors=held_sectors,
    )
    bought = [row for row in annotated if row.get("buyable")]
    if top >= 1:
        bought = bought[:top]
    skipped = [row for row in annotated if not row.get("buyable")]
    reasons: dict[str, int] = {}
    for row in skipped:
        key = str(row.get("skip_reason") or "other")
        reasons[key] = reasons.get(key, 0) + 1
    if not holdout_ok:
        get_logger().info("select_buys confirmation ignored holdout_ok=%s", holdout_ok)
    get_logger().info(
        "select_buys holdout_ok=%s scanned=%s bought=%s skipped=%s reasons=%s",
        holdout_ok,
        len(annotated),
        len(bought),
        len(skipped),
        reasons,
    )
    return {
        "bought": bought,
        "skipped": skipped,
        "scanned": annotated,
        "reasons": reasons,
        "holdout_ok": holdout_ok,
        "bought_n": len(bought),
        "skipped_n": len(skipped),
        "max_per_sector": max_per_sector,
    }


def holdout_return_ok(active: dict[str, Any] | None) -> bool:
    """買う判定は現金増減。TOPIX超過の holdout_ok は採点用。"""
    if not active:
        return True
    holdout = active.get("holdout") or {}
    raw = holdout.get("avg_return")
    if raw is not None:
        try:
            return float(raw) > 0
        except (TypeError, ValueError):
            get_logger().exception("holdout avg_return parse failed")
    return bool(active.get("holdout_ok"))


def active_holdout_ok(settings: Any | None) -> bool:
    if settings is None:
        return True
    try:
        from skabu.strategy import load_active_strategy

        active = load_active_strategy(settings)
    except Exception:
        get_logger().exception("active holdout read failed")
        return True
    return holdout_return_ok(active)


def ranking_holdout_ok(settings: Any | None, selections: list[dict[str, Any]] | None) -> bool:
    """採用戦略と同じ組み合わせなら確認期間ゲートを使う。違う組み合わせは探索として通す。"""
    adopted = active_holdout_ok(settings)
    if not selections:
        return adopted
    try:
        from skabu.ranking import normalize_selections
        from skabu.strategy import active_rank_selections

        same = normalize_selections(selections) == normalize_selections(active_rank_selections(settings))
    except Exception:
        get_logger().exception("holdout compare failed")
        return adopted
    if same:
        return adopted
    return True
