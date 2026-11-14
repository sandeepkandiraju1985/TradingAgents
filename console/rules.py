"""Pure decision rules: auto-exit, screening, order sizing and market hours.

No I/O here, so every rule is tested directly.
"""

from __future__ import annotations

import math
from datetime import datetime, time as dtime

import pytz

ANALYSTS = ("market", "social", "news", "fundamentals")
IST = pytz.timezone("Asia/Kolkata")
NSE_OPEN, NSE_CLOSE = dtime(9, 15), dtime(15, 30)

# Screening thresholds: a cheap price check that decides which stocks deserve a
# full (LLM-backed) analysis.
DAY_MOVE_PCT = 3.0
WEEK_MOVE_PCT = 5.0


def change_pct(base: float, price: float) -> float:
    return (price / base - 1.0) * 100.0


def should_exit(avg_price: float, price: float, stop_loss_pct: float) -> bool:
    """True once the price is more than ``stop_loss_pct`` below the average entry.

    "More than 5%" is the rule as set: a price exactly 5.0% down holds, anything
    lower closes the position.
    """
    if avg_price <= 0 or stop_loss_pct <= 0:
        return False
    # The tolerance absorbs float error: 95/100 computes as 5.0000000000004% down.
    return change_pct(avg_price, price) < -stop_loss_pct - 1e-9


def stop_price(avg_price: float, stop_loss_pct: float) -> float:
    return avg_price * (1.0 - stop_loss_pct / 100.0)


def size_order(budget: float, price: float) -> int:
    """Whole shares that fit in ``budget``; 0 when the budget buys none."""
    if price <= 0 or budget <= 0:
        return 0
    return int(math.floor(budget / price))


def proposal_side(rating: str | None, held_qty: float) -> str | None:
    """Map an agent rating onto an order to propose, given what is held.

    Buy/Overweight open a position when none is held. Sell/Underweight close a
    held position. Hold, REVIEW (unreadable decision) and anything else propose
    nothing: an unreadable decision needs a person, not a default trade.
    """
    r = (rating or "").strip().lower()
    if r in ("buy", "overweight") and held_qty <= 0:
        return "BUY"
    if r in ("sell", "underweight") and held_qty > 0:
        return "SELL"
    return None


def sma(values: list[float], n: int) -> list[float | None]:
    out: list[float | None] = []
    total = 0.0
    for i, v in enumerate(values):
        total += v
        if i >= n:
            total -= values[i - n]
        out.append(total / n if i >= n - 1 else None)
    return out


def screen(bars: list[dict]) -> list[str]:
    """Reasons a stock deserves a full analysis today, from its daily bars."""
    closes = [b["close"] for b in bars]
    if len(closes) < 2:
        return []
    flags = []
    day = change_pct(closes[-2], closes[-1])
    if abs(day) >= DAY_MOVE_PCT:
        flags.append(f"Moved {day:+.1f}% today")
    if len(closes) >= 6:
        week = change_pct(closes[-6], closes[-1])
        if abs(week) >= WEEK_MOVE_PCT:
            flags.append(f"Moved {week:+.1f}% in 5 days")
    avg20 = sma(closes, 20)
    if len(closes) >= 21 and avg20[-1] is not None and avg20[-2] is not None:
        was_above, is_above = closes[-2] > avg20[-2], closes[-1] > avg20[-1]
        if is_above and not was_above:
            flags.append("Crossed above its 20-day average")
        elif was_above and not is_above:
            flags.append("Fell below its 20-day average")
    return flags


def in_nse_hours(now: datetime) -> bool:
    """Monday to Friday, 9:15 to 15:30 India time. Exchange holidays are not
    known here; a scan on a holiday only re-reads the last close."""
    if now.tzinfo is None:
        now = pytz.utc.localize(now)
    local = now.astimezone(IST)
    return local.weekday() < 5 and NSE_OPEN <= local.time() <= NSE_CLOSE


def currency_for(symbol: str) -> str:
    s = symbol.upper()
    if s.endswith((".NS", ".BO")) or s in ("^NSEI", "^BSESN", "^NSEBANK"):
        return "INR"
    return "USD"
