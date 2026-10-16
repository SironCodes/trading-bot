"""Broker interface. Only bot.execution.manager.OrderManager may call the order methods.

To add a real broker: subclass Broker in bot/execution/brokers/<name>.py with the constructor signature
(settings, asset_class, env, conn) and point an asset class's `broker:` at it in settings.yaml.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class BrokerOrder:
    client_order_id: str
    symbol: str
    side: str
    qty: float
    status: str  # accepted | partially_filled | filled | canceled | expired | rejected
    filled_qty: float = 0.0
    avg_price: float | None = None
    broker_order_id: str | None = None
    fills: list = field(default_factory=list)  # new (qty, price, fee, ts) the caller has not seen yet


@dataclass
class Account:
    equity: float
    cash: float  # cash available for buying without margin
    currency: str


class BrokerError(Exception):
    pass


class Rejected(BrokerError):
    """The broker refused the order: never retry."""


class RateLimited(BrokerError):
    """Nothing was submitted: safe to retry after a pause."""


class Ambiguous(BrokerError):
    """Timeout or server error: the order MAY exist. Look it up by client_order_id before retrying."""


class Broker(ABC):
    realtime = True  # False: orders wait for the next bar (SimulatedBroker)

    @abstractmethod
    def get_account(self) -> Account: ...

    @abstractmethod
    def get_positions(self) -> dict[str, tuple[float, float]]:
        """{symbol: (qty, average price)}"""

    @abstractmethod
    def get_open_orders(self) -> list[BrokerOrder]: ...

    @abstractmethod
    def get_order(self, client_order_id: str) -> BrokerOrder | None: ...

    @abstractmethod
    def place_order(self, intent, client_order_id: str) -> BrokerOrder: ...

    @abstractmethod
    def cancel_order(self, client_order_id: str) -> None: ...

    @abstractmethod
    def cancel_all(self) -> int: ...

    @abstractmethod
    def flatten_all(self, id_prefix: str) -> list[BrokerOrder]:
        """Close every position with market orders."""

    def sync(self, now) -> list[BrokerOrder]:
        """Advance the broker to `now`; returns orders whose state changed. Real brokers do this themselves."""
        return []
