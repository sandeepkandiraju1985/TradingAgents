"""Follow one TradingAgents run agent by agent, from the graph's state stream.

The graph streams its state as agents finish. ``Progress.update`` folds each
state in, and ``snapshot`` turns it into the pipeline the page draws: every step
with its status (waiting, working, done, skipped), timings and output, the two
debates split into turns.
"""

from __future__ import annotations

import re
import time

# (key, label, state field) in the order the graph runs them. Debates have no
# single text field: their turns come from the debate state's history.
STEPS = [
    ("market", "Market analyst", "market_report"),
    ("social", "Sentiment analyst", "sentiment_report"),
    ("news", "News analyst", "news_report"),
    ("fundamentals", "Fundamentals analyst", "fundamentals_report"),
    ("research_debate", "Bull vs Bear debate", None),
    ("research_manager", "Research manager", "investment_plan"),
    ("trader", "Trader", "trader_investment_plan"),
    ("risk_debate", "Risk debate", None),
    ("portfolio_manager", "Portfolio manager", "final_trade_decision"),
]
ANALYSTS = ("market", "social", "news", "fundamentals")
_TURN = re.compile(r"(?:^|\n)(Bull|Bear|Aggressive|Conservative|Neutral) Analyst: ")


def split_turns(history: str) -> list[dict]:
    """A debate history ("\\nBull Analyst: ...\\nBear Analyst: ...") as turns."""
    if not history:
        return []
    marks = list(_TURN.finditer(history))
    turns = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(history)
        text = history[m.end():end].strip()
        if text:
            turns.append({"speaker": m.group(1), "text": text})
    return turns


class Progress:
    def __init__(self, analysts: list[str], started: float | None = None):
        self.analysts = set(analysts)
        self.started = started or time.time()
        self.texts: dict[str, str] = {}
        self.turns: dict[str, list[dict]] = {"research_debate": [], "risk_debate": []}
        self.finished: dict[str, float] = {}

    def update(self, state: dict) -> list[str]:
        """Fold one streamed state in; returns the keys of steps that just finished."""
        now = time.time()
        before = {s["key"] for s in self.snapshot()["steps"] if s["status"] == "done"}
        for key, _label, field in STEPS:
            if field and state.get(field):
                self.texts[key] = state[field]
        debate = state.get("investment_debate_state")
        if isinstance(debate, dict) and debate.get("history"):
            self.turns["research_debate"] = split_turns(debate["history"])
        risk = state.get("risk_debate_state")
        if isinstance(risk, dict) and risk.get("history"):
            self.turns["risk_debate"] = split_turns(risk["history"])
        newly = [s["key"] for s in self.snapshot()["steps"] if s["status"] == "done" and s["key"] not in before]
        for key in newly:
            self.finished.setdefault(key, now)
        return newly

    def _done(self, key: str) -> bool:
        if key == "research_debate":
            return "research_manager" in self.texts
        if key == "risk_debate":
            return "portfolio_manager" in self.texts
        return key in self.texts

    def snapshot(self) -> dict:
        steps = []
        working_found = False
        analysts_pending = any(a in self.analysts and a not in self.texts for a in ANALYSTS)
        prev_finish = self.started
        for key, label, _field in STEPS:
            step = {"key": key, "label": label}
            if key in ANALYSTS and key not in self.analysts:
                step["status"] = "skipped"
            elif self._done(key):
                step["status"] = "done"
            elif key in ANALYSTS:
                step["status"] = "working"          # the analysts run side by side
                working_found = True
            elif not working_found and not analysts_pending:
                step["status"] = "working"
                working_found = True
            else:
                step["status"] = "waiting"
            if step["status"] == "done" and key in self.finished:
                start = self.started if key in ANALYSTS else prev_finish
                step["seconds"] = round(self.finished[key] - start, 1)
                if key not in ANALYSTS:
                    prev_finish = self.finished[key]
            if key in ANALYSTS and step["status"] == "done" and key in self.finished:
                prev_finish = max(prev_finish, self.finished[key])
            if key in self.texts:
                step["text"] = self.texts[key]
            if key in self.turns:
                step["turns"] = self.turns[key]
            steps.append(step)
        current = next((s["label"] for s in steps if s["status"] == "working"), None)
        if any(s["key"] in ANALYSTS and s["status"] == "working" for s in steps):
            current = "Analysts"
        return {"steps": steps, "current": current}

    @staticmethod
    def summary(snapshot: dict | None) -> dict | None:
        """The pipeline without the texts, for lists."""
        if not snapshot:
            return None
        return {"current": snapshot.get("current"),
                "steps": [{k: v for k, v in s.items() if k not in ("text", "turns")} for s in snapshot["steps"]]}
