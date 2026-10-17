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
from skabu.indicators.mathutil import sma


class VolmaPlugin:
    id = "volma"
    group = "power"
    title = "出来高SMA"
    description = "出来高の単純移動平均"
    help = (
        "出来高（何株売買されたか）の平均線です。値段ではなく、取引の多さを見ます。"
        "\n\n読み取り: 今日の出来高が平均より多いと「いつもより注目されている」です。"
        "例: 値段が上がって、出来高も平均を大きく超えたら、本気の買いが入った可能性が高いです。"
        "出来高が少ない上昇は、弱いことがあります。"
        "\n\n点: 今日の出来高が平均より多いと＋1、少ないと−1です。"
    )
    pane = "oscillator"
    enabled_by_default = False
    params = [
        ParamSpec("length", "期間", "int", 20, 2, 200),
    ]

    def compute(self, bars: list[dict[str, Any]], params: dict[str, Any]) -> IndicatorResult:
        resolved = coerce_params(self.params, params)
        length = resolved["length"]
        times = [str(bar["date"]) for bar in bars]
        volumes: list[float | None] = []
        for bar in bars:
            volume = bar.get("volume")
            volumes.append(None if volume is None else float(volume))
        values = sma(volumes, length)
        last_ma = last_number(values)
        last_vol = last_number(volumes)
        if last_ma is None or last_vol is None:
            score = None
        elif last_vol > last_ma:
            score = 1
        else:
            score = -1
        return IndicatorResult(
            plugin_id=self.id,
            title=self.title,
            pane=self.pane,
            overlays=[
                LineSeries(
                    id="volma",
                    label=f"VolMA({length})",
                    color="#a3e635",
                    points=line_points(times, values),
                    pane="oscillator",
                )
            ],
            features={
                "value": last_ma,
                "volume": last_vol,
                "above_ma": None if last_ma is None or last_vol is None else int(last_vol > last_ma),
                "score": score,
            },
            debug={"length": length, "points": sum(1 for item in values if item is not None)},
        )
