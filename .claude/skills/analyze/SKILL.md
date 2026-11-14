---
name: analyze
description: Run the TradingAgents team on a stock with Claude as the agents - analysts, bull/bear debate, research manager, trader, risk debate, portfolio manager - and record the result in the console as a dry-run proposal. Use when the user asks to analyze a stock or ticker (e.g. "/analyze TCS.NS", "analyze Infosys", "run the agents on NVDA").
---

# Analyze a stock with the TradingAgents team

You are the orchestrator. Each role is a sub-agent in `.claude/agents/ta-*.md`; you start them in the right order, pass them the run folder, and record progress. Do not write the agents' reports yourself, and never approve a proposal: approval is the user's, in the console.

## 1. Find the project's Python

From the project folder, use the first that works, as `PY`:
- Windows: `.venv\Scripts\python.exe` (or `.venv/Scripts/python.exe` in a bash shell)
- macOS/Linux: `.venv/bin/python`
- otherwise `python`

Check it with `PY -c "import claude_harness, console"`. If that fails, tell the user to run `pip install .` with the project's virtual environment active, and stop.

## 2. Read the request

- **Ticker**: a Yahoo symbol. NSE stocks end in `.NS`, BSE in `.BO`, US stocks have no suffix. If the user names an Indian company without a suffix ("TCS", "Infosys"), use its NSE symbol (`TCS.NS`, `INFY.NS`). If you can't tell which listing is meant, ask.
- **Date**: the analysis date if given; otherwise leave it out (the latest weekday is used).
- **Analysts**: all four by default (`market,sentiment,news,fundamentals`). "Quick" means `market,news`. Use the user's list if they give one.
- **Rounds**: 1 per debate unless the user asks for more (maximum 3).

Several stocks: analyze them one after another.

## 3. Start the run

    PY -m claude_harness start TICKER [--date YYYY-MM-DD] [--analysts LIST] [--rounds N]

It prints JSON with `run_dir`, `date`, `analysts`, `rounds`, `context` (who the company is) and `files_in_order`. Tell the user in one line that the run has started and that the console's Agent view shows it live (if the console is open).

Every sub-agent prompt you write includes: `RUN_DIR`, `PY` (the full command), the analysis date, and the `context` text. Debaters also get the round number `N`.

## 4. Run the team

After each numbered step, run `PY -m claude_harness record RUN_DIR` so the console shows progress, and check the expected files exist. If an agent did not write its file, run it once more; if it fails again, stop and tell the user what failed.

1. **Analysts, in parallel**: start every selected analyst in a single message (one Agent call each): `ta-market-analyst` (market.md), `ta-sentiment-analyst` (sentiment.md), `ta-news-analyst` (news.md), `ta-fundamentals-analyst` (fundamentals.md). If the analysts report that the data tools fail for network reasons, stop and tell the user: the run needs internet access to Yahoo Finance and the other data sources.
2. **Bull vs Bear**: for each round N = 1..rounds: `ta-bull-researcher`, then `ta-bear-researcher`, one after the other (each reads the other's turn).
3. **Research manager**: `ta-research-manager` writes plan.md.
4. **Trader**: `ta-trader` writes trader.md.
5. **Risk debate**: for each round N: `ta-risk-aggressive`, then `ta-risk-conservative`, then `ta-risk-neutral`, one after the other.
6. **Portfolio manager**: `ta-portfolio-manager` writes decision.md, starting with `**Rating**: <one of Buy / Overweight / Hold / Underweight / Sell>`.

Between steps, give the user at most one short line of progress (e.g. "Analysts done; bull and bear are debating.").

## 5. Finish

    PY -m claude_harness record RUN_DIR --final

It sets the rating, writes the full report, and, for a Buy or Sell, creates a dry-run proposal in the console awaiting the user's approval. It prints JSON with `rating`, `proposal`, `report` and `seconds`.

Then reply to the user with:
- the **rating** and, in three to five bullets, the reasons from decision.md;
- the key levels (entry, stop, target) if the decision gives them;
- the proposal (side, quantity, price) and that it waits for their approval in the console's Approvals; or that no trade was proposed and why;
- the run time and the path of the full report.

End with one line: it is a dry-run research result, not investment advice.
