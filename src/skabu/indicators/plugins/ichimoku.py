from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from skabu.indicators.base import (
    IndicatorResult,
    LineSeries,
    ParamSpec,
    coerce_params,
    last_number,
    line_points,
)
from skabu.indicators.mathutil import ichimoku


def _forward_dates(last: str, count: int) -> list[str]:
    start = date.fromisoformat(last)
    return [(start + timedelta(days=offset + 1)).isoformat() for offset in range(count)]


def _lead_points(times: list[str], raw: list[float | None], displacement: int) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    if not times:
        return points
    extra = _forward_dates(times[-1], displacement)
    size = len(times)
    for index, value in enumerate(raw):
        if value is None:
            continue
        lead = index + displacement
        if lead < size:
            points.append({"time": times[lead], "value": round(float(value), 6)})
        else:
            extra_index = lead - size
            if 0 <= extra_index < len(extra):
                points.append({"time": extra[extra_index], "value": round(float(value), 6)})
    return points


class IchimokuPlugin:
    id = "ichimoku"
    group = "trend"
    title = "一目均衡"
    description = "転換・基準・先行1/2・遅行"
    help = (
        "転換線・基準線・雲（先行）・遅行線など、何本かの線で「今の立ち位置」を見る道具です。雲は抵抗や支えのように見ます。"
        "\n\n読み取り: 値段が雲の上なら強気、雲の中は迷い、雲の下は弱気、とよく言われます。"
        "例: 長い間雲の下にいた株が、雲を上に抜けたら、流れが変わった合図です。"
        "\n\n点: 終値が雲の上なら点を足します。門の「雲の上」（残す／消す）とは別です。"
    )
    pane = "price"
    enabled_by_default = False
    params = [
        ParamSpec("tenkan", "転換", "int", 9, 2, 40),
        ParamSpec("kijun", "基準", "int", 26, 5, 80),
        ParamSpec("senkou_b", "先行2", "int", 52, 10, 120),
        ParamSpec("displacement", "先行幅", "int", 26, 1, 80),
    ]

    def compute(self, bars: list[dict[str, Any]], params: dict[str, Any]) -> IndicatorResult:
        resolved = coerce_params(self.params, params)
        tenkan_len = resolved["tenkan"]
        kijun_len = resolved["kijun"]
        senkou_b_len = resolved["senkou_b"]
        displacement = resolved["displacement"]
        times = [str(bar["date"]) for bar in bars]
        highs = [bar.get("high") for bar in bars]
        lows = [bar.get("low") for bar in bars]
        closes = [bar.get("close") for bar in bars]
        series = ichimoku(highs, lows, closes, tenkan_len, kijun_len, senkou_b_len, displacement)
        last_close = last_number(closes)
        last_tenkan = last_number(series["tenkan"])
        last_kijun = last_number(series["kijun"])
        last_span_a = last_number(series["span_a"])
        last_span_b = last_number(series["span_b"])
        score = None
        above_cloud = None
        tenkan_above = None
        if last_close is not None and last_span_a is not None and last_span_b is not None:
            cloud_top = max(last_span_a, last_span_b)
            cloud_bottom = min(last_span_a, last_span_b)
            above_cloud = int(last_close > cloud_top)
            score = 1 if last_close > cloud_top else 0
            if last_close < cloud_bottom:
                score = 0
        if last_tenkan is not None and last_kijun is not None:
            tenkan_above = int(last_tenkan > last_kijun)
            extra = 1 if last_tenkan > last_kijun else 0
            score = extra if score is None else score + extra
        return IndicatorResult(
            plugin_id=self.id,
            title=self.title,
            pane=self.pane,
            overlays=[
                LineSeries(id="ichi_tenkan", label=f"転換({tenkan_len})", color="#38bdf8", points=line_points(times, series["tenkan"])),
                LineSeries(id="ichi_kijun", label=f"基準({kijun_len})", color="#f97316", points=line_points(times, series["kijun"])),
                LineSeries(
                    id="ichi_span_a",
                    label="先行1",
                    color="#26a69a",
                    points=_lead_points(times, series["span_a_raw"], displacement),
                    width=1,
                ),
                LineSeries(
                    id="ichi_span_b",
                    label="先行2",
                    color="#ef5350",
                    points=_lead_points(times, series["span_b_raw"], displacement),
                    width=1,
                ),
                LineSeries(id="ichi_chikou", label="遅行", color="#a78bfa", points=line_points(times, series["chikou"]), width=1),
            ],
            features={
                "value": last_tenkan,
                "kijun": last_kijun,
                "span_a": last_span_a,
                "span_b": last_span_b,
                "above_cloud": above_cloud,
                "tenkan_above": tenkan_above,
                "score": score,
            },
            debug={
                "tenkan": tenkan_len,
                "kijun": kijun_len,
                "senkou_b": senkou_b_len,
                "displacement": displacement,
                "points": sum(1 for item in series["tenkan"] if item is not None),
            },
        )
