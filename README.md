# skabu

J-Quants API を使った、日本株の日足リサーチ用アプリです。自分の PC で日足を取り込み、チャート、ランキング、検証、シミュレーションを動かします。

これは投資助言ではありません。売買の判断と損益は、使う人の自己責任です。相場データは同梱していません。J-Quants の API キーは各自で発行し、自分の環境へ取り込んでください。

ランキングの初期表示は EMA と RSI です。Super Smoother は John Ehlers が公開している計算式を使っています。

## できること

- `.env` から API キーとプランを読む
- 失敗を `logs/skabu.log` と `logs/debug/*.json` に残す
- プランのレート制限を守る（Light は 60 リクエスト/分）
- 取引カレンダー、上場銘柄、日足を SQLite に保存する
- 日足から週足を集計する
- 失敗した日付は飛ばして、次回は差分だけ取る
- チャート、ランキング、検証、日次のロックと採点、学習、シミュレーション
- カブコム kabuステーション API への発注。初期状態はペーパーで、`KABU_LIVE=true` のときだけ実発注

## 準備

1. Python 3.11 以上
2. [J-Quants](https://jpx-jquants.com/) で無料登録し、ダッシュボードから API キーを発行する
3. このリポジトリをクローンし、依存関係を入れる

```powershell
python -m pip install -e ".[dev]"
copy .env.example .env
```

`.env` の `JQUANTS_API_KEY=` に、自分のキーを貼ります。

Light プランは当日引け後の日足が取れます（60 リクエスト/分）。無料のままだと 12 週間遅れ、5 リクエスト/分です。`.env` の `JQUANTS_PLAN` を契約に合わせてください。

## 確認

```powershell
python -m skabu doctor
python -m pytest
```

## データ取り込み

```powershell
python -m skabu sync all
python -m skabu status
```

既定では直近 5 営業日の日足だけ取ります。契約期間の全部を取る場合:

```powershell
python -m skabu sync bars --full
```

無料プランの全期間は 5 リクエスト/分のため、1 時間以上かかることがあります。途中で止まっても、次回は取得済みの日付を飛ばします。取得した日足を再配布しないでください。

## チャートとランキング

```powershell
python -m skabu serve
```

ブラウザで http://127.0.0.1:8765/ を開きます。銘柄コードまたは社名で検索し、日足と週足を切り替えられます。画面はローカルの SQLite だけを読むので、表示のたびに J-Quants へは問い合わせません。

内蔵インジケータは SMA、EMA、RSI、MACD、ADX、Boost、ATR、ボリンジャー、ストキャス、出来高 SMA、Supertrend、一目均衡です。チャートとランキングの損切・目標は、予測日終値では仮の数字です。執行は翌営業日の始値と ATR で確定します。

http://127.0.0.1:8765/rank の初期選択は EMA と RSI です。プライムかつ出来高フィルタのあと、合計点で並べます。条件を満たさない銘柄は見送ります。

```powershell
python -m skabu rank --top 20
```

## 検証と日次

http://127.0.0.1:8765/verify では、ロックした予測を翌始値からの先着で採点します。損切は翌始値から 1.5×ATR、目標は 3.0×ATR、上限は 20 営業日です。同一日は損切を優先します。比べる指数は TOPIX ETF（1306）です。

16:30 以降（無料プランなら取得できる最新日）に日次を実行します。

```powershell
python -m skabu daily
python -m skabu verify --top 20
```

成功したときだけ `data/candidates.csv` を書き換えます。同期に失敗した日付があると候補は出しません。Windows のタスク スケジューラからは `scripts/daily.ps1` を引け後に動かします。

## 学習、戦略、シミュレーション

```powershell
python -m skabu train
python -m skabu adopt
python -m skabu sim --cash 1000000 --top 5
```

ラベルは検証と同じです。翌始値で入り、上限 20 営業日以内に目標へ損切より先に届いたかを当てます。未来の足は使いません。確認期間の超過がプラスでない戦略は本番に書きません。前の戦略が無いときは EMA と RSI にします。

http://127.0.0.1:8765/sim で資金と銘柄数を変えて再実行できます。発注画面は http://127.0.0.1:8765/trade です。実注文は `.env` の `KABU_LIVE=true` と kabuステーションの API パスワードが両方そろったときだけです。
