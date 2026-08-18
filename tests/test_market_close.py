"""Tests for the daily market-close script generator.

Everything here runs offline: the data reduction is exercised against frames
built in-process and the renderer against ``demo_facts``, so nothing needs
``PWB_API_KEY``, a Hugging Face login, or a live session.
"""

from datetime import date, datetime, timedelta

import pandas as pd
import pytest

from tools.market_close import free, market, script, spoken
from tools.market_close.cli import main, warn_on_digits
from tools.market_close.market import MarketFacts, Quote
from tools.market_close.script import ScriptOptions

# --------------------------------------------------------------------------
# spoken numbers
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        (0, "zero"),
        (1, "one"),
        (19, "nineteen"),
        (20, "twenty"),
        (21, "twenty-one"),
        (100, "a hundred"),
        (109, "a hundred and nine"),
        (140, "a hundred and forty"),
        (319, "three hundred and nineteen"),
        (1_140, "one thousand one hundred and forty"),
        (5_432, "five thousand four hundred and thirty-two"),
        (68_400, "sixty-eight thousand four hundred"),
        (2_026, "two thousand and twenty-six"),
        (-7, "negative seven"),
    ],
)
def test_int_to_words(value, expected):
    assert spoken.int_to_words(value) == expected


def test_hundreds_only_lead_with_the_article():
    """ "A hundred and forty" opens a number; mid-number it is "one hundred"."""
    assert spoken.int_to_words(140) == "a hundred and forty"
    assert "one hundred and forty" in spoken.int_to_words(1_140)


@pytest.mark.parametrize(
    "day,expected",
    [
        (1, "first"),
        (2, "second"),
        (3, "third"),
        (4, "fourth"),
        (11, "eleventh"),
        (12, "twelfth"),
        (13, "thirteenth"),
        (20, "twentieth"),
        (21, "twenty-first"),
        (30, "thirtieth"),
    ],
)
def test_ordinal_to_words(day, expected):
    assert spoken.ordinal_to_words(day) == expected


@pytest.mark.parametrize(
    "pct,expected",
    [
        (0.6, "six tenths of a percent"),
        (-0.6, "six tenths of a percent"),  # sign belongs to the verb
        (0.9, "nine tenths of a percent"),
        (0.1, "a tenth of a percent"),
        (0.25, "a quarter percent"),
        (0.26, "a quarter percent"),
        (0.5, "half a percent"),
        (0.75, "three quarters of a percent"),
        (0.28, "three tenths of a percent"),
        (1.0, "one percent"),
        (1.4, "one point four percent"),
        (2.5, "two and a half percent"),
        (14.0, "fourteen percent"),
        (0.001, "a fraction of a percent"),
        (0.94, "nine tenths of a percent"),
        (0.95, "one percent"),
        (0.99, "one percent"),
    ],
)
def test_say_percent_uses_desk_idiom(pct, expected):
    assert spoken.say_percent(pct) == expected


def test_say_percent_never_says_ten_tenths():
    """Regression: a clean one percent move arrives as 0.9999999999999998.

    Floating point puts it just under the whole-percent branch, where it used
    to round to ten tenths and say so out loud.
    """
    assert spoken.say_percent(-0.9999999999999998) == "one percent"
    for step in range(90, 111):
        said = spoken.say_percent(step / 100)
        assert "ten tenths" not in said, (step / 100, said)


@pytest.mark.parametrize(
    "rate,expected",
    [
        (4.09, "four-oh-nine"),
        (4.25, "four-twenty-five"),
        (4.10, "four-ten"),
        (4.00, "four percent"),
        (3.5, "three-fifty"),
    ],
)
def test_say_yield(rate, expected):
    assert spoken.say_yield(rate) == expected


def test_say_yield_rounds_into_the_next_whole_number():
    assert spoken.say_yield(4.996) == "five percent"


@pytest.mark.parametrize(
    "value,expected",
    [
        (1, "one basis point"),
        (-1, "one basis point"),
        (3, "three basis points"),
        (25, "twenty-five basis points"),
    ],
)
def test_say_basis_points(value, expected):
    assert spoken.say_basis_points(value) == expected


@pytest.mark.parametrize(
    "amount,expected",
    [
        (71.40, "seventy-one dollars and forty cents"),
        (27.00, "twenty-seven dollars"),
        (128.4, "a hundred and twenty-eight dollars and forty cents"),
        (68_400.0, "sixty-eight thousand four hundred dollars"),
        (1_012.0, "one thousand and twelve dollars"),
    ],
)
def test_say_dollars(amount, expected):
    assert spoken.say_dollars(amount) == expected


def test_say_dollars_rounds_five_figure_prices_to_the_hundred():
    """Nobody reads bitcoin to the dollar on air."""
    assert spoken.say_dollars(64_106.0) == "sixty-four thousand one hundred dollars"
    assert spoken.say_dollars(64_162.0) == "sixty-four thousand two hundred dollars"
    # Four figures stay exact — a thousand-dollar stock is quoted to the dollar.
    assert (
        spoken.say_dollars(9_999.0)
        == "nine thousand nine hundred and ninety-nine dollars"
    )


def test_say_dollars_carries_rounded_cents_into_the_dollar():
    assert spoken.say_dollars(9.999) == "ten dollars"


def test_say_points_and_ticker_and_date():
    assert spoken.say_points(140) == "a hundred and forty points"
    assert spoken.say_points(1) == "one point"
    assert spoken.say_ticker("NVDA") == "N V D A"
    assert spoken.say_date(date(2026, 8, 13)) == "Thursday, August thirteenth"


# --------------------------------------------------------------------------
# data reduction
# --------------------------------------------------------------------------


def prices(rows):
    """Build a long price frame from ``(date, symbol, close)`` triples."""
    return pd.DataFrame(rows, columns=["date", "symbol", "close"])


DAY_ONE = date(2026, 8, 12)
DAY_TWO = date(2026, 8, 13)


def test_latest_changes_pairs_the_last_two_observations():
    df = prices(
        [
            (DAY_ONE, "AAA", 100.0),
            (DAY_TWO, "AAA", 110.0),
            (DAY_ONE, "BBB", 50.0),
            (DAY_TWO, "BBB", 45.0),
        ]
    )
    changes = market.latest_changes(df).set_index("symbol")
    assert changes.loc["AAA", "close"] == 110.0
    assert changes.loc["AAA", "previous_close"] == 100.0
    assert changes.loc["BBB", "previous_close"] == 50.0


