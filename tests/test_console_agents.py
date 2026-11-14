"""The console's agent view: progress from the real graph's stream, and cancel."""

import time

import pytest

from console import engine
from console.engine import Conflict, Console
from console.progress import Progress, split_turns
from console.store import Store
from tests import test_graph_end_to_end as e2e
from tradingagents.graph import trading_graph

pytestmark = pytest.mark.unit
offline = e2e.offline   # vendors answer offline, as in the end-to-end test


def test_debate_history_splits_into_turns():
    history = "\nBull Analyst: Margins are rising.\nStill rising.\nBear Analyst: Valuation is stretched."
    assert split_turns(history) == [
        {"speaker": "Bull", "text": "Margins are rising.\nStill rising."},
        {"speaker": "Bear", "text": "Valuation is stretched."},
    ]
    assert split_turns("") == []


def test_progress_marks_working_waiting_and_skipped_steps():
    p = Progress(["market", "news"])
    status = {s["key"]: s["status"] for s in p.snapshot()["steps"]}
    assert status["market"] == status["news"] == "working"
    assert status["social"] == status["fundamentals"] == "skipped"
    assert status["research_debate"] == "waiting"
    assert p.snapshot()["current"] == "Analysts"

    assert p.update({"market_report": "m"}) == ["market"]
    assert p.update({"market_report": "m"}) == []          # reported once
    p.update({"news_report": "n"})
    snap = p.snapshot()
    status = {s["key"]: s["status"] for s in snap["steps"]}
    assert status["research_debate"] == "working" and status["trader"] == "waiting"
    assert snap["current"] == "Bull vs Bear debate"
    assert "text" not in Progress.summary(snap)["steps"][0]


def _real_runner(monkeypatch):
    model = e2e.ScriptedModel()
    monkeypatch.setattr(trading_graph, "create_tier_client", lambda config, tier, **k: e2e._Client(model))


def test_a_real_run_streams_every_agent_into_the_view(monkeypatch, offline):
    _real_runner(monkeypatch)
    states = []
    out = engine.run_tradingagents("NVDA", e2e.TRADE_DATE, ["market", "news"], None, on_progress=states.append)
    assert out["rating"] == "Overweight"
    assert out["report_path"] and out["report_path"].endswith("complete_report.html")

    p = Progress(["market", "news"])
    seen_done = []
    for s in states:
        seen_done += p.update(s)
    # The analysts' reports arrive while the run is still going, before the decision.
    assert seen_done.index("market") < seen_done.index("portfolio_manager")
    snap = p.snapshot()
    status = {s["key"]: s["status"] for s in snap["steps"]}
    assert status == {"market": "done", "social": "skipped", "news": "done", "fundamentals": "skipped",
                      "research_debate": "done", "research_manager": "done", "trader": "done",
                      "risk_debate": "done", "portfolio_manager": "done"}
    debate = next(s for s in snap["steps"] if s["key"] == "research_debate")
    assert {t["speaker"] for t in debate["turns"]} == {"Bull", "Bear"}
    risk = next(s for s in snap["steps"] if s["key"] == "risk_debate")
    assert {t["speaker"] for t in risk["turns"]} == {"Aggressive", "Conservative", "Neutral"}


def test_raising_from_progress_stops_a_real_run(monkeypatch, offline):
    _real_runner(monkeypatch)

    def stop(state):
        raise engine.Cancelled()
    with pytest.raises(engine.Cancelled):
        engine.run_tradingagents("NVDA", e2e.TRADE_DATE, ["market"], None, on_progress=stop)


# ---------- engine: progress saved, cancel ----------

class Market:
    def last_price(self, symbol):
        return 100.0


def test_progress_is_saved_and_a_running_analysis_cancels():
    store = Store(":memory:")
    c = Console(store, market=Market(), runner=None)

    def runner(symbol, trade_date, analysts, held, on_progress=None):
        on_progress({"market_report": "Uptrend on rising volume."})
        saved = store.analysis(aid)["progress"]
        assert next(s for s in saved["steps"] if s["key"] == "market")["text"] == "Uptrend on rising volume."
        c.cancel(aid)                       # the person presses Cancel mid-run
        on_progress({"news_report": "n"})   # the next agent step stops the run
        raise AssertionError("the run should have stopped")
    c.runner = runner
    aid = store.create_analysis("TCS.NS", "2026-11-13", ["market", "news"])
    c._run_analysis(aid)
    a = store.analysis(aid)
    assert a["status"] == "cancelled" and store.proposals() == []
    assert next(s for s in a["progress"]["steps"] if s["key"] == "market")["status"] == "done"


def test_a_queued_analysis_cancels_before_it_starts():
    store = Store(":memory:")
    calls = []
    c = Console(store, market=Market(), runner=lambda *a, **k: calls.append(a))
    aid = store.create_analysis("TCS.NS", "2026-11-13", ["market"])
    assert c.cancel(aid)["status"] == "cancelled"
    c._run_analysis(aid)
    assert calls == []
    with pytest.raises(Conflict):
        c.cancel(aid)


def test_an_old_database_gains_the_progress_column(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE analyses (id INTEGER PRIMARY KEY, symbol TEXT NOT NULL, trade_date TEXT NOT NULL, "
               "analysts TEXT NOT NULL, status TEXT NOT NULL, rating TEXT, summary TEXT, report_path TEXT, "
               "error TEXT, stats TEXT, created REAL NOT NULL, started REAL, finished REAL)")
    db.execute("INSERT INTO analyses(symbol, trade_date, analysts, status, created) "
               "VALUES('INFY.NS', '2026-11-13', '[\"market\"]', 'done', ?)", (time.time(),))
    db.commit()
    db.close()
    store = Store(path)
    [old] = store.analyses()
    assert old["symbol"] == "INFY.NS" and old["progress"] is None
    store.update_analysis(old["id"], progress={"steps": [], "current": None})
    assert store.analysis(old["id"])["progress"] == {"steps": [], "current": None}


def test_api_serves_the_agent_view_and_cancels():
    from fastapi.testclient import TestClient

    from console.server import create_app
    store = Store(":memory:")
    c = Console(store, market=Market(), runner=lambda *a, **k: None)
    http = TestClient(create_app(c))
    aid = store.create_analysis("TCS.NS", "2026-11-13", ["market"])
    store.update_analysis(aid, progress=Progress(["market"]).snapshot() | {"steps": [
        {"key": "market", "label": "Market analyst", "status": "done", "text": "long report"}]})
    listed = http.get("/api/state").json()["analyses"][0]
    assert "text" not in listed["progress"]["steps"][0]           # the list stays light
    detail = http.get(f"/api/analyses/{aid}").json()
    assert detail["progress"]["steps"][0]["text"] == "long report"
    assert http.post(f"/api/analyses/{aid}/cancel").json()["status"] == "cancelled"
    assert http.post(f"/api/analyses/{aid}/cancel").status_code == 409
    assert http.get("/api/analyses/999").status_code == 404
