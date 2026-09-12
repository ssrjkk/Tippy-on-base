"""PostgreSQL ledger: internal USDC balances, wallet links, history."""

import asyncio

from ._admin import LedgerAdminMixin
from ._base import (
    MICRO,
    audit_log,
    lmsr_buy_shares,
    lmsr_cost,
    lmsr_prices,
    lmsr_sell_value,
)
from ._bets import LedgerBetsMixin
from ._conn import ReconnectingConn
from ._core import LedgerCoreMixin
from ._markets import LedgerMarketsMixin
from ._messages import LedgerMessagesMixin
from ._notify import LedgerNotifyMixin
from ._onchain import LedgerOnchainMixin
from ._pay import LedgerPayMixin
from ._paywall import LedgerPaywallMixin
from ._schema import SCHEMA_DDL
from ._transfer import LedgerTransferMixin
from ._users import LedgerUsersMixin
from ._views import LedgerViewsMixin
from ._withdraw import LedgerWithdrawMixin

__all__ = [
    "MICRO",
    "SCHEMA_DDL",
    "AsyncLedger",
    "Ledger",
    "ReconnectingConn",
    "async_ledger",
    "audit_log",
    "ledger",
    "lmsr_buy_shares",
    "lmsr_cost",
    "lmsr_prices",
    "lmsr_sell_value",
]

class Ledger(LedgerCoreMixin, LedgerUsersMixin, LedgerPayMixin, LedgerPaywallMixin, LedgerTransferMixin, LedgerWithdrawMixin, LedgerBetsMixin, LedgerMarketsMixin, LedgerOnchainMixin, LedgerMessagesMixin, LedgerAdminMixin, LedgerNotifyMixin, LedgerViewsMixin):
    'Full ledger facade combining all domain mixins.'
    pass

ledger = Ledger()

class AsyncLedger:
    """Async proxy over the synchronous :class:`Ledger`.

    Every method call is dispatched to a worker thread via
    ``asyncio.to_thread`` so the aiogram event loop is never blocked by a
    synchronous ``psycopg`` query. Handlers ``await`` these calls exactly as
    if they were native coroutines.

    Non-callable attributes (e.g. ``_conn`` used by tests) pass through
    unchanged.
    """

    def __init__(self, real: "Ledger") -> None:
        # Use object.__setattr__ to avoid any __getattr__ recursion.
        object.__setattr__(self, "_real", real)

    def __getattr__(self, name: str):
        attr = getattr(object.__getattribute__(self, "_real"), name)
        if callable(attr):

            def _wrapper(*args, **kwargs):
                return asyncio.to_thread(attr, *args, **kwargs)

            return _wrapper
        return attr

async_ledger = AsyncLedger(ledger)