def test_latest_changes_drops_symbols_with_a_single_observation():
    """A session move needs two points; a half-known symbol is worse than none."""
    df = prices([(DAY_TWO, "NEW", 10.0), (DAY_ONE, "OLD", 5.0), (DAY_TWO, "OLD", 6.0)])
    assert set(market.latest_changes(df)["symbol"]) == {"OLD"}


def test_latest_changes_ignores_older_history():
    df = prices(
        [
            (date(2026, 1, 2), "AAA", 1.0),
            (DAY_ONE, "AAA", 100.0),
            (DAY_TWO, "AAA", 110.0),
        ]
    )
    row = market.latest_changes(df).iloc[0]
    assert row["previous_close"] == 100.0


def test_latest_changes_on_empty_frame():
    assert market.latest_changes(pd.DataFrame()).empty


def test_movers_picks_the_extremes():
    df = prices(
        [
            (DAY_ONE, "UP", 100.0),
            (DAY_TWO, "UP", 120.0),
            (DAY_ONE, "FLAT", 100.0),
            (DAY_TWO, "FLAT", 101.0),
            (DAY_ONE, "DOWN", 100.0),
            (DAY_TWO, "DOWN", 80.0),
        ]
    )
    gainer, loser = market.movers(df)
    assert gainer.symbol == "UP"
    assert loser.symbol == "DOWN"
    assert gainer.percent_change == pytest.approx(20.0)
    assert loser.percent_change == pytest.approx(-20.0)


def test_movers_skips_penny_names():
    """A forty-percent move off a two-dollar base is an artifact, not a story."""
    df = prices(
        [
            (DAY_ONE, "PENNY", 2.0),
            (DAY_TWO, "PENNY", 3.0),
            (DAY_ONE, "REAL", 100.0),
            (DAY_TWO, "REAL", 110.0),
        ]
    )
    gainer, _ = market.movers(df)
    assert gainer.symbol == "REAL"


def test_movers_on_empty_frame():
    assert market.movers(pd.DataFrame()) == (None, None)


def test_movers_names_known_companies_and_spells_the_rest():
    df = prices(
        [
            (DAY_ONE, "NVDA", 100.0),
            (DAY_TWO, "NVDA", 120.0),
            (DAY_ONE, "ZZZZ", 100.0),
            (DAY_TWO, "ZZZZ", 80.0),
        ]
    )
    gainer, loser = market.movers(df)
    assert gainer.name == "Nvidia"
    assert loser.name == "Z Z Z Z"


def test_movers_accepts_a_names_override():
    df = prices([(DAY_ONE, "ZZZZ", 100.0), (DAY_TWO, "ZZZZ", 120.0)])
    gainer, _ = market.movers(df, names={"ZZZZ": "Zed Industries"})
    assert gainer.name == "Zed Industries"


def test_breadth_counts_advancers_and_decliners():
    df = prices(
        [
            (DAY_ONE, "A", 10.0),
            (DAY_TWO, "A", 11.0),
            (DAY_ONE, "B", 10.0),
            (DAY_TWO, "B", 9.0),
            (DAY_ONE, "C", 10.0),
            (DAY_TWO, "C", 10.0),  # unchanged counts as neither
        ]
    )
    assert market.breadth(df) == (1, 1)


def test_session_date_takes_the_latest():
    df = prices([(DAY_ONE, "A", 1.0), (DAY_TWO, "A", 2.0)])
    assert market.session_date(df) == DAY_TWO
    assert market.session_date(pd.DataFrame()) is None


def test_quote_percent_change_survives_a_zero_previous_close():
    assert Quote("X", "X", 10.0, 0.0).percent_change == 0.0


@pytest.mark.parametrize(
    "changes,expected",
    [
        ([0.6, 0.9, 0.4], "up"),
        ([-0.6, -0.9], "down"),
        ([0.6, -0.9], "mixed"),
        ([0.01, -0.01], "mixed"),
    ],
)
def test_direction(changes, expected):
    facts = MarketFacts(
        session_date=DAY_TWO,
        indices=[
            Quote(f"I{i}", "idx", 100.0 + c, 100.0) for i, c in enumerate(changes)
        ],
    )
    assert facts.direction == expected


def session(advancers, decliners, index_pct=0.6):
    """A session with one index moved by ``index_pct`` and the given breadth."""
    return MarketFacts(
        session_date=DAY_TWO,
        indices=[
            Quote("SPX", market.INDEX_NAMES["SPX"], 100.0 + index_pct, 100.0),
        ],
        advancers=advancers,
        decliners=decliners,
    )


def test_narrow_breadth_needs_a_real_sample():
    assert session(1, 4).is_narrow is False
    assert session(181, 319).is_narrow is True
    assert session(300, 200).is_narrow is False


@pytest.mark.parametrize(
    "advancers,decliners,index_pct,expected",
    [
        (1, 4, 0.6, None),  # too small a sample to characterise
        (19, 0, 0.6, None),
        (13, 27, 0.6, "narrow"),  # index up, most names down
        (13, 27, -0.6, "declining"),  # index down, most names down
        (27, 13, 0.6, "advancing"),  # index up, most names up
        (27, 13, -0.6, "divergent"),  # index down, most names up
        (20, 20, 0.6, "even"),  # neither claim is true at a coin flip
        (24, 20, -0.6, "even"),
    ],
)
def test_breadth_state_reads_counts_against_the_index(
    advancers, decliners, index_pct, expected
):
    assert session(advancers, decliners, index_pct).breadth_state == expected


def test_a_broad_decline_is_not_called_a_narrow_advance():
    """Regression: 13 up / 27 down on a DOWN day is not a narrow advance.

    The counts alone said "narrow", so a session where every index fell was
    getting "The advance was narrow" — and one line in that bank says "on a day
    the index finished higher" outright.
    """
    text = script.tape(session(13, 27, index_pct=-0.6))
    assert any(joke in text for joke in script.BREADTH_DECLINING)
    assert not any(joke in text for joke in script.BREADTH_NARROW)
    assert "advance was narrow" not in text
    assert "finished higher" not in text


