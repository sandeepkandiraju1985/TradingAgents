"""The Claude Code harness: run folders, data tools, and recording into the console."""

import json
from datetime import date, timedelta

import pytest

from claude_harness import run, tools
from claude_harness.__main__ import main
from console.engine import Conflict, Console
from console.store import Store
from tests import test_graph_end_to_end as e2e, test_volume_profile as vpt

pytestmark = pytest.mark.unit
offline = e2e.offline


@pytest.fixture
def places(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_CLAUDE_RUNS", str(tmp_path / "runs"))
    monkeypatch.setenv("TRADINGAGENTS_CONSOLE_DB", str(tmp_path / "console.db"))
    return tmp_path


def test_start_opens_a_run_the_console_shows_as_running(places, capsys):
    main(["start", "tcs.ns", "--date", "2026-11-13", "--analysts", "market,sentiment"])
    out = json.loads(capsys.readouterr().out)
    assert out["ticker"] == "TCS.NS" and out["analysts"] == ["market", "social"]
    assert out["files_in_order"][:2] == ["market.md", "sentiment.md"]
    assert out["files_in_order"][-1] == "decision.md"
    a = Store(str(places / "console.db")).analysis(out["analysis_id"])
    assert a["status"] == "running" and a["stats"]["provider"] == "claude-code"


def test_start_refuses_bad_input(places):
    with pytest.raises(SystemExit):
        run.start("rm -rf /", None, "market")
    with pytest.raises(SystemExit):
        run.start("TCS.NS", None, "astrology")


def test_latest_weekday_skips_the_weekend():
    assert run.latest_weekday(date(2026, 11, 14)) == "2026-11-13"   # Saturday -> Friday
    assert run.latest_weekday(date(2026, 11, 12)) == "2026-11-12"


def test_data_tools_serve_the_framework_data_as_of_the_run_date(places, offline):
    info = run.start("NVDA", e2e.TRADE_DATE, "market,news")
    rd = info["run_dir"]
    assert "get_stock_data data" in tools.call(rd, "prices", [])
    assert "get_news data" in tools.call(rd, "news", [])
    assert "rsi" in tools.call(rd, "indicators", ["rsi"]) or "get_indicators" in tools.call(rd, "indicators", ["rsi"])
    assert "Unknown tool" in tools.call(rd, "astrology", [])
    assert "volume_profile" in tools.call(rd, "help", [])


def test_volume_profile_tool_reports_the_setup_without_future_bars(places, monkeypatch):
    end = date(2026, 11, 13)
    days = [(end - timedelta(days=89 - i)).isoformat() for i in range(90)]
    daily = [{**b, "time": t} for b, t in zip(vpt.daily(0.5), days, strict=True)]
    future = {**daily[-1], "time": "2026-12-01", "close": 1.0, "low": 0.5}
    from console.market import YahooMarket
    monkeypatch.setattr(YahooMarket, "bars", lambda self, s, period="2y", interval="1d":
                        vpt.ENTRY_BARS if interval == "30m" else daily + [future])
    info = run.start("TCS.NS", "2026-11-13", "market")
    text = tools.call(info["run_dir"], "volume_profile", [])
    assert "Status: entry" in text and "POC" in text and "reward/risk" in text


def _write(run_dir, files):
    for name, text in files.items():
        (run_dir / name).write_text(text, encoding="utf-8")


def test_record_shows_progress_then_finishes_with_a_report_and_a_proposal(places, monkeypatch):
    from console.market import YahooMarket
    monkeypatch.setattr(YahooMarket, "last_price", lambda self, s: 100.0)
    info = run.start("TCS.NS", "2026-11-13", "market,news")
    from pathlib import Path
    rd = Path(info["run_dir"])
    store = Store(str(places / "console.db"))

    _write(rd, {"market.md": "Uptrend; volume confirms.", "news.md": "Results beat."})
    out = run.record(str(rd))
    assert out["current"] == "Bull vs Bear debate"
    steps = {s["key"]: s["status"] for s in store.analysis(info["analysis_id"])["progress"]["steps"]}
    assert steps["market"] == "done" and steps["social"] == "skipped" and steps["research_debate"] == "working"

    _write(rd, {"debate_1_bull.md": "Margins rising.", "debate_1_bear.md": "Valuation stretched.",
                "plan.md": "**Recommendation**: Buy", "trader.md": "FINAL TRANSACTION PROPOSAL: **BUY**",
                "risk_1_aggressive.md": "Go big.", "risk_1_conservative.md": "Half size.",
                "risk_1_neutral.md": "Start at half.",
                "decision.md": "**Rating**: Overweight\n\n**Executive Summary**: Add gradually above the POC."})
    out = run.record(str(rd), final=True)
    assert out["rating"] == "Overweight"
    assert out["proposal"] == {"id": out["proposal"]["id"], "side": "BUY", "qty": 500, "price": 100.0,
                               "currency": "INR"}
    assert out["report"].endswith("complete_report.html")
    a = store.analysis(info["analysis_id"])
    assert a["status"] == "done" and a["rating"] == "Overweight"
    debate = next(s for s in a["progress"]["steps"] if s["key"] == "research_debate")
    assert [t["speaker"] for t in debate["turns"]] == ["Bull", "Bear"]
    assert all(s["status"] in ("done", "skipped") for s in a["progress"]["steps"])


def test_finishing_needs_the_decision(places):
    info = run.start("TCS.NS", "2026-11-13", "market")
    with pytest.raises(SystemExit):
        run.record(info["run_dir"], final=True)


def test_the_console_leaves_claude_runs_alone(places):
    info = run.start("TCS.NS", "2026-11-13", "market")
    store = Store(str(places / "console.db"))
    queued = store.create_analysis("INFY.NS", "2026-11-13", ["market"])
    c = Console(store, market=object(), runner=lambda *a, **k: None)   # a console (re)start
    assert store.analysis(info["analysis_id"])["status"] == "running"   # not "Interrupted"
    assert store.analysis(queued)["status"] == "failed"
    with pytest.raises(Conflict):
        c.cancel(info["analysis_id"])
