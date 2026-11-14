"""Run the TradingAgents team with Claude as the model, from Claude Code.

The ``/analyze`` skill (``.claude/skills/analyze``) orchestrates the run: Claude
sub-agents play the analysts, researchers, trader, risk team and portfolio
manager. This package is their toolbox:

- ``start``  opens a run folder and shows the run in the console as running.
- ``data``   serves market data through the framework's own data layer, never
             past the analysis date.
- ``record`` folds the agents' files into the console's agent view, and with
             ``--final`` writes the report and turns a Buy or Sell into a
             dry-run proposal awaiting approval.

No LLM API key is involved: the thinking happens in Claude Code.
"""
