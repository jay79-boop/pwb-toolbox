"""Checks on the bar-data structure engine the desk jobs read instead of a chart.

The tool exists so `premarket` and `journal` stop needing TradingView Desktop,
which is what forced their scheduled tasks to keep a desktop-bound
`LogonType`. Its numbers therefore go straight into a gameplan nobody reviews
before the open, so the bar that matters here is not "does it run" but **would
it tell me I was wrong**.

Every test below plants its own data. Nothing reads a fixture, nothing touches
the network, and nothing needs a broker or a key.

The centrepiece is the DST pair. `docs/backtesting.md` records the failure it
guards: a feed read at one flat offset is right in January and an hour out in
July, which moves the whole session window for eight months of every year and
turned an eight-year backtest from +39 points into +7. So the same session high
is planted in a winter month and a summer one, and both must be found. An
implementation that adds a constant offset passes one and fails the other --
verified by doing exactly that to the source, see the docstring on
`test_a_session_high_is_found_in_july_too`.
"""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from tools import desk_levels as dl

# ------------------------------------------------------------------ planting --


def utc_for(local, tz="America/New_York"):
    """A wall-clock time in the exchange's zone -> the naive UTC stamp for it."""
    naive = datetime.strptime(local, "%Y-%m-%d %H:%M")
    return (
        naive.replace(tzinfo=ZoneInfo(tz))
        .astimezone(ZoneInfo("UTC"))
        .replace(tzinfo=None)
    )


def flat_bars(start_utc, count, price=100.0, minutes=15):
    """`count` featureless bars. Every high/low sits within a hair of `price`.

    Deliberately dull: anything a test finds in here is something the test put
    there, so an acquittal cannot be an accident of the fixture.
    """
    index = pd.date_range(start_utc, periods=count, freq=f"{minutes}min")
    return pd.DataFrame(
        {
            "open": price,
            "high": price + 0.1,
            "low": price - 0.1,
            "close": price,
            "volume": 1000.0,
        },
        index=index,
    )


def spike(bars, at_utc, high=None, low=None):
    """Push one bar's high or low out, leaving every other bar alone."""
    bars = bars.copy()
    if high is not None:
        bars.loc[at_utc, "high"] = high
    if low is not None:
        bars.loc[at_utc, "low"] = low
    return bars


def plant_gap(bars, at_utc, direction="bullish", size=5.0):
    """Force a three-bar fair value gap centred on `at_utc`.

    The gap is between the bar before and the bar after, so `at_utc` is the
    displacement leg and the returned stamp is the third bar -- which is what
    `Gap.formed_at` records.
    """
    bars = bars.copy()
    stamps = bars.index
    i = stamps.get_indexer([pd.Timestamp(at_utc)])[0]
    before, leg, after = stamps[i - 1], stamps[i], stamps[i + 1]
    base = float(bars.loc[before, "close"])
    if direction == "bullish":
        bars.loc[before, ["high", "low"]] = [base, base - 1.0]
        bars.loc[leg, ["open", "high", "low", "close"]] = [
            base,
            base + size + 2,
            base,
            base + size + 2,
        ]
        bars.loc[after, ["high", "low"]] = [base + size + 3, base + size]
    else:
        bars.loc[before, ["high", "low"]] = [base + 1.0, base]
        bars.loc[leg, ["open", "high", "low", "close"]] = [
            base,
            base,
            base - size - 2,
            base - size - 2,
        ]
        bars.loc[after, ["high", "low"]] = [base - size, base - size - 3]
    return bars, after


# ------------------------------------------------------- the DST pair --------
#
# The one failure in docs/backtesting.md that this tool could repeat wholesale.


@pytest.mark.parametrize(
    "day,label",
    [("2026-01-14", "winter, EST, UTC-5"), ("2026-07-15", "summer, EDT, UTC-4")],
)
def test_a_session_high_is_found_in_both_offsets(day, label):
    """A spike at 02:15 New York must be the London high in January AND July.

    London runs 02:00-05:00 local. The UTC window that corresponds to is 07:00-
    10:00 in winter and 06:00-09:00 in summer. Any implementation that picks
    one offset and adds it all year searches the wrong three hours for eight
    months -- and the spike at 02:15 sits close enough to the open that a
    one-hour slip misses it entirely rather than merely clipping it.

    Convicted against a broken build: replacing the `zoneinfo` conversion in
    `session_bounds` with a constant `timedelta(hours=5)` passes the January
    case and fails July with `london high == 100.1` (the flat-bar high) instead
    of the planted 180.0.
    """
    start = utc_for(f"{day} 00:00")
    bars = flat_bars(start, 96)  # a full 24h of 15m bars
    planted = utc_for(f"{day} 02:15")
    bars = spike(bars, planted, high=180.0)

    levels = dl.session_levels(bars, datetime.strptime(day, "%Y-%m-%d").date())

    assert levels["london"] is not None, f"no London bars found at all ({label})"
    assert levels["london"]["high"] == pytest.approx(180.0), (
        f"the planted 02:15 spike is not the London high ({label}) -- the "
        "session window was built at the wrong offset"
    )


