"""HTTP API and static page for the console. Listens on 127.0.0.1 only."""

from __future__ import annotations

import re
import time
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import rules
from .engine import Conflict, Console, NotFound
from .market import MarketDataError
from .progress import Progress

STATIC = Path(__file__).parent / "static"
SYMBOL_RE = re.compile(r"^\^?[A-Z0-9][A-Z0-9.\-=&]{0,19}$")
PERIODS = {"1mo", "3mo", "6mo", "1y", "2y", "5y"}


def clean_symbol(raw: str) -> str:
    s = (raw or "").strip().upper()
    if not SYMBOL_RE.match(s):
        raise HTTPException(400, f"“{raw}” isn't a valid symbol. Use Yahoo symbols, e.g. INFY.NS or NVDA.")
    return s


class AnalysisIn(BaseModel):
    symbol: str
    trade_date: str | None = None
    analysts: list[str] = Field(default_factory=lambda: list(rules.ANALYSTS))


class WatchlistIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    benchmark: str = "^NSEI"
    symbols: list[str] = Field(default_factory=list)


class WatchlistPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=60)
    benchmark: str | None = None
    symbols: list[str] | None = None


class ScanIn(BaseModel):
    symbols: list[str] | None = None


class SettingsIn(BaseModel):
    stop_loss_pct: float | None = Field(default=None, gt=0, le=50)
    hourly_scan: bool | None = None
    trade_budget: dict[str, float] | None = None
    live_trading: bool | None = None


def _returns(dates: list[str], series: dict[str, list[float]], members: list[str]) -> dict:
    """1- and 5-year return of equal amounts held in each member."""
    def ew(start: int) -> float | None:
        if start < 0 or not members:
            return None
        return sum(series[m][-1] / series[m][start] for m in members) / len(members) - 1
    n = len(dates)
    return {"r1": ew(n - 1 - 252) if n > 252 else None, "r5": ew(n - 1 - 1260) if n > 1260 else None}


