from __future__ import annotations

from typing import Any

from skabu.indicators.base import (
    IndicatorResult,
    LineSeries,
    Marker,
    ParamSpec,
    coerce_params,
    last_number,
    line_points,
    source_series,
)
from skabu.indicators.mathutil import macd


class MacdPlugin:
    id = "macd"
    group = "power"
    title = "MACD"
    description = "MACD ラインとシグナル（別ペイン）"
    help = (
        "短い平均と長い平均の差です。差の線と、それをならした「シグナル」の2本が、下の段に出ます。"
        "\n\n読み取り: 差の線がシグナルより上なら、短い平均の方が勢いがあります。"
        "下から上に交差したら「買いの交差」とよく見ます。"
        "例: 長い間下だった線が上に抜けたら、流れが上向きに変わった合図です。"
        "\n\n点: 差の線がシグナルより上なら＋1、下なら−1です。"
    )
    pane = "oscillator"
    enabled_by_default = False
    params = [
        ParamSpec("fast", "短期", "int", 12, 2, 80),
        ParamSpec("slow", "長期", "int", 26, 5, 200),
        ParamSpec("signal", "シグナル", "int", 9, 2, 80),
    ]

    def compute(self, bars: list[dict[str, Any]], params: dict[str, Any]) -> IndicatorResult:
        resolved = coerce_params(self.params, params)
        fast = resolved["fast"]
        slow = max(resolved["slow"], fast + 1)
        signal = resolved["signal"]
        times = [str(bar["date"]) for bar in bars]
        closes = source_series(bars, "close")
        line, sig, hist = macd(closes, fast, slow, signal)
        last_line = last_number(line)
        last_sig = last_number(sig)
        last_hist = last_number(hist)
        if last_line is None or last_sig is None:
            score = None
        elif last_line > last_sig:
            score = 1
        else:
            score = -1
        markers = []
        prev = None
        for time, value in zip(times, hist, strict=True):
            if value is None:
                continue
            if prev is not None and prev <= 0 < value:
                markers.append(Marker(time=time, position="belowBar", shape="arrowUp", color="#26a69a", text="MACD"))
            elif prev is not None and prev >= 0 > value:
                markers.append(Marker(time=time, position="aboveBar", shape="arrowDown", color="#ef5350", text="MACD"))
            prev = value
        return IndicatorResult(
            plugin_id=self.id,
            title=self.title,
            pane=self.pane,
            overlays=[
                LineSeries(
                    id="macd_line",
                    label=f"MACD({fast},{slow})",
                    color="#38bdf8",
                    points=line_points(times, line),
                    pane="oscillator",
                ),
                LineSeries(
                    id="macd_signal",
                    label=f"Signal({signal})",
                    color="#f97316",
                    points=line_points(times, sig),
                    pane="oscillator",
                ),
            ],
            markers=markers,
            features={
                "value": last_line,
                "signal": last_sig,
                "hist": last_hist,
                "above_signal": None if last_line is None or last_sig is None else int(last_line > last_sig),
                "score": score,
            },
            debug={"fast": fast, "slow": slow, "signal": signal, "points": sum(1 for item in line if item is not None)},
        )
