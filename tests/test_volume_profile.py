"""Volume-profile pullback entries: trend, swing, POC, and the entry states."""

import math

import pytest

from console import volume_profile as vp

pytestmark = pytest.mark.unit


def daily(slope):
    """A zigzag with rising (slope > 0) or falling swings."""
    out = []
    for t in range(90):
        c = 100 + slope * t + 5 * math.sin(t / 3)
        out.append({"time": f"d{t:03d}", "open": c, "high": c + 1, "low": c - 1, "close": c, "volume": 1})
    return out


def bar(t, o, h, lo, c, v=100):
    return {"time": t, "open": o, "high": h, "low": lo, "close": c, "volume": v}


# Swing low at 100 (bar 3), rally to a swing high at 112 (bar 11) with most of the
# volume traded around 104, then a pullback toward it.
SWING = [
    bar(0, 102, 102.5, 101.5, 102), bar(1, 102, 102.4, 101.2, 101.5), bar(2, 101.5, 101.8, 100.8, 101),
    bar(3, 101, 101.3, 100, 100.8),
    bar(4, 100.8, 102, 100.6, 101.8), bar(5, 101.8, 103, 101.5, 102.8),
    bar(6, 102.8, 104.2, 102.6, 104, 1000), bar(7, 104, 104.4, 103.7, 104.1, 3000),
    bar(8, 104.1, 104.5, 103.8, 104.3, 2500),
    bar(9, 104.3, 106, 104.2, 105.8, 200), bar(10, 105.8, 109.5, 105.6, 109.3, 200),
    bar(11, 109.3, 112, 109, 111, 200),
    bar(12, 111, 111.5, 108, 108.5), bar(13, 108.5, 108.8, 106, 106.3), bar(14, 106.3, 106.5, 104.6, 104.9),
]


def test_daily_trend_reads_higher_highs_and_lows():
    assert vp.uptrend(daily(0.5))["up"] is True
    down = vp.uptrend(daily(-0.5))
    assert down["up"] is False and "downtrend" in down["reason"]


def test_the_poc_is_where_the_volume_traded():
    p = vp.profile(SWING[3:12])
    assert 103.7 <= p["poc"] <= 104.5
    assert len(p["bins"]) == vp.BINS
    assert sum(b["volume"] for b in p["bins"]) == pytest.approx(sum(b["volume"] for b in SWING[3:12]))


def test_no_trend_means_no_setup():
    assert vp.evaluate(daily(-0.5), SWING)["status"] == "no_trend"


def test_waiting_while_price_is_above_the_poc():
    r = vp.evaluate(daily(0.5), SWING)
    assert r["status"] == "waiting"
    assert r["swing_low"]["price"] == 100 and r["swing_high"]["price"] == 112
    assert r["stop"] == pytest.approx(99.9) and r["target"] == 112


def test_testing_when_price_reaches_the_poc_without_a_reaction():
    r = vp.evaluate(daily(0.5), SWING + [bar(15, 104.9, 105, 104.1, 104.2)])
    assert r["status"] == "testing"


def test_entry_on_a_rejection_wick_at_the_poc():
    r = vp.evaluate(daily(0.5), SWING + [bar(15, 104.6, 104.9, 104.0, 104.6)])
    assert r["status"] == "entry" and r["fresh"] is True
    assert r["entry"] == 104.6
    assert r["reward_risk"] == pytest.approx((112 - 104.6) / (104.6 - 99.9))
    assert r["reward_risk"] >= vp.MIN_REWARD_RISK


def test_an_old_signal_is_not_fresh():
    later = [bar(16, 104.8, 106, 104.7, 105.9), bar(17, 105.9, 107, 105.8, 106.8)]
    r = vp.evaluate(daily(0.5), SWING + [bar(15, 104.6, 104.9, 104.0, 104.6)] + later)
    assert r["status"] == "entry" and r["fresh"] is False


def test_a_close_below_the_swing_low_invalidates_the_setup():
    r = vp.evaluate(daily(0.5), SWING + [bar(15, 104.9, 105, 99, 99.5)])
    assert r["status"] == "invalid"


# ---------- in the console: scan -> proposal -> approval -> exit ----------

from console import rules  # noqa: E402
from console.engine import Console  # noqa: E402
from console.store import Store  # noqa: E402

ENTRY_BARS = SWING + [bar(15, 104.6, 104.9, 104.0, 104.6)]


class SetupMarket:
    def __init__(self, price=104.6):
        self.price = price

    def bars(self, symbol, period="2y", interval="1d"):
        return ENTRY_BARS if interval == "30m" else daily(0.5)

    def last_price(self, symbol):
        return self.price


def console_with(market):
    store = Store(":memory:")
    return store, Console(store, market=market, runner=lambda *a, **k: None)


def test_a_scan_proposes_a_fresh_entry_once():
    store, c = console_with(SetupMarket())
    out = c.scan(["TCS.NS"])
    [row] = out["results"]
    assert row["setup"]["status"] == "entry" and row["setup"]["proposal_id"]
    [p] = store.proposals()
    assert (p["side"], p["source"], p["price"]) == ("BUY", "volume_profile", 104.6)
    assert p["stop"] == pytest.approx(99.9) and p["target"] == 112
    assert p["qty"] == rules.size_order(50000, 104.6)
    c.scan(["TCS.NS"])                      # the same signal is not proposed twice
    assert len(store.proposals()) == 1


def test_no_proposal_while_the_stock_is_held():
    store, c = console_with(SetupMarket())
    store.add_order("TCS.NS", "BUY", 5, 100.0, "INR", "dry-run", "Approved by you")
    c.scan(["TCS.NS"])
    assert store.proposals() == []


def test_the_setup_stop_exits_before_the_five_percent_limit():
    market = SetupMarket()
    store, c = console_with(market)
    c.scan(["TCS.NS"])
    [p] = store.proposals()
    order = c.approve(p["id"])
    assert order["stop"] == pytest.approx(99.9)
    [pos] = store.book()["open"]
    assert pos["stop"] == pytest.approx(99.9)
    # 5% below 104.6 is 99.37; the setup stop at 99.90 comes first.
    assert rules.exit_price(pos["avg_price"], 5.0, pos["stop"]) == pytest.approx(99.9)
    market.price = 100.5                    # above both: holds
    assert c.check_exits() == []
    market.price = 99.8                     # below the setup stop, only 4.6% down
    [sell] = c.check_exits()
    assert "setup stop" in sell["reason"]
    assert store.held_qty("TCS.NS") == 0


def test_exit_price_takes_whichever_comes_first():
    assert rules.exit_price(100.0, 5.0, None) == 95.0
    assert rules.exit_price(100.0, 5.0, 97.0) == 97.0     # setup stop is closer
    assert rules.exit_price(100.0, 5.0, 90.0) == 95.0     # the 5% limit is closer
