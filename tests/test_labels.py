import json
from pathlib import Path

from skabu.config import Settings
from skabu.labels import consider_swap, run_label_compare
from skabu.model import MODEL_NAME, meta_path, model_dir


def _settings(tmp_path: Path) -> Settings:
    settings = Settings(
        jquants_api_key="test",
        jquants_plan="free",
        skabu_data_dir=tmp_path / "data",
        skabu_log_dir=tmp_path / "logs",
    )
    settings.ensure_dirs()
    return settings


def test_consider_swap_needs_floor_and_lift() -> None:
    assert consider_swap(0.50, 0.53) is False
    assert consider_swap(0.50, 0.55) is True
    assert consider_swap(0.54, 0.55) is False
    assert consider_swap(None, 0.60) is False


def test_run_label_compare_writes_aliases(tmp_path: Path, monkeypatch) -> None:
    settings = _settings(tmp_path)
    model_dir(settings).mkdir(parents=True, exist_ok=True)
    meta_path(settings, MODEL_NAME).write_text(
        json.dumps(
            {
                "label": "target_first",
                "metrics": {
                    "n_train": 80,
                    "n_valid": 20,
                    "valid_auc": 0.5059,
                    "valid_accuracy": 0.51,
                    "pos_rate": 0.33,
                    "train_as_of": ["2024-01-01", "2025-06-01"],
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (model_dir(settings) / "boost.joblib").write_bytes(b"production")
    names: list[str] = []

    def fake_train(_settings, **kwargs):
        names.append(kwargs["name"])
        assert kwargs["name"] != MODEL_NAME
        assert kwargs["label"] in {"ret_pos", "excess_pos"}
        return {
            "status": "candidate",
            "n_train": 70,
            "n_valid": 18,
            "auc": 0.51,
            "meta": {
                "label": kwargs["label"],
                "metrics": {
                    "n_train": 70,
                    "n_valid": 18,
                    "valid_auc": 0.51,
                    "valid_accuracy": 0.52,
                    "pos_rate": 0.48,
                    "train_as_of": ["2024-01-01", "2025-06-01"],
                },
            },
        }

    monkeypatch.setattr("skabu.labels.train_model", fake_train)
    payload = run_label_compare(settings, min_avg_volume=1, min_bars=10, stride=20)
    assert payload["ok"] is True
    assert payload["swapped"] is False
    assert payload["note"] == "本番維持"
    assert names == ["boost.ret_pos", "boost.excess_pos"]
    assert (tmp_path / "data" / "boost.joblib").exists() or (model_dir(settings) / "boost.joblib").read_bytes() == b"production"
    written = json.loads((settings.skabu_data_dir / "label_compare.json").read_text(encoding="utf-8"))
    assert [item["name"] for item in written["items"]] == ["boost", "boost.ret_pos", "boost.excess_pos"]
    assert all(item["name"] != "boost" or item["source"] == "production" for item in written["items"])
