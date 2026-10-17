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
from skabu.indicators.mathutil import bollinger


class BbandsPlugin:
    id = "bbands"
    group = "power"
    title = "ボリンジャー"
    description = "SMA ± 標準偏差"
    help = (
        "平均線の上下に、いつもの振れ幅の帯を描きます。σ倍率を大きくすると帯が広がります。"
        "\n\n読み取り: 上の線に近いほど「最近の中では高い」、下の線に近いほど「安い」です。"
        "帯をはみ出すと、行き過ぎか、強い勢いのどちらかです。"
        "例: 値段が上の帯を突き抜けたら、過熱か、強い上昇の始まりです。"
        "\n\n点: 上の帯より上なら＋2、平均より上なら＋1、平均より下なら−1、下の帯より下なら−2です。"
        "門の%B（過熱を落とす）とは別です。"
    )
    pane = "price"
    enabled_by_default = False
    params = [
        ParamSpec("length", "期間", "int", 20, 2, 200),
        ParamSpec("mult", "σ倍率", "float", 2.0, 0.5, 5.0, 0.1),
    ]

    def compute(self, bars: list[dict[str, Any]], params: dict[str, Any]) -> IndicatorResult:
        resolved = coerce_params(self.params, params)
        length = resolved["length"]
        mult = resolved["mult"]
        times = [str(bar["date"]) for bar in bars]
        closes = source_series(bars, "close")
        mid, upper, lower = bollinger(closes, length, mult)
        last_close = last_number(closes)
        last_mid = last_number(mid)
        last_upper = last_number(upper)
        last_lower = last_number(lower)
        pct_b = None
        if last_close is not None and last_upper is not None and last_lower is not None:
            span = float(last_upper) - float(last_lower)
            if span != 0:
                pct_b = (float(last_close) - float(last_lower)) / span
        if last_close is None or last_mid is None or last_upper is None or last_lower is None:
            score = None
        elif last_close > last_upper:
            score = 2
        elif last_close < last_lower:
            score = -2
        elif last_close > last_mid:
            score = 1
        elif last_close < last_mid:
            score = -1
        else:
            score = 0
        return IndicatorResult(
            plugin_id=self.id,
            title=self.title,
            pane=self.pane,
            overlays=[
                LineSeries(id="bb_upper", label=f"BB上({length})", color="#64748b", points=line_points(times, upper), width=1),
                LineSeries(id="bb_mid", label=f"BB中({length})", color="#94a3b8", points=line_points(times, mid)),
                LineSeries(id="bb_lower", label=f"BB下({length})", color="#64748b", points=line_points(times, lower), width=1),
            ],
            features={
                "value": last_mid,
                "upper": last_upper,
                "lower": last_lower,
                "price_above": None if last_close is None or last_mid is None else int(last_close > last_mid),
                "outside": None
                if last_close is None or last_upper is None or last_lower is None
                else int(last_close > last_upper or last_close < last_lower),
                "pct_b": None if pct_b is None else round(pct_b, 6),
                "score": score,
            },
            debug={"length": length, "mult": mult, "points": sum(1 for item in mid if item is not None)},
        )
