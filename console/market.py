"""Market data for the console, from Yahoo Finance via yfinance.

Prices for the screen, the charts and the auto-exit check. Results are cached
for a few minutes: Yahoo rate-limits bursts, and the page asks for the same
symbols repeatedly.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from typing import Any

STRIP = [
    ("^NSEI", "NIFTY 50"),
    ("^BSESN", "SENSEX"),
    ("^NDX", "NASDAQ 100"),
    ("INR=X", "USD/INR"),
    ("GC=F", "Gold, USD/oz"),
]


class MarketDataError(RuntimeError):
    """Yahoo returned nothing usable for a symbol."""


def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


class YahooMarket:
    def __init__(self, ttl: float = 300.0, timeout: float = 20.0):
        self.ttl, self.timeout = ttl, timeout
        self._cache: dict[tuple, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def _cached(self, key: tuple, fn: Callable[[], Any]) -> Any:
        now = time.time()
        with self._lock:
            hit = self._cache.get(key)
            if hit and now - hit[0] < self.ttl:
                return hit[1]
        value = fn()
        with self._lock:
            self._cache[key] = (now, value)
        return value

    def bars(self, symbol: str, period: str = "2y") -> list[dict]:
        """Daily OHLCV bars, oldest first, as {time, open, high, low, close, volume}."""
        def load():
            import yfinance as yf
            try:
                df = yf.Ticker(symbol).history(period=period, interval="1d", auto_adjust=False,
                                               timeout=self.timeout)
            except Exception as exc:  # yfinance raises many types for network trouble
                raise MarketDataError(f"{symbol}: {exc}") from exc
            out = []
            for ts, row in df.iterrows():
                o, h, lo, c = (_num(row.get(k)) for k in ("Open", "High", "Low", "Close"))
                if None in (o, h, lo, c):
                    continue
                out.append({"time": ts.strftime("%Y-%m-%d"), "open": o, "high": h, "low": lo, "close": c,
                            "volume": _num(row.get("Volume")) or 0.0})
            if not out:
                raise MarketDataError(f"No price data for {symbol}. Check the symbol, e.g. INFY.NS for NSE stocks.")
            return out
        return self._cached(("bars", symbol, period), load)

    def last_price(self, symbol: str) -> float:
        return self.bars(symbol, "1mo")[-1]["close"]

    def closes(self, symbols: list[str], period: str = "5y") -> dict:
        """Aligned daily closes: {"dates": [...], "series": {symbol: [...]}}.

        Dates where a market was shut carry the previous close forward, and the
        series starts on the first date every symbol has a price, so all of them
        cover the same span.
        """
        def load():
            import pandas as pd
            import yfinance as yf
            try:
                df = yf.download(list(symbols), period=period, interval="1d", auto_adjust=True,
                                 progress=False, threads=True, timeout=self.timeout)
            except Exception as exc:
                raise MarketDataError(str(exc)) from exc
            if df is None or df.empty:
                raise MarketDataError("No price data returned.")
            close = df["Close"]
            if isinstance(close, pd.Series):
                close = close.to_frame(name=symbols[0])
            close = close.sort_index().ffill()
            present = [s for s in symbols if s in close.columns and close[s].notna().any()]
            if not present:
                raise MarketDataError("No price data returned.")
            close = close[present].dropna()
            return {
                "dates": [d.strftime("%Y-%m-%d") for d in close.index],
                "series": {s: [round(float(v), 6) for v in close[s].tolist()] for s in present},
                "missing": [s for s in symbols if s not in present],
            }
        return self._cached(("closes", tuple(symbols), period), load)

    def strip(self) -> list[dict]:
        def load():
            out = []
            for sym, name in STRIP:
                try:
                    b = self.bars(sym, "1mo")
                except MarketDataError:
                    out.append({"symbol": sym, "name": name, "error": True})
                    continue
                last, prev = b[-1]["close"], b[-2]["close"] if len(b) > 1 else b[-1]["close"]
                out.append({"symbol": sym, "name": name, "last": last, "change": last - prev,
                            "change_pct": (last / prev - 1) * 100 if prev else 0.0,
                            "spark": [x["close"] for x in b]})
            return out
        return self._cached(("strip",), load)
