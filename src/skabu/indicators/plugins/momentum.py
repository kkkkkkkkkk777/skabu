from __future__ import annotations

from typing import Any

from skabu.indicators.base import IndicatorResult, ParamSpec, coerce_params, last_number, source_series

HIGH_LEN = 250
RET_60 = 60
RET_120 = 120


class MomentumPlugin:
    id = "momentum"
    group = "power"
    title = "高値モメンタム"
    description = "250日高値比と60日・120日リターン。順位はランキング側で付ける"
    pane = "price"
    enabled_by_default = False
    surface = ("score",)
    help = (
        "「他の株より、よく上がっているか」を見る点数です。"
        "250日の高値にどれだけ近いか、60日・120日でどれだけ上がったかを、全銘柄の中での順位にして足します。線ではありません。"
        "\n\n読み取り: 高値に近く、最近も上がっている株は順位が上になります。"
        "例: 1年の高値のすぐ下にいて、直近2〜4か月もプラスなら、勢いがある側です。"
        "高値から大きく沈んで、最近も下がっている株は下位です。"
        "\n\n点: 残った株の中での順位を＋2〜−2にして、他の点と足します。"
        "いちばん上は＋2、真ん中は0、いちばん下は−2です。チャートの線には使いません。"
    )
    params = [
        ParamSpec("high_len", "高値期間", "int", HIGH_LEN, 60, 400),
        ParamSpec("ret_fast", "短期リターン", "int", RET_60, 5, 200),
        ParamSpec("ret_slow", "長期リターン", "int", RET_120, 10, 400),
    ]

    def compute(self, bars: list[dict[str, Any]], params: dict[str, Any]) -> IndicatorResult:
        resolved = coerce_params(self.params, params)
        high_len = int(resolved["high_len"])
        ret_fast = int(resolved["ret_fast"])
        ret_slow = int(resolved["ret_slow"])
        closes = source_series(bars, "close")
        last = last_number(closes)
        need = max(high_len, ret_slow + 1, ret_fast + 1)
        if last is None or len(closes) < need:
            return IndicatorResult(
                plugin_id=self.id,
                title=self.title,
                pane=self.pane,
                features={"high_ratio": None, "ret_60": None, "ret_120": None, "score": None},
                debug={"need": need, "bars": len(closes)},
            )
        window = [value for value in closes[-high_len:] if value is not None]
        if len(window) < high_len or last is None:
            return IndicatorResult(
                plugin_id=self.id,
                title=self.title,
                pane=self.pane,
                features={"high_ratio": None, "ret_60": None, "ret_120": None, "score": None},
                debug={"need": need, "bars": len(closes), "window": len(window)},
            )
        peak = max(float(item) for item in window)
        high_ratio = None if peak <= 0 else float(last) / peak
        ret_60 = _past_ret(closes, ret_fast)
        ret_120 = _past_ret(closes, ret_slow)
        return IndicatorResult(
            plugin_id=self.id,
            title=self.title,
            pane=self.pane,
            features={
                "high_ratio": None if high_ratio is None else round(high_ratio, 6),
                "ret_60": None if ret_60 is None else round(ret_60, 6),
                "ret_120": None if ret_120 is None else round(ret_120, 6),
                "score": 0,
            },
            debug={"high_len": high_len, "peak": peak, "bars": len(closes)},
        )


def _past_ret(closes: list[Any], length: int) -> float | None:
    if len(closes) <= length:
        return None
    start = closes[-1 - length]
    end = closes[-1]
    if start in (None, 0) or end is None:
        return None
    return (float(end) / float(start)) - 1.0
