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
from skabu.indicators.mathutil import adx


class AdxPlugin:
    id = "adx"
    group = "power"
    title = "ADX"
    description = "トレンドの強さ（+DI / -DI）"
    help = (
        "「今、はっきりした流れがあるか」を見る数字です。上がっているか下がっているかは、+DIと-DIで分けます。"
        "\n\n読み取り: ADXが大きいほど一方通行の流れが強いです。小さいと行ったり来たりです。"
        "例: ADXが25で+DIの方が大きいなら、上方向の流れがはっきりしています。ADXが10なら、方向ははっきりしません。"
        "\n\n点: ADXが閾値以上で+DIが大きいと＋1、−DIが大きいと−1、弱い流れなら0です。"
    )
    pane = "oscillator"
    enabled_by_default = False
    params = [
        ParamSpec("length", "期間", "int", 14, 5, 80),
        ParamSpec("trend", "トレンド閾値", "int", 20, 5, 60),
    ]

    def compute(self, bars: list[dict[str, Any]], params: dict[str, Any]) -> IndicatorResult:
        resolved = coerce_params(self.params, params)
        length = resolved["length"]
        trend = resolved["trend"]
        times = [str(bar["date"]) for bar in bars]
        highs = [bar.get("high") for bar in bars]
        lows = [bar.get("low") for bar in bars]
        closes = [bar.get("close") for bar in bars]
        plus_di, minus_di, values = adx(highs, lows, closes, length)
        last = last_number(values)
        last_plus = last_number(plus_di)
        last_minus = last_number(minus_di)
        if last is None or last_plus is None or last_minus is None:
            score = None
        elif last >= trend and last_plus > last_minus:
            score = 1
        elif last >= trend and last_minus > last_plus:
            score = -1
        else:
            score = 0
        return IndicatorResult(
            plugin_id=self.id,
            title=self.title,
            pane=self.pane,
            overlays=[
                LineSeries(
                    id="adx",
                    label=f"ADX({length})",
                    color="#eab308",
                    points=line_points(times, values),
                    pane="oscillator",
                ),
                LineSeries(
                    id="plus_di",
                    label="+DI",
                    color="#26a69a",
                    points=line_points(times, plus_di),
                    width=1,
                    pane="oscillator",
                ),
                LineSeries(
                    id="minus_di",
                    label="-DI",
                    color="#ef5350",
                    points=line_points(times, minus_di),
                    width=1,
                    pane="oscillator",
                ),
            ],
            features={
                "value": last,
                "plus_di": last_plus,
                "minus_di": last_minus,
                "trending": None if last is None else int(last >= trend),
                "plus_above": None if last_plus is None or last_minus is None else int(last_plus > last_minus),
                "score": score,
            },
            debug={"length": length, "trend": trend, "points": sum(1 for item in values if item is not None)},
        )
