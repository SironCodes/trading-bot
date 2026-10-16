"""BrokerRouter: one broker per asset class, built from settings. A broker that fails to start is kept
as its error, so the other asset classes keep working and the failure is reported."""
from bot.config import load_class


class BrokerRouter:
    def __init__(self, brokers: dict):
        self.brokers = brokers  # asset class -> Broker or the Exception raised while creating it

    @classmethod
    def from_settings(cls, settings, env, conn, simulated_only=False):
        brokers = {}
        for name, ac in settings.asset_classes.items():
            try:
                klass = load_class(ac.broker)
                if simulated_only and klass.realtime:
                    raise RuntimeError("--as-of replays run simulated brokers only")
                brokers[name] = klass(settings, name, env, conn)
            except Exception as e:
                brokers[name] = e
        return cls(brokers)

    def for_asset(self, asset_class):
        broker = self.brokers[asset_class]
        if isinstance(broker, Exception):
            raise broker
        return broker

    def available(self) -> dict:
        return {n: b for n, b in self.brokers.items() if not isinstance(b, Exception)}
