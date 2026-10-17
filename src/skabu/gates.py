"""門の一覧と、URL の flt= を同じ id/feature/op/value にする。"""

from __future__ import annotations

import re
from typing import Any

from skabu.ind_groups import group_id

FACT_ID = "facts"
REGIME_ID = "regime"
WEEKLY_ID = "weekly"

_OPS = {
    "<": "lt",
    "<=": "lte",
    ">": "gt",
    ">=": "gte",
    "=": "eq",
    "==": "eq",
}
_OP_SYMBOL = {"eq": "=", "lt": "<", "lte": "<=", "gt": ">", "gte": ">="}
_FLT_RE = re.compile(
    r"^(?P<id>[a-z0-9_]+)\.(?P<feature>[a-z0-9_]+)(?P<op><=|>=|==|=|<|>)(?P<value>.+)$",
    re.IGNORECASE,
)


def gate_catalog() -> list[dict[str, Any]]:
    items = [
        {
            "id": "ichimoku",
            "feature": "above_cloud",
            "title": "雲の上",
            "help": (
                "一目の雲より、今日の終値が高い株だけ残します。=1 が「雲の上」、=0 だと逆に雲の上を落とします。"
                "\n\nなぜ: 雲の上は、買い側が優勢とよく見ます。"
                "例: 終値1200円、雲の天井1180円なら残す。終値1160円なら落ちます。"
                "\n\n点の一目とは別で、ここでは残すか消すかだけです。"
            ),
            "kind": "plugin",
            "ops": ["eq"],
            "default_op": "eq",
            "default_value": 1,
            "value_kind": "int",
        },
        {
            "id": "bbands",
            "feature": "pct_b",
            "title": "%B",
            "help": (
                "ボリンジャー帯のどこに値段がいるかです。0が下の帯、0.5が真ん中、1が上の帯です。"
                "\n\nなぜ: 上の方に張り付いている株は、過熱して戻りやすいことがあります。"
                "例: %Bが0.95なら帯のほぼ天井。最初の設定の 0.85未満 なら、そこまで過熱していない株だけ残します。"
                "\n\n点のボリンジャーとは別です。"
            ),
            "kind": "plugin",
            "ops": ["lt", "lte", "gt", "gte"],
            "default_op": "lt",
            "default_value": 0.85,
            "value_kind": "float",
        },
        {
            "id": "rsi",
            "feature": "value",
            "title": "RSI",
            "help": (
                "買われすぎの株を落とします。RSIは0〜100の「上がった力と下がった力」です。"
                "\n\nなぜ: 70を超えると「上がりすぎ」とよく言われます。"
                "例: RSI 75の株は最初の設定では落ち、RSI 55は残ります。"
                "\n\n点のRSI（50で加点）とは別です。ここでは残すか消すかだけです。"
            ),
            "kind": "plugin",
            "ops": ["lt", "lte", "gt", "gte"],
            "default_op": "lt",
            "default_value": 70,
            "value_kind": "float",
        },
        {
            "id": "doubletop",
            "feature": "double_top",
            "title": "ダブルトップ除外",
            "help": (
                "高い山が2つ並ぶ形なら買わない、という門です。=0 で「その形ではない株だけ残す」です。"
                "\n\nなぜ: 同じ高さで2回頭を打つと、上値が重くて下がりやすい、とよく言われます。"
                "例: 1500円で2回はね返されて下がっている株は落ちます。"
                "\n\n=1 にすると逆に、双子山の株だけ残します。"
            ),
            "kind": "plugin",
            "ops": ["eq"],
            "default_op": "eq",
            "default_value": 0,
            "value_kind": "int",
        },
        {
            "id": "headshoulders",
            "feature": "head_shoulders",
            "title": "ヘッドアンドショルダーズ除外",
            "help": (
                "山が3つ並び、真ん中の頭がいちばん高い形なら買わない、という門です。"
                "=0 で「その形ではない株だけ残す」です。"
                "\n\nなぜ: 頭のあと右肩が頭に届かないと、上値が重くて下がりやすい、とよく言われます。"
                "例: 山1200→頭1500→山1180の株は落ちます。"
                "\n\n頭は両肩よりはっきり高いときだけ数えます。点には使いません。"
            ),
            "kind": "plugin",
            "ops": ["eq"],
            "default_op": "eq",
            "default_value": 0,
            "value_kind": "int",
        },
        {
            "id": "downtrend",
            "feature": "lower_lows",
            "title": "切り下げ除外",
            "help": (
                "高値も安値も、前より低い位置になっている形（切り下げ）を買わない門です。=0 でその形を落とします。"
                "\n\nなぜ: 山も谷も下がっているのは、下降の途中です。"
                "例: 高値が1200→1100、安値が1000→900なら切り下げで落ちます。"
            ),
            "kind": "plugin",
            "ops": ["eq"],
            "default_op": "eq",
            "default_value": 0,
            "value_kind": "int",
        },
        {
            "id": "candles",
            "feature": "bearish",
            "title": "陰の包み・宵の明星除外",
            "help": (
                "直近が陰の包み、または宵の明星なら買わない門です。=0 でその形を落とします。"
                "\n\nなぜ: 陽線のあと陰線が包む、または3本で天井を打つと、売りの方が強いとよく見ます。"
                "例: 昨日陽線、今日その実体を覆う陰線なら落ちます。"
                "\n\n1本足の流れ星だけでは落ちません。点の陽の包みとは別です。"
            ),
            "kind": "plugin",
            "ops": ["eq"],
            "default_op": "eq",
            "default_value": 0,
            "value_kind": "int",
        },
        {
            "id": "elliott",
            "feature": "wave3_up",
            "title": "上昇3波中",
            "help": (
                "自動で数えた上昇の3波の途中だけ残す門です。=1 が「今3波」です。"
                "1波の高値を終値が超えていて、上げの5波はまだ無いときです。"
                "下げの5波の途中や、その内側の戻りでは通りません。"
                "下げが終わった安値から、新しい上げが始まったときだけ残します。"
                "\n\nなぜ: 教科書では上げの3波がいちばん強いことが多いです。"
                "例: 谷100→山140→谷120のあと、終値150なら残します。"
                "大きい下げの途中で少し戻っただけなら落ちます。"
                "\n\n数え方はプログラムが1通りに決めます。最後の波は仮です。日足だけでは大きい波は見えません。"
                "形が無い株は0なので落ちます。"
            ),
            "kind": "plugin",
            "ops": ["eq"],
            "default_op": "eq",
            "default_value": 1,
            "value_kind": "int",
        },
        {
            "id": "elliott",
            "feature": "wave5_done",
            "title": "上昇5波終了除外",
            "help": (
                "上昇の5波が終わっている株を落とす門です。=0 で「終わっていない株だけ残す」です。"
                "\n\nなぜ: 5波のあと、よく修正の下げが来ます。勢いの終わり、という見方です。"
                "例: 谷→山→谷→山→谷→山まで禁止3点を守って揃っている株は落ちます。"
                "形が無い株は0なので残ります。"
                "\n\n数え方は1通りです。最後の波は左右の足が揃うまで仮です。"
            ),
            "kind": "plugin",
            "ops": ["eq"],
            "default_op": "eq",
            "default_value": 0,
            "value_kind": "int",
        },
        {
            "id": "elliott",
            "feature": "correcting",
            "title": "修正波除外",
            "help": (
                "上昇5波のあと、修正（A-B-C、または5波の山を終値が割った）なら落とす門です。=0 でその形を落とします。"
                "\n\nなぜ: 修正のあいだは、配当目的でも値段が戻りにくいことがあります。"
                "例: 5波の山が240円で、終値が230円なら落ちます。まだ240円の上なら、この門だけでは残り得ます。"
                "形が無い株は0なので残ります。"
                "\n\n日足だけで数えます。大きい波の中の小さい波は見えません。"
            ),
            "kind": "plugin",
            "ops": ["eq"],
            "default_op": "eq",
            "default_value": 0,
            "value_kind": "int",
        },
        {
            "id": WEEKLY_ID,
            "feature": "higher_highs",
            "title": "週足の切り上げ",
            "help": (
                "週足で、高値も安値も前より高い株だけ残します。=1 が「切り上げ」です。"
                "\n\nなぜ: 20営業日の売買では、週足の向きが上のときの日足の形と、下のときの日足の形は別の取引です。"
                "例: 週の高値が1100→1200、安値が900→1000なら残します。山も谷も下がっている株は落ちます。"
                "\n\n予測日までの日足を、その週の金曜までの1本にまとめます。週の途中なら、その日までの足だけです。"
                "左右5週、直近の山か谷は25週以内です。週が足りない株は落ちます。点には使いません。"
            ),
            "kind": "weekly",
            "ops": ["eq"],
            "default_op": "eq",
            "default_value": 1,
            "value_kind": "int",
        },
        {
            "id": WEEKLY_ID,
            "feature": "above_sma",
            "title": "週足が13週線の上",
            "help": (
                "週の終値が、13週の終値平均より上の株だけ残します。=1 が「平均の上」です。"
                "\n\nなぜ: 切り上げとは別に、値段が中期の平均のどちら側にいるかを見ます。"
                "例: 今週の終値1200円、13週平均1150円なら残します。終値1100円なら落ちます。"
                "\n\n予測日までの日足から作った週足です。13週に足りない株は落ちます。点には使いません。"
            ),
            "kind": "weekly",
            "ops": ["eq"],
            "default_op": "eq",
            "default_value": 1,
            "value_kind": "int",
        },
        {
            "id": WEEKLY_ID,
            "feature": "rsi",
            "title": "週足RSI",
            "help": (
                "週足のRSIが買われすぎの株を落とします。0〜100で、最初の設定は70未満です。"
                "\n\nなぜ: 日足のRSIが落ち着いていても、週足では上がりすぎていることがあります。"
                "例: 週足RSIが75なら落ち、55なら残ります。"
                "\n\n期間は14週です。週が足りない株は落ちます。日足のRSIの門とは別です。"
            ),
            "kind": "weekly",
            "ops": ["lt", "lte", "gt", "gte"],
            "default_op": "lt",
            "default_value": 70,
            "value_kind": "float",
        },
        {
            "id": REGIME_ID,
            "feature": "above_ma200",
            "title": "地合い（13060>MA200）",
            "help": (
                "個別の株ではなく、市場全体の天気です。13060（日本株全体に近いETF）が200日平均より上なら「晴れ」、下なら「雨」です。"
                "\n\nなぜ: 市場全体が弱いときは、良い株でも下がりやすいです。"
                "例: 13060が200日線の下なら、この門を付けると全部買いません（現金）。"
                "\n\n銘柄ごとに見ません。1日1回だけ見ます。"
            ),
            "kind": "regime",
            "ops": ["eq"],
            "default_op": "eq",
            "default_value": 1,
            "value_kind": "int",
        },
        {
            "id": FACT_ID,
            "feature": "mkt_cap",
            "title": "時価総額",
            "help": (
                "会社の値段の合計（株価×株数）です。大きいほど大きな会社です。"
                "\n\nなぜ: 小さすぎる会社は値が飛びやすく、売買しにくいことがあります。"
                "例: 最初の設定の 0より大きい は「数字がある会社だけ残す」です。"
                "もっと絞るなら、大きな数字より上、などにします。"
                "\n\n数字が無い行は落ちます。"
            ),
            "kind": "fact",
            "ops": ["gt", "gte", "lt", "lte"],
            "default_op": "gt",
            "default_value": 0,
            "value_kind": "float",
        },
        {
            "id": FACT_ID,
            "feature": "avg_vo",
            "title": "平均出来高",
            "help": (
                "最近、1日に何株くらい売買されているかの平均です。"
                "\n\nなぜ: 出来高が少なすぎると、買いたいときに買えず、売りたいときに売れません。"
                "例: 平均1万株しかない株は、最初の設定の5万株以上だと落ちます。"
                "画面上の「最低出来高」とは別に、門としても付けられます。"
                "\n\n数字が無い行は落ちます。"
            ),
            "kind": "fact",
            "ops": ["gt", "gte", "lt", "lte"],
            "default_op": "gte",
            "default_value": 50_000,
            "value_kind": "float",
        },
        {
            "id": FACT_ID,
            "feature": "per",
            "title": "PER",
            "help": (
                "株価が、1年の利益の何倍かを表します。小さいほど「利益に対して安い」です。"
                "\n\nなぜ: 高すぎるPERは、期待が織り込みすぎ、という見方があります。"
                "例: 利益100円で株価3000円ならPERは30。最初の設定の20未満なら、この株は落ちます。"
                "\n\n予測日より後の決算は見ません。数字が無い株は落ちます。先に sync facts が必要です。"
            ),
            "kind": "fact",
            "ops": ["lt", "lte", "gt", "gte"],
            "default_op": "lt",
            "default_value": 20,
            "value_kind": "float",
        },
        {
            "id": FACT_ID,
            "feature": "pbr",
            "title": "PBR",
            "help": (
                "株価が、会社の純資産（持ち物から借金を引いたもの）の何倍かを表します。"
                "1なら「資産と同じ値段」、2なら「資産の2倍」です。"
                "\n\nなぜ: 高すぎると割高、低すぎると割安か問題あり、の両方があります。"
                "例: 最初の設定の2未満なら、資産の2倍未満の株だけ残します。"
                "\n\n数字が無い株は落ちます。先に sync facts が必要です。"
            ),
            "kind": "fact",
            "ops": ["lt", "lte", "gt", "gte"],
            "default_op": "lt",
            "default_value": 2,
            "value_kind": "float",
        },
        {
            "id": FACT_ID,
            "feature": "op_margin",
            "title": "営業利益率",
            "help": (
                "売上のうち、本業でどれだけ残ったかの割合です。0.10＝10%です。"
                "\n\nなぜ: 利益率が高い会社は、値段を決めやすかったり、無駄が少なかったりします。"
                "例: 売上が100億円、営業利益が8億円なら0.08。"
                "最初の設定の0より大きいなら、赤字でない会社だけ残します。"
                "動画なら 0.08。"
                "\n\n数字が無い株は落ちます。先に sync facts が必要です。"
            ),
            "kind": "fact",
            "ops": ["gt", "gte", "lt", "lte"],
            "default_op": "gt",
            "default_value": 0,
            "value_kind": "float",
        },
        {
            "id": FACT_ID,
            "feature": "div_yield",
            "title": "配当利回り下限",
            "help": (
                "1株配当を、予測日の終値で割った利回りです。0.035＝3.5%です。"
                "予想配当 FDivAnn を使い、無ければ実績 DivAnn です。"
                "\n\nなぜ: 長く持って配当をもらう門です。低すぎると配当目的には薄いです。"
                "例: 予想配当35円、終値1000円なら利回り0.035。最初の設定の0.035以上なら残します。"
                "\n\n利回りそのものはAPIにありません。分割があると1株配当がズレることがあります。"
                "数字が無い株は落ちます。先に sync facts が必要です。"
            ),
            "kind": "fact",
            "ops": ["gt", "gte", "lt", "lte"],
            "default_op": "gte",
            "default_value": 0.035,
            "value_kind": "float",
        },
        {
            "id": FACT_ID,
            "feature": "div_yield_hi",
            "title": "配当利回り上限",
            "help": (
                "中身は配当利回りと同じです。門が1条件1行なので、上限用です。0.05＝5%です。"
                "予想配当 FDivAnn、無ければ実績 DivAnn を、予測日の終値で割ります。"
                "\n\nなぜ: 利回りが高すぎると、減配や株価急落のあと、という見方があります。"
                "例: 配当60円、終値1000円なら0.06。最初の設定の0.05以下なら落ちます。"
                "\n\n分割があると1株配当がズレることがあります。"
                "数字が無い株は落ちます。先に sync facts が必要です。"
            ),
            "kind": "fact",
            "ops": ["gt", "gte", "lt", "lte"],
            "default_op": "lte",
            "default_value": 0.05,
            "value_kind": "float",
        },
        {
            "id": FACT_ID,
            "feature": "roe",
            "title": "ROE",
            "help": (
                "自己資本に対して、どれだけ利益を出したかの割合です。APIの ROE です。0.10＝10%です。"
                "\n\nなぜ: お金の使い方が上手い会社は、長く持ちやすいという見方があります。"
                "例: 自己資本100億円、利益10億円なら0.10。最初の設定の0.10以上なら残します。"
                "\n\n数字が無い株は落ちます。先に sync facts が必要です。"
            ),
            "kind": "fact",
            "ops": ["gt", "gte", "lt", "lte"],
            "default_op": "gte",
            "default_value": 0.10,
            "value_kind": "float",
        },
        {
            "id": FACT_ID,
            "feature": "eq_ar",
            "title": "自己資本比率",
            "help": (
                "持ち物のうち、借金でないお金の割合です。APIの EqAR です。0.40＝40%です。"
                "\n\nなぜ: 借金が多すぎると、不況のときに倒れやすいです。"
                "例: 総資産100億円、自己資本40億円なら0.40。最初の設定の0.40以上なら残します。"
                "\n\n数字が無い株は落ちます。先に sync facts が必要です。"
            ),
            "kind": "fact",
            "ops": ["gt", "gte", "lt", "lte"],
            "default_op": "gte",
            "default_value": 0.40,
            "value_kind": "float",
        },
        {
            "id": FACT_ID,
            "feature": "sales_growth3",
            "title": "3年平均売上成長",
            "help": (
                "通期の売上を4期並べ、前年比を3つ平均した伸びです。0.10＝10%、0なら横ばいです。"
                "\n\nなぜ: 売上が長く減っている会社は、配当を続けにくいことがあります。"
                "例: 売上が100、110、121、133なら、前年比は0.10、0.10、約0.10。平均は約0.10。"
                "最初の設定の0以上なら、平均で減っていない会社だけ残します。"
                "\n\n期が足りない、数字が無い株は落ちます。"
                "Freeプランは約2年しか取れないことが多く、この門はほぼ全滅します。"
                "先に sync facts が必要です。"
            ),
            "kind": "fact",
            "ops": ["gt", "gte", "lt", "lte"],
            "default_op": "gte",
            "default_value": 0,
            "value_kind": "float",
        },
    ]
    for item in items:
        item["group"] = group_id(item["id"])
    return items


