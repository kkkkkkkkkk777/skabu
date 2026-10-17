from datetime import date, timedelta

from skabu.indicators.plugins.elliott import ElliottPlugin, count_elliott
from skabu.ranking import passes_filters


SWING = 2
GAP = 8


def _price_bars(highs: list[float], lows: list[float], closes: list[float]) -> list[dict]:
    start = date(2024, 1, 2)
    rows = []
    for index, (high, low, close) in enumerate(zip(highs, lows, closes, strict=True)):
        rows.append(
            {
                "date": (start + timedelta(days=index)).isoformat(),
                "open": close,
                "high": high,
                "low": low,
                "close": close,
                "volume": 1,
            }
        )
    return rows


def _zigzag_bars(
    levels: list[tuple[str, float]],
    *,
    swing: int = SWING,
    gap: int = GAP,
    extra: list[float] | None = None,
) -> list[dict]:
    idxs = [swing + index * gap for index in range(len(levels))]
    last_idx = idxs[-1]
    extra = list(extra or [])
    n = last_idx + swing + 1 + len(extra)
    closes = [0.0] * n
    for index in range(idxs[0]):
        closes[index] = levels[0][1] + (3.0 if levels[0][0] == "L" else -3.0)
    for pos in range(len(levels) - 1):
        left, right = idxs[pos], idxs[pos + 1]
        start_px, end_px = levels[pos][1], levels[pos + 1][1]
        span = right - left
        for index in range(left, right + 1):
            t = (index - left) / span
            closes[index] = start_px + (end_px - start_px) * t
    hold = levels[-1][1]
    bump = 4.0 if levels[-1][0] == "L" else -4.0
    for index in range(last_idx + 1, last_idx + swing + 1):
        closes[index] = hold + bump
    for offset, price in enumerate(extra):
        closes[last_idx + swing + 1 + offset] = price
    highs = [close + 1.5 for close in closes]
    lows = [close - 1.5 for close in closes]
    for idx, (kind, price) in zip(idxs, levels, strict=True):
        if kind == "L":
            lows[idx] = price
            highs[idx] = price + 1.0
            closes[idx] = price + 0.3
            for neighbor in range(idx - swing, idx + swing + 1):
                if neighbor == idx or neighbor < 0 or neighbor >= n:
                    continue
                lows[neighbor] = max(lows[neighbor], price + 2.5)
                highs[neighbor] = max(highs[neighbor], lows[neighbor] + 1.0)
        else:
            highs[idx] = price
            lows[idx] = price - 1.0
            closes[idx] = price - 0.3
            for neighbor in range(idx - swing, idx + swing + 1):
                if neighbor == idx or neighbor < 0 or neighbor >= n:
                    continue
                highs[neighbor] = min(highs[neighbor], price - 2.5)
                lows[neighbor] = min(lows[neighbor], highs[neighbor] - 1.0)
    for index in range(n):
        if highs[index] < lows[index]:
            highs[index], lows[index] = lows[index], highs[index]
        closes[index] = min(highs[index], max(lows[index], closes[index]))
    return _price_bars(highs, lows, closes)


def _count(bars: list[dict]) -> dict:
    return count_elliott(bars, swing=SWING, lookback=120)


VALID5 = [
    ("L", 100.0),
    ("H", 140.0),
    ("L", 120.0),
    ("H", 200.0),
    ("L", 160.0),
    ("H", 240.0),
]


def test_valid_impulse_five() -> None:
    bars = _zigzag_bars(VALID5)
    counted = _count(bars)
    assert counted["debug"]["reason"] == "wave5_done"
    assert counted["wave5_done"] == 1
    assert counted["wave3_up"] == 0
    assert counted["correcting"] == 0
    labels = [item.text for item in counted["markers"]]
    assert labels[:5] == ["1", "2", "3", "4", "5"]
    assert counted["overlays"]


DOWN5 = [
    ("H", 240.0),
    ("L", 200.0),
    ("H", 220.0),
    ("L", 140.0),
    ("H", 180.0),
    ("L", 100.0),
]


def test_down_impulse_is_drawn_red() -> None:
    bars = _zigzag_bars(DOWN5)
    counted = _count(bars)
    assert counted["debug"].get("bear")
    assert counted["wave5_done"] == 0
    assert counted["wave3_up"] == 0
    assert counted["overlays"]
    assert [item.text for item in counted["markers"]][:5] == ["1", "2", "3", "4", "5"]
    assert counted["overlays"][0].color == "#ef5350"


def test_bounce_inside_down_is_not_wave3() -> None:
    bars = _zigzag_bars(
        [
            ("H", 240.0),
            ("L", 200.0),
            ("H", 220.0),
            ("L", 140.0),
            ("H", 160.0),
            ("L", 150.0),
        ],
        extra=[165.0, 168.0, 170.0],
    )
    counted = _count(bars)
    assert counted["wave3_up"] == 0
    assert passes_filters(
        bars,
        [{"id": "elliott", "feature": "wave3_up", "op": "eq", "value": 1, "params": {"swing": SWING, "lookback": 120}}],
    ) is False