def test_a_narrow_advance_still_reads_as_one():
    text = script.tape(session(13, 27, index_pct=0.6))
    assert any(joke in text for joke in script.BREADTH_NARROW)
    assert not any(joke in text for joke in script.BREADTH_DECLINING)


def test_an_index_falling_while_names_rise_is_called_out():
    text = script.tape(session(27, 13, index_pct=-0.6))
    assert any(joke in text for joke in script.BREADTH_DIVERGENT)
    assert not any(joke in text for joke in script.BREADTH_BROAD)


def test_an_even_split_is_not_called_broad_based():
    """20 up / 20 down must not claim "most names participated"."""
    text = script.tape(session(20, 20))
    assert any(joke in text for joke in script.BREADTH_EVEN)
    assert not any(joke in text for joke in script.BREADTH_BROAD)
    assert not any(joke in text for joke in script.BREADTH_NARROW)


def test_every_breadth_state_has_a_bank():
    states = {"narrow", "advancing", "declining", "divergent", "even"}
    assert set(script.BREADTH_BANKS) == states
    assert all(script.BREADTH_BANKS[state] for state in states)


def test_tape_omits_the_advance_decline_tally_by_default():
    """The breadth line already says which way it leaned; the tally is recitation."""
    text = script.tape(market.demo_facts())
    assert "names rose," not in text
    # The interpretation survives the cut — only the raw numbers went.
    assert any(line in text for line in script.BREADTH_BANKS["narrow"])


def test_counts_restores_the_tally():
    text = script.tape(market.demo_facts(), counts=True)
    assert "names rose," in text


def test_full_render_carries_the_tally_and_the_default_does_not():
    assert "names rose," not in script.render(market.demo_facts())
    assert "names rose," in script.render(market.demo_facts(), full=True)


def test_every_breadth_line_stands_without_the_counts():
    """With the tally gone each line has to open a sentence on its own."""
    for bank in script.BREADTH_BANKS.values():
        for line in bank:
            assert line[0].isupper() or line.startswith("["), line


def test_thin_breadth_drops_the_line_rather_than_guessing():
    facts = session(3, 2)
    text = script.tape(facts)
    assert "names\nrose" not in text and "names rose" not in text
    for bank in (script.BREADTH_NARROW, script.BREADTH_EVEN, script.BREADTH_BROAD):
        assert not any(joke in text for joke in bank)
    # The index clauses still stand.
    assert "S and P five hundred" in text


# --------------------------------------------------------------------------
# noise vs news
# --------------------------------------------------------------------------


def history(symbol="SPY", daily_pct=0.5, days=24, today_pct=-0.5):
    """Alternating moves of ``daily_pct``, then one final move of ``today_pct``.

    Gives a baseline of almost exactly ``daily_pct``, so the ratio a test is
    asserting on is ``today_pct / daily_pct``.
    """
    rows, price, start = [], 100.0, date(2026, 7, 10)
    for step in range(days):
        price *= 1 + (daily_pct / 100 if step % 2 else -daily_pct / 100)
        rows.append((start + timedelta(days=step), symbol, price))
    rows.append((start + timedelta(days=days), symbol, price * (1 + today_pct / 100)))
    return pd.DataFrame(rows, columns=["date", "symbol", "close"])


def test_typical_moves_measures_a_normal_day():
    assert market.typical_moves(history(daily_pct=0.5))["SPY"] == pytest.approx(
        0.5, abs=0.02
    )


def test_typical_moves_excludes_today_from_its_own_baseline():
    """A big day must not inflate the average it is measured against.

    Without this, the sessions most worth flagging are the ones the show would
    most understate.
    """
    calm = market.typical_moves(history(today_pct=-0.5))["SPY"]
    wild = market.typical_moves(history(today_pct=-8.0))["SPY"]
    assert wild == pytest.approx(calm, abs=1e-9)


def test_typical_moves_needs_enough_history():
    assert market.typical_moves(history(days=4)) == {}
    assert "SPY" in market.typical_moves(history(days=24))


def test_typical_moves_on_empty_frame():
    assert market.typical_moves(pd.DataFrame()) == {}


@pytest.mark.parametrize(
    "today_pct,expected",
    [
        (-0.15, "quiet"),
        (0.2, "quiet"),
        (-0.5, "ordinary"),
        (0.7, "ordinary"),
        (-1.0, "notable"),
        (-2.2, "big"),
        (5.0, "big"),
    ],
)
def test_move_scale(today_pct, expected):
    quote = market.index_quotes(history(today_pct=today_pct), ["SPY"])[0]
    assert quote.move_scale == expected


def test_move_scale_is_none_without_a_baseline():
    quote = Quote("SPY", "the index", 100.0, 101.0)
    assert quote.typical_move is None
    assert quote.move_ratio is None
    assert quote.move_scale is None


def test_move_scale_survives_a_zero_baseline():
    assert Quote("SPY", "x", 100.0, 101.0, typical_move=0.0).move_scale is None


@pytest.mark.parametrize(
    "ratio,expected",
    [
        (0.9, "about the same as"),
        (1.4, "about the same as"),
        (2.0, "about twice"),
        (2.4, "about twice"),
        (3.2, "about three times"),
        (8.0, "about eight times"),
    ],
)
def test_say_multiple_stays_coarse(ratio, expected):
    """A decimal would imply precision the baseline doesn't have."""
    assert spoken.say_multiple(ratio) == expected


def test_scale_line_is_none_without_a_baseline():
    assert script.scale_line(Quote("SPY", "x", 100.0, 101.0), DAY_TWO) is None


def test_scale_line_capitalizes_a_sentence_initial_multiple():
    """Regression: "That is a real move. [pause] about four times…"."""
    quote = market.index_quotes(history(today_pct=-2.2), ["SPY"])[0]
    for day in range(1, 15):
        line = script.scale_line(quote, date(2026, 8, day))
        assert "] about " not in line, line


