import json
from pathlib import Path

from skabu.buys import (
    BUY_SPEC,
    annotate_buys,
    holdout_return_ok,
    parse_risk_pct,
    ranking_holdout_ok,
    select_buys,
    size_position,
)
from skabu.ranking import cache_key
from skabu.config import Settings
from skabu.strategy import active_path
from skabu.verify import strategy_key


def _row(**kwargs):
    base = {
        "code": "11110",
        "total": 2,
        "close": 100.0,
        "entry_ref": 100.0,
        "atr": 2.0,
        "proba": None,
    }
    base.update(kwargs)
    return base


def test_select_buys_ignores_failed_holdout() -> None:
    picked = select_buys([_row(), _row(code="22220")], top=5, holdout_ok=False)
    assert [row["code"] for row in picked["bought"]] == ["11110", "22220"]
    assert picked["skipped_n"] == 0
    assert "strategy_holdout" not in picked["reasons"]


def test_select_buys_drops_wide_stop() -> None:
    wide = _row(code="58010", atr=10.0, close=100.0)
    ok = _row(code="11110", atr=2.0, close=100.0)
    picked = select_buys([wide, ok], top=5, holdout_ok=True)
    assert [row["code"] for row in picked["bought"]] == ["11110"]
    assert picked["skipped"][0]["skip_reason"] == "stop_wide"


def test_select_buys_ignores_ev_without_proba() -> None:
    picked = select_buys([_row(proba=None, total=1)], top=5, holdout_ok=True)
    assert picked["bought_n"] == 1


def test_select_buys_drops_nonpositive_ev() -> None:
    picked = select_buys([_row(proba=0.2, atr=2.0, close=100.0)], top=5, holdout_ok=True)
    assert picked["bought"] == []
    assert picked["reasons"]["ev_nonpositive"] == 1


def test_select_buys_respects_top() -> None:
    rows = [_row(code=f"{i}0", total=2, s33_nm=f"業種{i}") for i in range(6)]
    picked = select_buys(rows, top=3, holdout_ok=True)
    assert picked["bought_n"] == 3


def test_size_position_risk_one_percent() -> None:
    sized = size_position(
        cash=1_000_000,
        risk_pct=0.01,
        stop_width=0.05,
        price=100,
        leftover=1_000_000,
    )
    assert sized is not None
    assert sized["alloc"] == 200_000
    assert sized["qty"] == 2000
    assert sized["risk_yen"] == 10_000


def test_size_position_skips_less_than_one_share() -> None:
    sized = size_position(
        cash=1_000_000,
        risk_pct=0.01,
        stop_width=0.05,
        price=300_000,
        leftover=1_000_000,
    )
    assert sized is None


def test_parse_risk_pct_is_percent_points() -> None:
    assert parse_risk_pct(1) == 0.01
    assert parse_risk_pct(2.5) == 0.025
    assert parse_risk_pct(None) == 0.01


def test_select_buys_sector_cap() -> None:
    rows = [
        _row(code="11110", s33_nm="電気機器"),
        _row(code="22220", s33_nm="電気機器"),
        _row(code="33330", s33_nm="電気機器"),
        _row(code="44440", s33_nm="情報・通信業"),
    ]
    picked = select_buys(rows, top=5, holdout_ok=True)
    assert [row["code"] for row in picked["bought"]] == ["11110", "22220", "44440"]
    skipped = [row for row in picked["skipped"] if row["skip_reason"] == "sector_cap"]
    assert [row["code"] for row in skipped] == ["33330"]
    assert skipped[0]["skip_label"] == "同じ業種が上限"
    assert picked["reasons"]["sector_cap"] == 1


def test_cache_key_includes_max_per_sector() -> None:
    left = cache_key({"buys": BUY_SPEC, "top": 5})
    other = dict(BUY_SPEC)
    other["max_per_sector"] = 99
    right = cache_key({"buys": other, "top": 5})
    assert left != right
    assert BUY_SPEC["max_per_sector"] == 2
    assert "max_per_sector" in BUY_SPEC


def test_select_buys_drops_nonpositive_score() -> None:
    picked = select_buys([_row(total=0), _row(code="22220", total=-1)], top=5)
    assert picked["bought"] == []
    assert picked["reasons"]["score_nonpositive"] == 2


def test_annotate_sets_labels() -> None:
    rows = annotate_buys([_row(total=-1)])
    assert rows[0]["buyable"] is False
    assert rows[0]["skip_label"] == "合計点がプラスでない"


def test_strategy_key_includes_buy_gate() -> None:
    left = strategy_key([{"id": "sma", "params": {"length": 5}}], "プライム", 1, 10, 1, holdout_ok=True)
    right = strategy_key([{"id": "sma", "params": {"length": 5}}], "プライム", 1, 10, 1, holdout_ok=False)
    assert left != right
    assert BUY_SPEC["max_stop_pct"] == 0.08
    assert BUY_SPEC["max_per_sector"] == 2


def test_ranking_holdout_ok_uses_active_when_same(tmp_path: Path) -> None:
    settings = Settings(
        jquants_api_key="test",
        jquants_plan="free",
        skabu_data_dir=tmp_path / "data",
        skabu_log_dir=tmp_path / "logs",
    )
    settings.ensure_dirs()
    active_path(settings).write_text(
        json.dumps(
            {
                "id": "boost",
                "selections": [{"id": "boost", "params": {"high": 0.58, "low": 0.42}}],
                "filters": [],
                "holdout_ok": False,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    same = [{"id": "boost", "params": {"high": "0.58", "low": "0.42"}}]
    other = [{"id": "sma", "params": {"length": 5}}]
    assert ranking_holdout_ok(settings, same) is False
    assert ranking_holdout_ok(settings, other) is True
    assert ranking_holdout_ok(settings, None) is False


def test_holdout_return_ok_uses_absolute_return() -> None:
    losing_index = {"holdout_ok": False, "holdout": {"avg_return": 0.0079, "avg_excess": -0.05}}
    assert holdout_return_ok(losing_index) is True
    losing_cash = {"holdout_ok": True, "holdout": {"avg_return": -0.01, "avg_excess": 0.02}}
    assert holdout_return_ok(losing_cash) is False
    flag_only = {"holdout_ok": False}
    assert holdout_return_ok(flag_only) is False