def test_new_up_after_bear5_is_wave3() -> None:
    bars = _zigzag_bars(
        DOWN5
        + [
            ("H", 130.0),
            ("L", 110.0),
        ],
        extra=[140.0, 145.0, 150.0],
    )
    counted = _count(bars)
    assert counted["wave3_up"] == 1
    assert counted["debug"].get("bear")
    texts = [item.text for item in counted["markers"]]
    assert "3?" in texts
    assert "1" in texts


def test_wave2_break_is_not_counted() -> None:
    bars = _zigzag_bars(
        [
            ("L", 100.0),
            ("H", 140.0),
            ("L", 99.0),
            ("H", 200.0),
            ("L", 160.0),
            ("H", 240.0),
        ]
    )
    counted = _count(bars)
    assert counted["wave5_done"] == 0
    assert counted["wave3_up"] == 0
    assert counted["debug"]["reason"] in {"no_bull", "few_swings", "no_pattern"}


def test_wave4_overlap_is_not_counted() -> None:
    bars = _zigzag_bars(
        [
            ("L", 100.0),
            ("H", 140.0),
            ("L", 120.0),
            ("H", 200.0),
            ("L", 135.0),
            ("H", 240.0),
        ]
    )
    counted = _count(bars)
    assert counted["wave5_done"] == 0
    assert counted["wave3_up"] == 0


def test_wave3_shortest_is_not_counted() -> None:
    bars = _zigzag_bars(
        [
            ("L", 100.0),
            ("H", 120.0),
            ("L", 108.0),
            ("H", 118.0),
            ("L", 122.0),
            ("H", 160.0),
        ]
    )
    counted = _count(bars)
    assert counted["wave5_done"] == 0
    assert counted["wave3_up"] == 0


def test_wave3_in_progress() -> None:
    bars = _zigzag_bars(
        [("L", 100.0), ("H", 140.0), ("L", 120.0)],
        extra=[148.0, 152.0, 156.0],
    )
    counted = _count(bars)
    assert counted["wave3_up"] == 1
    assert counted["wave5_done"] == 0
    assert counted["correcting"] == 0
    texts = [item.text for item in counted["markers"]]
    assert "3?" in texts


def test_wave5_then_correcting_by_close() -> None:
    bars = _zigzag_bars(VALID5, extra=[230.0, 225.0, 220.0])
    counted = _count(bars)
    assert counted["wave5_done"] == 1
    assert counted["correcting"] == 1
    assert counted["wave3_up"] == 0


def test_wave5_then_abc() -> None:
    bars = _zigzag_bars(
        VALID5
        + [
            ("L", 200.0),
            ("H", 220.0),
            ("L", 180.0),
        ]
    )
    counted = _count(bars)
    assert counted["wave5_done"] == 1
    assert counted["correcting"] == 1
    texts = [item.text for item in counted["markers"]]
    assert "A" in texts and "B" in texts and "C" in texts


def test_no_pattern_wave3_gate_drops_wave5_gate_keeps() -> None:
    closes = [100.0 + index * 0.4 for index in range(40)]
    highs = [close + 1.0 for close in closes]
    lows = [close - 1.0 for close in closes]
    bars = _price_bars(highs, lows, closes)
    counted = _count(bars)
    assert counted["wave3_up"] == 0
    assert counted["wave5_done"] == 0
    assert passes_filters(bars, [{"id": "elliott", "feature": "wave3_up", "op": "eq", "value": 1}]) is False
    assert passes_filters(bars, [{"id": "elliott", "feature": "wave5_done", "op": "eq", "value": 0}]) is True
    assert passes_filters(bars, [{"id": "elliott", "feature": "correcting", "op": "eq", "value": 0}]) is True
    assert passes_filters(bars, [{"id": "facts", "feature": "op_margin", "op": "gt", "value": 0}], {"op_margin": 0.08}) is True


def test_wave3_gate_keeps_in_progress() -> None:
    bars = _zigzag_bars(
        [("L", 100.0), ("H", 140.0), ("L", 120.0)],
        extra=[148.0, 152.0, 156.0],
    )
    feats = ElliottPlugin().compute(bars, {"swing": SWING, "lookback": 120}).features
    assert feats["wave3_up"] == 1
    assert "score" not in feats
    gates_params = {"swing": SWING, "lookback": 120}
    assert (
        passes_filters(
            bars,
            [{"id": "elliott", "feature": "wave3_up", "op": "eq", "value": 1, "params": gates_params}],
        )
        is True
    )
    assert (
        passes_filters(
            bars,
            [{"id": "elliott", "feature": "wave5_done", "op": "eq", "value": 0, "params": gates_params}],
        )
        is True
    )