def test_no_placeholder_survives_substitution():
    for pct in (-0.15, -0.5, -1.0, -2.2, -6.0):
        quote = market.index_quotes(history(today_pct=pct), ["SPY"])[0]
        for day in range(1, 8):
            line = script.scale_line(quote, date(2026, 8, day))
            assert "{" not in line and "}" not in line, line


def test_scale_lines_keep_the_no_digits_invariant():
    for pct in (-0.15, -0.5, -1.0, -2.2, -12.0):
        quote = market.index_quotes(history(today_pct=pct), ["SPY"])[0]
        for day in range(1, 8):
            line = script.scale_line(quote, date(2026, 8, day))
            assert not any(c.isdigit() for c in line), line


def test_the_ordinary_band_never_waves_the_move_away():
    """ "Typical" and "nothing" are the same only in a calm month.

    A one percent drop can be perfectly average for a volatile market and still
    be the most interesting fact available. The band means "matched what this
    market has been doing lately" — it must not be read as "no story".
    """
    for line in script.SCALE_ORDINARY:
        for dismissal in (
            "Not a story",
            "least reportable",
            "boringly ordinary",
            "nothing",
        ):
            assert dismissal not in line, line


def test_the_ordinary_band_can_state_the_measured_baseline():
    """At least one line reports what average actually is right now."""
    assert any(
        "{typical}" in line or "{Typical}" in line for line in script.SCALE_ORDINARY
    )


def test_the_baseline_substitutes_into_the_ordinary_lines():
    quote = market.index_quotes(history(daily_pct=0.9, today_pct=-1.0), ["SPY"])[0]
    assert quote.move_scale == "ordinary"
    seen = {script.scale_line(quote, date(2026, 8, day)) for day in range(1, 20)}
    assert any("nine tenths of a percent" in line for line in seen)
    for line in seen:
        assert "{" not in line and "}" not in line, line
        assert not any(c.isdigit() for c in line), line


def test_every_scale_band_has_a_bank():
    assert set(script.SCALE_BANKS) == {"quiet", "ordinary", "notable", "big"}
    assert all(script.SCALE_BANKS[band] for band in script.SCALE_BANKS)


def test_tape_carries_the_scale_line():
    quotes = market.index_quotes(history(today_pct=-2.2), ["SPY"])
    quotes[0].name = market.INDEX_NAMES["SPX"]
    facts = MarketFacts(
        session_date=DAY_TWO, indices=quotes, advancers=13, decliners=27
    )
    text = script.tape(facts)
    assert script.scale_line(quotes[0], DAY_TWO) in text


def test_tape_omits_the_scale_line_without_a_baseline():
    facts = session(13, 27)
    assert facts.indices[0].move_scale is None
    for marker in (
        "wearing a headline",
        "boringly ordinary",
        "what this market does on",
    ):
        assert marker not in script.tape(facts)


# --------------------------------------------------------------------------
# open sessions
# --------------------------------------------------------------------------


def eastern(y, m, d, hour):
    from zoneinfo import ZoneInfo

    return datetime(y, m, d, hour, tzinfo=ZoneInfo(market.MARKET_TIMEZONE))


@pytest.mark.parametrize(
    "latest,now,expected",
    [
        (date(2026, 8, 18), eastern(2026, 8, 18, 11), True),  # mid-session
        (date(2026, 8, 18), eastern(2026, 8, 18, 9), True),  # just after the open
        (date(2026, 8, 18), eastern(2026, 8, 18, 16), False),  # the bell
        (date(2026, 8, 18), eastern(2026, 8, 18, 21), False),  # evening
        (date(2026, 8, 17), eastern(2026, 8, 18, 11), False),  # yesterday's bar
        (date(2026, 8, 14), eastern(2026, 8, 17, 11), False),  # over a weekend
    ],
)
def test_session_is_open(latest, now, expected):
    assert market.session_is_open(latest, now) is expected


def test_an_open_session_never_claims_a_close():
    """ "Closed down one percent" is false at eleven in the morning."""
    facts = market.demo_facts()
    facts.session_open = True
    text = script.render(facts, full=True)
    for claim in ("closed up", "closed down", "settled at", "closing at", "finished"):
        assert claim not in text, claim


def test_a_finished_session_still_speaks_in_the_past():
    text = script.render(market.demo_facts(), full=True)
    assert "closed up" in text
    assert "is up six tenths" not in text


@pytest.mark.parametrize(
    "segment,live_marker,past_marker",
    [
        ("tape", "is up six tenths", "closed up six tenths"),
        ("movers", "is down twenty-two", "finished down twenty-two"),
        ("rates", "has eased three basis points", "eased three basis points"),
        ("commodities", "Crude is at", "Crude settled at"),
    ],
)
def test_each_segment_switches_tense(segment, live_marker, past_marker):
    closed, live = market.demo_facts(), market.demo_facts()
    live.session_open = True
    render = getattr(script, segment)
    assert past_marker in render(closed)
    assert live_marker in render(live)


def test_a_flat_index_switches_tense_too():
    facts = MarketFacts(
        session_date=DAY_TWO,
        indices=[Quote("SPX", market.INDEX_NAMES["SPX"], 100.001, 100.0)],
        session_open=True,
    )
    assert "is essentially flat" in script.tape(facts)


def test_an_open_session_keeps_the_no_digits_invariant():
    facts = market.demo_facts()
    facts.session_open = True
    assert not any(c.isdigit() for c in script.render(facts, full=True))


def test_cli_notes_an_open_session(monkeypatch, capsys):
    real = market.demo_facts

    def open_facts(session=None):
        facts = real(session)
        facts.session_open = True
        return facts

    monkeypatch.setattr(market, "demo_facts", open_facts)
    assert main(["--demo"]) == 0
    assert "the market is still open" in capsys.readouterr().err


# --------------------------------------------------------------------------
# rotation
# --------------------------------------------------------------------------


def test_pick_is_stable_for_a_given_date():
    bank = ["a", "b", "c", "d"]
    assert script.pick(bank, DAY_TWO, "salt") == script.pick(bank, DAY_TWO, "salt")


def test_pick_varies_across_dates_and_salts():
    bank = [str(n) for n in range(40)]
    days = [date(2026, 8, day) for day in range(1, 15)]
    assert len({script.pick(bank, day, "cold-open") for day in days}) > 1
    assert script.pick(bank, DAY_TWO, "gainer") != script.pick(bank, DAY_TWO, "loser")


