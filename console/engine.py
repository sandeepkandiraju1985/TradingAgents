"""The console's behaviour, independent of the web server.

- Analyses run one at a time on a worker thread; each finished run may create a
  proposal (never an order).
- An order exists only after you approve a proposal, or when the auto-exit
  rule closes a losing position. Both go through the broker, which is dry-run.
- The monitor thread re-checks open positions every few minutes and, when
  hourly scanning is on, screens the watchlists during NSE hours.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import pytz

from . import rules
from .broker import get_broker
from .market import MarketDataError, YahooMarket
from .progress import Progress
from .store import Store

log = logging.getLogger("console")

EXIT_CHECK_SECONDS = 600
SCAN_SECONDS = 3600


class NotFound(LookupError):
    pass


class Conflict(RuntimeError):
    pass


class Cancelled(Exception):
    """Raised inside a run to stop it at the next agent step."""


def run_tradingagents(symbol: str, trade_date: str, analysts: list[str], held: dict | None,
                      on_progress: Callable[[dict], None] | None = None) -> dict:
    """One full TradingAgents run. Imported lazily: the graph is heavy to load.

    ``on_progress`` receives each state the graph streams as agents finish. It
    may raise to stop the run; the graph's own cleanup still runs.
    """
    from cli.stats_handler import StatsCallbackHandler
    from tradingagents.default_config import build_default_config
    from tradingagents.graph.trading_graph import TradingAgentsGraph
    from tradingagents.portfolio import PortfolioContext, Position

    class ObservedGraph(TradingAgentsGraph):
        # With debug on, propagate() streams the run through stream_run (the same
        # path the CLI uses); this hands each state to on_progress and passes no
        # messages on, so nothing is printed.
        def stream_run(self, graph_input, **args):
            for _messages, state in super().stream_run(graph_input, **args):
                if state and on_progress:
                    on_progress(state)
                yield [], state

    config = build_default_config()
    stats = StatsCallbackHandler()
    graph = ObservedGraph(selected_analysts=analysts, config=config, callbacks=[stats], debug=True)
    # Only the console's own dry-run book is known here, not your real account,
    # so the agents get portfolio context only for a position this book holds.
    portfolio = None
    if held:
        portfolio = PortfolioContext(positions=[Position(ticker=symbol, quantity=held["qty"],
                                                         average_price=held["avg_price"])])
    t0 = time.time()
    final_state, rating = graph.propagate(symbol, trade_date, portfolio=portfolio)
    # save_reports returns the complete_report.md path; the HTML page sits beside it.
    saved = Path(graph.save_reports(final_state, symbol))
    report = (saved.parent if saved.is_file() else saved) / "complete_report.html"
    return {
        "rating": rating,
        "state": {k: final_state.get(k) for k in (
            "market_report", "sentiment_report", "news_report", "fundamentals_report",
            "investment_debate_state", "investment_plan", "trader_investment_plan",
            "risk_debate_state", "final_trade_decision")},
        "summary": final_state.get("final_trade_decision") or "",
        "report_path": str(report) if report.exists() else None,
        "stats": {**stats.get_stats(), "seconds": round(time.time() - t0, 1),
                  "provider": config.get("llm_provider"), "deep_model": config.get("deep_think_llm"),
                  "quick_model": config.get("quick_think_llm")},
    }


def _secs(sec: float | None) -> str:
    if sec is None:
        return "-"
    m, s = divmod(int(round(sec)), 60)
    return f"{m}m {s}s" if m else f"{s}s"


def _first_line(text: str, limit: int = 240) -> str:
    """The first line of reasoning in a decision, skipping its rating label."""
    for line in (text or "").splitlines():
        line = line.strip().strip("#*- ").strip()
        if line and not re.match(r"^(final\s+)?rating\b", line, re.IGNORECASE):
            return line[:limit]
    return ""


class Console:
    def __init__(self, store: Store, market: YahooMarket | None = None,
                 runner: Callable[..., dict] | None = None, broker_factory=get_broker):
        self.store = store
        self.market = market or YahooMarket()
        self.runner = runner or run_tradingagents
        self.broker_factory = broker_factory
        self._exec = ThreadPoolExecutor(max_workers=1, thread_name_prefix="analysis")
        self._cancel: set[int] = set()
        self._exit_lock = threading.Lock()
        self._scan_lock = threading.Lock()
        self._stop = threading.Event()
        self._monitor: threading.Thread | None = None
        store.fail_interrupted()

    # ---------- orders ----------
    def _place(self, symbol: str, side: str, qty: float, price: float, currency: str, reason: str,
               proposal_id: int | None = None) -> dict:
        broker = self.broker_factory(self.store.settings())
        result = broker.place({"symbol": symbol, "side": side, "qty": qty, "price": price})
        return self.store.add_order(symbol, side, qty, price, currency, result["mode"], reason,
                                    proposal_id=proposal_id, broker_ref=result["ref"])

    def _price(self, symbol: str) -> float | None:
        try:
            return self.market.last_price(symbol)
        except MarketDataError as exc:
            self.store.add_event("error", f"Couldn't get a price for {symbol}: {exc}", symbol)
            return None

    def approve(self, pid: int) -> dict:
        p = self.store.proposal(pid)
        if p is None:
            raise NotFound(f"No proposal {pid}")
        if p["status"] != "pending":
            raise Conflict(f"Proposal {pid} is already {p['status']}")
        qty = p["qty"]
        if p["side"] == "SELL":
            qty = min(qty, self.store.held_qty(p["symbol"]))
            if qty <= 0:
                self.store.claim_proposal(pid, "void")
                raise Conflict(f"Nothing left to sell in {p['symbol']}; the proposal was voided.")
        price = self._price(p["symbol"]) or p["price"]
        if not self.store.claim_proposal(pid, "approved"):
            raise Conflict(f"Proposal {pid} was decided elsewhere")
        order = self._place(p["symbol"], p["side"], qty, price, p["currency"], "Approved by you", proposal_id=pid)
        self.store.add_event("approved", f"Approved {p['side']} {qty:g} {p['symbol']} at {price:,.2f} "
                             f"({order['mode']})", p["symbol"])
        return order

    def reject(self, pid: int) -> None:
        p = self.store.proposal(pid)
        if p is None:
            raise NotFound(f"No proposal {pid}")
        if not self.store.claim_proposal(pid, "rejected"):
            raise Conflict(f"Proposal {pid} is already {p['status']}")
        self.store.add_event("rejected", f"Rejected {p['side']} {p['symbol']}", p["symbol"])

    # ---------- auto-exit ----------
    def check_exits(self) -> list[dict]:
        """Close every open position that is down more than the stop-loss limit."""
        with self._exit_lock:
            stop = float(self.store.settings()["stop_loss_pct"])
            exits = []
            for pos in self.store.book()["open"]:
                price = self._price(pos["symbol"])
                if price is None or not rules.should_exit(pos["avg_price"], price, stop):
                    continue
                loss = rules.change_pct(pos["avg_price"], price)
                reason = f"Auto-exit: down {abs(loss):.1f}% from entry (limit {stop:g}%)"
                order = self._place(pos["symbol"], "SELL", pos["qty"], price, pos["currency"], reason)
                msg = (f"Sold {pos['qty']:g} {pos['symbol']} at {price:,.2f}, {abs(loss):.1f}% below the "
                       f"average entry of {pos['avg_price']:,.2f} ({order['mode']}).")
                self.store.add_event("auto_exit", msg, pos["symbol"],
                                     {"entry": pos["avg_price"], "price": price, "loss_pct": loss, "qty": pos["qty"]})
                exits.append(order)
            self.store.set_setting("last_exit_check", time.time())
            return exits

    # ---------- scan ----------
    def all_symbols(self) -> list[str]:
        seen: dict[str, None] = {}
        for wl in self.store.watchlists():
            for s in wl["symbols"]:
                seen[s] = None
        return list(seen)

    def scan(self, symbols: list[str] | None = None) -> dict:
        """Cheap price screen of the watchlists, then the auto-exit check. No LLM calls."""
        with self._scan_lock:
            symbols = symbols or self.all_symbols()
            results = []
            for sym in symbols:
                try:
                    bars = self.market.bars(sym, "6mo")
                except MarketDataError as exc:
                    results.append({"symbol": sym, "error": str(exc)})
                    continue
                last = bars[-1]["close"]
                prev = bars[-2]["close"] if len(bars) > 1 else last
                results.append({"symbol": sym, "last": last, "currency": rules.currency_for(sym),
                                "change_pct": rules.change_pct(prev, last), "flags": rules.screen(bars)})
            exits = self.check_exits()
            summary = {"ts": time.time(), "count": len(symbols),
                       "flagged": sum(1 for r in results if r.get("flags")), "exits": len(exits)}
            self.store.set_setting("last_scan", summary)
            self.store.set_setting("last_scan_results", results)
            self.store.add_event("scan", f"Screened {summary['count']} stocks: {summary['flagged']} flagged, "
                                 f"{summary['exits']} auto-exits.")
            return {"summary": summary, "results": results, "exits": exits}

    # ---------- analyses ----------
    def submit_analysis(self, symbol: str, trade_date: str, analysts: list[str]) -> int:
        aid = self.store.create_analysis(symbol, trade_date, analysts)
        self._exec.submit(self._run_analysis, aid)
        return aid

    def cancel(self, aid: int) -> dict:
        a = self.store.analysis(aid)
        if a is None:
            raise NotFound(f"No analysis {aid}")
        if a["status"] == "queued":
            self.store.update_analysis(aid, status="cancelled", finished=time.time(),
                                       error="Cancelled before it started.")
            self.store.add_event("analysis", f"Cancelled the queued analysis of {a['symbol']}.", a["symbol"])
        elif a["status"] == "running":
            self._cancel.add(aid)   # takes effect when the next agent finishes
        else:
            raise Conflict(f"This analysis is already {a['status']}.")
        return self.store.analysis(aid)

    def _run_analysis(self, aid: int) -> None:
        a = self.store.analysis(aid)
        if a is None or a["status"] != "queued":
            return   # cancelled while it waited
        started = time.time()
        self.store.update_analysis(aid, status="running", started=started)
        progress = Progress(a["analysts"], started)
        self.store.update_analysis(aid, progress=progress.snapshot())
        held = next((p for p in self.store.book()["open"] if p["symbol"] == a["symbol"]), None)

        def on_progress(state: dict) -> None:
            if aid in self._cancel:
                raise Cancelled()
            done = progress.update(state)
            if done:
                snap = progress.snapshot()
                self.store.update_analysis(aid, progress=snap)
                for step in snap["steps"]:
                    if step["key"] in done:
                        log.info("%s: %s finished (%s)", a["symbol"], step["label"],
                                 _secs(step.get("seconds")))
            if aid in self._cancel:
                raise Cancelled()

        try:
            out = self.runner(a["symbol"], a["trade_date"], a["analysts"], held, on_progress=on_progress)
        except Cancelled:
            self.store.update_analysis(aid, status="cancelled", finished=time.time(),
                                       error="Cancelled by you.", progress=progress.snapshot())
            self.store.add_event("analysis", f"Cancelled the analysis of {a['symbol']}.", a["symbol"])
            log.info("%s: cancelled", a["symbol"])
            return
        except Exception as exc:  # any failure belongs on the run, not in a dead thread
            log.exception("analysis %s failed", aid)
            self.store.update_analysis(aid, status="failed", error=f"{type(exc).__name__}: {exc}"[:800],
                                       finished=time.time(), progress=progress.snapshot())
            self.store.add_event("error", f"Analysis of {a['symbol']} failed: {exc}"[:300], a["symbol"])
            return
        finally:
            self._cancel.discard(aid)
        if out.get("state"):
            progress.update(out["state"])   # the final state carries every report
        self.store.update_analysis(aid, status="done", rating=out.get("rating"), summary=out.get("summary"),
                                   report_path=out.get("report_path"), stats=out.get("stats"),
                                   progress=progress.snapshot(), finished=time.time())
        log.info("%s: rated %s in %s", a["symbol"], out.get("rating"), _secs(time.time() - started))
        self._propose(aid, a["symbol"], out.get("rating"), out.get("summary") or "", held)

    def _propose(self, aid: int, symbol: str, rating: str | None, summary: str, held: dict | None) -> int | None:
        held_qty = held["qty"] if held else 0.0
        side = rules.proposal_side(rating, held_qty)
        if side is None:
            self.store.add_event("analysis", f"{symbol}: rated {rating}. No trade proposed.", symbol)
            return None
        price = self._price(symbol)
        if price is None:
            return None
        cur = rules.currency_for(symbol)
        if side == "SELL":
            qty = held_qty
        else:
            budget = float(self.store.settings()["trade_budget"].get(cur, 0))
            qty = rules.size_order(budget, price)
            if qty <= 0:
                self.store.add_event("analysis", f"{symbol}: rated {rating}, but the {cur} trade budget "
                                     f"buys no shares at {price:,.2f}.", symbol)
                return None
        note = _first_line(summary) or f"Agents rated {symbol} {rating}."
        pid = self.store.create_proposal(aid, symbol, side, qty, price, cur, rating, note)
        self.store.add_event("proposal", f"{side} {qty:g} {symbol} proposed (rated {rating}).", symbol)
        return pid

    # ---------- monitor ----------
    def start_monitor(self, tick: float = 60.0) -> None:
        if self._monitor and self._monitor.is_alive():
            return
        self._monitor = threading.Thread(target=self._loop, args=(tick,), name="monitor", daemon=True)
        self._monitor.start()

    def stop(self) -> None:
        self._stop.set()
        self._exec.shutdown(wait=False, cancel_futures=True)

    def monitor_once(self, now: datetime | None = None) -> None:
        now = now or datetime.now(pytz.utc)
        s = self.store.settings()
        ts = now.timestamp()
        if s.get("hourly_scan") and rules.in_nse_hours(now):
            last = (s.get("last_scan") or {}).get("ts", 0)
            if ts - last >= SCAN_SECONDS:
                self.scan()
                return  # scan already ran the exit check
        if ts - float(s.get("last_exit_check") or 0) >= EXIT_CHECK_SECONDS and self.store.book()["open"]:
            self.check_exits()

    def _loop(self, tick: float) -> None:
        while not self._stop.wait(tick):
            try:
                self.monitor_once()
            except Exception as exc:
                log.exception("monitor tick failed")
                self.store.add_event("error", f"Background check failed: {exc}"[:300])
