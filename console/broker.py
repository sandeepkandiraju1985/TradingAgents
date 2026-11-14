"""Where approved orders go.

Only the dry-run broker exists: it records the order and returns a reference,
and nothing leaves this computer. The INDmoney (INDstocks API) adapter is a
placeholder that refuses every order, so switching brokers can never start live
trading by accident. A real adapter needs the INDstocks order endpoints checked
against their documentation, plus an explicit decision to enable live orders.
"""

from __future__ import annotations

import itertools
import time


class LiveTradingDisabled(RuntimeError):
    """Raised for any attempt to send a real order."""


class DryRunBroker:
    name = "dry-run"
    _seq = itertools.count(1)

    def place(self, order: dict) -> dict:
        return {"mode": "dry-run", "ref": f"DRY-{int(time.time())}-{next(self._seq)}"}


class INDmoneyBroker:
    name = "indmoney"

    def place(self, order: dict) -> dict:
        raise LiveTradingDisabled(
            "Live INDmoney orders are not built yet. The console only records dry-run orders."
        )


def get_broker(settings: dict) -> DryRunBroker | INDmoneyBroker:
    # live_trading cannot be switched on from the console today; this check keeps
    # the broker choice honest if a settings file is edited by hand.
    if settings.get("live_trading") is True:
        return INDmoneyBroker()
    return DryRunBroker()
