"""`spec_desk open --place` — sending a logged plan to IB paper.

The desk's rule is "no log, no trade" (docs/spec-desk.md), and `--place` is
where that rule stops being a convention and becomes an ordering property: the
ledger's caps run first, so an order the caps would have refused can never
reach a broker. These tests pin that ordering, and pin the refusals — a desk
whose pot is *allowed to die* must never be the thing that reaches a funded
account.

No network and no ib_insync: the placement path is exercised through a stub.
"""

import argparse
from unittest.mock import MagicMock

import pytest

from tools import spec_desk


def _args(tmp_path, **over):
    base = dict(
        dir=str(tmp_path),
        lane="swing-buy",
        symbol="NVDA",
        instrument="NVDA 02OCT26 190C",
        venue="paperMoney",
        direction="long",
        qty=2,
        entry=4.0,
        multiplier=None,
        max_loss=None,
        stop=176.0,
        target=198.0,
        thesis="breakout over 182 on volume",
        place=False,
        ib_port=None,
        pot=10_000.0,
        force=True,
    )
    base.update(over)
    return argparse.Namespace(**base)


@pytest.fixture
def desk(tmp_path, capsys):
    spec_desk.cmd_init(_args(tmp_path))
    capsys.readouterr()
    return tmp_path


# --- the port guard -------------------------------------------------------


@pytest.mark.parametrize("port", sorted(spec_desk.PAPER_ONLY_PORTS))
def test_paper_ports_are_accepted(desk, port, monkeypatch, capsys):
    placed = {}

    def fake_connector(config):
        placed["port"] = config["port"]
        conn = MagicMock()
        conn.place_option_order.return_value = MagicMock(
            action="BUY",
            quantity=2,
            symbol="NVDA  261002C00190000",
            price=4.0,
            status="Submitted",
            order_id=9,
        )
        return conn

    monkeypatch.setattr(
        "pwb_toolbox.execution.create_connector", fake_connector, raising=False
    )
    spec_desk.cmd_open(_args(desk, place=True, ib_port=port))
    assert placed["port"] == port
    assert "Placed on IB paper" in capsys.readouterr().out


def test_a_funded_port_is_refused_outright(desk, capsys):
    """4001 is IB Gateway's funded-account port. This desk never goes there."""

    with pytest.raises(SystemExit) as excinfo:
        spec_desk.cmd_open(_args(desk, place=True, ib_port=4001))
    assert "not a paper port" in str(excinfo.value)


def test_the_ledger_entry_survives_a_refused_placement(desk, capsys):
    """The plan is committed even when the order is not — and says so."""

    with pytest.raises(SystemExit) as excinfo:
        spec_desk.cmd_open(_args(desk, place=True, ib_port=4001))
    assert "ledger entry stands" in str(excinfo.value)

    ledger = spec_desk.load(spec_desk.ledger_path(str(desk)))
    assert len(ledger["trades"]) == 1


def test_port_comes_from_the_environment_when_not_given(desk, monkeypatch):
    monkeypatch.setenv("PWB_IB_PORT", "4001")
    with pytest.raises(SystemExit, match="not a paper port"):
        spec_desk.cmd_open(_args(desk, place=True))


# --- what can and cannot be placed ---------------------------------------


def test_shares_cannot_be_placed_this_way(desk):
    """momentum-stock is TradingView paper, which has no public order API."""

    with pytest.raises(SystemExit) as excinfo:
        spec_desk.cmd_open(
            _args(
                desk,
                lane="momentum-stock",
                instrument="shares",
                entry=180.0,
                qty=4,
                place=True,
            )
        )
    assert "NOT PLACED" in str(excinfo.value)


# --- the ordering property ------------------------------------------------


def test_a_cap_violating_trade_never_reaches_the_broker(desk, monkeypatch):
    """The caps are the authorisation. Placement must sit behind them.

    A 10,000-pot desk caps a single trade at 10%. This one risks 40x that, so
    the ledger refuses it — and the broker must never hear about it.
    """

    connector = MagicMock()
    monkeypatch.setattr(
        "pwb_toolbox.execution.create_connector",
        lambda config: connector,
        raising=False,
    )
    with pytest.raises(SystemExit, match="REFUSED"):
        spec_desk.cmd_open(_args(desk, qty=100, entry=40.0, place=True))
    connector.place_option_order.assert_not_called()


def test_short_dte_sub_cap_also_gates_placement(desk, monkeypatch):
    """The 2.5% lottery sub-cap is the one that matters most here."""

    connector = MagicMock()
    monkeypatch.setattr(
        "pwb_toolbox.execution.create_connector",
        lambda config: connector,
        raising=False,
    )
    with pytest.raises(SystemExit, match="REFUSED"):
        spec_desk.cmd_open(_args(desk, lane="short-dte", qty=10, entry=5.0, place=True))
    connector.place_option_order.assert_not_called()


# --- direction and defaults ----------------------------------------------


def test_direction_becomes_the_sign_of_the_order(desk, monkeypatch):
    seen = {}
    conn = MagicMock()

    def capture(instrument, qty, **kwargs):
        seen["qty"] = qty
        return MagicMock(
            action="SELL",
            quantity=abs(qty),
            symbol="X",
            price=4.0,
            status="Submitted",
            order_id=1,
        )

    conn.place_option_order.side_effect = capture
    monkeypatch.setattr(
        "pwb_toolbox.execution.create_connector", lambda config: conn, raising=False
    )
    spec_desk.cmd_open(_args(desk, direction="short", place=True, ib_port=4002))
    assert seen["qty"] == -2


def test_the_logged_entry_price_becomes_the_limit(desk, monkeypatch):
    seen = {}
    conn = MagicMock()

    def capture(instrument, qty, **kwargs):
        seen.update(kwargs)
        return MagicMock(
            action="BUY",
            quantity=2,
            symbol="X",
            price=4.0,
            status="Submitted",
            order_id=1,
        )

    conn.place_option_order.side_effect = capture
    monkeypatch.setattr(
        "pwb_toolbox.execution.create_connector", lambda config: conn, raising=False
    )
    spec_desk.cmd_open(_args(desk, entry=4.20, place=True, ib_port=4002))
    assert seen["limit_price"] == pytest.approx(4.20)


def test_without_place_nothing_is_sent_anywhere(desk, monkeypatch, capsys):
    """Every other command must keep working with no broker in sight."""

    def explode(config):  # pragma: no cover - must never run
        raise AssertionError("create_connector called without --place")

    monkeypatch.setattr(
        "pwb_toolbox.execution.create_connector", explode, raising=False
    )
    spec_desk.cmd_open(_args(desk, place=False))
    out = capsys.readouterr().out
    assert "logged" in out
    assert "Placed on IB paper" not in out
