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
from skabu.indicators.mathutil import rsi


class RsiPlugin:
    id = "rsi"
    group = "power"
    title = "RSI"
    description = "相対力指数（別ペイン）"
    help = (
        "「このところ上がった力と下がった力」を0〜100の点数にしたものです。期間は何日分を見るかです。チャートでは下の段に出ます。"
        "\n\n読み取り: 70を超えると「買われすぎ（上がりすぎ）」、30を割ると「売られすぎ（下がりすぎ）」とよく言われます。"
        "50より上なら買い勢いはまだ強い、50より下なら弱い、と見ます。"
        "例: RSIが80なら、みんな買いすぎて、このあと戻りやすいことがあります。"
        "\n\n点: 50以上なら＋1、50未満なら−1です。門のRSI（70未満で残す）とは別物です。"
    )
    pane = "oscillator"
    enabled_by_default = False
    params = [
        ParamSpec("length", "期間", "int", 14, 2, 200),
    ]

    def compute(self, bars: list[dict[str, Any]], params: dict[str, Any]) -> IndicatorResult:
        resolved = coerce_params(self.params, params)
        length = resolved["length"]
        times = [str(bar["date"]) for bar in bars]
        closes = source_series(bars, "close")
        values = rsi(closes, length)
        last = last_number(values)
        if last is None:
            score = None
        elif last >= 50:
            score = 1
        else:
            score = -1
        return IndicatorResult(
            plugin_id=self.id,
            title=self.title,
            pane=self.pane,
            overlays=[
                LineSeries(
                    id="rsi",
                    label=f"RSI({length})",
                    color="#c084fc",
                    points=line_points(times, values),
                    pane="oscillator",
                ),
                LineSeries(
                    id="rsi_overbought",
                    label="70",
                    color="#4b5563",
                    points=[{"time": time, "value": 70} for time in times],
                    width=1,
                    style="dashed",
                    pane="oscillator",
                ),
                LineSeries(
                    id="rsi_oversold",
                    label="30",
                    color="#4b5563",
                    points=[{"time": time, "value": 30} for time in times],
                    width=1,
                    style="dashed",
                    pane="oscillator",
                ),
            ],
            features={"value": last, "above_50": None if last is None else int(last >= 50), "score": score},
            debug={"length": length, "points": sum(1 for item in values if item is not None)},
        )
