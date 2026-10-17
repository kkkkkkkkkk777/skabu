from __future__ import annotations

from typing import Any

from skabu.indicators.base import (
    IndicatorResult,
    LineSeries,
    ParamSpec,
    coerce_params,
    last_number,
    line_points,
    source_series,
)
from skabu.indicators.mathutil import sma


class SmaPlugin:
    id = "sma"
    group = "trend"
    title = "SMA"
    description = "単純移動平均"
    help = (
        "直近いくつかの終値を足して割った、ただの平均線です。期間25なら、だいたい直近25日の平均です。"
        "\n\n読み取り: 値段が平均より上なら「最近より高い」、下なら「最近より安い」です。"
        "例: 平均980円、今日の終値1020円なら平均より上で強め。平均を下から上に抜けるのも、流れが上に変わった合図です。"
        "\n\n点: 終値が平均より上なら＋1、下なら−1です。"
    )
    pane = "price"
    enabled_by_default = False
    params = [
        ParamSpec("length", "期間", "int", 25, 2, 400),
    ]

    def compute(self, bars: list[dict[str, Any]], params: dict[str, Any]) -> IndicatorResult:
        resolved = coerce_params(self.params, params)
        length = resolved["length"]
        times = [str(bar["date"]) for bar in bars]
        closes = source_series(bars, "close")
        values = sma(closes, length)
        last = last_number(values)
        last_close = last_number(closes)
        return IndicatorResult(
            plugin_id=self.id,
            title=self.title,
            pane=self.pane,
            overlays=[
                LineSeries(
                    id="sma",
                    label=f"SMA({length})",
                    color="#fbbf24",
                    points=line_points(times, values),
                )
            ],
            features={
                "value": last,
                "price_above": None if last is None or last_close is None else int(last_close > last),
                "score": None if last is None or last_close is None else (1 if last_close > last else -1),
            },
            debug={"length": length, "points": len(line_points(times, values))},
        )
