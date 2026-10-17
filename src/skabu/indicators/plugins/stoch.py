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
from skabu.indicators.mathutil import stochastic


class StochPlugin:
    id = "stoch"
    group = "power"
    title = "ストキャス"
    description = "ストキャスティクス %K / %D（別ペイン）"
    help = (
        "直近の高値と安値のあいだで、今の値段がどこにいるかを0〜100で表します。%Kが本体、%Dはその平均です。"
        "\n\n読み取り: 80より上は高い位置、20より下は安い位置です。"
        "%Kが%Dを下から上に抜けると、勢いが上に転じた、と見ることが多いです。"
        "例: 安値圏（20付近）で%Kが%Dを上に抜けたら、底から戻り始めた合図、という見方です。"
        "\n\n点: %Kが%Dより上で、かつ20より上なら＋1、そうでなければ−1です。"
    )
    pane = "oscillator"
    enabled_by_default = False
    params = [
        ParamSpec("k_len", "%K期間", "int", 14, 2, 80),
        ParamSpec("k_smooth", "%K平滑", "int", 3, 1, 20),
        ParamSpec("d_len", "%D期間", "int", 3, 1, 20),
    ]

    def compute(self, bars: list[dict[str, Any]], params: dict[str, Any]) -> IndicatorResult:
        resolved = coerce_params(self.params, params)
        k_len = resolved["k_len"]
        k_smooth = resolved["k_smooth"]
        d_len = resolved["d_len"]
        times = [str(bar["date"]) for bar in bars]
        highs = [bar.get("high") for bar in bars]
        lows = [bar.get("low") for bar in bars]
        closes = [bar.get("close") for bar in bars]
        k_line, d_line = stochastic(highs, lows, closes, k_len, k_smooth, d_len)
        last_k = last_number(k_line)
        last_d = last_number(d_line)
        if last_k is None or last_d is None:
            score = None
        elif last_k > last_d and last_k > 20:
            score = 1
        else:
            score = -1
        return IndicatorResult(
            plugin_id=self.id,
            title=self.title,
            pane=self.pane,
            overlays=[
                LineSeries(
                    id="stoch_k",
                    label=f"%K({k_len},{k_smooth})",
                    color="#22d3ee",
                    points=line_points(times, k_line),
                    pane="oscillator",
                ),
                LineSeries(
                    id="stoch_d",
                    label=f"%D({d_len})",
                    color="#fb7185",
                    points=line_points(times, d_line),
                    pane="oscillator",
                ),
            ],
            features={
                "value": last_k,
                "d": last_d,
                "k_above_d": None if last_k is None or last_d is None else int(last_k > last_d),
                "score": score,
            },
            debug={"k_len": k_len, "k_smooth": k_smooth, "d_len": d_len, "points": sum(1 for item in k_line if item is not None)},
        )
