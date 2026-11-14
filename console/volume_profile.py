"""Volume-profile pullback entries, as rules.

The method, from a chart tutorial:
1. The daily chart is in an uptrend: higher highs and higher lows.
2. On the 30-minute chart, take the latest swing low and the swing high after it.
3. Build a fixed-range volume profile over that move; its Point of Control (POC)
   is the price band where the most volume traded.
4. Wait for price to pull back to the POC.
5. Enter when a candle tests the POC and reacts bullishly: it dips into the POC
   zone and closes back above it with a long lower wick, or closes above the
   previous candle's high.

The tutorial gives no stop or target. Here the stop sits just below the swing
low, where the setup is proven wrong, and the first target is the swing high.

Volume is spread evenly across each bar's high-low range, the usual way to build
a profile from bars when trade-by-trade data isn't available, so the POC is an
approximation of what charting tools compute from finer data.
"""

from __future__ import annotations

PIVOT_SPAN = 3          # a swing high is higher than the 3 bars on each side
BINS = 40               # price bands in the profile
POC_TOLERANCE = 0.003   # the POC zone: POC +/- 0.3%
WICK_SHARE = 0.5        # a rejection wick is at least half the candle's range
STOP_BUFFER = 0.001     # stop 0.1% below the swing low
MIN_REWARD_RISK = 1.5   # proposals need at least 1.5x reward for the risk
FRESH_BARS = 2          # a signal older than 2 candles (1 hour) is not proposed


def swing_points(bars: list[dict], span: int = PIVOT_SPAN) -> tuple[list[int], list[int]]:
    """Indexes of confirmed swing highs and swing lows."""
    highs, lows = [], []
    for i in range(span, len(bars) - span):
        window = bars[i - span:i + span + 1]
        if bars[i]["high"] >= max(b["high"] for b in window) and \
                bars[i]["high"] > max(b["high"] for b in window[:span]):
            highs.append(i)
        if bars[i]["low"] <= min(b["low"] for b in window) and \
                bars[i]["low"] < min(b["low"] for b in window[:span]):
            lows.append(i)
    return highs, lows


def uptrend(daily: list[dict], span: int = PIVOT_SPAN) -> dict:
    """Higher highs and higher lows on the last two swings of each kind."""
    highs, lows = swing_points(daily, span)
    if len(highs) < 2 or len(lows) < 2:
        return {"up": False, "reason": "Not enough daily swings to read the trend."}
    h1, h2 = daily[highs[-2]]["high"], daily[highs[-1]]["high"]
    l1, l2 = daily[lows[-2]]["low"], daily[lows[-1]]["low"]
    up = h2 > h1 and l2 > l1
    if up:
        reason = "Daily uptrend: higher highs and higher lows."
    elif h2 <= h1 and l2 <= l1:
        reason = "Daily downtrend: lower highs and lower lows."
    else:
        reason = "No clear daily trend."
    return {"up": up, "reason": reason,
            "highs": [daily[highs[-2]]["time"], daily[highs[-1]]["time"]],
            "lows": [daily[lows[-2]]["time"], daily[lows[-1]]["time"]]}


def profile(bars: list[dict], bins: int = BINS) -> dict:
    """Volume by price band across ``bars``; the POC is the fullest band."""
    lo = min(b["low"] for b in bars)
    hi = max(b["high"] for b in bars)
    if hi <= lo:
        return {"bins": [{"low": lo, "high": hi, "volume": sum(b["volume"] for b in bars)}], "poc": lo}
    step = (hi - lo) / bins
    vols = [0.0] * bins
    for b in bars:
        span = b["high"] - b["low"]
        first = min(bins - 1, int((b["low"] - lo) / step))
        last = min(bins - 1, int((b["high"] - lo) / step))
        if span <= 0 or first == last:
            vols[first] += b["volume"]
            continue
        for k in range(first, last + 1):
            band_lo, band_hi = lo + k * step, lo + (k + 1) * step
            overlap = min(band_hi, b["high"]) - max(band_lo, b["low"])
            if overlap > 0:
                vols[k] += b["volume"] * overlap / span
    top = max(range(bins), key=lambda k: vols[k])
    return {"bins": [{"low": lo + k * step, "high": lo + (k + 1) * step, "volume": vols[k]} for k in range(bins)],
            "poc": lo + (top + 0.5) * step}


def _bullish_reaction(bar: dict, prev: dict, poc: float, zone_top: float) -> bool:
    rng = bar["high"] - bar["low"]
    if bar["low"] > zone_top or bar["close"] <= poc or rng <= 0:
        return False
    lower_wick = min(bar["open"], bar["close"]) - bar["low"]
    return lower_wick >= WICK_SHARE * rng or bar["close"] > prev["high"]


def evaluate(daily: list[dict], intraday: list[dict]) -> dict:
    """The setup's state for one stock: no_trend, no_swing, waiting, testing,
    entry or invalid, with the levels behind it."""
    trend = uptrend(daily)
    out: dict = {"trend": trend}
    if not trend["up"]:
        return {**out, "status": "no_trend", "message": trend["reason"]}
    highs, lows = swing_points(intraday)
    swing_high = highs[-1] if highs else None
    swing_low = max((i for i in lows if swing_high is not None and i < swing_high), default=None)
    if swing_high is None or swing_low is None:
        return {**out, "status": "no_swing", "message": "No completed 30-minute swing (low, then high) yet."}

    lo_bar, hi_bar = intraday[swing_low], intraday[swing_high]
    prof = profile(intraday[swing_low:swing_high + 1])
    poc = prof["poc"]
    zone = (poc * (1 - POC_TOLERANCE), poc * (1 + POC_TOLERANCE))
    stop = lo_bar["low"] * (1 - STOP_BUFFER)
    target = hi_bar["high"]
    out.update({"swing_low": {"time": lo_bar["time"], "price": lo_bar["low"]},
                "swing_high": {"time": hi_bar["time"], "price": target},
                "poc": poc, "zone": list(zone), "stop": stop, "target": target, "profile": prof["bins"]})

    last = intraday[-1]["close"]
    for i in range(swing_high + 1, len(intraday)):
        bar = intraday[i]
        if bar["close"] < lo_bar["low"]:
            return {**out, "status": "invalid", "message": "Closed below the swing low: the setup failed."}
        if _bullish_reaction(bar, intraday[i - 1], poc, zone[1]):
            entry = bar["close"]
            risk = entry - stop
            rr = (target - entry) / risk if risk > 0 else None
            fresh = i >= len(intraday) - FRESH_BARS
            return {**out, "status": "entry", "entry": entry, "reward_risk": rr, "signal_time": bar["time"],
                    "fresh": fresh,
                    "message": (f"Entry signal: tested the POC and closed at {entry:,.2f}"
                                + ("" if fresh else " (earlier; not proposed now)") + ".")}
    distance = (last / poc - 1) * 100
    if intraday[-1]["low"] <= zone[1]:
        return {**out, "status": "testing", "distance_pct": distance,
                "message": "Testing the POC; waiting for a bullish candle."}
    return {**out, "status": "waiting", "distance_pct": distance,
            "message": f"Waiting for a pullback to the POC, {distance:.1f}% below the last price."}
