from pathlib import Path

import httpx

from skabu.cli import main


def test_doctor_without_api_key(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("JQUANTS_API_KEY", raising=False)
    code = main(["doctor"])
    captured = capsys.readouterr()
    assert code == 2
    assert "api_key" in captured.out
    assert "NG" in captured.out
    assert "[OK] kabu_api" in captured.out
    assert "ペーパー" in captured.out


def test_ping_with_mock(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("JQUANTS_API_KEY", "test-key")
    monkeypatch.setenv("SKABU_RATE_LIMIT_ENABLED", "false")
    monkeypatch.setenv("SKABU_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SKABU_LOG_DIR", str(tmp_path / "logs"))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"Date": "2024-01-04"}, {"Date": "2024-01-05"}]})

    real_client = httpx.Client
    monkeypatch.setattr(
        "skabu.jquants.httpx.Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    code = main(["ping", "--from-date", "2024-01-04", "--to-date", "2024-01-10"])
    captured = capsys.readouterr()
    assert code == 0
    assert '"count": 2' in captured.out


def test_daily_skip_sync_cli(tmp_path: Path, monkeypatch, capsys) -> None:
    from skabu.store import connect, upsert_daily_bars, upsert_master

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("JQUANTS_API_KEY", "test-key")
    monkeypatch.setenv("SKABU_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SKABU_LOG_DIR", str(tmp_path / "logs"))
    db_path = tmp_path / "data" / "skabu.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    upsert_master(conn, [{"Date": "2026-01-20", "Code": "11110", "CoName": "上昇工業", "MktNm": "プライム"}])
    rows = []
    for index in range(20):
        close = 100 + index
        rows.append(
            {
                "Date": f"2026-01-{index + 1:02d}",
                "Code": "11110",
                "AdjO": close,
                "AdjH": close + 1,
                "AdjL": close - 1,
                "AdjC": close,
                "AdjVo": 80_000,
            }
        )
    upsert_daily_bars(conn, rows)
    conn.commit()
    conn.close()
    code = main(["daily", "--skip-sync", "--top", "5", "--min-volume", "1", "--min-bars", "10"])
    captured = capsys.readouterr()
    assert code == 0
    assert '"status": "ok"' in captured.out
    assert (tmp_path / "data" / "candidates.csv").exists()


def test_search_cli(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("JQUANTS_API_KEY", "test-key")
    monkeypatch.setenv("SKABU_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SKABU_LOG_DIR", str(tmp_path / "logs"))

    def fake_search(_settings, **_kwargs):
        return {
            "ok": True,
            "adopted": False,
            "winner_id": "v1",
            "previous_id": None,
            "gate": {"ok": False, "reason": "not_better_than_v1"},
            "note": "この確認は v1 採用と重なる。次の版の公式判定は、採用日以降の新しい足が溜まってから。",
            "active": {"title": "v1"},
            "candidates": [{"id": "v1", "title": "v1", "confirm": {"avg_return": 0.0}, "selections": [], "filters": []}],
        }

    monkeypatch.setattr("skabu.search.run_search", fake_search)
    code = main(["search"])
    captured = capsys.readouterr()
    assert code == 0
    assert '"adopted": false' in captured.out
    assert "v1" in captured.out


def test_labels_cli(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("JQUANTS_API_KEY", "test-key")
    monkeypatch.setenv("SKABU_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SKABU_LOG_DIR", str(tmp_path / "logs"))

    def fake_compare(_settings, **_kwargs):
        return {
            "ok": True,
            "note": "本番維持",
            "swapped": False,
            "path": str(tmp_path / "data" / "label_compare.json"),
            "items": [
                {"name": "boost", "label": "target_first", "source": "production"},
                {"name": "boost.ret_pos", "label": "ret_pos", "source": "candidate"},
                {"name": "boost.excess_pos", "label": "excess_pos", "source": "candidate"},
            ],
        }

    monkeypatch.setattr("skabu.labels.run_label_compare", fake_compare)
    code = main(["labels"])
    captured = capsys.readouterr()
    assert code == 0
    assert "boost.ret_pos" in captured.out
    assert '"swapped": false' in captured.out
    assert "本番維持" in captured.out