def test_the_offset_actually_differs_between_the_two_months():
    """The pair above is only a test if the two months really do differ.

    A guard on the guard: if both months resolved to the same UTC window the
    parametrised test would pass against a flat-offset implementation and prove
    nothing at all.
    """
    winter, _ = dl.session_bounds(datetime(2026, 1, 14).date(), "london")
    summer, _ = dl.session_bounds(datetime(2026, 7, 15).date(), "london")
    assert winter.hour != summer.hour, (
        "London opens at the same UTC hour in January and July, so the DST "
        "pair cannot distinguish a correct build from a flat-offset one"
    )


def test_a_session_with_no_bars_is_none_rather_than_borrowed():
    """Refuse rather than repair: an empty overnight is a real answer."""
    # Regular hours only: nothing between 20:00 and 00:00, so Asia is empty.
    start = utc_for("2026-03-11 09:30")
    bars = flat_bars(start, 26)  # 09:30 -> 16:00
    levels = dl.session_levels(bars, datetime(2026, 3, 11).date())
    assert levels["asia"] is None
    assert levels["ny_am"] is not None


# ------------------------------------------------- the day is not a calendar --


def test_the_evening_session_belongs_to_the_next_trading_day():
    """18:00 ET Sunday opens Monday's trade; it does not close Sunday's.

    A premarket run on 2026-08-29 hit the consequence of getting this wrong:
    the NQ daily bar and the hourly bars disagreed on the Wednesday and both
    were right, because the daily close was the 16:00 settlement while the
    session traded on to 17:00.
    """
    evening = utc_for("2026-03-15 18:30")  # Sunday
    morning = utc_for("2026-03-16 09:45")  # Monday
    index = pd.DatetimeIndex([evening, morning])
    days = dl.trading_days(index)
    assert (
        days.iloc[0] == datetime(2026, 3, 16).date()
    ), "Sunday evening is Monday's session"
    assert days.iloc[1] == datetime(2026, 3, 16).date()


def test_the_boundary_is_configurable_and_not_inferred():
    index = pd.DatetimeIndex([utc_for("2026-03-16 19:00")])
    assert (
        dl.trading_days(index, boundary=time(18, 0)).iloc[0]
        == datetime(2026, 3, 17).date()
    )
    assert (
        dl.trading_days(index, boundary=time(20, 0)).iloc[0]
        == datetime(2026, 3, 16).date()
    )


# ------------------------------------------------------ gaps: convict/acquit --


def test_a_planted_gap_is_found():
    bars = flat_bars(utc_for("2026-04-06 09:30"), 30)
    bars, formed = plant_gap(bars, bars.index[10], "bullish", size=5.0)
    gaps = dl.find_gaps(bars)
    assert any(
        g.direction == "bullish" and g.formed_at == formed.isoformat() for g in gaps
    ), "the planted three-bar gap was not detected"


def test_featureless_bars_yield_no_gaps():
    """The acquittal. A detector that convicts everything is not a detector."""
    bars = flat_bars(utc_for("2026-04-06 09:30"), 60)
    assert dl.find_gaps(bars) == []


def test_a_gap_price_traded_back_through_is_dropped():
    bars = flat_bars(utc_for("2026-04-06 09:30"), 40)
    bars, formed = plant_gap(bars, bars.index[10], "bullish", size=5.0)
    # Walk price back down through the whole gap on a later bar.
    bars.loc[bars.index[20], "low"] = float(bars.loc[bars.index[9], "high"]) - 1.0
    kept = dl.unmitigated(dl.find_gaps(bars), bars)
    assert all(
        g.formed_at != formed.isoformat() for g in kept
    ), "a gap price has fully traded back through is still being reported"


def test_a_gap_price_never_returned_to_is_kept():
    bars = flat_bars(utc_for("2026-04-06 09:30"), 40)
    bars, formed = plant_gap(bars, bars.index[10], "bullish", size=5.0)
    # Everything after the leg trades ABOVE the gap, so it is never filled.
    later = bars.index[12:]
    bars.loc[later, ["open", "high", "low", "close"]] = [200.0, 201.0, 199.0, 200.0]
    kept = dl.unmitigated(dl.find_gaps(bars), bars)
    assert any(g.formed_at == formed.isoformat() for g in kept)
    assert (
        next(g for g in kept if g.formed_at == formed.isoformat()).filled_fraction
        == 0.0
    )


