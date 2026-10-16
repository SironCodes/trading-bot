"""CanadianBroker: placeholder for a future Questrade or Interactive Brokers adapter. Not implemented."""
from bot.execution.brokers.base import Broker

TODO = "CanadianBroker is a stub; a Questrade / Interactive Brokers adapter is future work"


class CanadianBroker(Broker):
    def __init__(self, *args, **kwargs):
        raise NotImplementedError(TODO)

    def get_account(self):
        raise NotImplementedError(TODO)

    def get_positions(self):
        raise NotImplementedError(TODO)

    def get_open_orders(self):
        raise NotImplementedError(TODO)

    def get_order(self, client_order_id):
        raise NotImplementedError(TODO)

    def place_order(self, intent, client_order_id):
        raise NotImplementedError(TODO)

    def cancel_order(self, client_order_id):
        raise NotImplementedError(TODO)

    def cancel_all(self):
        raise NotImplementedError(TODO)

    def flatten_all(self, id_prefix):
        raise NotImplementedError(TODO)
