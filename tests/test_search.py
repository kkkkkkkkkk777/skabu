import json
from pathlib import Path

from skabu.config import Settings
from skabu.experiments import get_experiment
from skabu.search import (
    combo_presets,
    decide_gate,
    gate_atoms,
    need_n,
    one_sample_ttest,
    run_search,
    score_atoms,
    stage1_presets,
    student_t_cdf,
    walk_forward_folds,
)
from skabu.strategy import active_path, active_rank_selections, load_active_strategy
from skabu.verify import SEARCH_PRESETS


def _settings(tmp_path: Path) -> Settings:
    settings = Settings(
        jquants_api_key="test",
        jquants_plan="free",
        skabu_data_dir=tmp_path / "data",
        skabu_log_dir=tmp_path / "logs",
    )
    settings.ensure_dirs()
    return settings


def _periods(returns: list[float]) -> list[dict]:
    rows = []
    for index, value in enumerate(returns, start=1):
        rows.append(
            {
                "as_of": f"2025-{index:02d}-01",
                "period_return": value,
                "bench_return": 0.0,
            }
        )
    return rows


def _item(preset_id: str, spec: dict, returns: list[float]) -> dict:
    return {
        "id": preset_id,
        "title": spec["title"],
        "selections": spec.get("selections"),
        "filters": spec.get("filters") or [],
        "horizons": {"20": {"summary": {}, "periods": _periods(returns)}},
    }


def _fake_compare(returns_by_id: dict[str, list[float]]):
    def inner(_conn, **kwargs):
        presets = kwargs.get("presets") or SEARCH_PRESETS
        items = []
        for key, spec in presets.items():
            rets = returns_by_id.get(key) or ([0.0] * 4 + [-0.02] * 8)
            items.append(_item(key, spec, rets))
        return {"items": items, "rules": {}}

    return inner


def _gate_presets() -> dict:
    return {
        "filters": {"title": "フィルターのみ", "selections": [], "filters": [{"id": "rsi", "feature": "value", "op": "lt", "value": 70}]},
        "v1": {
            "title": "v1",
            "selections": [{"id": "ema", "params": {"length": 25}}],
            "filters": [{"id": "ichimoku", "feature": "above_cloud", "equals": 1}],
        },
        "ema_rsi": {
            "title": "EMA + RSI",
            "selections": [{"id": "ema", "params": {}}, {"id": "rsi", "params": {}}],
            "filters": [],
        },
    }


