"""The order book.

The matching rule for this assignment is deliberately tiny: two orders match
only if they are on the same instrument, on opposite sides, and at exactly the
same price.  So instead of a sorted price ladder we just keep one FIFO queue
per (instrument, side, price) and match against the queue for the incoming
order's own price.
"""

from collections import deque


class Order:
    __slots__ = ("oid", "side", "instrument", "price", "qty", "owner", "live")

    def __init__(self, oid, side, instrument, price, qty, owner):
        self.oid = oid
        self.side = side
        self.instrument = instrument
        self.price = price
        self.qty = qty
        self.owner = owner
        self.live = True

    def __repr__(self):
        return "<Order %d %s %s %d@%d>" % (
            self.oid, self.side, self.instrument, self.qty, self.price)


class Trade:
    __slots__ = ("instrument", "qty", "price", "buy", "sell")

    def __init__(self, instrument, qty, price, buy, sell):
        self.instrument = instrument
        self.qty = qty
        self.price = price
        self.buy = buy
        self.sell = sell


class OrderBook:
    def __init__(self):
        self.next_id = 1
        self.by_id = {}
        # (instrument, side, price) -> deque of resting orders
        self.levels = {}

    def _level(self, instrument, side, price):
        key = (instrument, side, price)
        q = self.levels.get(key)
        if q is None:
            q = deque()
            self.levels[key] = q
        return q

    def submit(self, side, instrument, qty, price, owner):
        """Accept an order, match it, and return (order, [trades])."""
        order = Order(self.next_id, side, instrument, price, qty, owner)
        self.next_id += 1
        self.by_id[order.oid] = order

        other = "SELL" if side == "BUY" else "BUY"
        resting = self._level(instrument, other, price)

        trades = []
        while order.qty > 0 and resting:
            head = resting[0]
            if not head.live or head.qty == 0:
                resting.popleft()
                continue

            traded = min(order.qty, head.qty)
            order.qty -= traded
            head.qty -= traded

            if side == "BUY":
                trades.append(Trade(instrument, traded, price, order, head))
            else:
                trades.append(Trade(instrument, traded, price, head, order))

            if head.qty == 0:
                head.live = False
                resting.popleft()
                self.by_id.pop(head.oid, None)

        if order.qty > 0:
            self._level(instrument, side, price).append(order)
        else:
            order.live = False
            self.by_id.pop(order.oid, None)

        return order, trades

    def cancel(self, oid, owner):
        """Cancel an order still carrying unfilled quantity.

        Returns the order on success, or a short reason string on failure.
        """
        order = self.by_id.get(oid)
        if order is None or not order.live or order.qty == 0:
            return "no_such_order"
        if order.owner is not owner:
            return "not_your_order"

        order.live = False
        self.by_id.pop(oid, None)
        # It stays in its price queue until matching walks past it; the dead
        # entry is skipped and dropped there.
        return order

    def outstanding(self):
        return len(self.by_id)
