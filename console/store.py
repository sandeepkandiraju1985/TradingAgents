"""SQLite storage for the console: settings, watchlists, analyses, proposals,
orders and events.

One connection guarded by a lock: the web server, the analysis worker and the
background monitor all share it, and SQLite serialises writes anyway.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS watchlists (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    benchmark TEXT NOT NULL,
    symbols TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS analyses (
    id INTEGER PRIMARY KEY,
    symbol TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    analysts TEXT NOT NULL,
    status TEXT NOT NULL,
    rating TEXT,
    summary TEXT,
    report_path TEXT,
    error TEXT,
    stats TEXT,
    progress TEXT,
    created REAL NOT NULL,
    started REAL,
    finished REAL
);
CREATE TABLE IF NOT EXISTS proposals (
    id INTEGER PRIMARY KEY,
    analysis_id INTEGER,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    qty REAL NOT NULL,
    price REAL NOT NULL,
    currency TEXT NOT NULL,
    rating TEXT,
    note TEXT,
    status TEXT NOT NULL,
    created REAL NOT NULL,
    decided REAL
);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    qty REAL NOT NULL,
    price REAL NOT NULL,
    currency TEXT NOT NULL,
    mode TEXT NOT NULL,
    reason TEXT,
    proposal_id INTEGER,
    broker_ref TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    kind TEXT NOT NULL,
    symbol TEXT,
    message TEXT NOT NULL,
    data TEXT
);
"""

DEFAULT_SETTINGS: dict[str, Any] = {
    # Close a position when its price falls this many percent below the average entry.
    "stop_loss_pct": 5.0,
    # Screen every watchlist once an hour during Indian market hours.
    "hourly_scan": False,
    # Money per new position, in the stock's own currency.
    "trade_budget": {"INR": 50000.0, "USD": 600.0},
    # Read-only here: the console has no live order path yet.
    "live_trading": False,
}

DEFAULT_WATCHLISTS = [
    ("India large caps", "^NSEI", ["RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS",
                                   "ICICIBANK.NS", "LT.NS", "BHARTIARTL.NS", "SUNPHARMA.NS"]),
    ("US tech", "^GSPC", ["NVDA", "MSFT", "AAPL", "GOOGL", "AMZN"]),
]

_ANALYSIS_FIELDS = {"status", "rating", "summary", "report_path", "error", "stats", "progress", "started", "finished"}
_JSON_FIELDS = ("stats", "progress")
# Columns added after the first release; older databases gain them on start.
_ADDED_COLUMNS = [
    ("analyses", "progress", "TEXT"),
    ("proposals", "stop", "REAL"),      # a strategy's own stop-loss price
    ("proposals", "target", "REAL"),
    ("proposals", "source", "TEXT"),    # "agents" or "volume_profile"
    ("proposals", "signal", "TEXT"),    # identifies the signal, so it is proposed once
    ("orders", "stop", "REAL"),
]
_EPS = 1e-9
CLAUDE_CODE = "claude-code"   # stats["provider"] of runs made by the claude_harness


def default_db_path() -> str:
    """The console's database: TRADINGAGENTS_CONSOLE_DB, else ~/.tradingagents/console.db."""
    return os.environ.get("TRADINGAGENTS_CONSOLE_DB") or os.path.join(Path.home(), ".tradingagents", "console.db")


