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
from skabu.indicators.mathutil import atr


class AtrPlugin:
    id = "atr"
    group = "power"
    title = "ATR"
    description = "Average True Range（別ペイン）"
    help = (
        "1日の値動きの大きさの平均です。「今日はどれくらい振れたか」を円で表します。下の段に出ます。"
        "\n\n読み取り: ATRが大きい日は、上がるにしても下がるにしても幅が大きいです。"
        "例: 1000円の株でATRが30なら、1日で30円くらい動くのが普通、という感じです。"
        "\n\n点: 昨日より幅が広がると＋1、狭まると−1です。動きが活発になってきたか、落ち着いてきたかを見ます。"
    )
    pane = "oscillator"
    enabled_by_default = False
    params = [
        ParamSpec("length", "期間", "int", 14, 2, 80),
    ]

    def compute(self, bars: list[dict[str, Any]], params: dict[str, Any]) -> IndicatorResult:
        resolved = coerce_params(self.params, params)
        length = resolved["length"]
        times = [str(bar["date"]) for bar in bars]
        highs = [bar.get("high") for bar in bars]
        lows = [bar.get("low") for bar in bars]
        closes = [bar.get("close") for bar in bars]
        values = atr(highs, lows, closes, length)
        last = last_number(values)
        prev: float | None = None
        seen = 0
        for value in reversed(values):
            if value is None:
                continue
            seen += 1
            if seen == 2:
                prev = float(value)
                break
        if last is None or prev is None:
            score = None
        elif last > prev:
            score = 1
        elif last < prev:
            score = -1
        else:
            score = 0
        return IndicatorResult(
            plugin_id=self.id,
            title=self.title,
            pane=self.pane,
            overlays=[
                LineSeries(
                    id="atr",
                    label=f"ATR({length})",
                    color="#f59e0b",
                    points=line_points(times, values),
                    pane="oscillator",
                )
            ],
            features={
                "value": last,
                "prev": prev,
                "expanding": None if last is None or prev is None else int(last > prev),
                "score": score,
            },
            debug={"length": length, "points": sum(1 for item in values if item is not None)},
        )