def test_pick_rejects_an_empty_bank():
    with pytest.raises(ValueError):
        script.pick([], DAY_TWO, "salt")


def test_a_working_week_does_not_repeat_the_cold_open():
    week = [date(2026, 8, day) for day in range(10, 15)]
    lines = {script.pick(script.COLD_OPEN["up"], day, "cold-open") for day in week}
    assert len(lines) >= 3


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------


def test_render_is_five_segments_by_default():
    """Rates, crude and crypto sit behind --full: that density is the overload."""
    text = script.render(market.demo_facts())
    for header in ("[COLD OPEN]", "[THE TAPE]", "[MOVERS]", "[STRAIGHT]", "[SIGN-OFF]"):
        assert header in text
    assert "[RATES]" not in text
    assert "[COMMODITIES]" not in text


def test_full_adds_the_dense_segments():
    text = script.render(market.demo_facts(), full=True)
    assert "[RATES]" in text
    assert "[COMMODITIES]" in text


def test_rendered_script_contains_no_digits():
    """The production invariant: ElevenLabs must never see a numeral.

    Every figure is spelled by ``spoken.py`` before it reaches the script, so
    a digit surviving to the output is a formatting path somebody forgot.
    """
    text = script.render(market.demo_facts())
    offenders = [line for line in text.splitlines() if any(c.isdigit() for c in line)]
    assert offenders == []


def test_no_digits_across_a_sweep_of_sessions():
    """Same invariant, over values that exercise each numeric branch."""
    for step, pct in enumerate([-12.5, -1.0, -0.25, 0.0, 0.1, 0.75, 3.33, 21.0]):
        for yield_rate in (0.5, 3.07, 4.0, 4.25, 11.9):
            facts = MarketFacts(
                session_date=date(2026, 8, 1 + step),
                indices=[
                    Quote("SPX", market.INDEX_NAMES["SPX"], 100.0 + pct, 100.0),
                    Quote("INDU", market.INDEX_NAMES["INDU"], 39_000.0 + pct, 39_000.0),
                ],
                gainer=Quote("NVDA", "Nvidia", 100.0 + abs(pct), 100.0),
                loser=Quote("PFE", "Pfizer", 100.0 - abs(pct), 100.0),
                advancers=181,
                decliners=319,
                rate=Quote("US10Y", "the ten-year", yield_rate, yield_rate + 0.03),
                crude=Quote("CL1", "crude", 71.4 + pct, 71.4),
                crypto=Quote("BTC", "Bitcoin", 68_400.0, 65_100.0),
            )
            text = script.render(facts)
            assert not any(c.isdigit() for c in text), text


def test_the_straight_beat_never_rotates():
    """The disclaimer is fixed on purpose — see the module docstring."""
    for day in range(10, 15):
        text = script.render(market.demo_facts(date(2026, 8, day)))
        assert script.STRAIGHT_BEAT in text
        assert "None of this is advice." in text


def test_the_straight_beat_does_not_announce_its_own_importance():
    """ "Because this actually matters" is the tell of copy trying to sound sincere."""
    for phrase in ("actually matters", "really matters", "this is important"):
        assert phrase not in script.STRAIGHT_BEAT


def test_segments_without_data_are_dropped_not_faked():
    text = script.render(MarketFacts(session_date=DAY_TWO), full=True)
    for absent in ("[THE TAPE]", "[MOVERS]", "[RATES]", "[COMMODITIES]"):
        assert absent not in text
    # The three that never depend on market data still stand.
    for present in ("[COLD OPEN]", "[STRAIGHT]", "[SIGN-OFF]"):
        assert present in text


def test_index_units_are_stated_once_then_dropped():
    """An anchor says "six tenths of a percent" then just "nine tenths"."""
    text = script.tape(market.demo_facts())
    assert "closed up six tenths of a percent" in text
    assert "The Nasdaq gained nine tenths." in text


def test_the_dow_is_quoted_in_points():
    assert "The Dow added a hundred and forty points" in script.tape(
        market.demo_facts()
    )


def test_tape_capitalizes_the_opening_index_name():
    assert script.tape(market.demo_facts()).count("The S and P five hundred") == 1


def test_flat_indices_are_described_not_quoted():
    facts = MarketFacts(
        session_date=DAY_TWO,
        indices=[Quote("SPX", market.INDEX_NAMES["SPX"], 100.001, 100.0)],
    )
    assert "finished essentially flat" in script.tape(facts)


def test_rate_joke_matches_the_size_of_the_move():
    """ "A move of approximately nothing" is wrong on twenty basis points."""
    quiet = MarketFacts(
        session_date=DAY_TWO, rate=Quote("US10Y", "the ten-year", 4.09, 4.12)
    )
    loud = MarketFacts(
        session_date=DAY_TWO, rate=Quote("US10Y", "the ten-year", 4.09, 4.34)
    )
    assert script.rates(quiet) is not None
    assert any(joke in script.rates(quiet) for joke in script.RATE_JOKES_SMALL)
    assert any(joke in script.rates(loud) for joke in script.RATE_JOKES_LARGE)


def test_rates_reads_to_the_level_but_at_it_when_unchanged():
    moved = MarketFacts(DAY_TWO, rate=Quote("US10Y", "the ten-year", 4.09, 4.12))
    still = MarketFacts(DAY_TWO, rate=Quote("US10Y", "the ten-year", 4.09, 4.0901))
    assert "to four-oh-nine" in script.rates(moved)
    assert "was effectively unchanged, at four-oh-nine" in script.rates(still)


def test_movers_names_one_stock_by_default():
    """A gainer and a loser every night is a format, not a reason."""
    text = script.movers(market.demo_facts())
    assert text.count("\n\n") == 1  # one block: line, then its joke


def test_movers_leads_with_the_larger_move():
    facts = market.demo_facts()
    # demo: Nvidia +14%, Pfizer -22% — the decline is the story.
    assert "Pfizer" in script.movers(facts)
    assert "Nvidia" not in script.movers(facts)