def create_app(console: Console) -> FastAPI:
    app = FastAPI(title="TradingAgents Console", docs_url=None, redoc_url=None)
    store = console.store

    def market_call(fn, *args):
        try:
            return fn(*args)
        except MarketDataError as exc:
            raise HTTPException(502, str(exc)) from exc

    @app.get("/", response_class=HTMLResponse)
    def index():
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/api/state")
    def state():
        s = store.settings()
        book = store.book()
        prices = {}
        for p in book["open"]:
            try:
                prices[p["symbol"]] = console.market.last_price(p["symbol"])
            except MarketDataError:
                prices[p["symbol"]] = None
        for p in book["open"]:
            last = prices[p["symbol"]]
            p["last"] = last
            p["change_pct"] = rules.change_pct(p["avg_price"], last) if last else None
            p["stop_price"] = rules.exit_price(p["avg_price"], float(s["stop_loss_pct"]), p.get("stop"))
        return {
            "now": time.time(),
            "settings": {k: v for k, v in s.items() if k != "last_scan_results"},
            "scan_results": s.get("last_scan_results") or [],
            "watchlists": store.watchlists(),
            "positions": book["open"],
            "closed": book["closed"][:30],
            "proposals": store.proposals(limit=40),
            "analyses": [{**a, "progress": Progress.summary(a.get("progress"))} for a in store.analyses(limit=30)],
            "orders": store.orders(limit=60),
            "events": store.events(limit=40),
        }

    @app.get("/api/strip")
    def strip():
        return console.market.strip()

    @app.get("/api/bars/{symbol}")
    def bars(symbol: str, period: str = "2y", interval: str = "1d"):
        sym = clean_symbol(symbol)
        if interval not in ("1d", "30m"):
            raise HTTPException(400, "interval must be 1d or 30m")
        if interval == "30m":
            period = "60d"   # all the 30-minute history Yahoo serves
        elif period not in PERIODS:
            raise HTTPException(400, f"period must be one of {sorted(PERIODS)}")
        data = market_call(console.market.bars, sym, period, interval)
        pos = next((p for p in store.book()["open"] if p["symbol"] == sym), None)
        stop = float(store.settings()["stop_loss_pct"])
        return {
            "symbol": sym,
            "currency": rules.currency_for(sym),
            "bars": data,
            "orders": store.orders(limit=500, symbol=sym),
            "interval": interval,
            "position": ({**pos, "stop_price": rules.exit_price(pos["avg_price"], stop, pos.get("stop"))}
                         if pos else None),
            "stop_loss_pct": stop,
        }

    @app.get("/api/setup/{symbol}")
    def setup(symbol: str):
        return market_call(console.setup, clean_symbol(symbol))

    @app.get("/api/watchlists/{wid}/performance")
    def performance(wid: int):
        wl = store.watchlist(wid)
        if wl is None:
            raise HTTPException(404, "No such watchlist")
        if not wl["symbols"]:
            raise HTTPException(400, "This watchlist has no stocks yet.")
        data = market_call(console.market.closes, [*wl["symbols"], wl["benchmark"]], "5y")
        if wl["benchmark"] not in data["series"]:
            raise HTTPException(502, f"No benchmark prices for {wl['benchmark']}.")
        members = [s for s in wl["symbols"] if s in data["series"]]
        return {
            "watchlist": wl,
            "dates": data["dates"],
            "members": members,
            "series": {m: data["series"][m] for m in members},
            "benchmark": data["series"][wl["benchmark"]],
            "missing": data["missing"],
            "currency": rules.currency_for(wl["benchmark"]),
            **_returns(data["dates"], data["series"], members),
        }

    @app.post("/api/watchlists")
    def add_watchlist(body: WatchlistIn):
        syms = [clean_symbol(s) for s in body.symbols]
        try:
            wid = store.add_watchlist(body.name.strip(), clean_symbol(body.benchmark), syms)
        except Exception as exc:
            raise HTTPException(400, f"Couldn't create the watchlist: {exc}") from exc
        return store.watchlist(wid)

    @app.patch("/api/watchlists/{wid}")
    def patch_watchlist(wid: int, body: WatchlistPatch):
        if store.watchlist(wid) is None:
            raise HTTPException(404, "No such watchlist")
        fields = {}
        if body.symbols is not None:
            fields["symbols"] = list(dict.fromkeys(clean_symbol(s) for s in body.symbols))
        if body.benchmark is not None:
            fields["benchmark"] = clean_symbol(body.benchmark)
        if body.name is not None:
            fields["name"] = body.name.strip()
        store.update_watchlist(wid, **fields)
        return store.watchlist(wid)

    @app.delete("/api/watchlists/{wid}")
    def delete_watchlist(wid: int):
        store.delete_watchlist(wid)
        return {"ok": True}

    @app.post("/api/analyses")
    def analyze(body: AnalysisIn):
        sym = clean_symbol(body.symbol)
        analysts = [a for a in body.analysts if a in rules.ANALYSTS]
        if not analysts:
            raise HTTPException(400, "Pick at least one analyst.")
        trade_date = body.trade_date or date.today().isoformat()
        try:
            date.fromisoformat(trade_date)
        except ValueError as exc:
            raise HTTPException(400, "Trade date must look like 2026-11-14.") from exc
        aid = console.submit_analysis(sym, trade_date, analysts)
        return store.analysis(aid)

    @app.get("/api/analyses/{aid}")
    def analysis(aid: int):
        a = store.analysis(aid)
        if a is None:
            raise HTTPException(404, "No such analysis.")
        a["proposals"] = [p for p in store.proposals(limit=200) if p["analysis_id"] == aid]
        a["now"] = time.time()
        return a

    @app.post("/api/analyses/{aid}/cancel")
    def cancel(aid: int):
        try:
            return console.cancel(aid)
        except NotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        except Conflict as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/analyses/{aid}/report")
    def report(aid: int):
        a = store.analysis(aid)
        if a is None or not a.get("report_path") or not Path(a["report_path"]).is_file():
            raise HTTPException(404, "No report for this analysis.")
        return FileResponse(a["report_path"], media_type="text/html")

    @app.post("/api/proposals/{pid}/approve")
    def approve(pid: int):
        try:
            return console.approve(pid)
        except NotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        except Conflict as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/proposals/{pid}/reject")
    def reject(pid: int):
        try:
            console.reject(pid)
        except NotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        except Conflict as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"ok": True}

    @app.post("/api/scan")
    def scan(body: ScanIn | None = None):
        syms = [clean_symbol(s) for s in body.symbols] if body and body.symbols else None
        return console.scan(syms)

    @app.post("/api/check-exits")
    def check_exits():
        return {"exits": console.check_exits()}

    @app.patch("/api/settings")
    def settings(body: SettingsIn):
        if body.live_trading:
            raise HTTPException(403, "Live trading can't be switched on: the console has no live order path yet.")
        if body.stop_loss_pct is not None:
            store.set_setting("stop_loss_pct", body.stop_loss_pct)
        if body.hourly_scan is not None:
            store.set_setting("hourly_scan", body.hourly_scan)
        if body.trade_budget is not None:
            budget = store.settings()["trade_budget"]
            for cur, amount in body.trade_budget.items():
                if cur in ("INR", "USD") and amount >= 0:
                    budget[cur] = amount
            store.set_setting("trade_budget", budget)
        return store.settings()

    return app
