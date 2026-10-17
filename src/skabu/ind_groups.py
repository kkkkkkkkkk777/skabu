"""点・門・チャートの一覧を同じ意味のグループに分ける。"""

from __future__ import annotations

from typing import Any

GROUPS: list[dict[str, str]] = [
    {
        "id": "trend",
        "title": "向き",
        "help": (
            "値段が、平均より上か下かを見ます。上なら上がりやすい、下なら下がりやすい、という見方です。"
            "\n\nなぜ: みんなの目安になる線より上にいる株は、買いが続いていることが多いです。"
            "例: 終値が200日平均より上なら「向きは上」です。"
        ),
    },
    {
        "id": "power",
        "title": "勢い",
        "help": (
            "今、上がる力と下がる力のどちらが強いかを見ます。速く上がりすぎると、あとで戻りやすいこともあります。"
            "\n\nなぜ: 勢いが強い株は続きやすい一方、熱しすぎると冷めることがあります。"
            "例: RSIが80なら「上がりすぎ」とよく言われます。"
        ),
    },
    {
        "id": "shape",
        "title": "形",
        "help": (
            "山と谷、または直近のローソクの模様を見ます。同じ高さで2回頭を打つ、谷が2つ並ぶ、などです。"
            "\n\nなぜ: よく出る形は、このあと上がるか下がるかのヒントになります。"
            "例: 高い山が2つ並ぶダブルトップは、下がりやすいと言われます。"
        ),
    },
    {
        "id": "wave",
        "title": "波",
        "help": (
            "上げと下げを1・2・3・4・5の波として数えます。今がいちばん強い3波か、もう5波が終わったかを見ます。"
            "\n\nなぜ: 教科書では、上げの3波がいちばん勢いが出やすいとされます。"
            "例: 谷→山→谷のあと、終値が山を超えたら3波の途中、と数えます。"
        ),
    },
    {
        "id": "market",
        "title": "市場全体",
        "help": (
            "1つの会社ではなく、日本株全体の天気を見ます。晴れなら買いやすく、雨なら良い株でも下がりやすいです。"
            "\n\nなぜ: 市場全体が弱い日は、個別の形が良くても負けやすいです。"
            "例: 13060が200日平均より下なら、この門を付けると全部買いません。"
        ),
    },
    {
        "id": "facts",
        "title": "会社の数字",
        "help": (
            "株価の線ではなく、会社の大きさ、もうけ、配当などの数字で残すか落とします。"
            "\n\nなぜ: 値段の形が良くても、会社が小さすぎたり高すぎたりすると続きにくいことがあります。"
            "例: PERが低すぎず高すぎない、配当が一定以上、などです。"
        ),
    },
    {
        "id": "model",
        "title": "機械の点数",
        "help": (
            "人が決めた線ではなく、過去のデータから機械が付けた点数です。"
            "\n\nなぜ: 人の目では拾いにくい組み合わせを、機械がまとめて見ます。"
            "例: Boostの点が高い株は、機械が「上がりやすい」と見ている、という意味です。"
        ),
    },
    {
        "id": "other",
        "title": "その他",
        "help": (
            "上のグループに入っていない項目です。"
            "\n\nなぜ: あとから足した項目が、まだグループに入っていないことがあります。"
            "例: 新しい門を足した直後などです。"
        ),
    },
]

PLUGIN_GROUP = {
    "sma": "trend",
    "ema": "trend",
    "supertrend": "trend",
    "ichimoku": "trend",
    "rsi": "power",
    "macd": "power",
    "adx": "power",
    "stoch": "power",
    "atr": "power",
    "bbands": "power",
    "volma": "power",
    "momentum": "power",
    "doubletop": "shape",
    "doublebottom": "shape",
    "downtrend": "shape",
    "uptrend": "shape",
    "weekly": "trend",
    "headshoulders": "shape",
    "invheadshoulders": "shape",
    "candles": "shape",
    "elliott": "wave",
    "boost": "model",
    "regime": "market",
    "facts": "facts",
}

GROUP_ORDER = [item["id"] for item in GROUPS]


def group_catalog() -> list[dict[str, str]]:
    return [dict(item) for item in GROUPS]


def group_id(plugin_id: str | None) -> str:
    return PLUGIN_GROUP.get(str(plugin_id or ""), "other")


def group_meta(plugin_id: str | None) -> dict[str, str]:
    gid = group_id(plugin_id)
    for item in GROUPS:
        if item["id"] == gid:
            return dict(item)
    return dict(GROUPS[-1])