def test_movers_leads_with_the_gainer_when_it_is_larger():
    facts = market.demo_facts()
    facts.gainer = Quote("NVDA", "Nvidia", 150.0, 100.0)
    facts.loser = Quote("PFE", "Pfizer", 95.0, 100.0)
    assert "Nvidia" in script.movers(facts)
    assert "Pfizer" not in script.movers(facts)


def test_a_lone_decline_does_not_open_mid_thought():
    """ "Going the other way" needs something to be the other way from."""
    facts = MarketFacts(session_date=DAY_TWO, loser=Quote("PFE", "Pfizer", 78.0, 100.0))
    text = script.movers(facts)
    assert "Going the other way" not in text
    assert "The biggest move today went the wrong way." in text


def test_movers_both_restores_the_second_name():
    text = script.movers(market.demo_facts(), both=True)
    assert "Nvidia" in text
    assert "Pfizer" in text
    assert "Going the other way" in text


def test_full_render_carries_two_movers_and_the_default_one():
    assert "Nvidia" not in script.render(market.demo_facts())
    assert "Nvidia" in script.render(market.demo_facts(), full=True)


def test_one_mover_keeps_the_no_digits_invariant():
    for both in (False, True):
        text = script.movers(market.demo_facts(), both=both)
        assert not any(c.isdigit() for c in text)


def test_movers_segment_never_asserts_a_cause():
    """The generator has prices, not press releases. See the module docstring."""
    text = script.movers(market.demo_facts())
    for invented in ("after the company", "beat expectations", "following", "because"):
        assert invented not in text


def test_options_carry_through_to_the_script():
    text = script.render(
        market.demo_facts(),
        ScriptOptions(anchor="Robin Vale", show="the Closing Bell", kicker="A parrot."),
    )
    assert "I'm Robin Vale" in text
    assert "A parrot." in text


def test_the_story_opens_the_show_rather_than_closing_it():
    """It leads because it is the only part a stranger cares about at ten seconds."""
    text = script.render(
        market.demo_facts(), ScriptOptions(kicker="I argued with a computer.")
    )
    first, _ = script.split_segments(text)[0]
    assert first == "cold-open"
    assert text.index("I argued with a computer.") < text.index("[THE TAPE]")
    assert "[KICKER]" not in text


def test_the_opener_falls_back_when_no_story_is_supplied():
    """An unattended run still has to produce something sayable."""
    text = script.render(market.demo_facts())
    assert "[COLD OPEN]" in text
    assert any(line in text for bank in script.COLD_OPEN.values() for line in bank)


def test_split_segments_round_trips_the_render():
    pairs = script.split_segments(script.render(market.demo_facts()))
    names = [name for name, _ in pairs]
    assert names == ["cold-open", "the-tape", "movers", "straight", "sign-off"]
    assert all(body for _, body in pairs)
    assert not any(body.startswith("[COLD OPEN]") for _, body in pairs)


# --------------------------------------------------------------------------
# preview
# --------------------------------------------------------------------------


def test_preview_carries_the_tape_and_movers_only():
    text = script.preview(market.demo_facts())
    assert "[THE TAPE]" in text
    assert "[MOVERS]" in text
    for dropped in ("[COLD OPEN]", "[RATES]", "[COMMODITIES]", "[KICKER]"):
        assert dropped not in text
    assert "[THE STRAIGHT BEAT]" not in text
    assert "[SIGN-OFF]" not in text


def test_preview_matches_the_full_render_word_for_word():
    """A preview that read differently from the broadcast would be useless."""
    facts = market.demo_facts()
    text = script.preview(facts)
    full = script.render(facts)
    for _, body in script.split_segments(text):
        assert body in full


def test_preview_keeps_the_no_digits_invariant():
    text = script.preview(market.demo_facts())
    assert not any(c.isdigit() for c in text)


def test_preview_is_empty_without_market_data():
    assert script.preview(MarketFacts(session_date=DAY_TWO)) == ""


def test_preview_survives_one_missing_segment():
    facts = MarketFacts(
        session_date=DAY_TWO,
        indices=[Quote("SPX", market.INDEX_NAMES["SPX"], 100.6, 100.0)],
    )
    text = script.preview(facts)
    assert "[THE TAPE]" in text
    assert "[MOVERS]" not in text


# --------------------------------------------------------------------------
# cli
# --------------------------------------------------------------------------


def test_warn_on_digits_flags_a_hand_written_kicker():
    assert warn_on_digits("all spelled out") == []
    assert warn_on_digits("up 4.09 percent") == ["up 4.09 percent"]