def gate_key(item: dict[str, Any]) -> str:
    return f"{item.get('id')}.{item.get('feature')}"


def known_gates() -> dict[str, dict[str, Any]]:
    return {gate_key(item): item for item in gate_catalog()}


def is_fact_gate(spec: dict[str, Any]) -> bool:
    return str(spec.get("id") or "") == FACT_ID


def is_regime_gate(spec: dict[str, Any]) -> bool:
    return str(spec.get("id") or "") == REGIME_ID


def is_weekly_gate(spec: dict[str, Any]) -> bool:
    return str(spec.get("id") or "") == WEEKLY_ID


def has_fact_gates(filters: list[dict[str, Any]] | None) -> bool:
    return any(is_fact_gate(item) for item in (filters or []))


def parse_filter_query(query: dict[str, str] | Any) -> list[dict[str, Any]] | None:
    if "flt" not in query:
        return None
    raw = str(query.get("flt") or "").strip()
    if not raw:
        return []
    known = known_gates()
    out: list[dict[str, Any]] = []
    for chunk in raw.split(","):
        text = chunk.strip()
        if not text:
            continue
        match = _FLT_RE.match(text)
        if not match:
            continue
        plugin_id = match.group("id")
        feature = match.group("feature")
        op = _OPS[match.group("op")]
        raw_value = match.group("value").strip()
        spec = known.get(f"{plugin_id}.{feature}")
        value: Any = raw_value
        if spec and spec.get("value_kind") == "int":
            try:
                value = int(float(raw_value))
            except (TypeError, ValueError):
                continue
        else:
            try:
                value = float(raw_value)
            except (TypeError, ValueError):
                continue
        item: dict[str, Any] = {"id": plugin_id, "feature": feature, "op": op, "value": value}
        if op == "eq":
            item["equals"] = value
        if spec and spec.get("kind") == "plugin":
            item["params"] = {}
        out.append(item)
    return out


