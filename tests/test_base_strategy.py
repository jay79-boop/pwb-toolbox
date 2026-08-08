"""Tests for `pwb_toolbox.backtesting.base_strategy`.

`is_tradable` only ever touches `data.close` and the `tradable_lookback`
parameter, so the cases below drive it with a stub line and a stub `self`
rather than standing up a Cerebro run.
"""

from types import SimpleNamespace

import pytest

from pwb_toolbox.backtesting.base_strategy import BaseStrategy, moved_every_bar


class FakeLine:
    """Stand-in for a backtrader line.

    Index 0 is the current bar and negative indices step backwards into
    history, matching backtrader's convention. Reading past the oldest bar
    raises rather than wrapping around, so a missing length guard shows up as
    an error instead of a silently wrong answer.
    """

    def __init__(self, values):
        self._values = list(values)  # oldest first; the last entry is bar 0

    def __len__(self):
        return len(self._values)

    def __getitem__(self, ago):
        if ago > 0:
            raise IndexError("lines only index backwards from the current bar")
        index = len(self._values) - 1 + ago
        if index < 0:
            raise IndexError(ago)
        return self._values[index]


class FakeData:
    def __init__(self, closes):
        self.close = FakeLine(closes)


def is_tradable(closes, lookback=None):
    """Call the method unbound, with a `self` carrying only the parameter it
    reads when `lookback` is left to default."""
    strategy = SimpleNamespace(params=SimpleNamespace(tradable_lookback=1))
    return BaseStrategy.is_tradable(strategy, FakeData(closes), lookback)


def test_moving_price_is_tradable():
    assert is_tradable([100.0, 100.0, 101.0]) is True


def test_flat_latest_bar_is_not_tradable():
    assert is_tradable([99.0, 100.0, 100.0]) is False


def test_two_bars_are_enough_to_decide():
    assert is_tradable([100.0, 101.0]) is True
    assert is_tradable([100.0, 100.0]) is False


def test_single_bar_has_no_predecessor_to_compare():
    assert is_tradable([100.0]) is False


def test_lookback_defaults_to_the_strategy_parameter():
    """The default parameter value of 1 keeps the single-comparison behavior,
    so a stale instrument clears the filter on its first differing close."""
    stale_then_moving = [100.0, 100.0, 100.0, 101.0]
    assert is_tradable(stale_then_moving) is True
    assert is_tradable(stale_then_moving, lookback=2) is False


def test_wider_lookback_requires_movement_on_every_bar_in_the_window():
    assert is_tradable([100.0, 101.0, 102.0, 103.0], lookback=3) is True
    # The stall sits at the far end of the window, so only the wider one sees it.
    assert is_tradable([100.0, 100.0, 102.0, 103.0], lookback=2) is True
    assert is_tradable([100.0, 100.0, 102.0, 103.0], lookback=3) is False


def test_window_longer_than_the_available_history_is_not_tradable():
    """Guards the line indexing: a window that outruns the feed reports False
    rather than reading past the oldest bar."""
    assert is_tradable([100.0, 101.0, 102.0], lookback=3) is False
    assert is_tradable([100.0, 101.0, 102.0], lookback=2) is True


def test_lookback_below_one_is_rejected():
    for lookback in (0, -1):
        with pytest.raises(ValueError, match="at least 1"):
            is_tradable([100.0, 101.0], lookback=lookback)


def test_helper_is_usable_without_a_strategy():
    assert moved_every_bar(FakeLine([100.0, 101.0]), 1) is True
    assert moved_every_bar(FakeLine([100.0, 100.0]), 1) is False
