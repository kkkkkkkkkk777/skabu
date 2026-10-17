from skabu.gates import (
    encode_filter_query,
    gate_catalog,
    parse_filter_query,
    sanitize_gates,
)
from skabu.ind_groups import PLUGIN_GROUP


def test_gate_catalog_covers_price_and_facts() -> None:
    keys = {f"{item['id']}.{item['feature']}" for item in gate_catalog()}
    assert "ichimoku.above_cloud" in keys
    assert "bbands.pct_b" in keys
    assert "rsi.value" in keys
    assert "doubletop.double_top" in keys
    assert "downtrend.lower_lows" in keys
    assert "headshoulders.head_shoulders" in keys
    assert "candles.bearish" in keys
    assert "elliott.wave3_up" in keys
    assert "elliott.wave5_done" in keys
    assert "elliott.correcting" in keys
    assert "weekly.higher_highs" in keys
    assert "weekly.above_sma" in keys
    assert "weekly.rsi" in keys
    assert "regime.above_ma200" in keys
    assert "facts.mkt_cap" in keys
    assert "facts.avg_vo" in keys
    assert "facts.per" in keys
    assert "facts.pbr" in keys
    assert "facts.op_margin" in keys
    assert "facts.div_yield" in keys
    assert "facts.div_yield_hi" in keys
    assert "facts.roe" in keys
    assert "facts.eq_ar" in keys
    assert "facts.sales_growth3" in keys
    by_key = {f"{item['id']}.{item['feature']}": item for item in gate_catalog()}
    assert by_key["facts.op_margin"]["default_value"] == 0
    assert by_key["facts.div_yield"]["default_value"] == 0.035
    assert by_key["facts.div_yield_hi"]["default_value"] == 0.05
    assert by_key["facts.roe"]["default_value"] == 0.10
    assert by_key["facts.eq_ar"]["default_value"] == 0.40
    assert by_key["facts.sales_growth3"]["default_value"] == 0
    helps = {key: item.get("help") for key, item in by_key.items()}
    assert helps["doubletop.double_top"]
    assert helps["downtrend.lower_lows"]
    assert helps["facts.per"]
    assert "無い" in helps["facts.per"]
    assert "例" in helps["facts.per"]
    assert "例" in helps["doubletop.double_top"]
    assert "動画なら 0.08" in helps["facts.op_margin"]
    assert "分割" in helps["facts.div_yield"]
    assert "Free" in helps["facts.sales_growth3"]
    assert "0.10＝10%" in helps["facts.roe"]
    assert by_key["headshoulders.head_shoulders"]["default_value"] == 0
    assert "頭" in helps["headshoulders.head_shoulders"]
    assert by_key["candles.bearish"]["default_value"] == 0
    assert "包み" in helps["candles.bearish"]
    assert by_key["elliott.wave3_up"]["default_value"] == 1
    assert by_key["elliott.wave5_done"]["default_value"] == 0
    assert by_key["elliott.correcting"]["default_value"] == 0
    assert by_key["weekly.higher_highs"]["default_value"] == 1
    assert by_key["weekly.above_sma"]["default_value"] == 1
    assert by_key["weekly.rsi"]["default_value"] == 70
    assert by_key["weekly.higher_highs"]["kind"] == "weekly"
    assert "週" in helps["weekly.higher_highs"]
    assert "13週" in helps["weekly.above_sma"]
    assert "70" in helps["weekly.rsi"]
    assert "仮" in helps["elliott.wave3_up"]
    assert "下げ" in helps["elliott.wave3_up"]
    assert "例" in helps["elliott.wave5_done"]
    assert "形が無い" in helps["elliott.correcting"]


def test_gate_catalog_has_groups() -> None:
    items = gate_catalog()
    assert items
    missing = [f"{item['id']}.{item['feature']}" for item in items if not item.get("group")]
    assert missing == []
    unmapped = [item["id"] for item in items if item["id"] not in PLUGIN_GROUP]
    assert unmapped == []
    for item in items:
        assert item["group"] == PLUGIN_GROUP[item["id"]]
        assert item["group"] != "other"


def test_parse_filter_query_absent_empty_and_csv() -> None:
    assert parse_filter_query({}) is None
    assert parse_filter_query({"ind": "rsi"}) is None
    assert parse_filter_query({"flt": ""}) == []
    parsed = parse_filter_query({"flt": "rsi.value<70,facts.per<=20,ichimoku.above_cloud=1"})
    assert parsed[0] == {"id": "rsi", "feature": "value", "op": "lt", "value": 70.0, "params": {}}
    assert parsed[1]["id"] == "facts"
    assert parsed[1]["feature"] == "per"
    assert parsed[1]["op"] == "lte"
    assert parsed[1]["value"] == 20.0
    assert parsed[2]["equals"] == 1
    assert encode_filter_query(parsed) == "rsi.value<70.0,facts.per<=20.0,ichimoku.above_cloud=1"


def test_sanitize_gates_keeps_known_only() -> None:
    clean = sanitize_gates(
        [
            {"id": "rsi", "feature": "value", "op": "lt", "value": "65"},
            {"id": "nope", "feature": "x", "op": "lt", "value": 1},
            {"id": "rsi", "feature": "value", "op": "lt", "value": 90},
        ]
    )
    assert len(clean) == 1
    assert clean[0]["value"] == 65.0
    assert clean[0]["op"] == "lt"
