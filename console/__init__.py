"""TradingAgents Console: a local web app around the TradingAgents framework.

Runs analyses in the background, turns each decision into a trade proposal you
approve or reject, keeps a dry-run ledger, screens a watchlist on demand or every
hour, and closes a position automatically (dry-run) when it falls past the
stop-loss limit. Nothing here sends orders to a broker.

Start it with ``python -m console`` from the project folder.
"""
