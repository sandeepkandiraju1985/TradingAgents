"""The console: auto-exit rule, approvals, dry-run guarantee, screening, API."""

from datetime import datetime

import pytest
import pytz

from console import rules
from console.broker import DryRunBroker, INDmoneyBroker, LiveTradingDisabled, get_broker
from console.engine import Conflict, Console
from console.market import MarketDataError
from console.store import Store

pytestmark = pytest.mark.unit


class FakeMarket:
    def __init__(self, prices=None):
        self.prices = dict(prices or {})

    def last_price(self, symbol):
        if symbol not in self.prices:
            raise MarketDataError(f"no price for {symbol}")
        return self.prices[symbol]

    def bars(self, symbol, period="2y", interval="1d"):
        p = self.last_price(symbol)
        return [{"time": f"2026-11-{d:02d}", "open": p, "high": p, "low": p, "close": p, "volume": 1}
                for d in range(1, 13)]

    def closes(self, symbols, period="5y"):
        return {"dates": ["2026-11-12", "2026-11-13"],
                "series": {s: [100.0, 110.0] for s in symbols}, "missing": []}

    def strip(self):
        return []


def fake_runner(rating, summary="Rating: x\nStrong quarter, rising estimates."):
    def run(symbol, trade_date, analysts, held, on_progress=None):
        run.calls.append((symbol, trade_date, tuple(analysts), held))
        return {"rating": rating, "summary": summary, "report_path": None,
                "stats": {"llm_calls": 3, "seconds": 1.0}}
    run.calls = []
    return run


@pytest.fixture
def store():
    return Store(":memory:")


def make_console(store, prices, rating="Buy"):
    return Console(store, market=FakeMarket(prices), runner=fake_runner(rating))


# ---------- rules ----------

def test_exit_fires_only_past_the_limit():
    assert not rules.should_exit(100.0, 96.0, 5.0)     # down 4%
    assert not rules.should_exit(100.0, 95.0, 5.0)     # down exactly 5%: holds
    assert rules.should_exit(100.0, 94.99, 5.0)        # down just over 5%
    assert not rules.should_exit(100.0, 120.0, 5.0)    # a gain never exits


def test_proposal_side_follows_rating_and_holdings():
    assert rules.proposal_side("Buy", 0) == "BUY"
    assert rules.proposal_side("Overweight", 0) == "BUY"
    assert rules.proposal_side("Buy", 10) is None          # already held
    assert rules.proposal_side("Sell", 10) == "SELL"
    assert rules.proposal_side("Underweight", 10) == "SELL"
    assert rules.proposal_side("Sell", 0) is None          # nothing to sell
    assert rules.proposal_side("Hold", 10) is None
    assert rules.proposal_side("REVIEW", 0) is None        # unreadable decision trades nothing


def test_size_order_uses_whole_shares():
    assert rules.size_order(50000, 1460.0) == 34
    assert rules.size_order(100, 1460.0) == 0


def test_screen_flags_big_moves_and_sma_crosses():
    flat = [{"close": 100.0}] * 25
    assert rules.screen(flat) == []
    jump = flat[:-1] + [{"close": 104.0}]
    flags = rules.screen(jump)
    assert any("today" in f for f in flags)
    assert any("Crossed above" in f for f in flags)


def test_nse_hours_in_india_time():
    ist = pytz.timezone("Asia/Kolkata")
    assert rules.in_nse_hours(ist.localize(datetime(2026, 11, 13, 10, 0)))       # Friday morning
    assert not rules.in_nse_hours(ist.localize(datetime(2026, 11, 13, 16, 0)))   # after close
    assert not rules.in_nse_hours(ist.localize(datetime(2026, 11, 14, 10, 0)))   # Saturday


# ---------- dry-run guarantee ----------

def test_only_the_dry_run_broker_places_orders():
    assert isinstance(get_broker({"live_trading": False}), DryRunBroker)
    assert get_broker({})  # default is dry-run
    with pytest.raises(LiveTradingDisabled):
        INDmoneyBroker().place({"symbol": "INFY.NS", "side": "BUY", "qty": 1, "price": 1})
    with pytest.raises(LiveTradingDisabled):
        get_broker({"live_trading": True}).place({})


# ---------- analysis -> proposal -> approval ----------

def test_buy_rating_creates_a_proposal_not_an_order(store):
    c = make_console(store, {"INFY.NS": 1500.0}, rating="Buy")
    aid = store.create_analysis("INFY.NS", "2026-11-13", ["market"])
    c._run_analysis(aid)
    a = store.analysis(aid)
    assert a["status"] == "done" and a["rating"] == "Buy"
    [p] = store.proposals()
    assert (p["side"], p["qty"], p["status"]) == ("BUY", 33, "pending")   # 50,000 / 1,500
    assert p["note"] == "Strong quarter, rising estimates."              # rating label skipped
    assert store.orders() == []


def test_approve_records_a_dry_run_order_once(store):
    c = make_console(store, {"INFY.NS": 1500.0})
    pid = store.create_proposal(None, "INFY.NS", "BUY", 10, 1500.0, "INR", "Buy", "note")
    order = c.approve(pid)
    assert order["mode"] == "dry-run" and order["side"] == "BUY"
    with pytest.raises(Conflict):
        c.approve(pid)
    assert len(store.orders()) == 1
    assert store.held_qty("INFY.NS") == 10