def test_cli_demo_writes_a_script(tmp_path, capsys):
    out = tmp_path / "close.txt"
    assert main(["--demo", "--out", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert "[COLD OPEN]" in text
    assert "Toadchu Y'all" in text


def test_cli_demo_prints_to_stdout(capsys):
    assert main(["--demo"]) == 0
    assert "[STRAIGHT]" in capsys.readouterr().out


def test_cli_writes_numbered_segment_files(tmp_path):
    target = tmp_path / "render"
    assert main(["--demo", "--segments", str(target)]) == 0
    assert sorted(p.name for p in target.iterdir()) == [
        "01-cold-open.txt",
        "02-the-tape.txt",
        "03-movers.txt",
        "04-straight.txt",
        "05-sign-off.txt",
    ]
    assert "[COLD OPEN]" not in (target / "01-cold-open.txt").read_text()


def test_cli_full_writes_seven_segments(tmp_path):
    target = tmp_path / "render"
    assert main(["--demo", "--full", "--segments", str(target)]) == 0
    assert len(list(target.iterdir())) == 7


def test_cli_notes_a_missing_story(capsys):
    assert main(["--demo"]) == 0
    assert "no --kicker-file" in capsys.readouterr().err


def test_cli_reads_a_kicker_file(tmp_path):
    kicker = tmp_path / "kicker.txt"
    kicker.write_text("A hedge fund sat in cash.", encoding="utf-8")
    out = tmp_path / "close.txt"
    assert main(["--demo", "--kicker-file", str(kicker), "--out", str(out)]) == 0
    assert "A hedge fund sat in cash." in out.read_text(encoding="utf-8")


def test_cli_warns_when_a_kicker_smuggles_in_digits(tmp_path, capsys):
    kicker = tmp_path / "kicker.txt"
    kicker.write_text("The fund returned 11 percent.", encoding="utf-8")
    assert main(["--demo", "--kicker-file", str(kicker)]) == 0
    assert "digits left in the script" in capsys.readouterr().err


def test_cli_merges_a_names_file_over_the_builtins(tmp_path):
    names = tmp_path / "names.json"
    names.write_text('{"ZZZZ": "Zed Industries"}', encoding="utf-8")
    out = tmp_path / "close.txt"
    # --demo bypasses the loader, so this exercises parsing and the merge only.
    assert main(["--demo", "--names", str(names), "--out", str(out)]) == 0
    assert out.exists()


def test_cli_date_overrides_the_rotation_seed(tmp_path):
    first = tmp_path / "a.txt"
    second = tmp_path / "b.txt"
    main(["--demo", "--date", "2026-08-10", "--out", str(first)])
    main(["--demo", "--date", "2026-08-13", "--out", str(second)])
    assert first.read_text() != second.read_text()


def test_cli_rejects_a_malformed_date():
    with pytest.raises(SystemExit):
        main(["--demo", "--date", "13/08/2026"])


def test_cli_preview_prints_only_the_two_segments(capsys):
    assert main(["--demo", "--preview"]) == 0
    out = capsys.readouterr().out
    assert "[THE TAPE]" in out
    assert "[MOVERS]" in out
    assert "[SIGN-OFF]" not in out
    assert "[THE STRAIGHT BEAT]" not in out


def test_cli_preview_writes_to_out(tmp_path):
    out = tmp_path / "preview.txt"
    assert main(["--demo", "--preview", "--out", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert "[THE TAPE]" in text
    assert "[KICKER]" not in text


def test_cli_preview_ignores_a_kicker(tmp_path, capsys):
    kicker = tmp_path / "kicker.txt"
    kicker.write_text("A parrot with a lawyer.", encoding="utf-8")
    assert main(["--demo", "--preview", "--kicker-file", str(kicker)]) == 0
    assert "A parrot with a lawyer." not in capsys.readouterr().out


def test_cli_preview_writes_two_numbered_segments(tmp_path):
    target = tmp_path / "render"
    assert main(["--demo", "--preview", "--segments", str(target)]) == 0
    assert sorted(p.name for p in target.iterdir()) == [
        "01-the-tape.txt",
        "02-movers.txt",
    ]


def test_cli_preview_reports_when_there_is_nothing_to_show(monkeypatch, capsys):
    monkeypatch.setattr(market, "demo_facts", lambda session=None: MarketFacts(DAY_TWO))
    assert main(["--demo", "--preview"]) == 1
    assert "no tape or movers data to preview" in capsys.readouterr().err


# --------------------------------------------------------------------------
# free mode (yfinance)
# --------------------------------------------------------------------------

# Values the fake Yahoo returns, as (previous_close, close).
FAKE_QUOTES = {
    "SPY": (540.0, 543.24),
    "QQQ": (470.0, 474.23),
    "DIA": (390.0, 391.40),
    "^TNX": (4.12, 4.09),
    "CL=F": (72.05, 71.40),
    "BTC-USD": (65_100.0, 68_400.0),
}


def fake_download(symbols, period):
    """Stand in for ``yfinance.download``, matching its column shape."""
    index = pd.to_datetime(["2026-08-12", "2026-08-13"])
    columns, data = [], []
    for position, symbol in enumerate(symbols):
        # Universe names get a spread of moves straddling zero without ever
        # landing exactly flat — an unchanged close counts as neither an
        # advancer nor a decliner, which would skew the breadth assertions.
        default = (100.0, 100.0 + (position - 19.5))
        previous, close = FAKE_QUOTES.get(symbol, default)
        for field, values in (
            ("Close", [previous, close]),
            ("Open", [previous, previous]),
        ):
            columns.append((field, symbol))
            data.append(values)
    frame = pd.DataFrame(
        dict(zip(range(len(columns)), data)),
        index=index,
    )
    frame.columns = pd.MultiIndex.from_tuples(columns)
    frame.index.name = "Date"
    return frame


def test_to_long_frame_handles_multi_symbol_downloads():
    raw = fake_download(["SPY", "QQQ"], "1mo")
    frame = free.to_long_frame(raw, ["SPY", "QQQ"])
    assert list(frame.columns) == ["date", "symbol", "close"]
    assert set(frame["symbol"]) == {"SPY", "QQQ"}
    assert len(frame) == 4


def test_to_long_frame_handles_a_single_symbol_flat_download():
    """One ticker comes back with flat columns, not a MultiIndex."""
    index = pd.to_datetime(["2026-08-12", "2026-08-13"])
    raw = pd.DataFrame({"Close": [540.0, 543.24], "Open": [1.0, 2.0]}, index=index)
    raw.index.name = "Date"
    frame = free.to_long_frame(raw, ["SPY"])
    assert set(frame["symbol"]) == {"SPY"}
    assert frame["close"].tolist() == [540.0, 543.24]


def test_to_long_frame_survives_an_unnamed_index():
    raw = pd.DataFrame(
        {"Close": [1.0, 2.0]}, index=pd.to_datetime(["2026-08-12", "2026-08-13"])
    )
    assert len(free.to_long_frame(raw, ["SPY"])) == 2


def test_to_long_frame_on_empty_and_malformed_downloads():
    assert free.to_long_frame(pd.DataFrame(), ["SPY"]).empty
    assert free.to_long_frame(None, ["SPY"]).empty
    no_close = pd.DataFrame({"Open": [1.0]}, index=pd.to_datetime(["2026-08-13"]))
    assert free.to_long_frame(no_close, ["SPY"]).empty


def test_to_long_frame_drops_missing_observations():
    index = pd.to_datetime(["2026-08-12", "2026-08-13"])
    raw = pd.DataFrame({"Close": [None, 543.24]}, index=index)
    raw.index.name = "Date"
    assert len(free.to_long_frame(raw, ["SPY"])) == 1


def test_fetch_uses_the_injected_downloader():
    frame = free.fetch(["SPY"], downloader=fake_download)
    assert set(frame["symbol"]) == {"SPY"}


def test_fetch_on_an_empty_symbol_list_makes_no_request():
    def explode(symbols, period):  # pragma: no cover - must never be called
        raise AssertionError("downloader should not run for an empty list")

    assert free.fetch([], downloader=explode).empty


@pytest.mark.parametrize(
    "close,previous,expected_close",
    [
        (4.09, 4.12, 4.09),  # already a percentage
        (40.9, 41.2, 4.09),  # the older tenths convention
    ],
)
def test_normalize_tnx_handles_both_yahoo_conventions(close, previous, expected_close):
    quote = free.normalize_tnx(Quote("^TNX", "the ten-year", close, previous))
    assert quote.close == pytest.approx(expected_close)


def test_normalize_tnx_passes_through_none():
    assert free.normalize_tnx(None) is None


def test_collect_free_builds_every_segment():
    facts = free.collect_free(downloader=fake_download)
    assert [q.symbol for q in facts.indices] == ["SPY", "QQQ", "DIA"]
    assert facts.rate is not None and facts.rate.close == pytest.approx(4.09)
    assert facts.crude is not None and facts.crude.close == pytest.approx(71.40)
    assert facts.crypto is not None
    assert facts.gainer is not None and facts.loser is not None
    assert facts.breadth_total == len(free.FREE_UNIVERSE)
    assert facts.session_date == date(2026, 8, 13)


def test_free_mode_names_the_proxies_honestly():
    """SPY tracks the index; it is not the index. See the module docstring."""
    facts = free.collect_free(downloader=fake_download)
    names = [quote.name for quote in facts.indices]
    assert names == [
        "the S and P five hundred E T F",
        "the Nasdaq one hundred E T F",
        "the Dow E T F",
    ]
    assert all("E T F" in name for name in names)


def test_free_mode_renders_a_script_with_no_digits():
    text = script.render(free.collect_free(downloader=fake_download), full=True)
    assert not any(c.isdigit() for c in text)
    assert "[THE TAPE]" in text
    assert "[RATES]" in text


def test_free_mode_universe_is_all_pronounceable():
    """Every free-mode ticker needs a spoken name, or movers reads as letters."""
    missing = [s for s in free.FREE_UNIVERSE if s not in market.COMPANY_NAMES]
    assert missing == []


def test_free_mode_universe_supports_breadth():
    """is_narrow needs twenty-plus names to say anything."""
    assert len(free.FREE_UNIVERSE) >= 20


def test_collect_free_survives_a_dead_feed(capsys):
    def dead(symbols, period):
        raise ConnectionError("Yahoo said no")

    facts = free.collect_free(downloader=dead)
    assert facts.indices == []
    assert facts.gainer is None
    assert "warning" in capsys.readouterr().out
    # The script still renders; the data segments just drop.
    text = script.render(facts)
    assert "[STRAIGHT]" in text
    assert "[THE TAPE]" not in text


def test_cli_free_flag_routes_to_yahoo(monkeypatch, capsys):
    real = free.collect_free
    monkeypatch.setattr(
        free, "collect_free", lambda **kw: real(downloader=fake_download)
    )
    assert main(["--free", "--preview"]) == 0
    out = capsys.readouterr().out
    assert "S and P five hundred E T F" in out
    assert not any(c.isdigit() for c in out)


def test_cli_reports_a_missing_kicker_file_without_a_traceback(tmp_path, capsys):
    """A mistyped --kicker-file should be one sentence, not a stack trace."""
    missing = tmp_path / "nope.txt"

    assert main(["--demo", "--kicker-file", str(missing)]) == 2

    err = capsys.readouterr().err
    assert "cannot read kicker file" in err
    assert str(missing) in err
    assert "Traceback" not in err


def test_cli_reports_a_missing_names_file_without_a_traceback(tmp_path, capsys):
    missing = tmp_path / "nope.json"

    assert main(["--demo", "--names", str(missing)]) == 2

    err = capsys.readouterr().err
    assert "cannot read names file" in err
    assert "Traceback" not in err


def test_cli_checks_the_kicker_before_collecting_quotes(tmp_path, capsys, monkeypatch):
    """The expensive part must not run when a local input is already unreadable.

    The kicker used to be read after collection, so a mistyped path cost a full
    download of every quote before the command failed.
    """
    called = []

    def explode(*args, **kwargs):
        called.append(True)
        raise AssertionError("collected quotes despite an unreadable kicker file")

    monkeypatch.setattr(free, "collect_free", explode)
    monkeypatch.setattr(market, "collect", explode)

    assert main(["--free", "--kicker-file", str(tmp_path / "nope.txt")]) == 2
    assert called == []


def test_cli_clears_segments_from_a_previous_render(tmp_path, capsys):
    """A re-render must not leave an earlier run's files behind.

    Segment numbering shifts whenever the script gains or loses a block, so the
    old sign-off lands under a different number than the new one and survives.
    That is how a stale sign-off carrying a since-renamed anchor stayed in an
    output directory and read as current.
    """
    target = tmp_path / "render"
    target.mkdir()
    ghost = target / "06-sign-off.txt"
    ghost.write_text("I'm Max Brennan, good night.\n", encoding="utf-8")

    assert main(["--demo", "--segments", str(target)]) == 0

    assert not ghost.exists()
    written = sorted(p.name for p in target.glob("*.txt"))
    assert written == [
        "01-cold-open.txt",
        "02-the-tape.txt",
        "03-movers.txt",
        "04-straight.txt",
        "05-sign-off.txt",
    ]
    assert "Max Brennan" not in (target / "05-sign-off.txt").read_text(encoding="utf-8")
    assert "removed 1 segment file(s)" in capsys.readouterr().err


def test_cli_leaves_unrelated_files_in_the_segment_directory(tmp_path):
    """Only this tool's own output is cleared — the directory may hold anything."""
    target = tmp_path / "render"
    target.mkdir()
    keep = target / "kicker-notes.txt"
    keep.write_text("mine\n", encoding="utf-8")
    also_keep = target / "readme.md"
    also_keep.write_text("mine too\n", encoding="utf-8")

    assert main(["--demo", "--segments", str(target)]) == 0

    assert keep.read_text(encoding="utf-8") == "mine\n"
    assert also_keep.read_text(encoding="utf-8") == "mine too\n"