def encode_filter_query(filters: list[dict[str, Any]] | None) -> str:
    parts = []
    for spec in filters or []:
        plugin_id = spec.get("id")
        feature = spec.get("feature")
        if not plugin_id or not feature:
            continue
        op = str(spec.get("op") or ("eq" if "equals" in spec else "eq"))
        value = spec["value"] if "value" in spec else spec.get("equals", 1)
        symbol = _OP_SYMBOL.get(op, "=")
        parts.append(f"{plugin_id}.{feature}{symbol}{value}")
    return ",".join(parts)


def sanitize_gates(raw: Any) -> list[dict[str, Any]]:
    known = known_gates()
    items = raw if isinstance(raw, list) else []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for spec in items:
        if not isinstance(spec, dict):
            continue
        key = gate_key(spec)
        catalog_item = known.get(key)
        if catalog_item is None or key in seen:
            continue
        seen.add(key)
        op = str(spec.get("op") or catalog_item["default_op"])
        if op not in catalog_item["ops"]:
            op = catalog_item["default_op"]
        value = spec["value"] if "value" in spec else spec.get("equals", catalog_item["default_value"])
        try:
            number = float(value)
            value = int(number) if catalog_item.get("value_kind") == "int" else number
        except (TypeError, ValueError):
            value = catalog_item["default_value"]
        item: dict[str, Any] = {
            "id": catalog_item["id"],
            "feature": catalog_item["feature"],
            "op": op,
            "value": value,
        }
        if op == "eq":
            item["equals"] = value
        if catalog_item.get("kind") == "plugin":
            item["params"] = {}
        out.append(item)
    return out
