from __future__ import annotations

from typing import Any

from skabu.indicators.base import IndicatorResult, ParamSpec, coerce_params


class BoostPlugin:
    id = "boost"
    group = "model"
    title = "Boost"
    description = "学習モデルの上昇確率（20日以内の目標先着）"
    pane = "price"
    enabled_by_default = False
    surface = ("score",)
    help = (
        "過去の値動きなどから、これから上がりやすいかを機械が確率で出した数字です。線ではありません。点の並び順だけに使います。"
        "\n\n読み取り: 0.5より大きいほど「上がりやすい」と機械が見ています。"
        "例: 0.62なら、だいたい10回中6回くらいは狙いの上昇が起きそう、という見立てです。絶対ではありません。"
        "\n\n点: 強気の数字以上なら＋2、0.5以上なら＋1、弱気の数字以下なら−2、0.5未満なら−1です。"
    )
    params = [
        ParamSpec("high", "強気閾値", "float", 0.58, 0.5, 0.9, 0.01),
        ParamSpec("low", "弱気閾値", "float", 0.42, 0.1, 0.5, 0.01),
    ]

    def compute(self, bars: list[dict[str, Any]], params: dict[str, Any]) -> IndicatorResult:
        from skabu.model import predict_proba

        resolved = coerce_params(self.params, params)
        high = resolved["high"]
        low = min(resolved["low"], high)
        proba = predict_proba(bars)
        if proba is None:
            score = None
        elif proba >= high:
            score = 2
        elif proba >= 0.5:
            score = 1
        elif proba <= low:
            score = -2
        elif proba < 0.5:
            score = -1
        else:
            score = 0
        return IndicatorResult(
            plugin_id=self.id,
            title=self.title,
            pane=self.pane,
            features={
                "value": proba,
                "proba": proba,
                "ready": int(proba is not None),
                "score": score,
            },
            debug={"high": high, "low": low, "ready": proba is not None},
        )
