from __future__ import annotations

from typing import Any

from skabu.indicators.base import (
    IndicatorResult,
    LineSeries,
    ParamSpec,
    coerce_params,
    last_number,
    line_points,
)
from skabu.indicators.mathutil import supertrend


class SupertrendPlugin:
    id = "supertrend"
    group = "trend"
    title = "Supertrend"
    description = "ATR ベースのトレンドライン"
    help = (
        "値動きの幅（ATR）を使って、「今は上向きか下向きか」を1本の線で示します。"
        "線の上に値段があれば上向き、下なら下向きです。"
        "\n\n読み取り: 線の位置が変わったら、流れが反転した合図です。"
        "例: 下向きの線の下にいた値段が、上向きの線の上に出たら、下降から上昇へ切り替わった、と見ます。"
        "\n\n点: 上向きなら＋1、下向きなら−1です。"
    )
    pane = "price"
    enabled_by_default = False
    params = [
        ParamSpec("length", "ATR期間", "int", 10, 2, 80),
        ParamSpec("mult", "倍率", "float", 3.0, 0.5, 10.0, 0.1),
    ]

    def compute(self, bars: list[dict[str, Any]], params: dict[str, Any]) -> IndicatorResult:
        resolved = coerce_params(self.params, params)
        length = resolved["length"]
        mult = resolved["mult"]
        times = [str(bar["date"]) for bar in bars]
        highs = [bar.get("high") for bar in bars]
        lows = [bar.get("low") for bar in bars]
        closes = [bar.get("close") for bar in bars]
        line, trend = supertrend(highs, lows, closes, length, mult)
        last_line = last_number(line)
        last_trend = None
        for value in reversed(trend):
            if value is not None:
                last_trend = int(value)
                break
        score = None if last_trend is None else (1 if last_trend > 0 else -1)
        up_line: list[float | None] = [value if direction == 1 else None for value, direction in zip(line, trend, strict=True)]
        down_line: list[float | None] = [value if direction == -1 else None for value, direction in zip(line, trend, strict=True)]
        return IndicatorResult(
            plugin_id=self.id,
            title=self.title,
            pane=self.pane,
            overlays=[
                LineSeries(id="st_up", label=f"ST↑({length})", color="#26a69a", points=line_points(times, up_line)),
                LineSeries(id="st_down", label=f"ST↓({length})", color="#ef5350", points=line_points(times, down_line)),
            ],
            features={
                "value": last_line,
                "trend": last_trend,
                "uptrend": None if last_trend is None else int(last_trend > 0),
                "score": score,
            },
            debug={"length": length, "mult": mult, "points": sum(1 for item in line if item is not None)},
        )