def _write_active(settings: Settings, *, adopted_at: str = "2025-04-15", ident: str = "v1") -> None:
    active_path(settings).write_text(
        json.dumps(
            {
                "id": ident,
                "title": ident,
                "selections": [{"id": "boost", "params": {"high": 0.58, "low": 0.42}}],
                "filters": [{"id": "rsi", "feature": "value", "op": "lt", "value": 70}],
                "holdout_ok": True,
                "adopted_at": adopted_at,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_search_presets_include_core_and_empty_filters() -> None:
    assert "boost" not in SEARCH_PRESETS
    assert "v1" not in SEARCH_PRESETS
    assert "filters" in SEARCH_PRESETS
    assert "ema_rsi_none" in SEARCH_PRESETS
    assert SEARCH_PRESETS["filters"]["selections"] == []
    assert "momentum" in SEARCH_PRESETS
    assert SEARCH_PRESETS["momentum"]["title"] == "地合い＋高値モメンタム"
    assert SEARCH_PRESETS["momentum"]["selections"] == [{"id": "momentum", "params": {}}]
    assert SEARCH_PRESETS["momentum"]["filters"] == [{"id": "regime", "feature": "above_ma200", "equals": 1}]
    assert "double_top" in SEARCH_PRESETS
    assert SEARCH_PRESETS["double_top"]["title"] == "フィルター＋ダブルトップ除外"
    assert SEARCH_PRESETS["double_top"]["selections"] == []
    assert SEARCH_PRESETS["double_top"]["filters"][-1] == {"id": "doubletop", "feature": "double_top", "equals": 0}
    assert "lower_lows" in SEARCH_PRESETS
    assert SEARCH_PRESETS["lower_lows"]["title"] == "フィルター＋切り下げ除外"
    assert SEARCH_PRESETS["lower_lows"]["selections"] == []
    assert SEARCH_PRESETS["lower_lows"]["filters"][-1] == {
        "id": "downtrend",
        "feature": "lower_lows",
        "equals": 0,
    }
    assert len(SEARCH_PRESETS) == 19
    assert sum(1 for key in SEARCH_PRESETS if key not in {"momentum", "double_top", "lower_lows"}) == 16


def test_walk_forward_last_fold_uses_tail() -> None:
    periods = _periods([0.01] * 8 + [0.09, 0.11])
    folds = walk_forward_folds(periods)
    assert folds
    last = folds[-1]["confirm"]
    assert last["avg_return"] is not None
    assert last["avg_return"] > 0.09


def test_student_t_cdf_center_and_need_n() -> None:
    assert abs(student_t_cdf(0.0, 10) - 0.5) < 1e-6
    assert student_t_cdf(4.0, 10) > 0.99
    assert one_sample_ttest([0.04] * 8)["p"] < 0.05
    assert one_sample_ttest([0.01, -0.01, 0.01, -0.01, 0.0, 0.0, 0.01, -0.01])["p"] >= 0.05
    assert need_n(0.0, 0.02) == 8
    assert need_n(0.05, 0.01) >= 8


def test_decide_gate_n_short_and_p_value() -> None:
    short = {
        "id": "filters",
        "need_n": 8,
        "confirm_periods": _periods([0.05, 0.05, 0.05]),
    }
    assert decide_gate(short, min_n=8)["reason"] == "n_short"
    noisy = {
        "id": "filters",
        "need_n": 8,
        "confirm_periods": _periods([0.01, -0.01, 0.01, -0.01, 0.0, 0.0, 0.01, -0.01]),
    }
    gate = decide_gate(noisy, min_n=8)
    assert gate["ok"] is False
    assert gate["reason"] == "p_ge_05"
    strong = {
        "id": "filters",
        "need_n": 8,
        "confirm_periods": _periods([0.04] * 8),
    }
    ok = decide_gate(strong, min_n=8)
    assert ok["ok"] is True
    assert ok["reason"] == "significant"


def test_search_keeps_active_when_confirm_too_short(tmp_path: Path, monkeypatch) -> None:
    settings = _settings(tmp_path)
    _write_active(settings, adopted_at="2025-10-15")
    monkeypatch.setattr(
        "skabu.search.run_compare",
        _fake_compare(
            {
                "filters": [0.03] * 4 + [0.04] * 8,
                "v1": [0.01] * 4 + [0.02] * 8,
            }
        ),
    )
    report = run_search(
        settings,
        min_avg_volume=1,
        min_bars=10,
        top=1,
        stride=5,
        min_history=15,
        presets=SEARCH_PRESETS,
    )
    assert report["adopted"] is False
    assert report["gate"]["reason"] == "n_short"
    assert load_active_strategy(settings)["id"] == "v1"


def test_search_does_not_adopt_on_mean_only(tmp_path: Path, monkeypatch) -> None:
    settings = _settings(tmp_path)
    _write_active(settings, adopted_at="2025-04-15")
    mixed = [0.03] * 4 + [0.02, -0.015, 0.018, -0.012, 0.01, -0.009, 0.016, -0.014]
    monkeypatch.setattr(
        "skabu.search.run_compare",
        _fake_compare({"filters": mixed, "v1": [0.01] * 4 + [0.0] * 8}),
    )
    report = run_search(
        settings,
        min_avg_volume=1,
        min_bars=10,
        top=1,
        stride=5,
        min_history=15,
        presets=SEARCH_PRESETS,
    )
    assert report["gate"]["n"] >= 8
    assert (report.get("candidates") or [{}])[0].get("confirm", {}).get("avg_return") is not None
    if report["candidate_id"] == "filters":
        assert report["adopted"] is False
        assert report["gate"]["reason"] in {"p_ge_05", "n_short"}
    assert load_active_strategy(settings)["id"] == "v1"


def test_search_adopts_when_significant(tmp_path: Path, monkeypatch) -> None:
    settings = _settings(tmp_path)
    _write_active(settings, adopted_at="2025-04-15")
    monkeypatch.setattr(
        "skabu.search.run_compare",
        _fake_compare(
            {
                "filters": [0.03] * 4 + [0.04] * 8,
                "v1": [0.01] * 4 + [0.01] * 8,
                "boost": [-0.01] * 4 + [0.00] * 8,
            }
        ),
    )
    report = run_search(
        settings,
        min_avg_volume=1,
        min_bars=10,
        top=1,
        stride=5,
        min_history=15,
        presets=_gate_presets(),
    )
    assert report["candidate_id"] == "filters"
    assert report["adopted"] is True
    assert report["gate"]["ok"] is True
    assert report["gate"]["p_gt_zero"] < 0.05
    active = load_active_strategy(settings)
    assert active["id"] == "filters"
    assert active["selections"] == []
    assert active_rank_selections(settings) == []
    stored = get_experiment(settings, "filters")
    assert stored["selections"] == []


def test_search_does_not_switch_to_confirm_leader(tmp_path: Path, monkeypatch) -> None:
    settings = _settings(tmp_path)
    _write_active(settings, adopted_at="2025-04-15", ident="ema_rsi")
    monkeypatch.setattr(
        "skabu.search.run_compare",
        _fake_compare(
            {
                "filters": [0.03] * 4 + [0.04] * 8,
                "v1": [0.005] * 4 + [0.50] * 8,
                "ema_rsi": [0.0] * 4 + [0.0] * 8,
            }
        ),
    )
    report = run_search(
        settings,
        min_avg_volume=1,
        min_bars=10,
        top=1,
        stride=5,
        min_history=15,
        presets=_gate_presets(),
    )
    assert report["candidate_id"] == "filters"
    assert report["winner_id"] == "filters"
    v1 = next(item for item in report["candidates"] if item["id"] == "v1")
    filters = next(item for item in report["candidates"] if item["id"] == "filters")
    assert (v1.get("confirm") or {}).get("avg_return", 0) > (filters.get("confirm") or {}).get("avg_return", 0)
    assert report["adopted"] is True
    assert load_active_strategy(settings)["id"] == "filters"


def test_search_v1_not_required(tmp_path: Path, monkeypatch) -> None:
    settings = _settings(tmp_path)
    _write_active(settings, adopted_at="2025-04-15", ident="ema_rsi")
    monkeypatch.setattr(
        "skabu.search.run_compare",
        _fake_compare(
            {
                "filters": [0.03] * 4 + [0.04] * 8,
                "v1": [0.02] * 4 + [0.10] * 8,
                "ema_rsi": [0.0] * 4 + [0.0] * 8,
            }
        ),
    )
    report = run_search(
        settings,
        min_avg_volume=1,
        min_bars=10,
        top=1,
        stride=5,
        min_history=15,
        presets=_gate_presets(),
    )
    assert report["candidate_id"] == "filters"
    assert report["adopted"] is True
    assert (next(item for item in report["candidates"] if item["id"] == "v1").get("confirm") or {}).get("avg_return", 0) > 0.04


def test_search_does_not_use_sim(tmp_path: Path, monkeypatch) -> None:
    settings = _settings(tmp_path)
    called: list[int] = []
    monkeypatch.setattr("skabu.sim.run_sim", lambda *_args, **_kwargs: called.append(1))
    monkeypatch.setattr("skabu.search.run_compare", _fake_compare({}))
    report = run_search(
        settings,
        min_avg_volume=1,
        min_bars=10,
        top=1,
        stride=5,
        min_history=15,
        presets=SEARCH_PRESETS,
    )
    assert called == []
    assert report["adopted"] is False
    assert report["gate"]["reason"] == "n_short"


def test_score_atoms_include_doublebottom() -> None:
    atoms = score_atoms()
    assert "score_doublebottom" in atoms
    assert atoms["score_doublebottom"]["selections"] == [{"id": "doublebottom", "params": {}}]
    assert atoms["score_doublebottom"]["filters"] == []
    assert "score_uptrend" not in atoms


def test_gate_atoms_include_elliott_skip_sales_growth() -> None:
    atoms = gate_atoms()
    assert "gate_elliott.wave3_up" in atoms
    assert atoms["gate_elliott.wave3_up"]["selections"] == []
    assert atoms["gate_elliott.wave3_up"]["filters"][0]["feature"] == "wave3_up"
    assert all("sales_growth3" not in key for key in atoms)


def test_combo_presets_builds_two_plus_two() -> None:
    scores = [
        {
            "id": "score_doublebottom",
            "title": "ダブルボトム",
            "selections": [{"id": "doublebottom", "params": {}}],
            "filters": [],
        },
        {
            "id": "score_boost",
            "title": "Boost",
            "selections": [{"id": "boost", "params": {}}],
            "filters": [],
        },
    ]
    gates = [
        {
            "id": "gate_elliott.wave3_up",
            "title": "上昇3波中",
            "selections": [],
            "filters": [{"id": "elliott", "feature": "wave3_up", "equals": 1}],
        },
        {
            "id": "gate_ichimoku.above_cloud",
            "title": "雲の上",
            "selections": [],
            "filters": [{"id": "ichimoku", "feature": "above_cloud", "equals": 1}],
        },
    ]
    combos = combo_presets(scores, gates)
    assert any(
        {item["id"] for item in spec["selections"]} == {"doublebottom", "boost"}
        and {f"{item['id']}.{item['feature']}" for item in spec["filters"]}
        == {"elliott.wave3_up", "ichimoku.above_cloud"}
        for spec in combos.values()
    )
    assert len(combos) <= 120
    assert len(combos) == 10


def test_combo_presets_respects_limit() -> None:
    scores = [
        {"id": f"s{index}", "title": f"S{index}", "selections": [{"id": f"p{index}", "params": {}}], "filters": []}
        for index in range(3)
    ]
    gates = [
        {
            "id": f"g{index}",
            "title": f"G{index}",
            "selections": [],
            "filters": [{"id": "elliott", "feature": f"f{index}", "equals": 1}],
        }
        for index in range(3)
    ]
    combos = combo_presets(scores, gates, limit=5)
    assert len(combos) == 5


def test_stage1_includes_catalog_and_stays_under_cap() -> None:
    specs = stage1_presets()
    assert "score_doublebottom" in specs
    assert "gate_elliott.wave3_up" in specs
    assert len(specs) <= 120


def test_two_stage_search_calls_compare_twice(tmp_path: Path, monkeypatch) -> None:
    settings = _settings(tmp_path)
    calls: list[list[str]] = []

    def fake(_conn, **kwargs):
        presets = kwargs.get("presets") or {}
        calls.append(list(presets))
        items = []
        for key, spec in presets.items():
            if key == "score_doublebottom":
                rets = [0.05] * 12
            elif key == "score_rsi":
                rets = [0.03] * 12
            elif key == "gate_elliott.wave3_up":
                rets = [0.045] * 12
            elif key == "gate_ichimoku.above_cloud":
                rets = [0.035] * 12
            elif key == "gate_rsi.value":
                rets = [0.025] * 12
            else:
                rets = [0.0] * 4 + [-0.02] * 8
            items.append(_item(key, spec, rets))
        return {"items": items, "rules": {}}

    monkeypatch.setattr("skabu.search.run_compare", fake)
    report = run_search(settings, min_avg_volume=1, min_bars=10, top=1, stride=5, min_history=15)
    assert len(calls) == 2
    assert "score_doublebottom" in calls[0]
    assert "gate_elliott.wave3_up" in calls[0]
    assert any("doublebottom" in key and "rsi" in key and "elliott" in key for key in calls[1])
    assert any("doublebottom" in key and "rsi" in key and "ichimoku" in key for key in calls[1])
    assert len(calls[1]) <= 120
    assert report["adopted"] is False
    assert report["gate"]["reason"] == "n_short"