def test_the_forming_bar_does_not_mitigate_the_gap_it_creates():
    """The third bar is part of the gap's construction, not trade against it.

    Counting it would mitigate every gap at birth, and the tool would report
    that structure never survives -- silently, and always.
    """
    bars = flat_bars(utc_for("2026-04-06 09:30"), 20)
    bars, formed = plant_gap(bars, bars.index[10], "bullish", size=5.0)
    bars = bars.loc[bars.index <= formed]  # nothing after it exists at all
    kept = dl.unmitigated(dl.find_gaps(bars), bars)
    assert [g.formed_at for g in kept] == [formed.isoformat()]


def test_the_half_filled_gap_survives_the_loose_rule_and_not_the_strict_one():
    bars = flat_bars(utc_for("2026-04-06 09:30"), 40)
    bars, formed = plant_gap(bars, bars.index[10], "bullish", size=10.0)
    planted = next(g for g in dl.find_gaps(bars) if g.formed_at == formed.isoformat())
    # Hold price above the gap after the leg, then trade back to just past its
    # midpoint exactly once. Without the first half the fixture's flat tail
    # sits below the gap and fills it completely, which is a fact about the
    # fixture rather than about the threshold.
    later = bars.index[bars.index > formed]
    bars.loc[later, ["open", "high", "low", "close"]] = [115.0, 116.0, 114.0, 115.0]
    bars.loc[bars.index[25], "low"] = planted.mid - 0.5
    # Assert on the PLANTED gap alone: the impulse leg leaves a second, genuine
    # gap behind it, and counting the whole list would test the fixture's shape.
    kept = lambda t: {
        g.formed_at for g in dl.unmitigated(dl.find_gaps(bars), bars, threshold=t)
    }
    assert formed.isoformat() in kept(
        1.0
    ), "a half-filled gap must survive the loose rule"
    assert formed.isoformat() not in kept(0.5), "and must not survive the strict one"


# ------------------------------------------------------------ order blocks --


def test_the_order_block_is_the_last_opposing_candle_before_the_leg():
    bars = flat_bars(utc_for("2026-04-06 09:30"), 30)
    # A down-close candle three bars before the impulse, and nothing else down.
    down_at = bars.index[7]
    bars.loc[down_at, ["open", "close", "high", "low"]] = [101.0, 99.0, 101.5, 98.5]
    bars, formed = plant_gap(bars, bars.index[10], "bullish", size=5.0)
    gap = next(g for g in dl.find_gaps(bars) if g.formed_at == formed.isoformat())
    block = dl.order_block(gap, bars)
    assert block is not None and block["at"] == down_at.isoformat()
    assert block["high"] == pytest.approx(101.5) and block["low"] == pytest.approx(98.5)


def test_no_opposing_candle_before_the_leg_is_reported_as_none():
    """A real outcome at the start of a series, not something to paper over."""
    bars = flat_bars(utc_for("2026-04-06 09:30"), 12)
    bars, formed = plant_gap(bars, bars.index[1], "bullish", size=5.0)
    gaps = [g for g in dl.find_gaps(bars) if g.formed_at == formed.isoformat()]
    assert gaps, "the fixture did not plant a gap near the start"
    assert dl.order_block(gaps[0], bars) is None


# ------------------------------------------------------- distance and rank --


def test_distance_is_basis_points_not_raw_points():
    """docs/backtesting.md: raw points rank by whichever quote is largest."""
    assert dl.bps(101.0, 100.0) == pytest.approx(100.0)
    assert dl.bps(20_200.0, 20_000.0) == pytest.approx(100.0)
    assert dl.bps(99.0, 100.0) == pytest.approx(-100.0)


def test_levels_are_ranked_nearest_first():
    bars = flat_bars(utc_for("2026-04-06 00:00"), 96, price=100.0)
    bars = spike(bars, utc_for("2026-04-06 03:00"), high=140.0)  # far, London
    bars = spike(bars, utc_for("2026-04-06 10:00"), high=101.0)  # near, NY am
    struct = dl.structure(bars, "TEST", now=bars.index[-1].to_pydatetime())
    ranked = dl.named_levels(struct)
    assert abs(ranked[0].bps_from_price) <= abs(ranked[-1].bps_from_price)
    assert ranked[0].side in ("above", "below", "at")


# ------------------------------------------------------ refusing to pretend --