def test_reject_records_nothing(store):
    c = make_console(store, {"INFY.NS": 1500.0})
    pid = store.create_proposal(None, "INFY.NS", "BUY", 10, 1500.0, "INR", "Buy", "note")
    c.reject(pid)
    assert store.orders() == []
    assert store.proposal(pid)["status"] == "rejected"


def test_hold_rating_proposes_nothing(store):
    c = make_console(store, {"TCS.NS": 3800.0}, rating="Hold")
    aid = store.create_analysis("TCS.NS", "2026-11-13", ["market"])
    c._run_analysis(aid)
    assert store.proposals() == []


def test_failed_run_is_recorded_on_the_analysis(store):
    def boom(*a, **k):
        raise TimeoutError("model did not answer")
    c = Console(store, market=FakeMarket({"TCS.NS": 1.0}), runner=boom)
    aid = store.create_analysis("TCS.NS", "2026-11-13", ["market"])
    c._run_analysis(aid)
    a = store.analysis(aid)
    assert a["status"] == "failed" and "model did not answer" in a["error"]


def test_interrupted_runs_are_marked_failed_on_start(store):
    aid = store.create_analysis("TCS.NS", "2026-11-13", ["market"])
    Console(store, market=FakeMarket())
    assert store.analysis(aid)["status"] == "failed"


# ---------- auto-exit ----------

def _hold(store, symbol, qty, price):
    store.add_order(symbol, "BUY", qty, price, "INR", "dry-run", "Approved by you")


def test_auto_exit_sells_the_whole_position_past_five_percent(store):
    _hold(store, "INFY.NS", 20, 1500.0)
    market = FakeMarket({"INFY.NS": 1420.0})   # down 5.3%
    c = Console(store, market=market, runner=fake_runner("Buy"))
    [order] = c.check_exits()
    assert (order["side"], order["qty"], order["mode"]) == ("SELL", 20, "dry-run")
    assert "Auto-exit" in order["reason"]
    assert store.held_qty("INFY.NS") == 0
    assert c.check_exits() == []               # never sells twice
    [trade] = store.book()["closed"]
    assert trade["return_pct"] == pytest.approx(-5.333, abs=0.01)
    assert store.events(kind="auto_exit")


def test_auto_exit_leaves_small_losses_alone(store):
    _hold(store, "INFY.NS", 20, 1500.0)
    c = Console(store, market=FakeMarket({"INFY.NS": 1440.0}), runner=fake_runner("Buy"))   # down 4%
    assert c.check_exits() == []
    assert store.held_qty("INFY.NS") == 20


def test_auto_exit_uses_the_stop_setting(store):
    _hold(store, "INFY.NS", 20, 1500.0)
    store.set_setting("stop_loss_pct", 3.0)
    c = Console(store, market=FakeMarket({"INFY.NS": 1440.0}), runner=fake_runner("Buy"))   # down 4%
    assert len(c.check_exits()) == 1


def test_scan_runs_the_exit_check_and_survives_bad_symbols(store):
    _hold(store, "INFY.NS", 20, 1500.0)
    c = Console(store, market=FakeMarket({"INFY.NS": 1400.0}), runner=fake_runner("Buy"))
    out = c.scan(["INFY.NS", "NOPE.NS"])
    assert out["summary"]["exits"] == 1
    assert any("error" in r for r in out["results"])


def test_monitor_scans_hourly_only_in_market_hours(store):
    store.set_setting("hourly_scan", True)
    c = Console(store, market=FakeMarket(dict.fromkeys(Console(store, market=FakeMarket()).all_symbols(), 100.0)),
                runner=fake_runner("Buy"))
    ist = pytz.timezone("Asia/Kolkata")
    c.monitor_once(ist.localize(datetime(2026, 11, 14, 11, 0)))   # Saturday
    assert "last_scan" not in store.settings()
    c.monitor_once(ist.localize(datetime(2026, 11, 13, 11, 0)))   # Friday
    assert store.settings()["last_scan"]["count"] > 0


# ---------- HTTP API ----------

@pytest.fixture
def client(store):
    from fastapi.testclient import TestClient

    from console.server import create_app
    c = make_console(store, {"INFY.NS": 1500.0})
    return TestClient(create_app(c)), c


def test_api_state_and_settings(client):
    http, _ = client
    body = http.get("/api/state").json()
    assert body["settings"]["stop_loss_pct"] == 5.0
    assert len(body["watchlists"]) == 2
    assert http.patch("/api/settings", json={"live_trading": True}).status_code == 403
    assert http.patch("/api/settings", json={"stop_loss_pct": 4}).json()["stop_loss_pct"] == 4


def test_api_rejects_bad_symbols(client):
    http, _ = client
    assert http.get("/api/bars/..%2Fsecrets").status_code in (400, 404)
    assert http.post("/api/analyses", json={"symbol": "rm -rf /"}).status_code == 400


def test_api_approve_flow(client, store):
    http, _ = client
    pid = store.create_proposal(None, "INFY.NS", "BUY", 5, 1500.0, "INR", "Buy", "note")
    r = http.post(f"/api/proposals/{pid}/approve")
    assert r.status_code == 200 and r.json()["mode"] == "dry-run"
    assert http.post(f"/api/proposals/{pid}/approve").status_code == 409
    assert http.get("/api/state").json()["positions"][0]["qty"] == 5


def test_api_performance_returns_aligned_series(client, store):
    http, _ = client
    wid = store.watchlists()[0]["id"]
    body = http.get(f"/api/watchlists/{wid}/performance").json()
    assert len(body["dates"]) == len(body["benchmark"])
    assert all(len(v) == len(body["dates"]) for v in body["series"].values())
