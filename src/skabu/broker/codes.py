"""内部の5桁コードとカブコムの4桁コードを行き来する。"""

from __future__ import annotations

from skabu.store import normalize_code


def to_internal(code: str) -> str:
    return normalize_code(str(code or ""))


def to_kabu(code: str) -> str:
    text = str(code or "").strip()
    if text.isdigit() and len(text) == 5 and text.endswith("0"):
        return text[:-1]
    return text


def public_quote_code(code: str) -> str | None:
    """公開チャートサイトが使う4桁コード。作れないときは None。"""
    text = to_kabu(code)
    if text.isdigit() and len(text) == 4:
        return text
    return None


def public_chart_links(code: str) -> list[dict[str, str]]:
    """いまの銘柄を開く公開チャート。4桁にできないコードは空。"""
    quote = public_quote_code(code)
    if quote is None:
        return []
    return [
        {
            "id": "yahoo",
            "label": "Yahoo!ファイナンス",
            "url": f"https://finance.yahoo.co.jp/quote/{quote}.T/chart",
        },
        {
            "id": "kabutan",
            "label": "株探",
            "url": f"https://kabutan.jp/stock/chart?code={quote}",
        },
        {
            "id": "minkabu",
            "label": "みんかぶ",
            "url": f"https://minkabu.jp/stock/{quote}/chart",
        },
        {
            "id": "tradingview",
            "label": "TradingView",
            "url": f"https://jp.tradingview.com/chart/?symbol=TSE:{quote}",
        },
    ]
