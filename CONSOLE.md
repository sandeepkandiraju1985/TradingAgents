# TradingAgents Console

A local web app around TradingAgents. It runs on your own computer and opens in
your browser.

- **Analyze** a stock with the agents. A Buy or Sell rating becomes a proposal.
- **Approve or reject** each proposal. Approving records a dry-run order.
- **Auto-exit**: a position is sold (dry-run) once its price falls more than 5%
  below the average entry. Checked every 10 minutes while the console is open,
  and with every scan. The limit is a setting.
- **Scan** your watchlists on demand, or every hour during NSE hours
  (Mon to Fri, 9:15 to 15:30 IST). A scan reads prices only and makes no AI calls.
- **Charts**: watchlist vs benchmark performance, calendar-year returns, and a
  stock chart with candles, moving averages, volume, your trades and the
  auto-exit line.
- **Efficiency numbers** for each run: time taken, LLM calls and tokens.

**Dry-run only.** Nothing is sent to INDmoney or any other broker. The console
has no live order path, and live trading cannot be switched on from it.

## Requirements

- Python 3.11 or newer (3.13 recommended)
- A `.env` file in the project folder with your LLM key, for example:

  ```
  GOOGLE_API_KEY=your-key-here
  TRADINGAGENTS_LLM_PROVIDER=google
  TRADINGAGENTS_DEEP_THINK_LLM=gemini-3.8-flash
  TRADINGAGENTS_QUICK_THINK_LLM=gemini-3.8-flash
  ```

## Start it on Windows

Open PowerShell in the project folder (the one with `README.md` and `.env`),
then run one command at a time:

```
py -m venv .venv
.venv\Scripts\activate
pip install .
python -m console
```

Your browser opens at http://127.0.0.1:8501. Stop the console with Ctrl+C in
PowerShell.

Next time, only the last two commands are needed: `.venv\Scripts\activate`,
then `python -m console`.

After downloading a newer version of the code, run `pip install .` again before
starting it.

## Symbols

Use Yahoo Finance symbols: `INFY.NS` for NSE, `500209.BO` for BSE, `NVDA` for US
stocks. Benchmarks: `^NSEI` (NIFTY 50), `^BSESN` (SENSEX), `^GSPC` (S&P 500),
`^NDX` (NASDAQ 100).

## Where things are kept

- Console data (watchlists, proposals, orders, settings):
  `%USERPROFILE%\.tradingagents\console.db`
- Analysis reports: under `%USERPROFILE%\.tradingagents\logs`

Delete `console.db` to start the dry-run book over.

## Options

```
python -m console --port 8600       # use another port
python -m console --no-browser      # don't open the browser
python -m console --db C:\path\to\other.db
```

The console listens on 127.0.0.1 only, so other computers on your network cannot
reach it. It has no login, so don't expose it to a network.
