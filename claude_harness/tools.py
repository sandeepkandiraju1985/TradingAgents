"""Market data for the Claude agents, through the framework's own data layer.

Every tool takes the run's ticker and analysis date from the run folder and
serves nothing dated after it, the same guarantee the framework's tools give
its agents. Errors come back as text, so an agent can say what it couldn't get.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, date as Date, datetime, timedelta
from pathlib import Path

HELP = """Data tools: python -m claude_harness data RUN_DIR TOOL [ARGS]

  context                         who the company is: name, exchange, currency
  snapshot [DAYS=30]              verified latest price row, common indicators, recent closes
                                  (the source of truth for exact prices and indicator values)
  prices [START END]              daily OHLCV; default the 90 days to the analysis date
  indicators NAMES [DAYS=30]      comma-separated: close_50_sma, close_200_sma, close_10_ema,
                                  macd, macds, macdh, rsi, boll, boll_ub, boll_lb, atr, vwma
  volume_profile                  the volume-profile pullback setup: daily trend, 30-minute
                                  swing, POC, status, entry/stop/target
  fundamentals                    company profile and key ratios
  balance_sheet [annual|quarterly]
  cashflow [annual|quarterly]
  income_statement [annual|quarterly]
  insider                         recent insider buying and selling
  news [DAYS=7]                   news about this company
  global_news [DAYS] [LIMIT]      broader market and macro news
  macro INDICATOR [DAYS]          FRED data: cpi, core_pce, unemployment, fed_funds_rate,
                                  10y_treasury, yield_curve
  prediction TOPIC [LIMIT]        prediction-market odds, e.g. "Fed rate cut"
  social [DAYS=7]                 StockTwits and Reddit posts about the stock
"""


def meta(run_dir: str | Path) -> dict:
    path = Path(run_dir) / "meta.json"
    if not path.is_file():
        raise SystemExit(f"No run at {run_dir}: start one with `python -m claude_harness start TICKER`.")
    return json.loads(path.read_text(encoding="utf-8"))


def _back(day: str, days: int) -> str:
    return (Date.fromisoformat(day) - timedelta(days=days)).isoformat()


def _volume_profile(ticker: str, day: str) -> str:
    from console import rules, volume_profile as vp
    from console.market import YahooMarket

    market = YahooMarket()
    daily = [b for b in market.bars(ticker, "1y") if b["time"] <= day]
    # Intraday times are the exchange clock written as UTC; keep bars up to the day's close.
    intraday = [b for b in market.bars(ticker, "60d", interval="30m")
                if datetime.fromtimestamp(b["time"], UTC).date().isoformat() <= day]
    if not daily or not intraday:
        return f"No price data for {ticker} up to {day}."
    r = vp.evaluate(daily, intraday)
    cur = "₹" if rules.currency_for(ticker) == "INR" else "$"
    lines = [f"Volume-profile pullback setup for {ticker} as of {day}",
             f"- Status: {r['status']}. {r.get('message', '')}",
             f"- Daily trend: {r['trend']['reason']}"]
    if r.get("poc"):
        lines += [f"- 30-minute swing: low {cur}{r['swing_low']['price']:,.2f}, high {cur}{r['swing_high']['price']:,.2f}",
                  f"- POC (most traded price in the swing): {cur}{r['poc']:,.2f}",
                  f"- Setup stop (below the swing low): {cur}{r['stop']:,.2f}; target (swing high): {cur}{r['target']:,.2f}"]
    if r.get("entry"):
        rr = r.get("reward_risk")
        lines.append(f"- Entry: {cur}{r['entry']:,.2f}" + (f"; reward/risk {rr:.2f}" if rr else ""))
    lines.append("- Method: daily higher highs and higher lows; enter on a bullish reaction at the POC "
                 "of the latest 30-minute swing; a close below the swing low cancels the setup.")
    return "\n".join(lines)


def _social(ticker: str, day: str, days: int) -> str:
    from tradingagents.agents.post_screen import jev_screen
    from tradingagents.dataflows.vendors.reddit import fetch_reddit_posts, subreddits_for
    from tradingagents.dataflows.vendors.stocktwits import fetch_stocktwits_messages

    start = _back(day, days)
    screen = jev_screen(ticker)
    twits = fetch_stocktwits_messages(ticker, limit=30, start_date=start, end_date=day, screen=screen)
    reddit = fetch_reddit_posts(ticker, subreddits_for(ticker), start_date=start, end_date=day, screen=screen)
    return f"## StockTwits ({start} to {day})\n{twits}\n\n## Reddit ({start} to {day})\n{reddit}"


def _tools() -> dict[str, Callable[[str, str, list[str]], str]]:
    from tradingagents.agents import tools as T
    from tradingagents.agents.context import build_instrument_context, resolve_instrument_identity

    def arg(a, i, default=None, kind=str):
        return kind(a[i]) if len(a) > i else default

    return {
        "context": lambda t, d, a: build_instrument_context(t, "stock", resolve_instrument_identity(t), d),
        "snapshot": lambda t, d, a: T.get_verified_market_snapshot.func(t, d, arg(a, 0, 30, int), trade_date=d),
        "prices": lambda t, d, a: T.get_stock_data.func(t, arg(a, 0, _back(d, 90)), arg(a, 1, d), trade_date=d),
        "indicators": lambda t, d, a: T.get_indicators.func(t, arg(a, 0, "rsi"), d, arg(a, 1, 30, int), trade_date=d),
        "volume_profile": lambda t, d, a: _volume_profile(t, d),
        "fundamentals": lambda t, d, a: T.get_fundamentals.func(t, d, trade_date=d),
        "balance_sheet": lambda t, d, a: T.get_balance_sheet.func(t, arg(a, 0, "quarterly"), d, trade_date=d),
        "cashflow": lambda t, d, a: T.get_cashflow.func(t, arg(a, 0, "quarterly"), d, trade_date=d),
        "income_statement": lambda t, d, a: T.get_income_statement.func(t, arg(a, 0, "quarterly"), d, trade_date=d),
        "insider": lambda t, d, a: T.get_insider_transactions.func(t, trade_date=d),
        "news": lambda t, d, a: T.get_news.func(t, _back(d, arg(a, 0, 7, int)), d, trade_date=d),
        "global_news": lambda t, d, a: T.get_global_news.func(d, arg(a, 0, None, int), arg(a, 1, None, int), trade_date=d),
        "macro": lambda t, d, a: T.get_macro_indicators.func(arg(a, 0, "cpi"), d, arg(a, 1, 365, int), trade_date=d),
        "prediction": lambda t, d, a: T.get_prediction_markets.func(arg(a, 0, ""), arg(a, 1, None, int), trade_date=d),
        "social": lambda t, d, a: _social(t, d, arg(a, 0, 7, int)),
    }


def call(run_dir: str, tool: str, args: list[str]) -> str:
    if tool in ("help", "--help", "-h"):
        return HELP
    m = meta(run_dir)
    from tradingagents.dataflows.config import set_config
    from tradingagents.default_config import build_default_config
    set_config(build_default_config())
    tools = _tools()
    if tool not in tools:
        return f"Unknown tool {tool!r}.\n\n{HELP}"
    try:
        out = tools[tool](m["ticker"], m["date"], args)
    except Exception as exc:  # report, don't crash the agent's turn
        return f"{tool} failed: {type(exc).__name__}: {exc}"
    return out if isinstance(out, str) else str(out)
