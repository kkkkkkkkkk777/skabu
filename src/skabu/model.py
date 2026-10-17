"""20日以内の目標先着を当てる勾配ブースティング。未来足は使わない。"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from skabu.config import Settings
from skabu.errors import SkabuError
from skabu.logging_setup import dump_debug_event, get_logger
from skabu.ranking import lookback_len
from skabu.store import (
    all_bar_dates,
    connect,
    finish_model_run,
    latest_model_run,
    list_universe,
    load_daily_bars_since,
    load_price_map,
    recent_bar_dates,
    start_model_run,
)
from skabu.levels import (
    LABEL_SPEC,
    STOP_MULT,
    TARGET_MULT,
    bars_from_prices,
    compute_levels,
    exit_pair,
    levels_spec,
    path_from_prices,
    simulate_trade,
)
from skabu.verify import BENCH_CODE, _ret, pick_as_of_dates

LABEL_TARGET_FIRST = LABEL_SPEC
LABEL_RET_POS = "ret_pos"
LABEL_EXCESS_POS = "excess_pos"
ARRIVAL_STOP = 0
ARRIVAL_TARGET = 1
ARRIVAL_TIME = 2
ARRIVAL_REASONS = {
    "stop": ARRIVAL_STOP,
    "target": ARRIVAL_TARGET,
    "time": ARRIVAL_TIME,
}
KNOWN_LABELS = (LABEL_TARGET_FIRST, LABEL_RET_POS, LABEL_EXCESS_POS)
COMPARE_LABELS = (LABEL_RET_POS, LABEL_EXCESS_POS)
LABEL_MODEL_NAMES = {
    LABEL_RET_POS: "boost.ret_pos",
    LABEL_EXCESS_POS: "boost.excess_pos",
}

FEATURE_SELECTIONS: list[dict[str, Any]] = [
    {"id": "rsi", "params": {"length": 14}},
    {"id": "sma", "params": {"length": 25}},
    {"id": "ema", "params": {"length": 25}},
    {"id": "macd", "params": {}},
    {"id": "adx", "params": {}},
    {"id": "supertrend", "params": {}},
    {"id": "ichimoku", "params": {}},
    {"id": "bbands", "params": {}},
]

FEATURE_NAMES = [
    "rsi.value",
    "rsi.score",
    "sma.score",
    "ema.score",
    "macd.hist",
    "macd.score",
    "adx.value",
    "adx.plus_above",
    "adx.score",
    "supertrend.uptrend",
    "ichimoku.above_cloud",
    "bbands.price_above",
    "ret_5",
    "ret_20",
    "vol_ratio",
]

def active_feature_selections() -> list[dict[str, Any]]:
    """学習に使うインジケータ。"""
    return list(FEATURE_SELECTIONS)


def active_feature_names() -> list[str]:
    """学習・推論の列名。"""
    return list(FEATURE_NAMES)


_FEATURE_MAP = (
    ("rsi", (("value", "rsi.value"), ("score", "rsi.score"))),
    ("sma", (("score", "sma.score"),)),
    ("ema", (("score", "ema.score"),)),
    ("macd", (("hist", "macd.hist"), ("score", "macd.score"))),
    ("adx", (("value", "adx.value"), ("plus_above", "adx.plus_above"), ("score", "adx.score"))),
    ("supertrend", (("uptrend", "supertrend.uptrend"),)),
    ("ichimoku", (("above_cloud", "ichimoku.above_cloud"),)),
    ("bbands", (("price_above", "bbands.price_above"),)),
)

MODEL_NAME = "boost"
CANDIDATE_NAME = "boost.candidate"
PREV_NAME = "boost.prev"
_CACHE: dict[str, Any] | None = None
_CACHE_KEY: tuple[str, float] | None = None
_LOCK = threading.Lock()
_TLS = threading.local()


def model_dir(settings: Settings) -> Path:
    return settings.skabu_data_dir / "models"


def active_model_name() -> str:
    return str(getattr(_TLS, "name", None) or MODEL_NAME)


def use_model(name: str | None):
    previous = getattr(_TLS, "name", None)

    class _Guard:
        def __enter__(self):
            _TLS.name = name
            _clear_cache()
            return self

        def __exit__(self, exc_type, exc, tb):
            _TLS.name = previous
            _clear_cache()
            return False

    return _Guard()


def model_path(settings: Settings, name: str | None = None) -> Path:
    return model_dir(settings) / f"{name or active_model_name()}.joblib"


def meta_path(settings: Settings, name: str | None = None) -> Path:
    return model_dir(settings) / f"{name or active_model_name()}.meta.json"


def normalize_label(label: str | None) -> str:
    value = str(label or LABEL_TARGET_FIRST).strip() or LABEL_TARGET_FIRST
    if value not in KNOWN_LABELS:
        raise SkabuError(f"不明なラベルです: {value}。使えるのは {', '.join(KNOWN_LABELS)} です。")
    return value


def arrival_class(reason: str | None) -> int | None:
    if reason is None:
        return None
    return ARRIVAL_REASONS.get(str(reason))


def sample_y(
    *,
    label: str,
    y_hit: Any,
    ret: Any,
    excess: Any,
    reason: str | None = None,
) -> int | None:
    """トレード結果を学習ラベルにする。excess_pos で超過が無い行は None（捨てる）。"""
    kind = normalize_label(label)
    if kind == LABEL_TARGET_FIRST:
        arrived = arrival_class(reason)
        if arrived is not None:
            return arrived
        return int(y_hit)
    if kind == LABEL_RET_POS:
        if ret is None:
            return None
        return int(float(ret) > 0)
    if excess is None:
        return None
    try:
        return int(float(excess) > 0)
    except (TypeError, ValueError):
        return None


def extract_features(bars: list[dict[str, Any]]) -> dict[str, float] | None:
    from skabu.indicators.registry import compute_indicators

    results = compute_indicators(bars, active_feature_selections(), features_only=True)
    by_id = {item.get("id"): item for item in results}
    out: dict[str, float] = {}
    for plugin_id, pairs in _FEATURE_MAP:
        item = by_id.get(plugin_id) or {}
        if item.get("error"):
            return None
        feats = item.get("features") or {}
        for src, dest in pairs:
            value = feats.get(src)
            if value is None:
                continue
            out[dest] = float(value)
    closes = [bar.get("close") for bar in bars]
    volumes = [bar.get("volume") for bar in bars]
    ret_5 = _past_ret(closes, 5)
    ret_20 = _past_ret(closes, 20)
    vol_ratio = _vol_ratio(volumes, 20)
    if ret_5 is None or ret_20 is None or vol_ratio is None:
        return None
    out["ret_5"] = ret_5
    out["ret_20"] = ret_20
    out["vol_ratio"] = vol_ratio
    return out


def feature_vector(feats: dict[str, float], names: list[str] | None = None) -> list[float] | None:
    values = []
    for name in names or active_feature_names():
        value = feats.get(name)
        if value is None:
            return None
        values.append(float(value))
    return values


def predict_proba(bars: list[dict[str, Any]], settings: Settings | None = None) -> float | None:
    loaded = load_model(settings)
    if loaded is None:
        return None
    feats = extract_features(bars)
    if feats is None:
        return None
    names = list((loaded.get("meta") or {}).get("feature_names") or active_feature_names())
    vector = feature_vector(feats, names)
    if vector is None:
        return None
    return _class_proba(loaded["clf"], vector, ARRIVAL_TARGET)


def predict_arrivals(bars: list[dict[str, Any]], settings: Settings | None = None) -> dict[str, Any] | None:
    """目標が先・損切が先・期限までの3つの確率。古い2値モデルは目標だけ返す。"""
    loaded = load_model(settings)
    if loaded is None:
        return None
    feats = extract_features(bars)
    if feats is None:
        return None
    names = list((loaded.get("meta") or {}).get("feature_names") or active_feature_names())
    vector = feature_vector(feats, names)
    if vector is None:
        return None
    clf = loaded["clf"]
    classes = {int(item) for item in list(clf.classes_)}
    target = _class_proba(clf, vector, ARRIVAL_TARGET)
    split = ARRIVAL_TIME in classes and ARRIVAL_STOP in classes
    if not split:
        get_logger().info("arrival model is binary classes=%s", sorted(classes))
    return {
        "target": target,
        "stop": _class_proba(clf, vector, ARRIVAL_STOP) if split else None,
        "time": _class_proba(clf, vector, ARRIVAL_TIME) if split else None,
        "split": split,
    }


def _class_proba(clf: Any, vector: list[float], label: int) -> float | None:
    proba = clf.predict_proba([vector])[0]
    classes = [int(item) for item in list(clf.classes_)]
    if label in classes:
        return float(proba[classes.index(label)])
    if label == ARRIVAL_TARGET and classes:
        return float(proba[-1])
    return None


def load_model(settings: Settings | None = None) -> dict[str, Any] | None:
    global _CACHE, _CACHE_KEY
    settings = settings or _try_settings()
    if settings is None:
        return None
    path = model_path(settings)
    if not path.exists():
        return None
    cache_key = (str(path), path.stat().st_mtime)
    if _CACHE is not None and _CACHE_KEY == cache_key:
        return _CACHE
    try:
        import joblib
    except ImportError:
        get_logger().warning("joblib/sklearn が未導入のためモデルを読めません")
        return None
    with _LOCK:
        clf = joblib.load(path)
        meta = {}
        meta_file = meta_path(settings)
        if meta_file.exists():
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
        _CACHE = {"clf": clf, "meta": meta, "path": str(path), "name": active_model_name()}
        _CACHE_KEY = cache_key
        return _CACHE


def latest_model_payload(settings: Settings) -> dict[str, Any]:
    with connect(settings.db_path) as conn:
        last = latest_model_run(conn)
    loaded = load_model(settings)
    return {
        "last": last,
        "ready": loaded is not None,
        "path": str(model_path(settings)) if loaded else None,
        "meta": (loaded or {}).get("meta") or (last or {}).get("meta"),
    }


def train_model(
    settings: Settings,
    *,
    market: str = "プライム",
    min_avg_volume: float = 50_000,
    min_bars: int = 60,
    stride: int = 20,
    min_history: int = 80,
    horizon: int = 20,
    bench_code: str = BENCH_CODE,
    min_samples: int = 20,
    name: str = MODEL_NAME,
    label: str | None = None,
) -> dict[str, Any]:
    try:
        from sklearn.ensemble import HistGradientBoostingClassifier
        from sklearn.metrics import accuracy_score, roc_auc_score
        import joblib
    except ImportError as exc:
        raise SkabuError("学習には scikit-learn が必要です。`python -m pip install scikit-learn`") from exc

    settings.ensure_dirs()
    model_dir(settings).mkdir(parents=True, exist_ok=True)
    kind = normalize_label(label)
    if kind != LABEL_TARGET_FIRST and name == MODEL_NAME:
        raise SkabuError(
            f"{kind} は {LABEL_MODEL_NAMES.get(kind, '別名')} へ書いてください。本番 {MODEL_NAME} は上書きしません。"
        )
    log = get_logger()
    with connect(settings.db_path) as conn:
        run_id = start_model_run(conn, horizon=horizon)
    try:
        with connect(settings.db_path) as conn:
            stop_mult, target_mult = exit_pair(settings)
            samples = collect_samples(
                conn,
                market=market,
                min_avg_volume=min_avg_volume,
                min_bars=min_bars,
                stride=stride,
                min_history=min_history,
                horizon=horizon,
                bench_code=bench_code,
                label=kind,
                stop_mult=stop_mult,
                target_mult=target_mult,
            )
        if len(samples) < min_samples:
            raise SkabuError(f"学習サンプルが足りません（{len(samples)}件）。日足を増やしてください。")
        dates = sorted({item["as_of"] for item in samples})
        cut_index = max(1, int(len(dates) * 0.7))
        cut = dates[cut_index - 1]
        train = [item for item in samples if item["as_of"] <= cut]
        valid = [item for item in samples if item["as_of"] > cut]
        if len(train) < 15:
            train = samples
            valid = []
        x_train = [item["x"] for item in train]
        y_train = [item["y"] for item in train]
        if len(set(y_train)) < 2:
            raise SkabuError("学習ラベルが一方に偏っています。期間を広げてください。")
        clf = HistGradientBoostingClassifier(
            max_depth=4,
            max_iter=80,
            learning_rate=0.08,
            min_samples_leaf=8,
            random_state=42,
        )
        clf.fit(x_train, y_train)
        metrics = {
            "n_samples": len(samples),
            "n_train": len(train),
            "n_valid": len(valid),
            "train_as_of": [dates[0], cut],
            "valid_as_of": [valid[0]["as_of"], valid[-1]["as_of"]] if valid else [],
            "train_accuracy": round(float(accuracy_score(y_train, clf.predict(x_train))), 4),
            "valid_accuracy": None,
            "valid_auc": None,
            "pos_rate_train": round(sum(int(item == ARRIVAL_TARGET) for item in y_train) / len(y_train), 4),
            "pos_rate": round(sum(int(item["y"] == ARRIVAL_TARGET) for item in samples) / len(samples), 4),
            "feature_gap": _feature_gap(train),
        }
        if valid:
            x_valid = [item["x"] for item in valid]
            y_valid = [item["y"] for item in valid]
            pred = clf.predict(x_valid)
            metrics["valid_accuracy"] = round(float(accuracy_score(y_valid, pred)), 4)
            metrics["pos_rate_valid"] = round(sum(int(item == ARRIVAL_TARGET) for item in y_valid) / len(y_valid), 4)
            y_bin = [int(item == ARRIVAL_TARGET) for item in y_valid]
            classes = [int(item) for item in list(clf.classes_)]
            if ARRIVAL_TARGET in classes and len(set(y_bin)) == 2:
                proba = clf.predict_proba(x_valid)[:, classes.index(ARRIVAL_TARGET)]
                metrics["valid_auc"] = round(float(roc_auc_score(y_bin, proba)), 4)
        dest = model_path(settings, name)
        dest_meta = meta_path(settings, name)
        joblib.dump(clf, dest)
        meta = {
            "feature_names": active_feature_names(),
            "horizon": horizon,
            "label": kind,
            "arrival_classes": {"stop": ARRIVAL_STOP, "target": ARRIVAL_TARGET, "time": ARRIVAL_TIME},
            "levels": levels_spec(stop_mult, target_mult),
            "bench_code": bench_code,
            "market": market,
            "name": name,
            "trained_at": datetime.now(timezone.utc).isoformat(),
            "metrics": metrics,
        }
        dest_meta.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        if name == active_model_name():
            _clear_cache()
        with connect(settings.db_path) as conn:
            finished = finish_model_run(
                conn,
                run_id,
                status="ok" if name == MODEL_NAME else "candidate",
                horizon=horizon,
                n_train=len(train),
                n_valid=len(valid),
                accuracy=metrics.get("valid_accuracy") or metrics.get("train_accuracy"),
                auc=metrics.get("valid_auc"),
                model_path=str(dest),
                meta=meta,
            )
        log.info(
            "model trained name=%s label=%s samples=%s train=%s valid=%s acc=%s auc=%s cut=%s",
            name,
            kind,
            len(samples),
            len(train),
            len(valid),
            metrics.get("valid_accuracy"),
            metrics.get("valid_auc"),
            cut,
        )
        return finished
    except Exception as exc:
        log.exception("model train failed")
        dump_debug_event(settings.debug_dir, {"kind": "model_train", "error": str(exc)})
        with connect(settings.db_path) as conn:
            finish_model_run(conn, run_id, status="failed", horizon=horizon, error=str(exc))
        raise


def collect_samples(
    conn: Any,
    *,
    market: str,
    min_avg_volume: float,
    min_bars: int,
    stride: int,
    min_history: int,
    horizon: int,
    bench_code: str,
    label: str | None = None,
    stop_mult: float = STOP_MULT,
    target_mult: float = TARGET_MULT,
) -> list[dict[str, Any]]:
    dates = all_bar_dates(conn)
    as_ofs = pick_as_of_dates(dates, min_history=min_history, stride=stride, horizon=horizon)
    if not as_ofs:
        return []
    index = {day: i for i, day in enumerate(dates)}
    lookback = lookback_len(active_feature_selections())
    kind = normalize_label(label)
    samples: list[dict[str, Any]] = []
    codes_all: set[str] = {bench_code}
    staged: list[dict[str, Any]] = []
    dropped = 0
    log = get_logger()
    for as_of in as_ofs:
        window = recent_bar_dates(conn, lookback, as_of)
        if not window:
            continue
        universe = list_universe(
            conn,
            market=market,
            min_avg_volume=min_avg_volume,
            min_bars=min(min_bars, 20),
            vol_days=20,
            as_of=as_of,
        )
        codes = [item["code"] for item in universe]
        by_code = load_daily_bars_since(conn, window[0], as_of, codes)
        kept = 0
        for code in codes:
            bars = by_code.get(code) or []
            if len(bars) < min_bars:
                continue
            feats = extract_features(bars)
            if feats is None:
                continue
            vector = feature_vector(feats)
            if vector is None:
                continue
            staged.append({"as_of": as_of, "code": code, "x": vector})
            codes_all.add(code)
            kept += 1
        log.info("model samples as_of=%s kept=%s universe=%s", as_of, kept, len(universe))
    if not staged:
        return []
    prices = load_price_map(conn, codes_all, dates[0], dates[-1])
    bench = prices.get(bench_code, {})
    for item in staged:
        as_of = item["as_of"]
        if as_of not in index:
            continue
        pred_i = index[as_of]
        entry_i = pred_i + 1
        exit_i = entry_i + horizon
        if exit_i >= len(dates):
            continue
        entry_date = dates[entry_i]
        exit_date = dates[exit_i]
        series = prices.get(item["code"], {})
        entry = (series.get(entry_date) or {}).get("open")
        hist = bars_from_prices(series, dates, as_of)
        atr_value = compute_levels(hist).get("atr")
        path = path_from_prices(series, dates, entry_date, exit_date)
        trade = simulate_trade(
            entry,
            atr_value,
            path,
            horizon=horizon,
            stop_mult=stop_mult,
            target_mult=target_mult,
        )
        if trade.get("reason") is None or trade.get("ret") is None:
            continue
        bench_ret = _ret(
            (bench.get(entry_date) or {}).get("open"),
            (bench.get(trade.get("exit_date") or exit_date) or {}).get("close"),
        )
        excess = None if bench_ret is None else trade["ret"] - bench_ret
        y_value = sample_y(
            label=kind,
            y_hit=trade.get("y_hit"),
            ret=trade.get("ret"),
            excess=excess,
            reason=trade.get("reason"),
        )
        if y_value is None:
            dropped += 1
            continue
        samples.append(
            {
                **item,
                "y": y_value,
                "excess": excess,
                "ret": trade.get("ret"),
                "y_hit": int(trade["y_hit"]),
                "reason": trade.get("reason"),
                "label": kind,
            }
        )
    if kind == LABEL_TARGET_FIRST and samples:
        counts = {name: sum(int(item["y"] == code) for item in samples) for name, code in ARRIVAL_REASONS.items()}
        log.info("model arrival counts target=%s stop=%s time=%s", counts.get("target"), counts.get("stop"), counts.get("time"))
    if dropped:
        log.info("model samples dropped label=%s n=%s kept=%s", kind, dropped, len(samples))
    return samples


def _past_ret(closes: list[Any], length: int) -> float | None:
    if len(closes) <= length:
        return None
    start = closes[-1 - length]
    end = closes[-1]
    if start in (None, 0) or end is None:
        return None
    return (float(end) / float(start)) - 1.0


def _vol_ratio(volumes: list[Any], length: int) -> float | None:
    if len(volumes) < length:
        return None
    window = volumes[-length:]
    if any(item is None for item in window):
        return None
    avg = sum(float(item) for item in window) / length
    last = float(window[-1])
    if avg == 0:
        return None
    return last / avg


def _feature_gap(samples: list[dict[str, Any]]) -> dict[str, float]:
    pos = [item["x"] for item in samples if item["y"] == ARRIVAL_TARGET]
    neg = [item["x"] for item in samples if item["y"] != ARRIVAL_TARGET]
    if not pos or not neg:
        return {}
    gap = {}
    for index, name in enumerate(active_feature_names()):
        mean_pos = sum(row[index] for row in pos) / len(pos)
        mean_neg = sum(row[index] for row in neg) / len(neg)
        gap[name] = round(mean_pos - mean_neg, 6)
    return gap


def _clear_cache() -> None:
    global _CACHE, _CACHE_KEY
    _CACHE = None
    _CACHE_KEY = None


def _try_settings() -> Settings | None:
    try:
        from skabu.config import load_settings

        return load_settings()
    except Exception:
        return None


def model_fingerprint(settings: Settings | None = None, name: str | None = None) -> str:
    settings = settings or _try_settings()
    if settings is None:
        return ""
    path = meta_path(settings, name)
    if path.exists():
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            get_logger().exception("model meta read failed")
            return ""
        trained = str(meta.get("trained_at") or "")
        names = ",".join(str(item) for item in meta.get("feature_names") or [])
        label = str(meta.get("label") or "")
        return f"{trained}|{names}|{label}"
    joblib_path = model_path(settings, name)
    if joblib_path.exists():
        return str(int(joblib_path.stat().st_mtime))
    return ""
