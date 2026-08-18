import backtrader as bt
from tqdm import tqdm


def moved_every_bar(close, lookback):
    """Return True if `close` changed on each of the last `lookback` bars.

    Needs `lookback` + 1 bars of history and reports False until it has them.
    """
    if lookback < 1:
        raise ValueError("lookback must be at least 1")
    if len(close) < lookback + 1:
        return False
    return all(close[-i] != close[-i - 1] for i in range(lookback))


class BaseStrategy(bt.Strategy):
    """Base strategy providing progress logging utilities."""

    params = (("total_days", 0), ("tradable_lookback", 1))

    def __init__(self):
        super().__init__()
        self.pbar = tqdm(total=self.params.total_days)
        self.log_data = []

    def is_tradable(self, data, lookback=None):
        """Return True if the instrument's price moved on each of the last
        `lookback` bars, defaulting to the `tradable_lookback` parameter.

        A lookback of 1 compares the current close with the previous one, which
        clears an instrument that has been stale for weeks as soon as a single
        close differs. Widening the window also rejects instruments that
        stalled earlier in it, at the cost of rejecting live instruments that
        happen to print an unchanged close.
        """
        if lookback is None:
            lookback = self.params.tradable_lookback
        return moved_every_bar(data.close, lookback)

    def next(self):
        """Update progress bar and log current value."""
        self.pbar.update(1)
        self.log_data.append(
            {
                "date": self.datas[0].datetime.date(0).isoformat(),
                "value": self.broker.getvalue(),
            }
        )

    def get_latest_positions(self):
        """Get a dictionary of the latest positions."""
        return {data._name: self.broker.getposition(data).size for data in self.datas}
