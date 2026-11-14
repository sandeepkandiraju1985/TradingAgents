"""A Claude-run analysis: its folder, and how it reaches the console.

The agents write one Markdown file each into the run folder:

    market.md  sentiment.md  news.md  fundamentals.md       the analysts
    debate_<round>_bull.md  debate_<round>_bear.md           bull vs bear
    plan.md                                                  research manager
    trader.md                                                trader
    risk_<round>_aggressive.md  _conservative.md  _neutral.md   risk debate
    decision.md                                              portfolio manager

``record`` reads whatever is there, so it can run after every step.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import date as Date, timedelta
from pathlib import Path

ANALYST_FILES = {"market": "market.md", "social": "sentiment.md", "news": "news.md",
                 "fundamentals": "fundamentals.md"}
ALIASES = {"sentiment": "social", "social": "social", "market": "market", "news": "news",
           "fundamentals": "fundamentals"}
TICKER_RE = re.compile(r"^\^?[A-Z0-9][A-Z0-9.\-=&]{0,19}$")
PROVIDER = "claude-code"


def runs_dir() -> Path:
    return Path(os.environ.get("TRADINGAGENTS_CLAUDE_RUNS") or Path.home() / ".tradingagents" / "claude_runs")


def latest_weekday(today: Date | None = None) -> str:
    d = today or Date.today()
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.isoformat()


def _store():
    from console.store import Store, default_db_path
    return Store(default_db_path())


def _write_meta(run_dir: Path, meta: dict) -> None:
    (run_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")


def start(ticker: str, day: str | None, analysts: str, rounds: int = 1) -> dict:
    sym = ticker.strip().upper()
    if not TICKER_RE.match(sym):
        raise SystemExit(f"{ticker!r} isn't a valid symbol. Use Yahoo symbols, e.g. INFY.NS or NVDA.")
    day = day or latest_weekday()
    Date.fromisoformat(day)   # raises on a malformed date
    keys = []
    for a in analysts.split(","):
        k = ALIASES.get(a.strip().lower())
        if k is None:
            raise SystemExit(f"Unknown analyst {a!r}: use market, sentiment, news, fundamentals.")
        if k not in keys:
            keys.append(k)
    rounds = max(1, min(int(rounds), 3))

    run_dir = runs_dir() / f"{sym.replace('^', '')}_{day}_{time.strftime('%H%M%S')}"
    run_dir.mkdir(parents=True, exist_ok=False)
    try:
        from tradingagents.agents.context import (
            build_instrument_context,
            resolve_instrument_identity,
        )
        context = build_instrument_context(sym, "stock", resolve_instrument_identity(sym), day)
    except Exception as exc:   # identity lookup is a nicety; the run goes on without it
        context = f"Ticker {sym}. (Company lookup failed: {exc})"

    from console.progress import Progress
    started = time.time()
    store = _store()
    aid = store.create_analysis(sym, day, keys)
    store.update_analysis(aid, status="running", started=started, progress=Progress(keys, started).snapshot(),
                          stats={"provider": PROVIDER})
    meta = {"ticker": sym, "date": day, "analysts": keys, "rounds": rounds, "started": started,
            "analysis_id": aid, "context": context, "finished": {}}
    _write_meta(run_dir, meta)
    files = [ANALYST_FILES[k] for k in keys]
    for r in range(1, rounds + 1):
        files += [f"debate_{r}_bull.md", f"debate_{r}_bear.md"]
    files += ["plan.md", "trader.md"]
    for r in range(1, rounds + 1):
        files += [f"risk_{r}_aggressive.md", f"risk_{r}_conservative.md", f"risk_{r}_neutral.md"]
    files.append("decision.md")
    return {"run_dir": str(run_dir), "analysis_id": aid, "ticker": sym, "date": day, "analysts": keys,
            "rounds": rounds, "files_in_order": files, "context": context}


def _read(run_dir: Path, name: str) -> str:
    path = run_dir / name
    return path.read_text(encoding="utf-8").strip() if path.is_file() else ""


def _debate(run_dir: Path, rounds: int, prefix: str, speakers: list[str]) -> dict:
    history, per = "", dict.fromkeys(speakers, "")
    for r in range(1, rounds + 1):
        for s in speakers:
            text = _read(run_dir, f"{prefix}_{r}_{s}.md")
            if text:
                turn = f"\n{s.capitalize()} Analyst: {text}"
                history += turn
                per[s] += turn
    return {"history": history, **{f"{s}_history": v for s, v in per.items()}}


def state(run_dir: Path, meta: dict) -> dict:
    """The run as the framework's state, from the files written so far."""
    debate = _debate(run_dir, meta["rounds"], "debate", ["bull", "bear"])
    risk = _debate(run_dir, meta["rounds"], "risk", ["aggressive", "conservative", "neutral"])
    plan, decision = _read(run_dir, "plan.md"), _read(run_dir, "decision.md")
    return {
        "company_of_interest": meta["ticker"],
        "trade_date": meta["date"],
        "market_report": _read(run_dir, "market.md"),
        "sentiment_report": _read(run_dir, "sentiment.md"),
        "news_report": _read(run_dir, "news.md"),
        "fundamentals_report": _read(run_dir, "fundamentals.md"),
        "investment_debate_state": {**debate, "judge_decision": plan},
        "investment_plan": plan,
        "trader_investment_plan": _read(run_dir, "trader.md"),
        "risk_debate_state": {**risk, "judge_decision": decision},
        "final_trade_decision": decision,
    }