class Store:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.executescript(SCHEMA)
            # Databases from before the agent view lack the progress column.
            for table, col, kind in _ADDED_COLUMNS:
                cols = {r["name"] for r in self._db.execute(f"PRAGMA table_info({table})")}
                if col not in cols:
                    self._db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {kind}")
            self._db.commit()
            if not self._all("SELECT id FROM watchlists LIMIT 1"):
                for name, bench, symbols in DEFAULT_WATCHLISTS:
                    self.add_watchlist(name, bench, symbols)

    # ---------- low level ----------
    def _run(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._db.execute(sql, args)
            self._db.commit()
            return cur

    def _all(self, sql: str, args: tuple = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._db.execute(sql, args).fetchall()]

    def _one(self, sql: str, args: tuple = ()) -> dict | None:
        rows = self._all(sql, args)
        return rows[0] if rows else None

    # ---------- settings ----------
    def settings(self) -> dict:
        out = json.loads(json.dumps(DEFAULT_SETTINGS))
        for row in self._all("SELECT key, value FROM settings"):
            out[row["key"]] = json.loads(row["value"])
        return out

    def set_setting(self, key: str, value: Any) -> None:
        self._run("INSERT INTO settings(key, value) VALUES(?, ?) "
                  "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, json.dumps(value)))

    # ---------- watchlists ----------
    @staticmethod
    def _wl(row: dict | None) -> dict | None:
        if row is not None:
            row["symbols"] = json.loads(row["symbols"])
        return row

    def watchlists(self) -> list[dict]:
        return [self._wl(r) for r in self._all("SELECT * FROM watchlists ORDER BY id")]

    def watchlist(self, wid: int) -> dict | None:
        return self._wl(self._one("SELECT * FROM watchlists WHERE id = ?", (wid,)))

    def add_watchlist(self, name: str, benchmark: str, symbols: list[str]) -> int:
        cur = self._run("INSERT INTO watchlists(name, benchmark, symbols) VALUES(?, ?, ?)",
                        (name, benchmark, json.dumps(symbols)))
        return cur.lastrowid

    def update_watchlist(self, wid: int, **fields) -> None:
        if "symbols" in fields:
            self._run("UPDATE watchlists SET symbols = ? WHERE id = ?", (json.dumps(fields["symbols"]), wid))
        if "name" in fields:
            self._run("UPDATE watchlists SET name = ? WHERE id = ?", (fields["name"], wid))
        if "benchmark" in fields:
            self._run("UPDATE watchlists SET benchmark = ? WHERE id = ?", (fields["benchmark"], wid))

    def delete_watchlist(self, wid: int) -> None:
        self._run("DELETE FROM watchlists WHERE id = ?", (wid,))

    # ---------- analyses ----------
    @staticmethod
    def _an(row: dict | None) -> dict | None:
        if row is not None:
            row["analysts"] = json.loads(row["analysts"])
            for f in _JSON_FIELDS:
                row[f] = json.loads(row[f]) if row.get(f) else None
        return row

    def create_analysis(self, symbol: str, trade_date: str, analysts: list[str]) -> int:
        cur = self._run("INSERT INTO analyses(symbol, trade_date, analysts, status, created) "
                        "VALUES(?, ?, ?, 'queued', ?)", (symbol, trade_date, json.dumps(analysts), time.time()))
        return cur.lastrowid

    def update_analysis(self, aid: int, **fields) -> None:
        bad = set(fields) - _ANALYSIS_FIELDS
        if bad:
            raise ValueError(f"unknown analysis fields: {sorted(bad)}")
        for f in _JSON_FIELDS:
            if fields.get(f) is not None:
                fields[f] = json.dumps(fields[f])
        cols = ", ".join(f"{k} = ?" for k in fields)
        self._run(f"UPDATE analyses SET {cols} WHERE id = ?", (*fields.values(), aid))

    def analysis(self, aid: int) -> dict | None:
        return self._an(self._one("SELECT * FROM analyses WHERE id = ?", (aid,)))

    def analyses(self, limit: int = 30) -> list[dict]:
        return [self._an(r) for r in self._all("SELECT * FROM analyses ORDER BY id DESC LIMIT ?", (limit,))]

    def fail_interrupted(self) -> int:
        """Runs left queued or running by a previous session can never finish.

        A run in Claude Code (the claude_harness) is not the console's to end: it
        records itself, whether or not the console is open.
        """
        cur = self._run("UPDATE analyses SET status = 'failed', error = 'Interrupted: the console was closed "
                        "before this run finished.', finished = ? WHERE status IN ('queued', 'running') "
                        "AND (stats IS NULL OR stats NOT LIKE ?)", (time.time(), f'%"provider": "{CLAUDE_CODE}"%'))
        return cur.rowcount

    # ---------- proposals ----------
    def create_proposal(self, analysis_id: int | None, symbol: str, side: str, qty: float, price: float,
                        currency: str, rating: str | None, note: str, stop: float | None = None,
                        target: float | None = None, source: str = "agents", signal: str | None = None) -> int:
        cur = self._run("INSERT INTO proposals(analysis_id, symbol, side, qty, price, currency, rating, note, "
                        "status, created, stop, target, source, signal) "
                        "VALUES(?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?)",
                        (analysis_id, symbol, side, qty, price, currency, rating, note, time.time(),
                         stop, target, source, signal))
        return cur.lastrowid

    def has_signal(self, signal: str) -> bool:
        return self._one("SELECT id FROM proposals WHERE signal = ?", (signal,)) is not None

    def proposal(self, pid: int) -> dict | None:
        return self._one("SELECT * FROM proposals WHERE id = ?", (pid,))

    def proposals(self, status: str | None = None, limit: int = 50) -> list[dict]:
        if status:
            return self._all("SELECT * FROM proposals WHERE status = ? ORDER BY id DESC LIMIT ?", (status, limit))
        return self._all("SELECT * FROM proposals ORDER BY id DESC LIMIT ?", (limit,))

    def claim_proposal(self, pid: int, status: str) -> bool:
        """Move a pending proposal to ``status``; False if it was already decided."""
        cur = self._run("UPDATE proposals SET status = ?, decided = ? WHERE id = ? AND status = 'pending'",
                        (status, time.time(), pid))
        return cur.rowcount == 1

    # ---------- orders ----------
    def add_order(self, symbol: str, side: str, qty: float, price: float, currency: str, mode: str,
                  reason: str, proposal_id: int | None = None, broker_ref: str | None = None,
                  stop: float | None = None) -> dict:
        ts = time.time()
        cur = self._run("INSERT INTO orders(ts, symbol, side, qty, price, currency, mode, reason, proposal_id, "
                        "broker_ref, stop) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (ts, symbol, side, qty, price, currency, mode, reason, proposal_id, broker_ref, stop))
        return self._one("SELECT * FROM orders WHERE id = ?", (cur.lastrowid,))

    def orders(self, limit: int = 100, symbol: str | None = None) -> list[dict]:
        if symbol:
            return self._all("SELECT * FROM orders WHERE symbol = ? ORDER BY id DESC LIMIT ?", (symbol, limit))
        return self._all("SELECT * FROM orders ORDER BY id DESC LIMIT ?", (limit,))

    def book(self) -> dict:
        """Open positions at average cost, and every round trip that closed.

        Built from the order history each time, so the ledger is the only record.
        """
        held: dict[str, dict] = {}
        closed: list[dict] = []
        for o in self._all("SELECT * FROM orders ORDER BY ts, id"):
            p = held.setdefault(o["symbol"], {"symbol": o["symbol"], "qty": 0.0, "cost": 0.0,
                                              "currency": o["currency"], "opened": o["ts"], "stop": None})
            if o["side"] == "BUY":
                if p["qty"] <= _EPS:
                    p["opened"], p["stop"] = o["ts"], None
                if o.get("stop"):
                    p["stop"] = o["stop"]   # the latest strategy stop on the position
                p["qty"] += o["qty"]
                p["cost"] += o["qty"] * o["price"]
            else:
                if p["qty"] <= _EPS:
                    continue
                avg = p["cost"] / p["qty"]
                sold = min(o["qty"], p["qty"])
                p["qty"] -= sold
                p["cost"] -= avg * sold
                if p["qty"] <= _EPS:
                    closed.append({"symbol": o["symbol"], "currency": o["currency"], "entry": avg,
                                   "exit": o["price"], "qty": sold, "return_pct": (o["price"] / avg - 1) * 100,
                                   "opened": p["opened"], "closed": o["ts"], "reason": o["reason"]})
                    p["qty"], p["cost"] = 0.0, 0.0
        open_ = [{"symbol": p["symbol"], "qty": p["qty"], "avg_price": p["cost"] / p["qty"],
                  "currency": p["currency"], "opened": p["opened"], "stop": p["stop"]}
                 for p in held.values() if p["qty"] > _EPS]
        closed.reverse()
        return {"open": open_, "closed": closed}

    def held_qty(self, symbol: str) -> float:
        for p in self.book()["open"]:
            if p["symbol"] == symbol:
                return p["qty"]
        return 0.0

    # ---------- events ----------
    def add_event(self, kind: str, message: str, symbol: str | None = None, data: dict | None = None) -> None:
        self._run("INSERT INTO events(ts, kind, symbol, message, data) VALUES(?, ?, ?, ?, ?)",
                  (time.time(), kind, symbol, message, json.dumps(data) if data else None))

    def events(self, limit: int = 50, kind: str | None = None) -> list[dict]:
        if kind:
            rows = self._all("SELECT * FROM events WHERE kind = ? ORDER BY id DESC LIMIT ?", (kind, limit))
        else:
            rows = self._all("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))
        for r in rows:
            r["data"] = json.loads(r["data"]) if r["data"] else None
        return rows
