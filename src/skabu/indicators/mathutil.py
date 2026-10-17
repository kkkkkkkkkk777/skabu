"""インジケータ共通の時系列計算。未来足は見ない。"""

from __future__ import annotations

import math


def sma(values: list[float | None], length: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if length < 1:
        return out
    for i in range(len(values)):
        start = i - length + 1
        if start < 0:
            continue
        window = values[start : i + 1]
        if any(item is None for item in window):
            continue
        out[i] = sum(window) / length  # type: ignore[arg-type]
    return out


def ema(values: list[float | None], length: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if length < 1:
        return out
    alpha = 2.0 / (length + 1)
    prev: float | None = None
    seed = 0.0
    count = 0
    for i, value in enumerate(values):
        if value is None:
            continue
        if prev is None:
            seed += value
            count += 1
            if count == length:
                prev = seed / length
                out[i] = prev
            continue
        prev = alpha * value + (1 - alpha) * prev
        out[i] = prev
    return out


def rma(values: list[float | None], length: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if length < 1:
        return out
    prev: float | None = None
    seed = 0.0
    count = 0
    for i, value in enumerate(values):
        if value is None:
            continue
        if prev is None:
            seed += value
            count += 1
            if count == length:
                prev = seed / length
                out[i] = prev
            continue
        prev = (prev * (length - 1) + value) / length
        out[i] = prev
    return out


def rsi(closes: list[float | None], length: int = 14) -> list[float | None]:
    gains: list[float | None] = [None] * len(closes)
    losses: list[float | None] = [None] * len(closes)
    prev: float | None = None
    for i, close in enumerate(closes):
        if close is None or prev is None:
            prev = close
            continue
        delta = close - prev
        gains[i] = max(delta, 0.0)
        losses[i] = max(-delta, 0.0)
        prev = close
    avg_gain = rma(gains, length)
    avg_loss = rma(losses, length)
    out: list[float | None] = [None] * len(closes)
    for i, (gain, loss) in enumerate(zip(avg_gain, avg_loss, strict=True)):
        if gain is None or loss is None:
            continue
        if loss == 0:
            out[i] = 100.0
        else:
            rs = gain / loss
            out[i] = 100.0 - (100.0 / (1.0 + rs))
    return out


def true_range(highs: list[float | None], lows: list[float | None], closes: list[float | None]) -> list[float | None]:
    out: list[float | None] = [None] * len(closes)
    prev_close: float | None = None
    for i, (high, low, close) in enumerate(zip(highs, lows, closes, strict=True)):
        if high is None or low is None:
            prev_close = close
            continue
        parts = [high - low]
        if prev_close is not None:
            parts.append(abs(high - prev_close))
            parts.append(abs(low - prev_close))
        out[i] = max(parts)
        prev_close = close
    return out


def macd(
    closes: list[float | None],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    fast_line = ema(closes, fast)
    slow_line = ema(closes, slow)
    macd_line: list[float | None] = [None] * len(closes)
    for i, (left, right) in enumerate(zip(fast_line, slow_line, strict=True)):
        if left is None or right is None:
            continue
        macd_line[i] = left - right
    signal_line = ema(macd_line, signal)
    hist: list[float | None] = [None] * len(closes)
    for i, (line, sig) in enumerate(zip(macd_line, signal_line, strict=True)):
        if line is None or sig is None:
            continue
        hist[i] = line - sig
    return macd_line, signal_line, hist


def adx(
    highs: list[float | None],
    lows: list[float | None],
    closes: list[float | None],
    length: int = 14,
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    size = len(closes)
    plus_dm: list[float | None] = [None] * size
    minus_dm: list[float | None] = [None] * size
    for i in range(1, size):
        high = highs[i]
        low = lows[i]
        prev_high = highs[i - 1]
        prev_low = lows[i - 1]
        if None in (high, low, prev_high, prev_low):
            continue
        up = high - prev_high
        down = prev_low - low
        plus_dm[i] = up if up > down and up > 0 else 0.0
        minus_dm[i] = down if down > up and down > 0 else 0.0
    tr = true_range(highs, lows, closes)
    atr_vals = rma(tr, length)
    plus_smooth = rma(plus_dm, length)
    minus_smooth = rma(minus_dm, length)
    plus_di: list[float | None] = [None] * size
    minus_di: list[float | None] = [None] * size
    dx: list[float | None] = [None] * size
    for i, (plus, minus, width) in enumerate(zip(plus_smooth, minus_smooth, atr_vals, strict=True)):
        if plus is None or minus is None or width is None or width == 0:
            continue
        pdi = 100.0 * plus / width
        mdi = 100.0 * minus / width
        plus_di[i] = pdi
        minus_di[i] = mdi
        denom = pdi + mdi
        if denom == 0:
            continue
        dx[i] = 100.0 * abs(pdi - mdi) / denom
    return plus_di, minus_di, rma(dx, length)


def atr(
    highs: list[float | None],
    lows: list[float | None],
    closes: list[float | None],
    length: int = 21,
) -> list[float | None]:
    return rma(true_range(highs, lows, closes), length)


def super_smoother(values: list[float | None], period: int) -> list[float | None]:
    """Ehlers 2-pole Super Smoother。因果フィルター。"""
    out: list[float | None] = [None] * len(values)
    if period < 2:
        return list(values)
    a1 = math.exp(-math.sqrt(2.0) * math.pi / period)
    b1 = 2.0 * a1 * math.cos(math.sqrt(2.0) * math.pi / period)
    c2 = b1
    c3 = -(a1 * a1)
    c1 = 1.0 - c2 - c3
    prev1: float | None = None
    prev2: float | None = None
    prev_src: float | None = None
    for i, value in enumerate(values):
        if value is None:
            continue
        if prev1 is None:
            out[i] = value
            prev2 = value
            prev1 = value
            prev_src = value
            continue
        src_prev = prev_src if prev_src is not None else value
        current = c1 * (value + src_prev) / 2.0 + c2 * prev1 + c3 * (prev2 if prev2 is not None else prev1)
        out[i] = current
        prev2 = prev1
        prev1 = current
        prev_src = value
    return out






def stdev(values: list[float | None], length: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if length < 2:
        return out
    for i in range(len(values)):
        start = i - length + 1
        if start < 0:
            continue
        window = values[start : i + 1]
        if any(item is None for item in window):
            continue
        mean = sum(window) / length  # type: ignore[arg-type]
        var = sum((float(item) - mean) ** 2 for item in window) / (length - 1)
        out[i] = math.sqrt(var)
    return out


def bollinger(
    values: list[float | None],
    length: int = 20,
    mult: float = 2.0,
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    mid = sma(values, length)
    width = stdev(values, length)
    upper: list[float | None] = [None] * len(values)
    lower: list[float | None] = [None] * len(values)
    for i, (center, sigma) in enumerate(zip(mid, width, strict=True)):
        if center is None or sigma is None:
            continue
        upper[i] = center + mult * sigma
        lower[i] = center - mult * sigma
    return mid, upper, lower


def donchian_mid(
    highs: list[float | None],
    lows: list[float | None],
    length: int,
) -> list[float | None]:
    out: list[float | None] = [None] * len(highs)
    if length < 1:
        return out
    for i in range(len(highs)):
        start = i - length + 1
        if start < 0:
            continue
        hi = highs[start : i + 1]
        lo = lows[start : i + 1]
        if any(item is None for item in hi) or any(item is None for item in lo):
            continue
        out[i] = (max(hi) + min(lo)) / 2  # type: ignore[arg-type]
    return out


def stochastic(
    highs: list[float | None],
    lows: list[float | None],
    closes: list[float | None],
    k_len: int = 14,
    k_smooth: int = 3,
    d_len: int = 3,
) -> tuple[list[float | None], list[float | None]]:
    raw: list[float | None] = [None] * len(closes)
    if k_len < 1:
        return raw, list(raw)
    for i in range(len(closes)):
        start = i - k_len + 1
        if start < 0 or closes[i] is None:
            continue
        hi = highs[start : i + 1]
        lo = lows[start : i + 1]
        if any(item is None for item in hi) or any(item is None for item in lo):
            continue
        highest = max(hi)  # type: ignore[arg-type]
        lowest = min(lo)  # type: ignore[arg-type]
        if highest == lowest:
            raw[i] = 50.0
        else:
            raw[i] = 100.0 * (float(closes[i]) - lowest) / (highest - lowest)
    k_line = sma(raw, max(1, k_smooth))
    d_line = sma(k_line, max(1, d_len))
    return k_line, d_line


def supertrend(
    highs: list[float | None],
    lows: list[float | None],
    closes: list[float | None],
    length: int = 10,
    mult: float = 3.0,
) -> tuple[list[float | None], list[int | None]]:
    size = len(closes)
    atr_vals = atr(highs, lows, closes, length)
    line: list[float | None] = [None] * size
    trend: list[int | None] = [None] * size
    up: float | None = None
    down: float | None = None
    direction = 1
    for i, (high, low, close, width) in enumerate(zip(highs, lows, closes, atr_vals, strict=True)):
        if high is None or low is None or close is None or width is None:
            continue
        mid = (high + low) / 2.0
        basic_up = mid - mult * width
        basic_down = mid + mult * width
        if up is None or down is None:
            up = basic_up
            down = basic_down
            direction = 1 if close >= mid else -1
        else:
            up = max(basic_up, up) if closes[i - 1] is not None and float(closes[i - 1]) > up else basic_up
            down = min(basic_down, down) if closes[i - 1] is not None and float(closes[i - 1]) < down else basic_down
            if direction == 1 and close < up:
                direction = -1
            elif direction == -1 and close > down:
                direction = 1
        line[i] = up if direction == 1 else down
        trend[i] = direction
    return line, trend


def ichimoku(
    highs: list[float | None],
    lows: list[float | None],
    closes: list[float | None],
    tenkan_len: int = 9,
    kijun_len: int = 26,
    senkou_b_len: int = 52,
    displacement: int = 26,
) -> dict[str, list[float | None]]:
    tenkan = donchian_mid(highs, lows, tenkan_len)
    kijun = donchian_mid(highs, lows, kijun_len)
    span_b_raw = donchian_mid(highs, lows, senkou_b_len)
    size = len(closes)
    span_a_now: list[float | None] = [None] * size
    span_b_now: list[float | None] = [None] * size
    span_a_lead: list[float | None] = [None] * size
    span_b_lead: list[float | None] = [None] * size
    chikou: list[float | None] = [None] * size
    for i in range(size):
        if tenkan[i] is not None and kijun[i] is not None:
            span_a_raw = (float(tenkan[i]) + float(kijun[i])) / 2.0
            lead = i + displacement
            if lead < size:
                span_a_lead[lead] = span_a_raw
        if span_b_raw[i] is not None:
            lead = i + displacement
            if lead < size:
                span_b_lead[lead] = span_b_raw[i]
        past = i - displacement
        if past >= 0 and tenkan[past] is not None and kijun[past] is not None:
            span_a_now[i] = (float(tenkan[past]) + float(kijun[past])) / 2.0
        if past >= 0:
            span_b_now[i] = span_b_raw[past]
        past = i - displacement
        if past >= 0:
            chikou[past] = closes[i]
    return {
        "tenkan": tenkan,
        "kijun": kijun,
        "span_a": span_a_now,
        "span_b": span_b_now,
        "span_a_lead": span_a_lead,
        "span_b_lead": span_b_lead,
        "chikou": chikou,
        "span_a_raw": [
            (float(tenkan[i]) + float(kijun[i])) / 2.0 if tenkan[i] is not None and kijun[i] is not None else None
            for i in range(size)
        ],
        "span_b_raw": span_b_raw,
    }