def record(run_dir: str, final: bool = False) -> dict:
    from claude_harness.tools import meta as load_meta
    from console.progress import Progress
    from tradingagents.agents.rating import parse_rating

    path = Path(run_dir)
    meta = load_meta(path)
    st = state(path, meta)
    progress = Progress(meta["analysts"], meta["started"])
    progress.finished = dict(meta.get("finished") or {})
    progress.update(st)
    meta["finished"] = progress.finished
    _write_meta(path, meta)
    snap = progress.snapshot()
    store = _store()
    aid = meta["analysis_id"]
    done = [s["label"] for s in snap["steps"] if s["status"] == "done"]
    out = {"analysis_id": aid, "done": done, "current": snap["current"]}

    if not final:
        store.update_analysis(aid, progress=snap)
        return out
    if not st["final_trade_decision"]:
        raise SystemExit("decision.md is missing: the portfolio manager writes it before the run can finish.")

    import tradingagents
    from tradingagents.reporting import write_report_tree
    rating = parse_rating(st["final_trade_decision"])
    st["final_rating"] = rating
    settings = {"llm_provider": PROVIDER, "deep_think_llm": "Claude", "quick_think_llm": "Claude",
                "version": tradingagents.__version__, "analysts": meta["analysts"],
                "max_debate_rounds": meta["rounds"], "max_risk_discuss_rounds": meta["rounds"]}
    saved = Path(write_report_tree(st, meta["ticker"], path / "report", settings=settings))
    html = (saved.parent if saved.is_file() else saved) / "complete_report.html"
    seconds = round(time.time() - meta["started"], 1)
    store.update_analysis(aid, status="done", rating=rating, summary=st["final_trade_decision"],
                          report_path=str(html) if html.exists() else None, progress=snap,
                          stats={"provider": PROVIDER, "seconds": seconds}, finished=time.time())

    from console.engine import propose_from_rating
    from console.market import MarketDataError, YahooMarket
    market = YahooMarket()

    def price_of(symbol):
        try:
            return market.last_price(symbol)
        except MarketDataError as exc:
            store.add_event("error", f"Couldn't get a price for {symbol}: {exc}", symbol)
            return None
    held = next((p for p in store.book()["open"] if p["symbol"] == meta["ticker"]), None)
    pid = propose_from_rating(store, price_of, aid, meta["ticker"], rating, st["final_trade_decision"], held)
    proposal = store.proposal(pid) if pid else None
    out.update(rating=rating, seconds=seconds, report=str(html) if html.exists() else str(saved),
               proposal=({k: proposal[k] for k in ("id", "side", "qty", "price", "currency")} if proposal else None))
    return out