def test_too_few_bars_raises_rather_than_guessing():
    bars = flat_bars(utc_for("2026-04-06 09:30"), 2)
    with pytest.raises(ValueError, match="not enough"):
        dl.structure(bars, "TEST")


def test_a_stale_last_bar_is_flagged_and_said_out_loud():
    bars = flat_bars(utc_for("2026-04-06 09:30"), 40)
    late = bars.index[-1].to_pydatetime() + timedelta(hours=30)
    struct = dl.structure(bars, "TEST", now=late)
    assert struct.stale is True
    assert any("stale" in note.lower() for note in struct.notes)
    assert "STALE" in dl.to_markdown(struct)


def test_a_fresh_last_bar_is_not_flagged():
    """The acquittal for staleness: a live feed must not read as broken."""
    bars = flat_bars(utc_for("2026-04-06 09:30"), 40)
    soon = bars.index[-1].to_pydatetime() + timedelta(minutes=20)
    struct = dl.structure(bars, "TEST", now=soon)
    assert struct.stale is False
    assert not any("stale" in note.lower() for note in struct.notes)
    assert "STALE" not in dl.to_markdown(struct)


def test_a_bar_stamped_in_the_future_is_named_as_a_fault():
    """Found by smoke-testing the CLI: the age simply printed as `-212 min`.

    A negative age is a clock skew or a feed whose stamps are not UTC, and
    either makes every level on the page suspect. Printing it as a negative
    number reports the symptom in a form nobody reads as a problem.
    """
    bars = flat_bars(utc_for("2026-04-06 09:30"), 40)
    early = bars.index[-1].to_pydatetime() - timedelta(hours=4)
    struct = dl.structure(bars, "TEST", now=early)
    assert any("FUTURE" in note for note in struct.notes)
    assert "FUTURE" in dl.to_markdown(struct)


def test_a_bar_labelled_by_its_open_time_is_not_called_a_fault():
    """The acquittal. A bar exists before it closes; that is not clock skew."""
    bars = flat_bars(utc_for("2026-04-06 09:30"), 40)
    just_after_open = bars.index[-1].to_pydatetime() - timedelta(minutes=10)
    struct = dl.structure(bars, "TEST", now=just_after_open)
    assert not any("FUTURE" in note for note in struct.notes)


def test_the_bar_age_is_always_reported_even_when_fresh():
    bars = flat_bars(utc_for("2026-04-06 09:30"), 40)
    struct = dl.structure(bars, "TEST", now=bars.index[-1].to_pydatetime())
    assert struct.bar_age_minutes is not None
    assert "min old" in dl.to_markdown(struct)


# --------------------------------------------------------- the offline rule --


def test_the_network_call_is_confined_to_one_function():
    """House rule: the fetch is a separate command, and only scoring is tested.

    `import yfinance` at module scope would drag a network client into every
    import of this file, including the ones these tests make.
    """
    source = (dl.__file__).replace(".pyc", ".py")
    for line in open(source, encoding="utf-8"):
        if line.startswith("import yfinance") or line.startswith("import matplotlib"):
            raise AssertionError(
                f"{line.strip()!r} is at module scope; it belongs inside the "
                "one function that needs it, so importing this module stays free"
            )


def test_reading_a_csv_keeps_naive_utc(tmp_path):
    bars = flat_bars(utc_for("2026-04-06 09:30"), 8)
    path = tmp_path / "bars.csv"
    bars.to_csv(path, index_label="time")
    back = dl.read_bars(path)
    assert back.index.tz is None
    assert list(back.index) == list(bars.index)


def test_an_aware_csv_is_converted_rather_than_relabelled(tmp_path):
    """A tz-aware file is converted to UTC. A naive one is never *guessed* at."""
    bars = flat_bars(utc_for("2026-04-06 09:30"), 8)
    aware = bars.copy()
    aware.index = bars.index.tz_localize("UTC").tz_convert("America/New_York")
    path = tmp_path / "aware.csv"
    aware.to_csv(path, index_label="time")
    back = dl.read_bars(path)
    assert back.index.tz is None
    assert list(back.index) == list(bars.index)


# ------------------------------------------------------------- the drawing --


def test_the_chart_renders_headless_to_a_file(tmp_path):
    """The whole point: a chart image with no display and no Electron app."""
    bars = flat_bars(utc_for("2026-04-06 00:00"), 96)
    bars = spike(bars, utc_for("2026-04-06 10:00"), high=105.0)
    struct = dl.structure(bars, "TEST", now=bars.index[-1].to_pydatetime())
    out = tmp_path / "shot.png"
    dl.render(bars, struct, out, marks={"entry": 100.5, "stop": 99.0})
    assert out.exists() and out.stat().st_size > 5_000, "no real image was written"
