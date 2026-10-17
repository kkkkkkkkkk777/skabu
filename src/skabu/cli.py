"""コマンドライン入口。"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from skabu import __version__
from skabu.config import load_settings, plan_safe_sample_window
from skabu.errors import ConfigError, SkabuError
from skabu.jquants import JQuantsClient
from skabu.logging_setup import get_logger, setup_logging
from skabu.sync import SyncService


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="skabu",
        description="日本株日足の長期予測システム（フェーズ9: 運用しながら学習）",
    )
    parser.add_argument("--version", action="version", version=f"skabu {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="設定・ログ・API接続を診断する")

    ping = sub.add_parser("ping", help="取引カレンダーを1件取って接続確認する")
    ping.add_argument("--from-date", default=None, help="カレンダー取得開始日（未指定ならプランに合わせて自動）")
    ping.add_argument("--to-date", default=None, help="カレンダー取得終了日")

    sample = sub.add_parser("sample-bars", help="指定銘柄の日足を数件取得して表示する")
    sample.add_argument("--code", default="86970", help="銘柄コード（既定: JPX 8697）")
    sample.add_argument("--from-date", default=None)
    sample.add_argument("--to-date", default=None)

    sync = sub.add_parser("sync", help="J-Quants からカレンダー・銘柄・日足をローカルへ取り込む")
    sync.add_argument("dataset", choices=["calendar", "master", "bars", "facts", "all"])
    sync.add_argument("--from-date", default=None)
    sync.add_argument("--to-date", default=None)
    sync.add_argument("--max-days", type=int, default=5, help="bars/facts で取り込む営業日数の上限（新しい日付から）")
    sync.add_argument("--full", action="store_true", help="プラン期間の全日足を取り込む（無料プランは1時間以上かかることがある）")

    sub.add_parser("status", help="ローカルDBの件数と最終同期を表示する")

    serve = sub.add_parser("serve", help="チャートとランキング画面を開く")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)

    rank = sub.add_parser("rank", help="インジケータ総合点で上昇候補を並べる")
    rank.add_argument("--market", default="プライム")
    rank.add_argument("--top", type=int, default=20)
    rank.add_argument("--min-volume", type=float, default=50_000)
    rank.add_argument("--refresh", action="store_true")

    verify = sub.add_parser("verify", help="ロックした予測の勝率と超過リターンを出す")
    verify.add_argument("--top", type=int, default=20)
    verify.add_argument("--stride", type=int, default=20)
    verify.add_argument("--market", default="プライム")
    verify.add_argument("--min-volume", type=float, default=50_000)
    verify.add_argument("--refresh", action="store_true")
    verify.add_argument("--compare", action="store_true")

    daily = sub.add_parser("daily", help="引け後に差分同期し、候補をロックして CSV に出す")
    daily.add_argument("--skip-sync", action="store_true", help="J-Quants へ問い合わせずローカル足だけで出す")
    daily.add_argument("--top", type=int, default=20)
    daily.add_argument("--market", default="プライム")
    daily.add_argument("--min-volume", type=float, default=50_000)
    daily.add_argument("--max-days", type=int, default=5)
    daily.add_argument("--min-bars", type=int, default=60)
    daily.add_argument("--refresh", action="store_true")
    daily.add_argument("--no-learn", action="store_true", help="採点だけして再学習判定はしない")
    daily.add_argument("--cash", type=float, default=1_000_000, help="推奨金額の計算に使う資金")
    daily.add_argument("--risk-pct", type=float, default=1.0, help="1本の損切上限（資金に対する%）")

    train = sub.add_parser("train", help="20日以内の目標先着を当てるモデルを学習する")
    train.add_argument("--market", default="プライム")
    train.add_argument("--min-volume", type=float, default=50_000)
    train.add_argument("--min-bars", type=int, default=60)
    train.add_argument("--stride", type=int, default=20)
    train.add_argument("--horizon", type=int, default=20)
    train.add_argument("--label", default="target_first", help="target_first / ret_pos / excess_pos。ret_pos と excess_pos は --name 必須")
    train.add_argument("--name", default=None, help="保存名。省略時は boost。比較は labels を使う")

    labels = sub.add_parser("labels", help="当てる対象を2つ測る。本番 Boost は読んだだけで上書きしない")
    labels.add_argument("--market", default="プライム")
    labels.add_argument("--min-volume", type=float, default=50_000)
    labels.add_argument("--min-bars", type=int, default=60)
    labels.add_argument("--stride", type=int, default=20)
    labels.add_argument("--horizon", type=int, default=20)

    adopt = sub.add_parser("adopt", help="ウォークフォワードで組み合わせを選び、本番戦略にする")
    adopt.add_argument("--market", default="プライム")
    adopt.add_argument("--min-volume", type=float, default=50_000)
    adopt.add_argument("--min-bars", type=int, default=60)
    adopt.add_argument("--top", type=int, default=20)
    adopt.add_argument("--stride", type=int, default=20)
    adopt.add_argument("--refresh", action="store_true")

    search = sub.add_parser("search", help="点数＋フィルターを自動探索し、v1より儲かるときだけ日次を更新する")
    search.add_argument("--market", default="プライム")
    search.add_argument("--min-volume", type=float, default=50_000)
    search.add_argument("--min-bars", type=int, default=60)
    search.add_argument("--top", type=int, default=20)
    search.add_argument("--stride", type=int, default=20)
    search.add_argument("--refresh", action="store_true")

    grade = sub.add_parser("grade", help="期限が来たロック予測を採点して台帳に残す")
    grade.add_argument("--horizon", type=int, default=20)
    grade.add_argument("--rebuild", action="store_true", help="台帳を消してから再採点する")

    learn = sub.add_parser("learn", help="採点済みが溜まったら候補モデルを作り、良いときだけ採用する")
    learn.add_argument("--force", action="store_true", help="件数条件を無視して再学習する")
    learn.add_argument("--market", default="プライム")
    learn.add_argument("--min-volume", type=float, default=50_000)
    learn.add_argument("--min-bars", type=int, default=60)
    learn.add_argument("--top", type=int, default=20)
    learn.add_argument("--stride", type=int, default=20)
    learn.add_argument("--horizon", type=int, default=20)

    sub.add_parser("experiments", help="作戦の版を一覧する")
    use = sub.add_parser("use", help="作戦の版を採用する（日次・ランキング・シミュの既定になる）")
    use.add_argument("id", help="版の id（例: v1 / boost）")

    sim = sub.add_parser("sim", help="1か月前の予測で買った資金の日次推移を出す")
    sim.add_argument("--as-of", default=None, help="予測日（未指定なら最新足の20営業日前）")
    sim.add_argument("--cash", type=float, default=1_000_000)
    sim.add_argument("--top", type=int, default=5)
    sim.add_argument("--horizon", type=int, default=20)
    sim.add_argument("--market", default="プライム")
    sim.add_argument("--min-volume", type=float, default=50_000)
    sim.add_argument("--min-bars", type=int, default=60)
    sim.add_argument("--risk-pct", type=float, default=1.0, help="1本の損切上限（資金に対する%）")

    trade = sub.add_parser("trade", help="カブコムで候補を発注・監視する")
    trade.add_argument("--buy", action="store_true", help="自動なら発注、承認なら待ちにする")
    trade.add_argument("--watch", action="store_true", help="約定と利確・損切を更新する")
    trade.add_argument("--status", action="store_true", help="接続と台帳を表示する")

    args = parser.parse_args(argv)
    try:
        settings = load_settings()
        setup_logging(settings.skabu_log_dir, settings.skabu_log_level)
        if args.command == "doctor":
            return _cmd_doctor(settings)
        if args.command == "ping":
            from_date, to_date = _resolve_window(settings.jquants_plan, args.from_date, args.to_date)
            return _cmd_ping(settings, from_date, to_date)
        if args.command == "sample-bars":
            from_date, to_date = _resolve_window(settings.jquants_plan, args.from_date, args.to_date)
            return _cmd_sample_bars(settings, args.code, from_date, to_date)
        if args.command == "sync":
            return _cmd_sync(settings, args)
        if args.command == "status":
            return _cmd_status(settings)
        if args.command == "serve":
            return _cmd_serve(settings, args.host, args.port)
        if args.command == "rank":
            return _cmd_rank(settings, args)
        if args.command == "verify":
            return _cmd_verify(settings, args)
        if args.command == "daily":
            return _cmd_daily(settings, args)
        if args.command == "train":
            return _cmd_train(settings, args)
        if args.command == "labels":
            return _cmd_labels(settings, args)
        if args.command == "adopt":
            return _cmd_adopt(settings, args)
        if args.command == "search":
            return _cmd_search(settings, args)
        if args.command == "grade":
            return _cmd_grade(settings, args)
        if args.command == "learn":
            return _cmd_learn(settings, args)
        if args.command == "sim":
            return _cmd_sim(settings, args)
        if args.command == "experiments":
            return _cmd_experiments(settings)
        if args.command == "use":
            return _cmd_use(settings, args.id)
        if args.command == "trade":
            return _cmd_trade(settings, args)
        parser.error(f"unknown command: {args.command}")
        return 2
    except ConfigError as exc:
        print(f"設定エラー: {exc}", file=sys.stderr)
        return 2
    except SkabuError as exc:
        get_logger().exception("command failed")
        print(f"エラー: {exc}", file=sys.stderr)
        return 1


def _resolve_window(plan: str, from_date: str | None, to_date: str | None) -> tuple[str, str]:
    auto_from, auto_to = plan_safe_sample_window(plan)
    return from_date or auto_from, to_date or auto_to


def _cmd_doctor(settings: Any) -> int:
    log = get_logger()
    checks: list[tuple[str, bool, str]] = []

    checks.append(("data_dir", settings.skabu_data_dir.exists(), str(settings.skabu_data_dir.resolve())))
    checks.append(("log_dir", settings.skabu_log_dir.exists(), str(settings.skabu_log_dir.resolve())))
    checks.append(("debug_dir", settings.debug_dir.exists(), str(settings.debug_dir.resolve())))
    checks.append(("plan", True, f"{settings.jquants_plan} ({settings.requests_per_minute} req/min)"))

    key_ok = bool(settings.jquants_api_key.strip())
    checks.append(
        (
            "api_key",
            key_ok,
            "設定済み" if key_ok else "未設定（.env の JQUANTS_API_KEY を入れてください）",
        )
    )

    api_ok = False
    api_detail = "スキップ（APIキーなし）"
    if key_ok:
        try:
            with JQuantsClient(settings) as client:
                result = client.ping()
            api_ok = True
            api_detail = (
                f"接続成功 calendar_rows={result['calendar_rows']} "
                f"{result.get('from')}～{result.get('to')}"
            )
        except SkabuError as exc:
            api_detail = str(exc)
            log.error("doctor ping failed: %s", exc)
    checks.append(("jquants_api", api_ok if key_ok else False, api_detail))

    try:
        from skabu.broker.execute import snapshot_broker

        snap = snapshot_broker(settings)
        if settings.kabu_live:
            kabu_ok = bool(snap.get("online"))
            kabu_detail = "接続成功" if kabu_ok else (snap.get("error") or "kabuステーション未接続")
        else:
            kabu_ok = True
            kabu_detail = "ペーパー（KABU_LIVE=false）。実発注しない"
        if settings.kabu_live and not settings.kabu_api_password.strip():
            kabu_ok = False
            kabu_detail = "KABU_API_PASSWORD が未設定"
    except Exception as exc:
        kabu_ok = not settings.kabu_live
        kabu_detail = str(exc)
        log.exception("doctor kabu failed")
    checks.append(("kabu_api", kabu_ok, kabu_detail))

    print(f"skabu doctor  v{__version__}")
    print("-" * 60)
    failed = 0
    for name, ok, detail in checks:
        mark = "OK" if ok else "NG"
        if not ok:
            failed += 1
        print(f"[{mark}] {name}: {detail}")
    print("-" * 60)
    if not key_ok:
        print("次の手順: .env.example を .env にコピーし、J-Quants の APIキーを入れて `python -m skabu doctor`")
        return 2
    if failed:
        print("失敗した項目があります。logs/skabu.log と logs/debug/ を確認してください。")
        return 1
    print("フェーズ0の接続基盤は問題ありません。次は `python -m skabu sync all` です。")
    return 0


def _cmd_ping(settings: Any, from_date: str, to_date: str) -> int:
    with JQuantsClient(settings) as client:
        rows = client.get_calendar(from_date=from_date, to_date=to_date)
    print(json.dumps({"count": len(rows), "sample": rows[:3]}, ensure_ascii=False, indent=2))
    return 0


def _cmd_sample_bars(settings: Any, code: str, from_date: str, to_date: str) -> int:
    with JQuantsClient(settings) as client:
        rows = client.get_daily_bars(code=code, from_date=from_date, to_date=to_date)
    slim = [
        {
            "Date": row.get("Date"),
            "Code": row.get("Code"),
            "AdjO": row.get("AdjO"),
            "AdjH": row.get("AdjH"),
            "AdjL": row.get("AdjL"),
            "AdjC": row.get("AdjC"),
            "AdjVo": row.get("AdjVo"),
        }
        for row in rows
    ]
    print(json.dumps({"count": len(slim), "bars": slim}, ensure_ascii=False, indent=2))
    return 0


def _cmd_sync(settings: Any, args: argparse.Namespace) -> int:
    with JQuantsClient(settings) as client:
        service = SyncService(settings, client)
        results: list[dict[str, Any]] = []
        if args.dataset in {"calendar", "all"}:
            results.append(service.sync_calendar(from_date=args.from_date, to_date=args.to_date))
        if args.dataset in {"master", "all"}:
            results.append(service.sync_master(as_of=args.to_date))
        if args.dataset in {"bars", "all"}:
            results.append(
                service.sync_bars(
                    from_date=args.from_date,
                    to_date=args.to_date,
                    max_days=None if args.full else args.max_days,
                    full=args.full,
                    progress=lambda msg: print(msg, file=sys.stderr),
                )
            )
        if args.dataset == "facts":
            results.append(
                service.sync_facts(
                    from_date=args.from_date,
                    to_date=args.to_date,
                    max_days=None if args.full else args.max_days,
                    full=args.full,
                    progress=lambda msg: print(msg, file=sys.stderr),
                )
            )
    print(json.dumps(results, ensure_ascii=False, indent=2))
    if any(item.get("error_days") for item in results):
        return 1
    return 0


def _cmd_status(settings: Any) -> int:
    from skabu.config import plan_data_window
    from skabu.store import connect, status_snapshot

    with connect(settings.db_path) as conn:
        snap = status_snapshot(conn)
    printable = {
        "db_path": str(settings.db_path.resolve()),
        "plan_window": plan_data_window(settings.jquants_plan),
        "counts": snap["counts"],
        "daily_bar_from": snap["daily_bar_from"],
        "daily_bar_to": snap["daily_bar_to"],
        "sync_state": {
            name: {
                "last_from": row["last_from"],
                "last_to": row["last_to"],
                "row_count": row["row_count"],
                "detail": row["detail"],
                "updated_at": row["updated_at"],
            }
            for name, row in snap["sync_state"].items()
        },
    }
    print(json.dumps(printable, ensure_ascii=False, indent=2))
    return 0


def _cmd_serve(settings: Any, host: str, port: int) -> int:
    import uvicorn

    from skabu.web.app import create_app

    url = f"http://{host}:{port}/"
    print(f"チャート: {url}")
    print(f"ランキング: {url}rank")
    print(f"検証: {url}verify")
    print(f"日次候補: {url}daily")
    print(f"学習モデル: {url}model")
    print(f"シミュ: {url}sim")
    print(f"戦略: {url}strategy")
    print("停止: Ctrl+C")
    app = create_app(settings)
    runner = getattr(app.state, "auto_sync", None)
    if runner is not None:
        runner.start()
    uvicorn.run(app, host=host, port=port, log_level="info")
    return 0


def _cmd_rank(settings: Any, args: argparse.Namespace) -> int:
    from skabu.levels import exit_pair
    from skabu.ranking import build_ranking
    from skabu.store import connect
    from skabu.strategy import active_rank_filters, active_rank_selections

    stop_mult, target_mult = exit_pair(settings)
    with connect(settings.db_path) as conn:
        payload = build_ranking(
            conn,
            selections=active_rank_selections(settings),
            filters=active_rank_filters(settings),
            settings=settings,
            market=args.market,
            min_avg_volume=args.min_volume,
            top=args.top,
            refresh=args.refresh,
            stop_mult=stop_mult,
            target_mult=target_mult,
        )
    slim = {
        "as_of": payload.get("as_of"),
        "universe": payload.get("universe"),
        "scored": payload.get("scored"),
        "elapsed_ms": payload.get("elapsed_ms"),
        "cached": payload.get("cached"),
        "rows": [
            {
                "rank": row["rank"],
                "code": row["code"],
                "co_name": row["co_name"],
                "total": row["total"],
                "scores": row["scores"],
                "reason": row["reason"],
            }
            for row in payload.get("rows") or []
        ],
    }
    print(json.dumps(slim, ensure_ascii=False, indent=2))
    return 0 if payload.get("scored") else 1


def _cmd_verify(settings: Any, args: argparse.Namespace) -> int:
    from skabu.store import connect
    from skabu.verify import run_compare, run_verify

    with connect(settings.db_path) as conn:
        if args.compare:
            payload = run_compare(
                conn,
                market=args.market,
                min_avg_volume=args.min_volume,
                top=args.top,
                stride=args.stride,
                refresh=args.refresh,
            )
        else:
            payload = run_verify(
                conn,
                settings=settings,
                market=args.market,
                min_avg_volume=args.min_volume,
                top=args.top,
                stride=args.stride,
                refresh=args.refresh,
            )
    if args.compare:
        printable = payload
    else:
        printable = {
            "locked_runs": payload.get("locked_runs"),
            "as_of_dates": payload.get("as_of_dates"),
            "elapsed_ms": payload.get("elapsed_ms"),
            "cached": payload.get("cached"),
            "rules": payload.get("rules"),
            "horizons": {
                name: body.get("summary")
                for name, body in (payload.get("horizons") or {}).items()
            },
        }
    print(json.dumps(printable, ensure_ascii=False, indent=2))
    return 0


def _cmd_daily(settings: Any, args: argparse.Namespace) -> int:
    from skabu.daily import run_daily

    try:
        payload = run_daily(
            settings,
            skip_sync=args.skip_sync,
            top=args.top,
            market=args.market,
            min_avg_volume=args.min_volume,
            min_bars=args.min_bars,
            max_days=args.max_days,
            refresh=args.refresh,
            auto_learn=not args.no_learn,
            cash=args.cash,
            risk_pct=args.risk_pct,
        )
    except SkabuError as exc:
        print(f"日次運用失敗: {exc}", file=sys.stderr)
        return 1
    report = payload.get("report") or {}
    printable = {
        "status": payload.get("status"),
        "as_of": payload.get("as_of"),
        "plan": payload.get("plan"),
        "freshness": (report.get("freshness") or {}).get("message"),
        "locked": payload.get("locked"),
        "candidates_path": payload.get("candidates_path"),
        "names": len((report.get("rows") or [])),
        "start_cash": report.get("start_cash"),
        "risk_pct": report.get("risk_pct"),
        "synced_days": payload.get("synced_days"),
        "error_days": payload.get("error_days"),
        "grade": report.get("grade"),
        "learn": report.get("learn"),
        "rows": [
            {
                "rank": row.get("rank"),
                "code": row.get("code"),
                "co_name": row.get("co_name"),
                "s33_nm": row.get("s33_nm"),
                "alloc": row.get("alloc"),
                "qty": row.get("qty"),
                "total": row.get("total"),
            }
            for row in (report.get("rows") or [])[: args.top]
        ],
    }
    print(json.dumps(printable, ensure_ascii=False, indent=2))
    return 0 if payload.get("status") == "ok" else 1


def _cmd_train(settings: Any, args: argparse.Namespace) -> int:
    from skabu.model import MODEL_NAME, train_model

    try:
        payload = train_model(
            settings,
            market=args.market,
            min_avg_volume=args.min_volume,
            min_bars=args.min_bars,
            stride=args.stride,
            horizon=args.horizon,
            name=args.name or MODEL_NAME,
            label=args.label,
        )
    except SkabuError as exc:
        print(f"学習失敗: {exc}", file=sys.stderr)
        return 1
    meta = payload.get("meta") or {}
    printable = {
        "status": payload.get("status"),
        "n_train": payload.get("n_train"),
        "n_valid": payload.get("n_valid"),
        "accuracy": payload.get("accuracy"),
        "auc": payload.get("auc"),
        "model_path": payload.get("model_path"),
        "metrics": meta.get("metrics"),
    }
    print(json.dumps(printable, ensure_ascii=False, indent=2))
    return 0 if payload.get("status") == "ok" else 1


def _cmd_labels(settings: Any, args: argparse.Namespace) -> int:
    from skabu.labels import run_label_compare

    try:
        payload = run_label_compare(
            settings,
            market=args.market,
            min_avg_volume=args.min_volume,
            min_bars=args.min_bars,
            stride=args.stride,
            horizon=args.horizon,
        )
    except SkabuError as exc:
        print(f"ラベル比較失敗: {exc}", file=sys.stderr)
        return 1
    printable = {
        "ok": payload.get("ok"),
        "note": payload.get("note"),
        "swapped": payload.get("swapped"),
        "path": payload.get("path"),
        "items": payload.get("items"),
    }
    print(json.dumps(printable, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


def _cmd_grade(settings: Any, args: argparse.Namespace) -> int:
    from skabu.learn import grade_outcomes

    try:
        payload = grade_outcomes(settings, horizon=args.horizon, rebuild=args.rebuild)
    except SkabuError as exc:
        print(f"採点失敗: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("status") == "ok" else 1


def _cmd_learn(settings: Any, args: argparse.Namespace) -> int:
    from skabu.learn import grade_outcomes, maybe_learn

    try:
        graded = grade_outcomes(settings, horizon=args.horizon)
        payload = maybe_learn(
            settings,
            force=args.force,
            market=args.market,
            min_avg_volume=args.min_volume,
            min_bars=args.min_bars,
            top=args.top,
            stride=args.stride,
            horizon=args.horizon,
        )
    except SkabuError as exc:
        print(f"学習ループ失敗: {exc}", file=sys.stderr)
        return 1
    printable = {"grade": graded, **payload}
    print(json.dumps(printable, ensure_ascii=False, indent=2))
    if payload.get("status") == "failed":
        return 1
    return 0


def _cmd_sim(settings: Any, args: argparse.Namespace) -> int:
    from skabu.sim import run_sim

    try:
        payload = run_sim(
            settings,
            as_of=args.as_of,
            cash=args.cash,
            top=args.top,
            horizon=args.horizon,
            market=args.market,
            min_avg_volume=args.min_volume,
            min_bars=args.min_bars,
            risk_pct=args.risk_pct,
        )
    except SkabuError as exc:
        print(f"シミュレーション失敗: {exc}", file=sys.stderr)
        return 1
    printable = {
        "status": payload.get("status"),
        "as_of": payload.get("as_of"),
        "entry_date": payload.get("entry_date"),
        "end_date": payload.get("end_date"),
        "cash_start": payload.get("cash_start"),
        "risk_pct": payload.get("risk_pct"),
        "equity_end": payload.get("equity_end"),
        "pnl": payload.get("pnl"),
        "return": payload.get("return"),
        "max_drawdown": payload.get("max_drawdown"),
        "wins": payload.get("wins"),
        "n": payload.get("n"),
        "trades": [
            {
                "rank": row.get("rank"),
                "code": row.get("code"),
                "co_name": row.get("co_name"),
                "s33_nm": row.get("s33_nm"),
                "alloc": row.get("alloc"),
                "qty": row.get("qty"),
                "entry": row.get("entry"),
                "exit": row.get("exit"),
                "exit_reason": row.get("exit_reason"),
                "pnl": row.get("pnl"),
            }
            for row in payload.get("trades") or []
        ],
        "curve": payload.get("curve"),
    }
    print(json.dumps(printable, ensure_ascii=False, indent=2))
    return 0 if payload.get("status") == "ok" else 1


def _cmd_adopt(settings: Any, args: argparse.Namespace) -> int:
    from skabu.strategy import adopt_strategy

    try:
        payload = adopt_strategy(
            settings,
            market=args.market,
            min_avg_volume=args.min_volume,
            min_bars=args.min_bars,
            top=args.top,
            stride=args.stride,
            refresh=args.refresh,
        )
    except SkabuError as exc:
        print(f"戦略選定失敗: {exc}", file=sys.stderr)
        return 1
    report = payload.get("report") or {}
    active = report.get("active") or {}
    printable = {
        "status": payload.get("status"),
        "chosen_id": payload.get("chosen_id"),
        "title": active.get("title"),
        "holdout_ok": payload.get("holdout_ok"),
        "warning": active.get("warning"),
        "train": active.get("train"),
        "holdout": active.get("holdout"),
        "selections": active.get("selections"),
        "candidates": [
            {
                "id": item.get("id"),
                "title": item.get("title"),
                "train": item.get("train"),
                "holdout": item.get("holdout"),
            }
            for item in report.get("candidates") or []
        ],
    }
    print(json.dumps(printable, ensure_ascii=False, indent=2))
    return 0 if payload.get("status") == "ok" else 1


def _cmd_search(settings: Any, args: argparse.Namespace) -> int:
    from skabu.search import run_search

    try:
        payload = run_search(
            settings,
            market=args.market,
            min_avg_volume=args.min_volume,
            min_bars=args.min_bars,
            top=args.top,
            stride=args.stride,
            refresh=args.refresh,
        )
    except SkabuError as exc:
        print(f"自動探索失敗: {exc}", file=sys.stderr)
        return 1
    active = payload.get("active") or {}
    printable = {
        "ok": payload.get("ok"),
        "adopted": payload.get("adopted"),
        "winner_id": payload.get("winner_id"),
        "previous_id": payload.get("previous_id"),
        "gate": payload.get("gate"),
        "note": payload.get("note"),
        "title": active.get("title"),
        "confirm": ((payload.get("candidates") or [{}])[0] or {}).get("confirm")
        if payload.get("winner_id")
        else None,
        "candidates": [
            {
                "id": item.get("id"),
                "title": item.get("title"),
                "confirm": item.get("confirm"),
                "selections": item.get("selections"),
                "filters": item.get("filters"),
            }
            for item in payload.get("candidates") or []
        ],
    }
    winner = next((item for item in payload.get("candidates") or [] if item.get("id") == payload.get("winner_id")), None)
    if winner is not None:
        printable["confirm"] = winner.get("confirm")
    print(json.dumps(printable, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


def _cmd_experiments(settings: Any) -> int:
    from skabu.experiments import experiments_payload

    payload = experiments_payload(settings)
    printable = {
        "active_id": payload.get("active_id"),
        "items": [
            {
                "id": item.get("id"),
                "title": item.get("title"),
                "note": item.get("note"),
                "filter_summary": item.get("filter_summary"),
                "active": item.get("active"),
                "verify": item.get("verify"),
                "sim": item.get("sim"),
            }
            for item in payload.get("items") or []
        ],
    }
    print(json.dumps(printable, ensure_ascii=False, indent=2))
    return 0


def _cmd_trade(settings: Any, args: argparse.Namespace) -> int:
    from skabu.broker.execute import prepare_buy, trade_status, watch_exits

    if args.buy:
        payload = prepare_buy(settings)
    elif args.watch:
        payload = watch_exits(settings)
    else:
        payload = trade_status(settings)
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0 if payload.get("ok", True) else 1


def _cmd_use(settings: Any, experiment_id: str) -> int:
    from skabu.experiments import activate

    active = activate(settings, experiment_id)
    print(
        json.dumps(
            {
                "id": active.get("id"),
                "title": active.get("title"),
                "filters": active.get("filters"),
                "note": active.get("note"),
                "adopted_at": active.get("adopted_at"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0
