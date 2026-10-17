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
from skabu.indicators.mathutil import ema


class EmaPlugin:
    id = "ema"
    group = "trend"
    title = "EMA"
    description = "指数平滑移動平均"
    help = (
        "SMAに似ていますが、いちばん新しい日を重く見た平均です。最近の変化に、より早く反応します。"
        "\n\n読み取り: SMAより先に曲がりやすいです。例: 急に上がり始めたとき、EMAはSMAより早く上を向きます。"
        "短い流れを見たいときに使います。"
        "\n\n点: 終値がEMAより上なら＋1、下なら−1です。"
    )
    pane = "price"
    enabled_by_default = False
    params = [
        ParamSpec("length", "期間", "int", 20, 2, 400),
    ]

    def compute(self, bars: list[dict[str, Any]], params: dict[str, Any]) -> IndicatorResult:
        resolved = coerce_params(self.params, params)
        length = resolved["length"]
        times = [str(bar["date"]) for bar in bars]
        closes = source_series(bars, "close")
        values = ema(closes, length)
        last = last_number(values)
        last_close = last_number(closes)
        return IndicatorResult(
            plugin_id=self.id,
            title=self.title,
            pane=self.pane,
            overlays=[
                LineSeries(
                    id="ema",
                    label=f"EMA({length})",
                    color="#60a5fa",
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
