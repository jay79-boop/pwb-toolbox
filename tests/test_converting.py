"""Tests for `pwb_toolbox.converting`.

The important ones are at the bottom: generated strategies are compiled and run
through a real Backtrader `cerebro` on synthetic bars, so "it converted" is
never mistaken for "it works".
"""

import calendar
import datetime
import math
import random

import backtrader as bt
import pandas as pd
import pytest

from pwb_toolbox.converting import PineSyntaxError, convert, parse, tokenize
from pwb_toolbox.converting.nodes import (
    Assign,
    Binary,
    Call,
    If,
    Num,
    Ternary,
    Unsupported,
)

DUAL_MA = """//@version=5
strategy("Dual MA Cross", overlay=true)
fast = input.int(5, title="Fast length")
slow = input.int(20, title="Slow length")
maFast = ta.sma(close, fast)
maSlow = ta.sma(close, slow)
if ta.crossover(maFast, maSlow) and close > maSlow
    strategy.entry("long", strategy.long)
if ta.crossunder(maFast, maSlow)
    strategy.close("long")
plot(maFast)
"""

RSI_STRATEGY = """//@version=5
strategy("RSI Reversion")
length = input.int(14)
oversold = input.int(30)
overbought = input.int(70)
r = ta.rsi(close, length)
if r < oversold
    strategy.entry("long", strategy.long)
if r > overbought
    strategy.close("long")
"""


# --- lexer -------------------------------------------------------------------


def _kinds(source):
    return [t.kind for t in tokenize(source)]


def test_tokenize_emits_indent_and_dedent():
    kinds = _kinds("if close > open\n    strategy.close()\n")
    assert "INDENT" in kinds and "DEDENT" in kinds


def test_tokenize_lexes_dotted_names_as_single_token():
    tokens = [t for t in tokenize("ta.sma(close, 10)\n") if t.kind == "NAME"]
    assert [t.value for t in tokens] == ["ta.sma", "close"]


def test_tokenize_ignores_comment_and_blank_lines():
    assert _kinds("// just a comment\n\n") == ["EOF"]


def test_tokenize_keeps_double_slash_inside_string():
    tokens = [t for t in tokenize('x = "http://a.b"\n') if t.kind == "STRING"]
    assert tokens[0].value == "http://a.b"


def test_tokenize_ignores_newlines_inside_parentheses():
    kinds = _kinds("x = ta.sma(\n    close,\n    10\n)\n")
    assert kinds.count("NEWLINE") == 1
    assert "INDENT" not in kinds


def test_tokenize_rejects_unterminated_string():
    with pytest.raises(PineSyntaxError):
        tokenize('x = "oops\n')


# --- lexer: expressions split across lines ------------------------------------
#
# Pine's own rule keys on the continuation being indented by something that is
# not a multiple of four, which collides with the indentation that opens a
# block. Reading the operator is unambiguous instead: no statement ends with a
# binary operator, and none begins with one.


@pytest.mark.parametrize(
    "source",
    [
        "ok = (a > 0) and\n     (b > 0)\n",  # trailing word operator
        "t = c > o ? 1 :\n     2\n",  # trailing ternary colon
        "s = 'a' +\n    'b'\n",  # trailing arithmetic
        "z =\n    ta.sma(close, 10)\n",  # trailing assignment
        "y = c > o\n     ? 1\n     : 2\n",  # leading ternary arms
        "ok = (a > 0)\n     and (b > 0)\n",  # leading word operator
        "q = 1 and\n\n     2\n",  # blank line between
        "q = 1 and\n// a comment\n     2\n",  # comment between
    ],
)
def test_tokenize_joins_a_split_expression(source):
    kinds = _kinds(source)
    assert kinds.count("NEWLINE") == 1, kinds
    assert "INDENT" not in kinds, "a continuation must not open a block"


def test_tokenize_still_opens_blocks_on_real_indentation():
    kinds = _kinds("if close > open\n    strategy.close()\n")
    assert "INDENT" in kinds and "DEDENT" in kinds


def test_tokenize_does_not_join_a_tuple_destructuring():
    """`[a, b] = ...` starts a statement; `[` must never read as a continuation."""
    kinds = _kinds("x = close\n[m, s, h] = ta.macd(close, 12, 26, 9)\n")
    assert kinds.count("NEWLINE") == 2


def _split_pair(joined, split):
    head = '//@version=6\nstrategy("Split")\nma = ta.sma(close, 10)\n'
    tail = 'if entryOk\n    strategy.entry("l", strategy.long)\nif close < ma\n    strategy.close()\n'
    return head + joined + tail, head + split + tail


@pytest.mark.parametrize(
    "joined, split",
    [
        (
            "entryOk = (close > ma) and (high > low)\n",
            "entryOk = (close > ma) and\n          (high > low)\n",
        ),
        (
            "entryOk = (close > ma) and (high > low)\n",
            "entryOk = (close > ma)\n          and (high > low)\n",
        ),
        (
            "entryOk = close > ma ? true : false\n",
            "entryOk = close > ma\n          ? true\n          : false\n",
        ),
    ],
)
def test_splitting_a_line_changes_nothing(joined, split):
    """Where the line breaks fall must not reach the generated strategy."""
    one, many = _split_pair(joined, split)
    assert convert(one).code == convert(many).code


def test_generated_split_strategy_still_trades():
    """And the joined-up condition has to actually drive orders."""
    _, split = _split_pair("", "entryOk = (close > ma) and\n          (high > low)\n")
    _, closed = _run(split)
    assert closed > 0


# --- parser ------------------------------------------------------------------


def test_parse_reads_version_and_declaration():
    program = parse(DUAL_MA)
    assert program.version == 5
    assert program.declaration == ("strategy", "Dual MA Cross")


def test_parse_normalises_legacy_study_declaration():
    program = parse('//@version=4\nstudy("Legacy")\n')
    assert program.declaration == ("indicator", "Legacy")


def test_parse_builds_if_else_with_bodies():
    program = parse(
        "if close > open\n    strategy.close()\nelse\n    strategy.close()\n"
    )
    node = program.body[0]
    assert isinstance(node, If)
    assert len(node.body) == 1 and len(node.orelse) == 1


def test_parse_handles_else_if_chain():
    program = parse(
        "if close > open\n    strategy.close()\n"
        "else if close < open\n    strategy.close()\n"
    )
    assert isinstance(program.body[0].orelse[0], If)


def test_parse_respects_arithmetic_precedence():
    program = parse("x = 1 + 2 * 3\n")
    value = program.body[0].value
    assert value.op == "+" and value.right.op == "*"


def test_parse_comparison_binds_looser_than_arithmetic():
    program = parse("x = close - 1 > open\n")
    value = program.body[0].value
    assert value.op == ">" and value.left.op == "-"


def test_parse_ternary():
    program = parse("x = close > open ? 1 : 2\n")
    assert isinstance(program.body[0].value, Ternary)


def test_parse_history_index():
    program = parse("x = close[1]\n")
    assert program.body[0].value.offset == Num(1.0)


def test_parse_keyword_arguments():
    program = parse('x = input.int(10, title="Len")\n')
    call = program.body[0].value
    assert call.args == (Num(10.0),)
    assert call.kwargs[0][0] == "title"


def test_parse_records_var_qualifier():
    program = parse("var count = 0\n")
    assert program.body[0].qualifier == "var"


def test_parse_skips_for_loop_as_unsupported():
    program = parse("for i = 0 to 10\n    x = i\ny = close\n")
    assert isinstance(program.body[0], Unsupported)
    assert program.body[0].kind == "for"
    # Parsing must resume cleanly after the skipped block.
    assert isinstance(program.body[1], Assign)


def test_parse_rejects_unknown_character():
    with pytest.raises(PineSyntaxError):
        parse("x = 1 @ 2\n")


# --- conversion: structure ---------------------------------------------------


def test_convert_collects_inputs_as_params():
    result = convert(DUAL_MA)
    assert result.params == [("fast", 5), ("slow", 20)]
    assert "('fast', 5)" in result.code


def test_convert_derives_class_name_from_title():
    assert convert(DUAL_MA).class_name == "DualMACross"


def test_convert_honours_explicit_class_name():
    assert convert(DUAL_MA, class_name="MyStrat").class_name == "MyStrat"


def test_convert_hoists_indicators_into_init():
    code = convert(DUAL_MA).code
    init = code.split("def __init__")[1].split("def next")[0]
    assert "bt.indicators.SMA(self.data.close, period=self.p.fast)" in init
    assert "bt.indicators.SMA" not in code.split("def next")[1]


def test_convert_shares_one_crossover_between_crossover_and_crossunder():
    """Backtrader recomputes every indicator each bar; duplicates are waste."""
    init = convert(DUAL_MA).code.split("def __init__")[1].split("def next")[0]
    assert init.count("bt.indicators.CrossOver") == 1


def test_convert_maps_cross_helpers_to_their_direction():
    next_body = convert(DUAL_MA).code.split("def next")[1]
    assert "> 0" in next_body and "< 0" in next_body


def test_convert_maps_entry_and_close_to_orders():
    next_body = convert(DUAL_MA).code.split("def next")[1]
    assert "self._pine_entry(True)" in next_body
    assert "self.close()" in next_body


def test_convert_maps_short_entry_to_sell():
    source = '//@version=5\nstrategy("S")\nif close > open\n    strategy.entry("s", strategy.short)\n'
    assert "self._pine_entry(False)" in convert(source).code


def test_convert_passes_entry_quantity_as_size():
    source = (
        '//@version=5\nstrategy("S")\nif close > open\n'
        '    strategy.entry("l", strategy.long, qty=5)\n'
    )
    assert "self._pine_entry(True, size=5)" in convert(source).code


def test_convert_reports_plot_as_ignored_not_unsupported():
    result = convert(DUAL_MA)
    assert result.ok
    assert any("plot()" in item for item in result.ignored)
    assert result.unsupported == []


def test_convert_translates_history_access():
    source = '//@version=5\nstrategy("S")\nif close > close[1]\n    strategy.close()\n'
    assert "self.data.close[-1]" in convert(source).code


def test_convert_translates_derived_series():
    source = '//@version=5\nstrategy("S")\nif hl2 > close\n    strategy.close()\n'
    code = convert(source).code
    assert "self.data.high[0] + self.data.low[0]" in code


def test_convert_translates_ternary():
    source = '//@version=5\nstrategy("S")\nx = close > open ? 1 : 2\nif x > 1\n    strategy.close()\n'
    assert "if" in convert(source).code and "else" in convert(source).code


def test_convert_atr_takes_no_source_argument():
    source = (
        '//@version=5\nstrategy("S")\na = ta.atr(14)\nif a > 1\n    strategy.close()\n'
    )
    result = convert(source)
    assert result.ok, result.unsupported
    assert "bt.indicators.ATR(self.data, period=14)" in result.code


def test_convert_highest_defaults_to_high_series():
    source = '//@version=5\nstrategy("S")\nh = ta.highest(20)\nif close > h\n    strategy.close()\n'
    result = convert(source)
    assert result.ok, result.unsupported
    assert "bt.indicators.Highest(self.data.high, period=20)" in result.code


# --- conversion: refusals ----------------------------------------------------


def _unsupported(source):
    return convert(source).unsupported


@pytest.mark.parametrize(
    "snippet, marker",
    [
        ("while true\n    x = close\n", "while"),
        ("for i = 0 to 10\n    x = close\n", "for"),
        ("[m, s, h] = ta.macd(close, 12, 26, 9)\n", "tuple destructuring"),
        ("a = array.new_float(0)\n", "array.new_float"),
    ],
)
def test_convert_reports_untranslatable_constructs(snippet, marker):
    result = convert('//@version=5\nstrategy("S")\n' + snippet)
    assert not result.ok
    assert any(marker in item for item in result.unsupported)


def test_convert_reports_strategy_exit_with_a_tick_offset():
    """`loss`/`profit` are distances in ticks, which the script never states."""
    source = (
        '//@version=5\nstrategy("S")\nif close > open\n'
        '    strategy.exit("x", loss=100)\n'
    )
    result = convert(source)
    assert not result.ok
    assert any("ticks" in item for item in result.unsupported)


def test_convert_allows_plain_strategy_exit():
    source = '//@version=5\nstrategy("S")\nif close > open\n    strategy.exit("x")\n'
    result = convert(source)
    assert result.ok, result.unsupported
    assert "self.close()" in result.code


def test_convert_reports_unknown_identifier():
    source = '//@version=5\nstrategy("S")\nif mystery > 1\n    strategy.close()\n'
    assert any("mystery" in item for item in _unsupported(source))


def test_convert_reports_missing_declaration():
    result = convert("x = ta.sma(close, 10)\n")
    assert not result.ok
    assert any("declaration" in item for item in result.unsupported)


def test_convert_notes_indicator_scripts_place_no_orders():
    result = convert('//@version=5\nindicator("Just Lines")\nx = ta.sma(close, 10)\n')
    assert result.ok
    assert any("places no orders" in item for item in result.ignored)


def test_unsupported_items_appear_in_generated_docstring():
    code = convert(
        '//@version=5\nstrategy("S")\n[m, s, h] = ta.macd(close, 12, 26, 9)\n'
    ).code
    assert "Not translated" in code and "tuple destructuring" in code


def test_reserved_names_are_renamed_to_avoid_clobbering_strategy_attrs():
    source = '//@version=5\nstrategy("S")\nposition = input.int(3)\nif close > position\n    strategy.close()\n'
    result = convert(source)
    assert result.ok, result.unsupported
    assert "'pine_position'" in result.code


# --- end to end: the generated code must actually run ------------------------


def _price_frame(bars=300, seed=7):
    rng = random.Random(seed)
    price = 100.0
    start = datetime.datetime(2022, 1, 1)
    rows = []
    for i in range(bars):
        price *= 1 + rng.gauss(0, 0.02)
        rows.append(
            {
                "datetime": start + datetime.timedelta(days=i),
                "open": price,
                "high": price * 1.01,
                "low": price * 0.99,
                "close": price,
                "volume": 1000,
            }
        )
    return pd.DataFrame(rows).set_index("datetime")


def _run(source, **params):
    result = convert(source)
    assert result.ok, f"conversion reported: {result.unsupported}"

    namespace = {}
    exec(compile(result.code, "<converted>", "exec"), namespace)

    cerebro = bt.Cerebro()
    cerebro.adddata(bt.feeds.PandasData(dataname=_price_frame()))
    cerebro.addstrategy(namespace[result.class_name], **params)
    cerebro.broker.setcash(10_000.0)
    cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name="trades")
    strategy = cerebro.run()[0]
    closed = strategy.analyzers.trades.get_analysis().get("total", {}).get("total", 0)
    return cerebro.broker.getvalue(), closed


def test_generated_strategy_compiles_and_runs():
    value, _ = _run(DUAL_MA)
    assert value > 0


def test_generated_strategy_actually_places_orders():
    """A converted strategy that never trades has not really been converted."""
    _, closed = _run(DUAL_MA)
    assert closed > 0


def test_generated_rsi_strategy_runs_and_trades():
    _, closed = _run(RSI_STRATEGY)
    assert closed > 0


def test_generated_params_are_overridable_from_cerebro():
    """Pine inputs must land as real Backtrader params, not baked-in constants."""
    baseline, _ = _run(DUAL_MA)
    tuned, _ = _run(DUAL_MA, fast=3, slow=40)
    assert baseline != tuned


def test_generated_history_access_runs():
    source = (
        '//@version=5\nstrategy("Momentum")\n'
        'if close > close[5]\n    strategy.entry("l", strategy.long)\n'
        "if close < close[5]\n    strategy.close()\n"
    )
    _, closed = _run(source)
    assert closed > 0


# --- regressions found by converting real published scripts ------------------
#
# Everything below was hit by running the converter over scripts collected from
# GitHub rather than over fixtures written here.


@pytest.mark.parametrize(
    "declaration",
    [
        "float entryPrice = na",
        "int n = 5",
        "bool flag = true",
        "string label = 'x'",
        "series float x = 1.0",
        "simple int n = 5",
    ],
)
def test_convert_accepts_explicit_type_declarations(declaration):
    """Pine lets a declaration name its type; that used to be a hard crash."""
    result = convert('//@version=6\nstrategy("S")\n' + declaration + "\n")
    assert result.ok, result.unsupported


def test_type_declaration_does_not_hide_var():
    """`var float x = na` is still persistent state, type annotation or not.

    The type words are consumed before the assignment is read, so the risk is
    that `var` gets consumed with them and the declaration silently becomes an
    ordinary local -- recomputed every bar instead of carried across them.
    """
    result = convert('//@version=6\nstrategy("S")\nvar float entryPrice = na\n')
    assert result.ok, result.unsupported
    assert "self.entryPrice = float('nan')" in result.code.split("def next")[0]


@pytest.mark.parametrize(
    "snippet",
    ["x = float(close)\n", "line = 5\n", "color = 3\n"],
)
def test_type_words_are_only_consumed_when_they_are_types(snippet):
    """`float(...)` is a cast and `line` is a legal name -- neither is a type here."""
    parse('//@version=6\nstrategy("S")\n' + snippet)


def test_convert_reports_a_parse_failure_instead_of_raising():
    """Raising would kill a loop over a corpus on its first odd script."""
    result = convert('//@version=6\nstrategy("S")\nx = = =\n')
    assert not result.ok
    assert any("could not parse" in item for item in result.unsupported)


def test_unparsable_source_still_yields_runnable_code():
    """`convert` promises a result that always carries code. Hold it to that."""
    result = convert('//@version=6\nstrategy("S")\nx = = =\n')
    namespace = {}
    exec(compile(result.code, "<converted>", "exec"), namespace)

    cerebro = bt.Cerebro()
    cerebro.adddata(bt.feeds.PandasData(dataname=_price_frame()))
    cerebro.addstrategy(namespace[result.class_name])
    cerebro.broker.setcash(10_000.0)
    assert cerebro.run()
    assert cerebro.broker.getvalue() == 10_000.0  # a placeholder trades nothing


def test_convert_accepts_an_input_nested_in_an_expression():
    """`input.float(...) / 100` is how real scripts write a percentage."""
    result = convert(
        '//@version=6\nstrategy("S")\nstop = input.float(5.0, "Stop Percent") / 100\n'
    )
    assert result.ok, result.unsupported
    assert ("stop_percent", 5) in result.params


@pytest.mark.parametrize("literal", ["#00c853", "#ff0000", "#00c85380"])
def test_hex_colour_literals_are_presentational_not_syntax_errors(literal):
    """`#00c853` broke the lexer outright -- the commonest cause in the corpus."""
    source = (
        '//@version=6\nstrategy("S")\n'
        f"c = close > open ? {literal} : #000000\n"
        "plot(close, color=c)\n"
    )
    result = convert(source)
    assert result.ok, result.unsupported
    assert any(literal in item for item in result.ignored)


@pytest.mark.parametrize(
    "declaration",
    [
        "f(x) =>",
        "atan2(series float y, series float x) =>",
        "ema(series float src, simple int period=0) =>",
    ],
)
def test_a_declaration_carrying_types_and_defaults_still_parses(declaration):
    """Parameters may carry a type, a qualifier, a default, or all three."""
    source = '//@version=6\nstrategy("S")\n' + declaration + "\n    close\ny = close\n"
    result = convert(source)
    assert result.ok, result.unsupported
    assert "y = self.data.close[0]" in result.code


def test_parsing_resumes_after_a_user_defined_function():
    program = parse(
        '//@version=6\nstrategy("S")\nf(x) =>\n    x * 2\ny = ta.sma(close, 10)\n'
    )
    assert isinstance(program.body[-1], Assign)
    assert program.body[-1].target == "y"


def test_a_declaration_is_kept_out_of_the_program_body():
    """A declaration produces no code on its own -- the call sites do."""
    program = parse('//@version=6\nstrategy("S")\nf(x) =>\n    x * 2\ny = f(close)\n')
    assert [type(node).__name__ for node in program.body] == ["Assign"]
    assert list(program.functions) == ["f"]
    assert [p.name for p in program.functions["f"].params] == ["x"]


def test_user_defined_type_block_is_reported_not_fatal():
    """`type Zone` is out of scope to translate, but not to get past."""
    source = (
        '//@version=6\nstrategy("S")\n'
        "type Zone\n    float top\n    bool bull\n"
        "ma = ta.sma(close, 10)\n"
    )
    result = convert(source)
    assert not result.ok
    assert any("user-defined type" in item for item in result.unsupported)
    assert not any("could not parse" in item for item in result.unsupported)


def test_type_block_fields_may_carry_defaults():
    source = (
        '//@version=6\nstrategy("S")\n'
        "type bar\n    float o = open\n    float c = close\n"
        "ma = ta.sma(close, 10)\n"
    )
    assert any("user-defined type" in item for item in convert(source).unsupported)


def test_parsing_resumes_after_a_type_block():
    program = parse(
        '//@version=6\nstrategy("S")\n'
        "type Zone\n    float top\n    bool bull\n"
        "y = ta.sma(close, 10)\n"
    )
    assert isinstance(program.body[-1], Assign)
    assert program.body[-1].target == "y"


@pytest.mark.parametrize(
    "declaration, expected",
    [
        ("var array<float> b = array.new_float(10, na)", "array.new_float()"),
        ("var array<float> b = array.new<float>()", "array.new()"),
        ("var matrix<float> m = matrix.new<float>(2, 2)", "matrix.new()"),
        (
            "var map<string, array<float>> m = map.new<string, array<float>>()",
            "map.new()",
        ),
    ],
)
def test_generic_types_reach_a_real_reason(declaration, expected):
    """`array<float>` used to be a syntax error; the array is the real gap."""
    result = convert('//@version=6\nstrategy("S")\n' + declaration + "\n")
    assert not result.ok
    assert any(expected in item for item in result.unsupported)
    assert not any("could not parse" in item for item in result.unsupported)


@pytest.mark.parametrize(
    "declaration, expected",
    [
        ("var box[] zones = array.new_box(0)", "array.new_box()"),
        ("var label[] pend = array.new_label()", "array.new_label()"),
        ("float[] arr = array.new_float()", "array.new_float()"),
        ("int[] xs = array.new_int(5)", "array.new_int()"),
        ("string[] names = array.new_string()", "array.new_string()"),
    ],
)
def test_bracket_shorthand_types_reach_a_real_reason(declaration, expected):
    """`float[]` is the older spelling of `array<float>` and reads the same."""
    result = convert('//@version=6\nstrategy("S")\n' + declaration + "\n")
    assert not result.ok
    assert any(expected in item for item in result.unsupported)
    assert not any("could not parse" in item for item in result.unsupported)


@pytest.mark.parametrize(
    "snippet",
    [
        "if close > close[1]\n    strategy.close()\n",
        "x = high[2] - low[3]\nif x > 0\n    strategy.close()\n",
        'v = input.string("a", "T", options=["a","b"])\n',
    ],
)
def test_bracket_shorthand_does_not_eat_indexing_or_lists(snippet):
    """Emptiness is the discriminator: `[]` is a type, `[1]` is a bar offset."""
    result = convert('//@version=6\nstrategy("S")\n' + snippet)
    assert result.ok, result.unsupported


def test_bracket_shorthand_does_not_eat_tuple_destructuring():
    result = convert(
        '//@version=6\nstrategy("S")\n[m, s, h] = ta.macd(close, 12, 26, 9)\n'
    )
    assert any("tuple destructuring" in item for item in result.unsupported)


def test_a_declared_user_type_is_recognised_after_its_block():
    """`bar b = bar.new()` only reads as a declaration once `bar` is known."""
    source = (
        '//@version=6\nstrategy("S")\n'
        "type bar\n    float o = open\n"
        "bar b = bar.new()\n"
    )
    result = convert(source)
    assert any("bar.new()" in item for item in result.unsupported)
    assert not any("could not parse" in item for item in result.unsupported)


@pytest.mark.parametrize(
    "snippet",
    [
        "n = 3\nlimit = 9\nif n < limit\n    strategy.close()\n",
        "a = 2\nb = 9\nif a < (b + 1)\n    strategy.close()\n",
        "x = 1\ny = 2\nz = x < y ? 1 : 2\n",
        "type = 5\n",
    ],
)
def test_angle_brackets_are_only_generics_when_they_really_are(snippet):
    """`a < b` is a comparison; eating it as a type parameter would be silent."""
    result = convert('//@version=6\nstrategy("S")\n' + snippet)
    assert result.ok, result.unsupported


def test_a_plain_call_is_not_mistaken_for_a_function_declaration():
    result = convert('//@version=6\nstrategy("S")\nx = ta.sma(close, 10)\n')
    assert result.ok, result.unsupported


# --- strategy.exit: stop and limit brackets ----------------------------------

BRACKET_STRATEGY = """//@version=6
strategy("Bracket")
rr = input.float(2.0, "Reward multiple")
var float sl = na
var float tp = na
a = ta.atr(14)
ma = ta.sma(close, 20)
if strategy.position_size == 0 and close > ma
    strategy.entry("Long", strategy.long)
    sl := close - a
    tp := close + a * rr
if strategy.position_size > 0
    strategy.exit("Long Exit", "Long", stop=sl, limit=tp)
"""

SHORT_BRACKET = """//@version=6
strategy("Short Bracket")
a = ta.atr(14)
ma = ta.sma(close, 20)
var float sl = na
if strategy.position_size == 0 and close < ma
    strategy.entry("S", strategy.short)
    sl := close + a
if strategy.position_size < 0
    strategy.exit("SX", "S", stop=sl)
"""


def _run_counting_orders(source):
    """Run a converted strategy, counting orders so stacking would show up."""
    result = convert(source)
    assert result.ok, result.unsupported
    namespace = {}
    exec(compile(result.code, "<converted>", "exec"), namespace)

    class Counted(namespace[result.class_name]):
        def __init__(self):
            super().__init__()
            self.submitted = 0
            self.bracket_fills = {"buy": 0, "sell": 0}
            self.exit_calls = 0

        def notify_order(self, order):
            if order.status == order.Submitted:
                self.submitted += 1
            elif order.status == order.Completed and order.exectype in (
                bt.Order.Stop,
                bt.Order.Limit,
            ):
                self.bracket_fills["buy" if order.isbuy() else "sell"] += 1

        def _pine_exit(self, *args, **kwargs):
            if self.position.size:
                self.exit_calls += 1
            return super()._pine_exit(*args, **kwargs)

    cerebro = bt.Cerebro()
    cerebro.adddata(bt.feeds.PandasData(dataname=_price_frame()))
    cerebro.addstrategy(Counted)
    cerebro.broker.setcash(10_000.0)
    cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name="trades")
    strategy = cerebro.run()[0]
    totals = strategy.analyzers.trades.get_analysis().get("total", {})
    # `closed` is what filled; `opened` includes a position still on at the end
    # of the data, which has entry and exit orders but no completed trade.
    return strategy, totals.get("closed", 0), totals.get("total", 0)


def test_exit_with_stop_and_limit_emits_bracket_orders():
    code = convert(BRACKET_STRATEGY).code
    assert "bt.Order.Stop" in code and "bt.Order.Limit" in code
    assert '"oco"' in code, "the pair must be one-cancels-other"


def test_exit_without_levels_is_still_a_plain_close():
    source = '//@version=6\nstrategy("S")\nif close > open\n    strategy.exit("x")\n'
    result = convert(source)
    assert result.ok, result.unsupported
    assert "self.close()" in result.code
    assert "_pine_exit" not in result.code


@pytest.mark.parametrize(
    "argument", ["loss=100", "profit=50", "trail_points=10", "trail_offset=5"]
)
def test_exit_with_a_tick_offset_is_reported(argument):
    source = (
        '//@version=6\nstrategy("S")\nif close > open\n'
        f'    strategy.exit("x", {argument})\n'
    )
    result = convert(source)
    assert not result.ok
    assert any("ticks" in item for item in result.unsupported)


def test_generated_bracket_exits_actually_fill():
    """Every trade must close through a stop or a limit, not by other means."""
    strategy, closed, _ = _run_counting_orders(BRACKET_STRATEGY)
    assert closed > 5
    fills = strategy.bracket_fills["buy"] + strategy.bracket_fills["sell"]
    assert fills == closed


def test_generated_bracket_exits_do_not_stack_orders():
    """The correctness crux, and a silent failure if it is wrong.

    Pine's strategy.exit is a standing instruction re-evaluated every bar. If
    each evaluation submitted a fresh pair, a position held ten bars would
    carry twenty live exit orders and fill several times over. So the order
    count has to track trades, not bars.
    """
    strategy, closed, opened = _run_counting_orders(BRACKET_STRATEGY)
    assert strategy.exit_calls > closed * 2, "the exit has to be re-evaluated a lot"
    # One entry plus one stop and one limit per position taken, and nothing
    # more. `opened` rather than `closed`, so a position still on at the end of
    # the data still counts its orders.
    assert strategy.submitted == opened * 3


def test_generated_short_bracket_exits_buy_to_cover():
    strategy, closed, opened = _run_counting_orders(SHORT_BRACKET)
    assert closed > 5
    assert strategy.bracket_fills["buy"] == closed
    assert strategy.bracket_fills["sell"] == 0
    # A stop only, so two orders per position rather than three.
    assert strategy.submitted == opened * 2


def test_a_moving_stop_replaces_its_order_rather_than_adding_one():
    """A stop recomputed each bar must move, which means cancel and resubmit."""
    source = (
        '//@version=6\nstrategy("Trail")\n'
        "a = ta.atr(14)\nma = ta.sma(close, 20)\n"
        'if strategy.position_size == 0 and close > ma\n    strategy.entry("L", strategy.long)\n'
        'if strategy.position_size > 0\n    strategy.exit("LX", "L", stop=close - a)\n'
    )
    strategy, closed, _ = _run_counting_orders(source)
    assert closed > 5
    assert strategy.bracket_fills["sell"] == closed
    # Replacement, not accumulation: comfortably fewer than one per evaluation.
    assert strategy.submitted < strategy.exit_calls * 2


def test_a_na_level_submits_no_order():
    """`var float sl = na` is 'no level yet'; a stop at NaN never compares."""
    source = (
        '//@version=6\nstrategy("S")\nvar float sl = na\n'
        "ma = ta.sma(close, 20)\n"
        'if close > ma\n    strategy.entry("L", strategy.long)\n'
        'if strategy.position_size > 0\n    strategy.exit("LX", "L", stop=sl)\n'
    )
    strategy, _, _ = _run_counting_orders(source)
    assert strategy.exit_calls > 0, "the exit has to actually be reached"
    assert strategy.bracket_fills == {"buy": 0, "sell": 0}


def test_convert_maps_strategy_position_avg_price():
    source = (
        '//@version=6\nstrategy("S")\n'
        "ma = ta.sma(close, 20)\n"
        'if close > ma\n    strategy.entry("L", strategy.long)\n'
        "if strategy.position_size > 0\n"
        '    strategy.exit("BE", "L", stop=strategy.position_avg_price)\n'
    )
    result = convert(source)
    assert result.ok, result.unsupported
    assert "self.position.price" in result.code


# --- request.security: a second timeframe ------------------------------------

HTF_STRATEGY = """//@version=6
strategy("HTF Trend")
htfTF = input.timeframe("W", "Higher timeframe")
htfMa = request.security(syminfo.tickerid, htfTF, ta.ema(close, 4))
htfClose = request.security(syminfo.tickerid, htfTF, close)
ma = ta.sma(close, 10)
if close > ma and htfClose > htfMa
    strategy.entry("long", strategy.long)
if close < ma
    strategy.close("long")
"""


def _run_htf(source, feeds=None):
    """Compile, wire up the feeds the class asks for, and run."""
    result = convert(source)
    assert result.ok, result.unsupported
    namespace = {}
    exec(compile(result.code, "<converted>", "exec"), namespace)
    strategy_cls = namespace[result.class_name]

    cerebro = bt.Cerebro()
    data = bt.feeds.PandasData(dataname=_price_frame())
    cerebro.adddata(data)
    for _symbol, timeframe, compression in (
        feeds if feeds is not None else strategy_cls.feed_spec
    ):
        # Every symbol here is stood up from the same frame; what is being
        # tested is the wiring, not that two instruments differ.
        if timeframe is None:
            cerebro.adddata(bt.feeds.PandasData(dataname=_price_frame()))
        else:
            cerebro.resampledata(data, timeframe=timeframe, compression=compression)
    cerebro.addstrategy(strategy_cls)
    cerebro.broker.setcash(10_000.0)
    cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name="trades")
    strategy = cerebro.run()[0]
    closed = strategy.analyzers.trades.get_analysis().get("total", {}).get("total", 0)
    return cerebro.broker.getvalue(), closed


@pytest.mark.parametrize(
    "timeframe, expected",
    [
        ("D", "(None, bt.TimeFrame.Days, 1)"),
        ("1D", "(None, bt.TimeFrame.Days, 1)"),
        ("W", "(None, bt.TimeFrame.Weeks, 1)"),
        ("240", "(None, bt.TimeFrame.Minutes, 240)"),
        ("30S", "(None, bt.TimeFrame.Seconds, 30)"),
    ],
)
def test_security_records_the_feed_it_needs(timeframe, expected):
    source = (
        '//@version=6\nstrategy("S")\n'
        f"h = request.security(syminfo.tickerid, '{timeframe}', close)\n"
        "if h > close\n    strategy.close()\n"
    )
    result = convert(source)
    assert result.ok, result.unsupported
    assert expected in result.code


def test_security_reads_from_the_resampled_feed():
    source = (
        '//@version=6\nstrategy("S")\n'
        "h = request.security(syminfo.tickerid, 'D', ta.ema(close, 20))\n"
        "if h > close\n    strategy.close()\n"
    )
    code = convert(source).code
    assert "bt.indicators.EMA(self.datas[1].close, period=20)" in code
    # The chart's own close must not have moved onto the resampled feed.
    assert "self.data.close[0]" in code


def test_two_calls_on_one_timeframe_share_a_feed():
    source = (
        '//@version=6\nstrategy("S")\n'
        "a = request.security(syminfo.tickerid, 'D', close)\n"
        "b = request.security(syminfo.tickerid, 'D', high)\n"
        "if a > b\n    strategy.close()\n"
    )
    code = convert(source).code
    assert code.count("bt.TimeFrame.Days") == 1
    assert "self.datas[2]" not in code


def test_chart_timeframe_needs_no_second_feed():
    """`timeframe.period` is the chart itself; Pine just routes it oddly."""
    source = (
        '//@version=6\nstrategy("S")\n'
        "h = request.security(syminfo.tickerid, timeframe.period, close)\n"
        "if h > open\n    strategy.close()\n"
    )
    result = convert(source)
    assert result.ok, result.unsupported
    assert "feed_spec" not in result.code
    assert "h = self.data.close[0]" in result.code


def test_security_on_another_symbol_asks_for_that_instrument():
    """A second instrument is a feed to load, and now it is recorded as one."""
    result = convert(
        "//@version=6\nstrategy(\"S\")\nh = request.security('AAPL', 'D', close)\n"
        "if h > close\n    strategy.close()\n"
    )
    assert result.ok, result.unsupported
    assert "('AAPL', bt.TimeFrame.Days, 1)," in result.code
    assert "h = self.datas[1].close[0]" in result.code


def test_lookahead_on_is_reported():
    """lookahead_on reads a bar before it closes -- there is no equivalent."""
    source = (
        '//@version=6\nstrategy("S")\n'
        "h = request.security(syminfo.tickerid, 'D', close, "
        "lookahead=barmerge.lookahead_on)\n"
    )
    result = convert(source)
    assert not result.ok
    assert any("before it closes" in item for item in result.unsupported)


def test_lookahead_off_is_the_supported_default():
    source = (
        '//@version=6\nstrategy("S")\n'
        "h = request.security(syminfo.tickerid, 'D', close, "
        "lookahead=barmerge.lookahead_off)\n"
        "if h > close\n    strategy.close()\n"
    )
    assert convert(source).ok


def test_timeframe_from_a_param_says_the_param_cannot_move_the_feed():
    """A knob that looks live and is not would be a silently wrong backtest."""
    result = convert(HTF_STRATEGY)
    assert result.ok, result.unsupported
    assert any("htfTF" in item and "feed_spec" in item for item in result.ignored)


def test_security_with_a_non_literal_timeframe_is_reported():
    source = (
        '//@version=6\nstrategy("S")\n'
        "tf = close > open ? 'D' : 'W'\n"
        "h = request.security(syminfo.tickerid, tf, close)\n"
    )
    result = convert(source)
    assert not result.ok
    assert any("literal string" in item for item in result.unsupported)


def test_generated_htf_strategy_refuses_to_run_miswired():
    """One feed short, the reads would silently be IndexErrors deep in next()."""
    result = convert(HTF_STRATEGY)
    namespace = {}
    exec(compile(result.code, "<converted>", "exec"), namespace)

    cerebro = bt.Cerebro()
    cerebro.adddata(bt.feeds.PandasData(dataname=_price_frame()))
    cerebro.addstrategy(namespace[result.class_name])
    with pytest.raises(ValueError, match="data feeds"):
        cerebro.run()


def test_generated_htf_strategy_runs_and_trades():
    _, closed = _run_htf(HTF_STRATEGY)
    assert closed > 0


def test_resampled_feed_never_shows_a_bar_from_the_future():
    """The property the whole feature rests on.

    Pine's default is `barmerge.lookahead_off`: the higher timeframe must not
    leak data the chart bar could not have seen. A violation here would not
    fail loudly -- it would produce a beautiful, entirely fake backtest.
    """

    class Probe(bt.Strategy):
        def __init__(self):
            self.violations = 0
            self.checked = 0

        def next(self):
            if len(self.datas[1]) == 0:
                return
            self.checked += 1
            if self.datas[1].datetime.datetime(0) > self.data.datetime.datetime(0):
                self.violations += 1

    for timeframe in (bt.TimeFrame.Weeks, bt.TimeFrame.Months):
        cerebro = bt.Cerebro()
        data = bt.feeds.PandasData(dataname=_price_frame())
        cerebro.adddata(data)
        cerebro.resampledata(data, timeframe=timeframe, compression=1)
        cerebro.addstrategy(Probe)
        probe = cerebro.run()[0]
        assert probe.checked > 100, "the probe has to actually see bars"
        assert probe.violations == 0


# --- var: state that survives the bar ----------------------------------------

STATE_STRATEGY = """//@version=6
strategy("Stop Tracker")
stopPct = input.float(2.0, "Stop Percent") / 100
var float entryPrice = na
var int trades = 0
ma = ta.sma(close, 20)
if na(entryPrice) and close > ma
    strategy.entry("long", strategy.long)
    entryPrice := close
    trades := trades + 1
if not na(entryPrice) and close < entryPrice * (1 - stopPct)
    strategy.close("long")
    entryPrice := na
"""


@pytest.mark.parametrize(
    "declaration, expected",
    [
        ("var float x = na", "self.x = float('nan')"),
        ("var int n = 0", "self.n = 0"),
        ("var bool flag = false", "self.flag = False"),
        ("var float lowest = -1.5", "self.lowest = -1.5"),
        ('var string tag = "a"', "self.tag = 'a'"),
    ],
)
def test_var_becomes_an_attribute_initialised_in_init(declaration, expected):
    result = convert('//@version=6\nstrategy("S")\n' + declaration + "\n")
    assert result.ok, result.unsupported
    assert expected in result.code.split("def next")[0]


def test_var_named_after_a_strategy_attribute_is_renamed():
    """`var position = 0` must not clobber `self.position`."""
    result = convert('//@version=6\nstrategy("S")\nvar int position = 0\n')
    assert result.ok, result.unsupported
    assert "self.pine_position = 0" in result.code


def test_var_reassignment_writes_through_to_the_attribute():
    source = (
        '//@version=6\nstrategy("S")\nvar int n = 0\n'
        "if close > open\n    n := n + 1\n"
    )
    result = convert(source)
    assert result.ok, result.unsupported
    assert "self.n = (self.n + 1)" in result.code.split("def next")[1]


def test_var_with_a_non_literal_initialiser_is_reported():
    """`var float x = close` means the first bar's close; __init__ has no bar."""
    result = convert('//@version=6\nstrategy("S")\nvar float x = close\n')
    assert not result.ok
    assert any("literal initial value" in item for item in result.unsupported)


def test_varip_is_read_as_var():
    """`varip` and `var` differ only on a realtime bar, where varip is not
    rolled back between ticks. A backtest has no realtime bars, and Pine's own
    documentation says the distinction cannot be reproduced on historical ones
    -- so on the bars a backtest runs, reading varip as var is what Pine
    itself does rather than an approximation of it.

    Refusing it used to cascade: the name never entered scope, so every later
    read and every reassignment reported separately. Three corpus strategies
    spent about seventy per cent of their gap lists on that one refusal.
    """
    result = convert(
        '//@version=6\nstrategy("S")\n'
        "varip int n = 0\n"
        "if close > open\n"
        "    n := n + 1\n"
        "if n > 3\n"
        '    strategy.entry("L", strategy.long)\n'
    )
    assert result.ok, result.unsupported
    assert "self.n = 0" in result.code
    assert "self.n = (self.n + 1)" in result.code
    # Noted rather than silent: the author wanted intrabar behaviour
    # somewhere, and a live run is where they would not get it.
    assert any("varip read as var" in item for item in result.notes)


def test_varip_and_var_generate_the_same_code():
    """The claim in one assertion: on the bars a backtest has there is no
    difference between the two to generate.

    Compared past the docstring, which is the one place they *should* differ
    -- the varip reading is recorded there, and recording it is the point.
    """

    def body_for(qualifier):
        code = convert(
            '//@version=6\nstrategy("S")\n'
            f"{qualifier} int n = 0\n"
            "if close > open\n"
            "    n := n + 1\n"
            "if n > 3\n"
            '    strategy.entry("L", strategy.long)\n'
        ).code
        # Everything after the class docstring's closing quotes.
        return code.split('"""', 2)[2]

    assert body_for("varip") == body_for("var")
    assert (
        "varip"
        in convert(
            '//@version=6\nstrategy("S")\nvarip int n = 0\nif n > 3\n    strategy.close()\n'
        ).code
    )  # the note itself survives into the generated docstring


def test_var_history_access_is_reported():
    source = (
        '//@version=6\nstrategy("S")\nvar float x = na\n'
        "if close > x[1]\n    strategy.close()\n"
    )
    result = convert(source)
    assert not result.ok
    assert any("not a series with history" in item for item in result.unsupported)


def test_na_call_tests_for_the_missing_value():
    result = convert(
        '//@version=6\nstrategy("S")\nvar float x = na\n'
        "if na(x)\n    strategy.close()\n"
    )
    assert result.ok, result.unsupported
    assert "(self.x != self.x)" in result.code


def test_bare_na_is_still_the_literal():
    result = convert('//@version=6\nstrategy("S")\nvar float x = na\nx := na\n')
    assert result.ok, result.unsupported
    assert "self.x = float('nan')" in result.code.split("def next")[1]


SWITCH_STRATEGY = """//@version=6
strategy("Switch")
mode = input.string("Tight", "Mode")
band = switch mode
    "Tight" => 0.002
    "Wide"  => 0.05
    => 0.02
ma = ta.sma(close, 20)
if close > ma * (1 + band)
    strategy.entry("l", strategy.long)
if close < ma
    strategy.close()
"""


IF_EXPRESSION_STRATEGY = """//@version=6
strategy("If Expression")
edge = input.float(1.0, "Edge")
ma = ta.sma(close, 20)
score = if close > ma * (1 + 0.002 * edge)
    1.0
else if close > ma
    0.5
else
    0.0
if score > 0.75
    strategy.entry("l", strategy.long)
if close < ma
    strategy.close()
"""


def test_if_used_for_its_value_folds_into_conditionals():
    """Pine spells a conditional expression with its arms on separate lines."""
    source = (
        '//@version=6\nstrategy("S")\nma = ta.sma(close, 10)\n'
        "x = if close > ma\n    1.0\nelse\n    0.0\n"
        "if x > 0.5\n    strategy.close()\n"
    )
    result = convert(source)
    assert result.ok, result.unsupported
    assert "1 if (self.data.close[0] > self._sma_1[0]) else 0" in result.code


def test_if_expression_chains_through_else_if():
    source = (
        '//@version=6\nstrategy("S")\nma = ta.sma(close, 10)\n'
        "x = if close > ma\n    1.0\nelse if close < ma\n    0.45\nelse\n    0.0\n"
        "if x > 0.5\n    strategy.close()\n"
    )
    result = convert(source)
    assert result.ok, result.unsupported
    assert "0.45 if (self.data.close[0] < self._sma_1[0])" in result.code


def test_if_expression_without_an_else_yields_na():
    source = (
        '//@version=6\nstrategy("S")\nma = ta.sma(close, 10)\n'
        "x = if close > ma\n    1.0\n"
        "if x > 0.5\n    strategy.close()\n"
    )
    result = convert(source)
    assert result.ok, result.unsupported
    assert "float('nan')" in result.code


def test_if_expression_may_carry_a_declared_type():
    source = (
        '//@version=6\nstrategy("S")\nma = ta.sma(close, 10)\n'
        "float x = if close > ma\n    1.0\nelse\n    0.0\n"
        "if x > 0.5\n    strategy.close()\n"
    )
    assert convert(source).ok


def test_parsing_resumes_after_an_if_expression():
    program = parse(
        '//@version=6\nstrategy("S")\nma = ta.sma(close, 10)\n'
        "x = if close > ma\n    1.0\nelse\n    0.0\n"
        "y = ta.sma(close, 5)\n"
    )
    assert isinstance(program.body[-1], Assign)
    assert program.body[-1].target == "y"


def test_if_expression_branch_carrying_a_block_is_reported():
    """A branch with side effects cannot become a conditional expression."""
    result = convert(
        '//@version=6\nstrategy("S")\nma = ta.sma(close, 10)\n'
        "x = if close > ma\n    strategy.close()\n    1.0\nelse\n    0.0\n"
    )
    assert not result.ok
    assert any("one expression per branch" in item for item in result.unsupported)


def test_if_used_as_a_statement_is_untouched():
    """The same keyword still opens an ordinary block when nothing reads it."""
    result = convert(
        '//@version=6\nstrategy("S")\nma = ta.sma(close, 10)\n'
        'if close > ma\n    strategy.entry("l", strategy.long)\nelse\n    strategy.close()\n'
    )
    assert result.ok, result.unsupported
    assert "self._pine_entry(True)" in result.code and "self.close()" in result.code


def test_generated_if_expression_strategy_trades_on_every_branch():
    tight, tight_trades = _run(IF_EXPRESSION_STRATEGY, edge=1.0)
    wide, wide_trades = _run(IF_EXPRESSION_STRATEGY, edge=25.0)
    assert tight_trades > 0
    assert tight_trades != wide_trades


def test_switch_with_a_subject_folds_into_conditionals():
    """Pine's switch is a chain of conditionals written vertically."""
    source = (
        '//@version=6\nstrategy("S")\nmode = input.string("a", "M")\n'
        'm = switch mode\n    "a" => 1.0\n    "b" => 2.0\n    => 3.0\n'
        "if close > m\n    strategy.close()\n"
    )
    result = convert(source)
    assert result.ok, result.unsupported
    assert "1 if (self.p.mode == 'a')" in result.code
    assert "2 if (self.p.mode == 'b')" in result.code
    assert "else 3" in result.code


def test_switch_without_a_default_yields_na():
    """Pine returns `na` when nothing matches and no default was written."""
    source = (
        '//@version=6\nstrategy("S")\nmode = input.string("a", "M")\n'
        'm = switch mode\n    "a" => 1.0\n'
        "if close > m\n    strategy.close()\n"
    )
    result = convert(source)
    assert result.ok, result.unsupported
    assert "float('nan')" in result.code


def test_switch_without_a_subject_tests_each_case_as_a_condition():
    source = (
        '//@version=6\nstrategy("S")\n'
        "m = switch\n    close > open => 1.0\n    close < open => 2.0\n    => 3.0\n"
        "if close > m\n    strategy.close()\n"
    )
    result = convert(source)
    assert result.ok, result.unsupported
    assert "1 if (self.data.close[0] > self.data.open[0])" in result.code


def test_switch_case_may_itself_be_a_ternary():
    source = (
        '//@version=6\nstrategy("S")\nmode = input.string("a", "M")\n'
        'm = switch mode\n    "a" => close > open ? 1.0 : 2.0\n    => 3.0\n'
        "if close > m\n    strategy.close()\n"
    )
    assert convert(source).ok


def test_parsing_resumes_after_a_switch_block():
    program = parse(
        '//@version=6\nstrategy("S")\nmode = input.string("a", "M")\n'
        'm = switch mode\n    "a" => 1.0\n    => 2.0\n'
        "y = ta.sma(close, 5)\n"
    )
    assert isinstance(program.body[-1], Assign)
    assert program.body[-1].target == "y"


def test_a_switch_used_as_a_statement_is_reported():
    """A switch whose result goes nowhere is a side-effecting block, not this."""
    result = convert(
        '//@version=6\nstrategy("S")\nswitch\n    close > open => 1\n    => 2\n'
    )
    assert not result.ok
    assert any("switch statement" in item for item in result.unsupported)


def test_switch_is_still_usable_as_a_variable_name():
    result = convert(
        '//@version=6\nstrategy("S")\nswitch = 5\nif close > switch\n    strategy.close()\n'
    )
    assert result.ok, result.unsupported


def test_generated_switch_strategy_responds_to_its_input():
    """Every branch, default included, has to actually reach the trades."""
    _, tight = _run(SWITCH_STRATEGY, mode="Tight")
    _, wide = _run(SWITCH_STRATEGY, mode="Wide")
    _, fallthrough = _run(SWITCH_STRATEGY, mode="Neither")
    assert tight > 0
    assert len({tight, wide, fallthrough}) == 3, (tight, wide, fallthrough)


@pytest.mark.parametrize(
    "operator, expected",
    [
        ("+=", "self.n = (self.n + 2)"),
        ("-=", "self.n = (self.n - 2)"),
        ("*=", "self.n = (self.n * 2)"),
        ("/=", "self.n = (self.n / 2)"),
        ("%=", "self.n = (self.n % 2)"),
    ],
)
def test_compound_assignment_writes_through_to_var_state(operator, expected):
    source = (
        '//@version=6\nstrategy("S")\nvar float n = 8.0\n'
        f"if close > open\n    n {operator} 2\n"
    )
    result = convert(source)
    assert result.ok, result.unsupported
    assert expected in result.code


def test_compound_assignment_on_a_local_stays_local():
    source = (
        '//@version=6\nstrategy("S")\nq = 0\nq += 1\nif q > 0\n    strategy.close()\n'
    )
    result = convert(source)
    assert result.ok, result.unsupported
    assert "q = (q + 1)" in result.code
    assert "self.q" not in result.code


def test_compound_assignment_takes_the_whole_right_hand_side():
    """`q += a ? 1 : 0` is `q := q + (a ? 1 : 0)`, not `(q + a) ? 1 : 0`."""
    source = (
        '//@version=6\nstrategy("S")\nvar int q = 0\n'
        "if close > open\n    q += close > open ? 1 : 0\n"
    )
    code = convert(source).code
    assert "self.q = (self.q + (1 if" in code


def test_compound_assignment_to_an_undefined_name_is_reported():
    result = convert('//@version=6\nstrategy("S")\nzzz += 1\n')
    assert not result.ok
    assert any("zzz" in item and "not defined" in item for item in result.unsupported)


def test_compound_assignment_may_be_split_across_lines():
    source = (
        '//@version=6\nstrategy("S")\nvar int n = 0\n'
        "if close > open\n    n +=\n        1\n"
    )
    result = convert(source)
    assert result.ok, result.unsupported
    assert "self.n = (self.n + 1)" in result.code


def test_generated_compound_counter_survives_across_bars():
    """A `+=` counter that resets each bar has not really been converted."""
    source = (
        '//@version=6\nstrategy("Counter")\n'
        "var int trades = 0\nma = ta.sma(close, 10)\n"
        "if strategy.position_size == 0 and close > ma\n"
        '    strategy.entry("l", strategy.long)\n'
        "    trades += 1\n"
        "if close < ma\n    strategy.close()\n"
    )
    result = convert(source)
    assert result.ok, result.unsupported
    namespace = {}
    exec(compile(result.code, "<converted>", "exec"), namespace)

    cerebro = bt.Cerebro()
    cerebro.adddata(bt.feeds.PandasData(dataname=_price_frame()))
    cerebro.addstrategy(namespace[result.class_name])
    cerebro.broker.setcash(10_000.0)
    cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name="trades")
    strategy = cerebro.run()[0]
    totals = strategy.analyzers.trades.get_analysis().get("total", {})

    assert totals.get("total", 0) > 1
    assert strategy.trades == totals.get("total", 0)


def test_generated_var_state_survives_across_bars():
    """The whole point: a counter that resets each bar has not been converted.

    Compiling proves nothing here -- a local assigned in `next()` would also
    compile, and would silently count to one and stay there.
    """
    result = convert(STATE_STRATEGY)
    assert result.ok, result.unsupported
    namespace = {}
    exec(compile(result.code, "<converted>", "exec"), namespace)

    cerebro = bt.Cerebro()
    cerebro.adddata(bt.feeds.PandasData(dataname=_price_frame()))
    cerebro.addstrategy(namespace[result.class_name])
    cerebro.broker.setcash(10_000.0)
    cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name="trades")
    strategy = cerebro.run()[0]

    closed = strategy.analyzers.trades.get_analysis().get("total", {}).get("total", 0)
    assert closed > 1, "the strategy has to trade more than once to prove anything"
    assert strategy.trades == closed, "the Pine counter must match the real trade count"


def test_generated_var_strategy_responds_to_its_stop_param():
    tight, tight_trades = _run(STATE_STRATEGY, stop_percent=2.0)
    loose, loose_trades = _run(STATE_STRATEGY, stop_percent=8.0)
    assert tight_trades != loose_trades


def test_list_literal_in_an_argument_parses():
    """`options=[...]` is a dropdown hint; it blocked 9 of 17 corpus strategies."""
    result = convert(
        '//@version=6\nstrategy("S")\n'
        'ma = input.string("EMA", "Type", options=["EMA", "SMA", "WMA"])\n'
    )
    assert result.ok, result.unsupported


def test_list_literal_does_not_break_history_or_destructuring():
    """`[` is a list only in prefix position -- indexing is postfix."""
    assert convert(
        '//@version=6\nstrategy("S")\nif close > close[1]\n    strategy.close()\n'
    ).ok
    destructured = convert(
        '//@version=6\nstrategy("S")\n[m, s, h] = ta.macd(close, 12, 26, 9)\n'
    )
    assert any("tuple destructuring" in item for item in destructured.unsupported)


def test_nested_input_without_a_title_still_becomes_a_param():
    result = convert('//@version=6\nstrategy("S")\nx = close * input.float(1.5)\n')
    assert result.ok, result.unsupported
    assert len(result.params) == 1


def test_repeated_nested_input_becomes_one_param():
    source = (
        '//@version=6\nstrategy("S")\n'
        'a = input.float(2.0, "Mult") * 1\n'
        'b = input.float(2.0, "Mult") * 2\n'
    )
    result = convert(source)
    assert result.ok, result.unsupported
    assert len(result.params) == 1


def test_nested_input_does_not_collide_with_an_existing_param():
    source = (
        '//@version=6\nstrategy("S")\n'
        'mult = input.int(1, "M")\n'
        'x = close * input.float(2.0, "Mult")\n'
    )
    result = convert(source)
    assert result.ok, result.unsupported
    assert [name for name, _ in result.params] == ["mult", "mult_2"]


def test_nested_input_named_after_a_strategy_attribute_is_renamed():
    """The rename that protects `position` must survive a title-derived name."""
    result = convert(
        '//@version=6\nstrategy("S")\nx = close * input.float(2.0, "Position")\n'
    )
    assert result.ok, result.unsupported
    assert "'pine_position'" in result.code


def test_convert_maps_strategy_position_size():
    source = (
        '//@version=6\nstrategy("S")\n'
        "if strategy.position_size == 0 and close > open\n"
        '    strategy.entry("l", strategy.long)\n'
    )
    result = convert(source)
    assert result.ok, result.unsupported
    assert "self.position.size" in result.code


def test_computed_local_shadows_a_param_of_the_same_name():
    """The local, not the raw param, is what Pine means by `width` here.

    Naming the param from the title makes it collide with the assignment
    target. Resolving later references to the param silently used a threshold
    100x too large -- wrong output rather than an error, so it is pinned.
    """
    source = (
        '//@version=6\nstrategy("S")\n'
        'width = input.float(2.0, "Width") / 100\n'
        'if close > 1 + width\n    strategy.entry("l", strategy.long)\n'
    )
    code = convert(source).code
    assert "width = (self.p.width / 100)" in code
    assert "(1 + width)" in code
    assert "(1 + self.p.width)" not in code


def test_generated_nested_input_param_is_overridable():
    """A param recovered from inside an expression must still be tunable."""
    source = (
        '//@version=6\nstrategy("Band")\n'
        'width = input.float(2.0, "Width") / 100\n'
        "ma = ta.sma(close, 20)\n"
        'if close > ma * (1 + width)\n    strategy.entry("l", strategy.long)\n'
        "if close < ma\n    strategy.close()\n"
    )
    baseline, closed = _run(source)
    assert closed > 0
    tuned, _ = _run(source, width=25.0)
    assert baseline != tuned


def test_convert_ignores_drawing_constants():
    """A colour cannot change a trade, so it must not fail a conversion."""
    source = (
        '//@version=6\nstrategy("S")\n'
        "ema = ta.sma(close, 200)\n"
        "col = close > ema ? color.green : color.red\n"
        "plot(ema, color=col)\n"
    )
    result = convert(source)
    assert result.ok, result.unsupported
    assert any("color.green" in item for item in result.ignored)


# --- user-defined functions: inlined at the call site ------------------------

FUNCTION_STRATEGY = """//@version=6
strategy("Functions")
band = input.float(1.0, "Band")
z(src, len) =>
    m = ta.sma(src, len)
    s = ta.stdev(src, len)
    (src - m) / s
grade(v) =>
    if v > band
        1.0
    else if v < -band
        -1.0
    else
        0.0
score = grade(z(close, 20))
if score > 0.5
    strategy.entry("l", strategy.long)
if score < -0.5
    strategy.close()
"""


def test_a_one_line_function_is_inlined_at_the_call_site():
    """The body takes the place of the call, with the arguments substituted."""
    source = (
        '//@version=6\nstrategy("S")\n'
        "clamp01(x) => math.max(0.0, math.min(1.0, x))\n"
        "score = clamp01(close / open)\n"
        "if score > 0.5\n    strategy.close()\n"
    )
    result = convert(source)
    assert result.ok, result.unsupported
    assert (
        "score = max(0, min(1, (self.data.close[0] / self.data.open[0])))"
        in result.code
    )


def test_a_body_local_is_substituted_wherever_it_is_read():
    """Locals fold into the expression rather than becoming Python names."""
    source = (
        '//@version=6\nstrategy("S")\n'
        "z(src, len) =>\n"
        "    m = ta.sma(src, len)\n"
        "    s = ta.stdev(src, len)\n"
        "    (src - m) / s\n"
        "v = z(close, 20)\n"
        "if v > 1.0\n    strategy.close()\n"
    )
    result = convert(source)
    assert result.ok, result.unsupported
    assert "bt.indicators.SMA(self.data.close, period=20)" in result.code
    assert "bt.indicators.StandardDeviation(self.data.close, period=20)" in result.code
    assert (
        "v = ((self.data.close[0] - self._sma_1[0]) / self._standarddeviation_2[0])"
        in result.code
    )


def test_an_argument_can_supply_an_indicator_length():
    """Substitution happens before lowering, so `ta.sma(src, len)` resolves.

    A Backtrader indicator fixes its period when it is constructed, so a
    length that is still a parameter cannot be built. Inlining removes the
    parameter: by the time the call is lowered the length is the literal the
    call site passed.
    """
    source = (
        '//@version=6\nstrategy("S")\n'
        "smooth(src, len) => ta.sma(src, len)\n"
        "fast = smooth(close, 5)\n"
        "slow = smooth(close, 30)\n"
        "if fast > slow\n    strategy.close()\n"
    )
    result = convert(source)
    assert result.ok, result.unsupported
    assert "bt.indicators.SMA(self.data.close, period=5)" in result.code
    assert "bt.indicators.SMA(self.data.close, period=30)" in result.code


def test_a_function_wrapping_an_indicator_still_becomes_a_line():
    """`ma = smooth(close, 20)` must be a line object, not a read of one.

    Otherwise `ma[1]` -- history -- stops working for no reason the caller
    could see from the Pine source.
    """
    source = (
        '//@version=6\nstrategy("S")\n'
        "smooth(src, n) => ta.sma(src, n)\n"
        "ma = smooth(close, 20)\n"
        "if ma > ma[1]\n    strategy.close()\n"
    )
    result = convert(source)
    assert result.ok, result.unsupported
    assert "if (self._sma_1[0] > self._sma_1[-1]):" in result.code


def test_two_call_sites_do_not_share_state():
    """Pine gives each call site its own instance, which inlining reproduces."""
    source = (
        '//@version=6\nstrategy("S")\n'
        "roc(src, n) => src - src[n]\n"
        "a = roc(close, 1)\n"
        "b = roc(high, 5)\n"
        "if a > b\n    strategy.close()\n"
    )
    result = convert(source)
    assert result.ok, result.unsupported
    assert "a = (self.data.close[0] - self.data.close[-1])" in result.code
    assert "b = (self.data.high[0] - self.data.high[-5])" in result.code


def test_a_parameter_default_fills_an_omitted_argument():
    source = (
        '//@version=6\nstrategy("S")\n'
        "lever(x, float k = 2.0) => x * k\n"
        "a = lever(close)\n"
        "b = lever(close, 3.0)\n"
        "if a > b\n    strategy.close()\n"
    )
    result = convert(source)
    assert result.ok, result.unsupported
    assert "a = (self.data.close[0] * 2)" in result.code
    assert "b = (self.data.close[0] * 3)" in result.code


def test_an_argument_may_be_passed_by_name():
    source = (
        '//@version=6\nstrategy("S")\n'
        "band(src, mult) => src * mult\n"
        "b = band(close, mult=2.0)\n"
        "if close > b\n    strategy.close()\n"
    )
    result = convert(source)
    assert result.ok, result.unsupported
    assert "b = (self.data.close[0] * 2)" in result.code


def test_a_function_calling_another_is_resolved_all_the_way_down():
    source = (
        '//@version=6\nstrategy("S")\n'
        "half(x) => x / 2\n"
        "mid(a, b) => half(a + b)\n"
        "m = mid(high, low)\n"
        "if close > m\n    strategy.close()\n"
    )
    result = convert(source)
    assert result.ok, result.unsupported
    assert "m = ((self.data.high[0] + self.data.low[0]) / 2)" in result.code


def test_a_trailing_if_is_the_functions_value():
    """Pine hands back the last expression of whichever branch ran."""
    source = (
        '//@version=6\nstrategy("S")\n'
        "sign(v) =>\n"
        "    if v > 0\n        1\n"
        "    else if v < 0\n        -1\n"
        "    else\n        0\n"
        "s = sign(close - open)\n"
        "if s > 0\n    strategy.close()\n"
    )
    result = convert(source)
    assert result.ok, result.unsupported
    assert "1 if ((self.data.close[0] - self.data.open[0]) > 0)" in result.code
    assert (
        "(-1) if ((self.data.close[0] - self.data.open[0]) < 0) else 0" in result.code
    )


def test_a_trailing_switch_is_the_functions_value():
    """A bare switch is a statement at top level and a value inside a body."""
    source = (
        '//@version=6\nstrategy("S")\n'
        "smooth(src, len, mode) =>\n"
        "    switch mode\n"
        '        "SMA" => ta.sma(src, len)\n'
        "        =>      ta.ema(src, len)\n"
        's = smooth(close, 14, "SMA")\n'
        "if close > s\n    strategy.close()\n"
    )
    result = convert(source)
    assert result.ok, result.unsupported
    assert "bt.indicators.SMA(self.data.close, period=14)" in result.code
    assert "bt.indicators.EMA(self.data.close, period=14)" in result.code


def test_a_bare_switch_at_top_level_is_still_a_statement():
    """The value reading must not leak out of a function body."""
    source = (
        '//@version=6\nstrategy("S")\nmode = input.string("a", "M")\n'
        "switch mode\n"
        '    "a" => strategy.close()\n'
        "ma = ta.sma(close, 10)\n"
    )
    result = convert(source)
    assert not result.ok
    assert any("switch statement" in item for item in result.unsupported)


def test_a_reassigned_local_reads_as_its_latest_value():
    """`:=` rebinds, so earlier reads keep the earlier value."""
    source = (
        '//@version=6\nstrategy("S")\n'
        "step(x) =>\n"
        "    a = x + 1\n"
        "    a := a * 2\n"
        "    a\n"
        "v = step(close)\n"
        "if v > 0\n    strategy.close()\n"
    )
    result = convert(source)
    assert result.ok, result.unsupported
    assert "v = ((self.data.close[0] + 1) * 2)" in result.code


def test_a_recursive_function_is_reported_not_followed():
    source = (
        '//@version=6\nstrategy("S")\n'
        "f(x) => f(x) + 1\n"
        "y = f(close)\n"
        "if y > 0\n    strategy.close()\n"
    )
    result = convert(source)
    assert not result.ok
    assert any("recursive" in item for item in result.unsupported)


def test_mutual_recursion_is_reported_not_followed():
    """A stack catches two functions calling each other, which a flag would not."""
    source = (
        '//@version=6\nstrategy("S")\n'
        "g(x) => h(x) + 1\n"
        "h(x) => g(x) + 1\n"
        "y = g(close)\n"
        "if y > 0\n    strategy.close()\n"
    )
    result = convert(source)
    assert not result.ok
    assert any("recursive" in item for item in result.unsupported)


def test_var_inside_a_body_becomes_an_attribute():
    """Pine keeps a `var` per call site, so each call site gets an attribute."""
    source = (
        '//@version=6\nstrategy("S")\n'
        "acc(x) =>\n"
        "    var float total = 0.0\n"
        "    total := total + x\n"
        "    total\n"
        "y = acc(close)\n"
        "if y > 0\n    strategy.close()\n"
    )
    result = convert(source)
    assert result.ok, result.unsupported
    assert "self._acc_total_1 = 0" in result.code
    assert "self._acc_total_1 = (self._acc_total_1 + self.data.close[0])" in result.code


def test_a_missing_argument_is_reported():
    source = '//@version=6\nstrategy("S")\nf(a, b) => a + b\ny = f(close)\n'
    result = convert(source)
    assert not result.ok
    assert any("no argument for 'b'" in item for item in result.unsupported)


def test_a_function_returning_a_tuple_is_reported():
    """`[lower, upper]` needs a destructuring call site, which is refused."""
    source = (
        '//@version=6\nstrategy("S")\n'
        "bands(src, n) =>\n"
        "    m = ta.sma(src, n)\n"
        "    [m - 1.0, m + 1.0]\n"
        "b = bands(close, 20)\n"
    )
    result = convert(source)
    assert not result.ok
    assert any("returns a tuple" in item for item in result.unsupported)


def test_destructuring_inside_a_body_is_still_read_as_a_target_list():
    """The `=` past the bracket is what separates the two readings."""
    program = parse(
        '//@version=6\nstrategy("S")\n'
        "f(x) =>\n"
        "    [a, b] = ta.macd(x, 12, 26, 9)\n"
        "    a + b\n"
        "y = f(close)\n"
    )
    body = program.functions["f"].body
    assert [type(node).__name__ for node in body] == ["TupleAssign", "ExprStmt"]


def test_a_body_the_grammar_cannot_read_is_reported_not_fatal():
    """Reading a body is best-effort; one outside the subset is still skipped.

    Pine allows several declarations on one line, which this grammar does not
    model. Failing the whole file over it would tell the caller far less than
    naming the one function it could not read.
    """
    source = (
        '//@version=6\nstrategy("S")\n'
        "f(src, len) =>\n"
        "    var float a = na, var float b = 0.0\n"
        "    a + b\n"
        "ma = ta.sma(close, 10)\n"
    )
    result = convert(source)
    assert any("user-defined function" in item for item in result.unsupported)
    assert not any("could not parse" in item for item in result.unsupported)


def test_parsing_resumes_after_a_body_the_grammar_cannot_read():
    program = parse(
        '//@version=6\nstrategy("S")\n'
        "f(src, len) =>\n"
        "    var float a = na, var float b = 0.0\n"
        "    a + b\n"
        "y = ta.sma(close, 10)\n"
    )
    assert isinstance(program.body[-1], Assign)
    assert program.body[-1].target == "y"


def test_runaway_inlining_is_reported_rather_than_emitted():
    """Substitution copies a local per read, so nesting multiplies.

    The guard exists because the alternative is a single expression thousands
    of nodes wide, which is neither readable nor what the author meant.
    """
    source = (
        '//@version=6\nstrategy("S")\n'
        "a(x) => x + x\n"
        "b(x) => a(x) + a(x)\n"
        "c(x) => b(x) + b(x)\n"
        "d(x) => c(x) + c(x)\n"
        "e(x) => d(x) + d(x)\n"
        "g(x) => e(x) + e(x)\n"
        "h(x) => g(x) + g(x)\n"
        "i(x) => h(x) + h(x)\n"
        "y = i(close)\n"
    )
    result = convert(source)
    assert not result.ok
    assert any("expands past" in item for item in result.unsupported)


def test_a_signed_branch_value_is_not_eaten_as_a_continuation():
    """`-1` under a block opener is that block's body, not the tail above.

    Both readings are legal for a line starting with `-`, and only the line
    above can decide. Getting it wrong silently turned `else if v < 0` plus a
    branch of `-1` into the expression `v < 0 - 1`.
    """
    source = (
        '//@version=6\nstrategy("S")\n'
        "x = if close > open\n    1\nelse\n    -1\n"
        "if x > 0\n    strategy.close()\n"
    )
    result = convert(source)
    assert result.ok, result.unsupported
    assert "1 if (self.data.close[0] > self.data.open[0]) else (-1)" in result.code


def test_a_genuine_signed_continuation_still_joins():
    """The guard must not break the continuation it was carved out of."""
    split = (
        '//@version=6\nstrategy("S")\n'
        "spread = high\n         - low\n"
        "if spread > 0\n    strategy.close()\n"
    )
    joined = (
        '//@version=6\nstrategy("S")\n'
        "spread = high - low\n"
        "if spread > 0\n    strategy.close()\n"
    )
    assert convert(split).code == convert(joined).code


def test_generated_function_strategy_runs_and_trades():
    value, closed = _run(FUNCTION_STRATEGY)
    assert closed > 0
    assert value != 10_000.0


def test_generated_function_strategy_responds_to_its_input():
    """The param reaches the inlined body, so the knob is live end to end."""
    _, tight = _run(FUNCTION_STRATEGY, band=0.5)
    _, wide = _run(FUNCTION_STRATEGY, band=3.0)
    assert tight != wide


# --- user-defined functions: var state across bars ---------------------------

JMA_STRATEGY = """//@version=6
strategy("JMA")
length = input.int(14, "Length")
f_jma(src, len, phase, power) =>
    var float jma = na
    var float e0  = na
    var float e1  = na
    var float e2  = na
    _pr    = phase < -100 ? 0.5 : phase > 100 ? 2.5 : phase / 100.0 + 1.5
    _beta  = 0.45 * (len - 1) / (0.45 * (len - 1) + 2)
    _alpha = math.pow(_beta, power)
    e0    := (1 - _alpha) * src + _alpha * nz(e0[1], src)
    e1    := (src - e0) * (1 - _beta) + _beta * nz(e1[1], 0)
    e2    := (e0 + _pr * e1 - nz(jma[1], src)) * math.pow(1 - _alpha, 2) + math.pow(_alpha, 2) * nz(e2[1], 0)
    jma   := nz(jma[1], src) + e2
    jma
smooth = f_jma(close, length, 0, 2.0)
if close > smooth
    strategy.entry("l", strategy.long)
if close < smooth
    strategy.close()
"""


def test_a_stateful_body_emits_updates_in_source_order():
    """The `var` updates are statements, not part of one expression.

    A pure body folds into a single expression. State has to be *updated*, in
    order, once per bar, so it becomes lines in front of the statement that
    asked for the value.
    """
    source = (
        '//@version=6\nstrategy("S")\n'
        "f(x) =>\n"
        "    var float a = 0.0\n"
        "    var float b = 0.0\n"
        "    a := a + x\n"
        "    b := b + a\n"
        "    b\n"
        "y = f(close)\n"
        "if y > 0\n    strategy.close()\n"
    )
    result = convert(source)
    assert result.ok, result.unsupported
    body = result.code.split("def next(self):")[1]
    first = body.index("self._f_a_1 = (self._f_a_1 + self.data.close[0])")
    second = body.index("self._f_b_2 = (self._f_b_2 + self._f_a_1)")
    assert first < second


def test_each_call_site_gets_its_own_state():
    """Two calls to one filter are two filters, and Pine says so."""
    source = (
        '//@version=6\nstrategy("S")\n'
        "f(src, n) =>\n"
        "    var float acc = 0.0\n"
        "    acc := acc * (1 - 1.0 / n) + src / n\n"
        "    acc\n"
        "a = f(close, 5)\n"
        "b = f(close, 20)\n"
        "if a > b\n    strategy.close()\n"
    )
    result = convert(source)
    assert result.ok, result.unsupported
    setup = result.code.split("def __init__")[1].split("def next")[0]
    assert setup.count("= 0") == 2, setup
    assert "self._f_acc_1" in setup and "self._f_acc_3" in setup


def test_history_of_a_var_reads_the_previous_bar_before_its_write():
    """`nz(e0[1], src)` wants the value from last bar, and gets it.

    One attribute holds one value. Read before this bar's assignment it still
    holds the previous bar's, which is exactly what Pine's `[1]` means here.
    """
    source = (
        '//@version=6\nstrategy("S")\n'
        "f(x) =>\n"
        "    var float e = na\n"
        "    e := 0.5 * x + 0.5 * nz(e[1], x)\n"
        "    e\n"
        "y = f(close)\n"
        "if y > 0\n    strategy.close()\n"
    )
    result = convert(source)
    assert result.ok, result.unsupported
    assert (
        "self._f_e_1 = ((0.5 * self.data.close[0]) + (0.5 * "
        "(self._f_e_1 if self._f_e_1 == self._f_e_1 else self.data.close[0])))"
    ) in result.code


def test_a_bare_var_read_after_its_write_is_this_bars_value():
    """Pine's bare `e0` after `e0 :=` is the new value, and so is this."""
    source = (
        '//@version=6\nstrategy("S")\n'
        "f(x) =>\n"
        "    var float e = 0.0\n"
        "    e := x * 2\n"
        "    d = x - e\n"
        "    d\n"
        "y = f(close)\n"
        "if y > 0\n    strategy.close()\n"
    )
    result = convert(source)
    assert result.ok, result.unsupported
    assert "f_d_2 = (self.data.close[0] - self._f_e_1)" in result.code


def test_history_of_a_var_read_after_its_write_is_reported():
    """After the assignment the attribute holds this bar, not the last one."""
    source = (
        '//@version=6\nstrategy("S")\n'
        "f(x) =>\n"
        "    var float a = 0.0\n"
        "    a := a + x\n"
        "    b = a[1]\n"
        "    b\n"
        "y = f(close)\n"
    )
    result = convert(source)
    assert not result.ok
    assert any("read after this bar's assignment" in i for i in result.unsupported)


def test_two_bars_of_var_history_is_reported():
    """One attribute cannot answer `a[2]`, and guessing would be worse."""
    source = (
        '//@version=6\nstrategy("S")\n'
        "f(x) =>\n"
        "    var float a = 0.0\n"
        "    a := x + nz(a[2], 0)\n"
        "    a\n"
        "y = f(close)\n"
    )
    result = convert(source)
    assert not result.ok
    assert any("a: a var holds one value" in i for i in result.unsupported)


def test_a_stateful_call_from_inside_an_if_is_reported():
    """Pine updates the state every bar wherever the call sits.

    Emitting the updates under an `if` would update them only on the bars the
    condition held, which is a different strategy -- so it is refused rather
    than emitted.
    """
    source = (
        '//@version=6\nstrategy("S")\n'
        "f(x) =>\n"
        "    var float a = 0.0\n"
        "    a := a + x\n"
        "    a\n"
        "if close > open\n    y = f(close)\n    strategy.close()\n"
    )
    result = convert(source)
    assert not result.ok
    assert any("top-level statement" in i for i in result.unsupported)


def test_varip_in_a_body_carries_state_the_way_var_does():
    """A function body gets the same reading, and keeps the per-call-site
    state that makes an inlined Pine function behave like Pine's."""
    source = (
        '//@version=6\nstrategy("S")\n'
        "f(x) =>\n"
        "    varip float a = 0.0\n"
        "    a := a + x\n"
        "    a\n"
        "y = f(close)\n"
        "if y > 0\n"
        '    strategy.entry("L", strategy.long)\n'
    )
    result = convert(source)
    assert result.ok, result.unsupported
    assert any("varip read as var" in i for i in result.notes)

    namespace = {}
    exec(compile(result.code, "<converted>", "exec"), namespace)
    seen = []

    class Watched(namespace[result.class_name]):
        def next(self):
            super().next()
            seen.append(self.position.size)

    cerebro = bt.Cerebro()
    cerebro.adddata(bt.feeds.PandasData(dataname=_price_frame(bars=40)))
    cerebro.addstrategy(Watched)
    cerebro.broker.setcash(100_000.0)
    cerebro.run()
    # The running total climbs from the first bar, so the entry fires and the
    # state is genuinely carried rather than reset each bar.
    assert seen[-1] > 0


def test_a_non_literal_var_initial_value_is_reported():
    """`__init__` runs before there is a first bar to read `close` from."""
    source = (
        '//@version=6\nstrategy("S")\n'
        "f(x) =>\n"
        "    var float a = close\n"
        "    a := a + x\n"
        "    a\n"
        "y = f(close)\n"
    )
    result = convert(source)
    assert not result.ok
    assert any("literal initial value" in i for i in result.unsupported)


@pytest.mark.parametrize(
    "call,expected",
    [
        ("math.pow(close, 2)", "(self.data.close[0] ** 2)"),
        ("math.sqrt(close)", "math.sqrt(self.data.close[0])"),
        ("math.log(close)", "math.log(self.data.close[0])"),
        ("math.exp(close)", "math.exp(self.data.close[0])"),
        ("math.floor(close)", "math.floor(self.data.close[0])"),
        ("math.sign(close)", "((self.data.close[0] > 0) - (self.data.close[0] < 0))"),
        ("math.avg(high, low)", "((self.data.high[0] + self.data.low[0]) / 2)"),
    ],
)
def test_scalar_math_functions(call, expected):
    """The JMA needs `math.pow`; the rest of the set comes free with it."""
    source = (
        '//@version=6\nstrategy("S")\n'
        f"v = {call}\n"
        "if v > 0\n    strategy.close()\n"
    )
    result = convert(source)
    assert result.ok, result.unsupported
    assert f"v = {expected}" in result.code


def test_math_import_is_emitted_only_when_it_is_used():
    used = convert(
        '//@version=6\nstrategy("S")\nv = math.sqrt(close)\n'
        "if v > 0\n    strategy.close()\n"
    )
    unused = convert(
        '//@version=6\nstrategy("S")\nv = math.pow(close, 2)\n'
        "if v > 0\n    strategy.close()\n"
    )
    assert "import math" in used.code
    assert "import math" not in unused.code


def test_generated_jma_strategy_runs_and_trades():
    """The real filter from the corpus, executed on a real cerebro."""
    value, closed = _run(JMA_STRATEGY)
    assert closed > 0
    assert value != 10_000.0


def test_generated_jma_state_actually_carries_across_bars():
    """A filter that reset every bar would track close exactly and never trade.

    The point of `var` is that the attribute survives `next()`. If it did not,
    `jma` would collapse to a function of this bar alone and the crossing
    tests would behave completely differently.
    """
    _, fast = _run(JMA_STRATEGY, length=5)
    _, slow = _run(JMA_STRATEGY, length=60)
    assert fast != slow


# --- barstate: where the script sits in the chart's history ------------------

BARSTATE_STRATEGY = """//@version=6
strategy("Barstate")
confirmOnly = input.bool(true, "Confirm close only")
ma = ta.sma(close, 10)
raw = close > ma
signal = raw and (not confirmOnly or barstate.isconfirmed)
if signal
    strategy.entry("l", strategy.long)
if close < ma
    strategy.close()
if barstate.islast
    strategy.close()
"""


@pytest.mark.parametrize(
    "name,expected",
    [
        ("barstate.isconfirmed", "True"),
        ("barstate.isnew", "True"),
        ("barstate.ishistory", "True"),
        ("barstate.isrealtime", "False"),
        ("barstate.isfirst", "(len(self) == 1)"),
        ("barstate.islast", "(len(self) == self.data.buflen())"),
        ("barstate.islastconfirmedhistory", "(len(self) == self.data.buflen())"),
    ],
)
def test_barstate_identifiers(name, expected):
    """A bar-close backtest already knows where it is in the history."""
    source = (
        '//@version=6\nstrategy("S")\n' f"v = {name}\n" "if v\n    strategy.close()\n"
    )
    result = convert(source)
    assert result.ok, result.unsupported
    assert f"v = {expected}" in result.code


def test_barstate_positions_are_parenthesised():
    """`len(self) == buflen()` bare would rebind under `*`, silently.

    `==` binds looser than arithmetic, so an unparenthesised comparison
    dropped into a larger expression means something else entirely.
    """
    source = (
        '//@version=6\nstrategy("S")\n'
        "n = (barstate.islast ? 1 : 0) * 2\n"
        "if close > n\n    strategy.close()\n"
    )
    result = convert(source)
    assert result.ok, result.unsupported
    assert "n = ((1 if (len(self) == self.data.buflen()) else 0) * 2)" in result.code


def test_a_confirmed_bar_guard_becomes_a_no_op():
    """Every bar `next()` sees has closed, so the repaint guard is vacuous."""
    source = (
        '//@version=6\nstrategy("S")\n'
        "ma = ta.sma(close, 10)\n"
        "signal = close > ma and barstate.isconfirmed\n"
        "if signal\n    strategy.close()\n"
    )
    result = convert(source)
    assert result.ok, result.unsupported
    assert "signal = ((self.data.close[0] > self._sma_1[0]) and True)" in result.code


def test_barstate_history_is_reported():
    """`barstate.isconfirmed[1]` is a series read this cannot answer."""
    source = (
        '//@version=6\nstrategy("S")\n'
        "v = barstate.isconfirmed[1]\n"
        "if v\n    strategy.close()\n"
    )
    result = convert(source)
    assert not result.ok
    assert any("history of this builtin" in i for i in result.unsupported)


def test_generated_barstate_strategy_runs_and_trades():
    value, closed = _run(BARSTATE_STRATEGY)
    assert closed > 0
    assert value != 10_000.0


def test_generated_isfirst_and_islast_fire_on_exactly_one_bar_each():
    """`islast` and `isfirst` are positions in the feed, not constants.

    Counting them needs `var`, because trades cannot see either one: an order
    placed on the last bar has no next bar to fill on, so a strategy that
    closes on `islast` finishes with the position still open and the trade
    count says nothing. A counter says everything -- wrong in either
    direction, never or every bar, shows up as 0 or 300.
    """
    source = (
        '//@version=6\nstrategy("S")\n'
        "var int firstBars = 0\n"
        "var int lastBars = 0\n"
        "if barstate.isfirst\n    firstBars := firstBars + 1\n"
        "if barstate.islast\n    lastBars := lastBars + 1\n"
        "if close > open\n    strategy.close()\n"
    )
    result = convert(source)
    assert result.ok, result.unsupported

    namespace = {}
    exec(compile(result.code, "<converted>", "exec"), namespace)
    cerebro = bt.Cerebro()
    frame = _price_frame()
    cerebro.adddata(bt.feeds.PandasData(dataname=frame))
    cerebro.addstrategy(namespace[result.class_name])
    strategy = cerebro.run()[0]

    assert len(frame) > 1
    assert strategy.firstBars == 1
    assert strategy.lastBars == 1


# --- strategy.entry: one entry per direction ---------------------------------


def _run_strategy(source, bars=300, **params):
    """Run a converted strategy and hand back the instance itself.

    Some behaviour is only visible on the strategy -- counters it kept, the
    position it held -- rather than in the value or trade count `_run` returns.
    """
    result = convert(source)
    assert result.ok, f"conversion reported: {result.unsupported}"
    namespace = {}
    exec(compile(result.code, "<converted>", "exec"), namespace)
    cerebro = bt.Cerebro()
    cerebro.adddata(bt.feeds.PandasData(dataname=_price_frame(bars=bars)))
    cerebro.addstrategy(namespace[result.class_name], **params)
    cerebro.broker.setcash(10_000.0)
    cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name="trades")
    return cerebro.run()[0]


REPEAT_ENTRY = """//@version=6
strategy("Repeat")
ma = ta.sma(close, 10)
if close > ma
    strategy.entry("l", strategy.long)
if close < ma
    strategy.close()
"""


def test_a_repeated_entry_does_not_add_to_the_position():
    """Pine's default `pyramiding=0` allows one entry per direction.

    `self.buy()` has no such rule -- it adds every time -- so a condition
    holding for twenty bars running built a twenty-unit position where Pine
    holds one. On this feed the position reached **21 units** before this was
    fixed.

    Both halves are checked, because the two ways of getting this wrong show
    up in different places. Adding to the position moves the *size* (21, not
    1) and leaves the trade count alone. Closing and reopening instead moves
    the *trade count* (146, not 26, each lasting a single bar) and leaves the
    size alone. Only asserting one of them lets the other through.
    """
    result = convert(REPEAT_ENTRY)
    assert result.ok, result.unsupported
    namespace = {}
    exec(compile(result.code, "<converted>", "exec"), namespace)

    seen = []
    base = namespace[result.class_name]

    class Watched(base):
        def next(self):
            super().next()
            seen.append(self.position.size)

    cerebro = bt.Cerebro()
    cerebro.adddata(bt.feeds.PandasData(dataname=_price_frame()))
    cerebro.addstrategy(Watched)
    cerebro.broker.setcash(10_000.0)
    cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name="trades")
    analysis = cerebro.run()[0].analyzers.trades.get_analysis()

    assert set(seen) == {0, 1}, f"position reached {max(seen)}"
    assert analysis["len"]["average"] > 1.0, "every trade lasted a single bar"


def test_an_entry_against_the_position_reverses_it():
    """Pine closes the old side and opens the new one; so does this."""
    source = (
        '//@version=6\nstrategy("S")\n'
        "ma = ta.sma(close, 10)\n"
        "if close > ma\n"
        '    strategy.entry("l", strategy.long)\n'
        "if close < ma\n"
        '    strategy.entry("s", strategy.short)\n'
    )
    result = convert(source)
    assert result.ok, result.unsupported
    namespace = {}
    exec(compile(result.code, "<converted>", "exec"), namespace)

    seen = []
    base = namespace[result.class_name]

    class Watched(base):
        def next(self):
            super().next()
            seen.append(self.position.size)

    cerebro = bt.Cerebro()
    cerebro.adddata(bt.feeds.PandasData(dataname=_price_frame()))
    cerebro.addstrategy(Watched)
    cerebro.broker.setcash(10_000.0)
    cerebro.run()

    assert set(seen) <= {-1, 0, 1}, sorted(set(seen))
    assert 1 in seen and -1 in seen


def test_pyramiding_above_zero_is_reported():
    """Above zero Pine and a netted position genuinely differ."""
    source = (
        '//@version=6\nstrategy("S", pyramiding=3)\n'
        "if close > open\n"
        '    strategy.entry("l", strategy.long)\n'
    )
    result = convert(source)
    assert not result.ok
    assert any("pyramiding" in item for item in result.unsupported)


def test_pyramiding_of_zero_is_the_default_and_passes():
    source = (
        '//@version=6\nstrategy("S", pyramiding=0)\n'
        "if close > open\n"
        '    strategy.entry("l", strategy.long)\n'
    )
    assert convert(source).ok


# --- strategy.*trades: the counters and the ledger ---------------------------

COUNTER_STRATEGY = """//@version=6
strategy("Counters")
ma = ta.sma(close, 10)
var int seenClosed = 0
var int seenWins = 0
var int seenLosses = 0
var int newLossEvents = 0
seenClosed := strategy.closedtrades
seenWins := strategy.wintrades
seenLosses := strategy.losstrades
if strategy.losstrades > strategy.losstrades[1]
    newLossEvents := newLossEvents + 1
if close > ma
    strategy.entry("l", strategy.long)
if close < ma
    strategy.close()
"""


def test_trade_counters_agree_with_backtraders_own_analyzer():
    """The ledger is built by hand, so it is checked against something else.

    `TradeAnalyzer` counts the same trades from the same notifications by a
    different route. If the two agree there is no plausible way the counters
    are wrong.
    """
    strategy = _run_strategy(COUNTER_STRATEGY)
    analysis = strategy.analyzers.trades.get_analysis()

    assert analysis["total"]["closed"] > 0
    assert strategy.seenClosed == analysis["total"]["closed"]
    assert strategy.seenWins == analysis["won"]["total"]
    assert strategy.seenLosses == analysis["lost"]["total"]
    assert strategy.seenWins + strategy.seenLosses <= strategy.seenClosed


def test_a_counter_history_fires_once_per_trade():
    """`strategy.losstrades > strategy.losstrades[1]` is 'did a loss book'."""
    strategy = _run_strategy(COUNTER_STRATEGY)
    assert strategy.newLossEvents == strategy.seenLosses
    assert strategy.newLossEvents > 0


def test_the_counter_snapshot_is_taken_at_the_end_of_the_bar():
    """That is what makes `[1]` the previous bar rather than this one.

    A trade closing on bar N is notified before `next()` runs, so by the time
    the body reads the counter it has already moved. Only the snapshot holds
    what it was.
    """
    result = convert(COUNTER_STRATEGY)
    assert result.ok, result.unsupported
    body = result.code.split("def next(self):")[1].rstrip().splitlines()
    assert body[-1].strip().startswith("self._pine_prev = (")


def test_trade_accessors_read_the_ledger():
    source = (
        '//@version=6\nstrategy("S")\n'
        "ma = ta.sma(close, 10)\n"
        "var float lastEntry = 0.0\n"
        "var float lastExit = 0.0\n"
        "var float lastProfit = 0.0\n"
        "var int heldBars = 0\n"
        "if strategy.closedtrades > 0\n"
        "    lastEntry := strategy.closedtrades.entry_price(strategy.closedtrades - 1)\n"
        "    lastExit := strategy.closedtrades.exit_price(strategy.closedtrades - 1)\n"
        "    lastProfit := strategy.closedtrades.profit(strategy.closedtrades - 1)\n"
        "    heldBars := strategy.closedtrades.exit_bar_index(strategy.closedtrades - 1)"
        " - strategy.closedtrades.entry_bar_index(strategy.closedtrades - 1)\n"
        "if close > ma\n"
        '    strategy.entry("l", strategy.long)\n'
        "if close < ma\n"
        "    strategy.close()\n"
    )
    strategy = _run_strategy(source)
    record = strategy._pine_closed[-1]

    assert strategy.lastEntry == record["entry_price"]
    assert strategy.lastExit == record["exit_price"]
    assert strategy.lastProfit == record["profit"]
    assert strategy.heldBars == record["exit_bar"] - record["entry_bar"]
    # The exit price comes from the fill, not from arithmetic on the P&L.
    assert record["exit_price"] is not None
    assert record["exit_bar"] > record["entry_bar"]


def test_an_out_of_range_trade_index_is_na_rather_than_a_crash():
    """Pine answers `na`; Python would either raise or count from the end."""
    source = (
        '//@version=6\nstrategy("S")\n'
        "var float missing = 0.0\n"
        "var float negative = 0.0\n"
        "missing := strategy.closedtrades.entry_price(9999)\n"
        "negative := strategy.closedtrades.entry_price(-1)\n"
        "if close > open\n    strategy.close()\n"
    )
    strategy = _run_strategy(source)
    assert strategy.missing != strategy.missing
    assert strategy.negative != strategy.negative


def test_the_trade_ledger_is_only_emitted_when_it_is_used():
    plain = convert(REPEAT_ENTRY)
    using = convert(COUNTER_STRATEGY)
    assert "_pine_closed" not in plain.code
    assert "def notify_trade" not in plain.code
    assert "_pine_closed" in using.code


@pytest.mark.parametrize(
    "field",
    ["max_runup", "max_drawdown", "commission", "exit_comment", "entry_id"],
)
def test_untracked_trade_fields_are_named_individually(field):
    """ "unknown identifier" would not tell the caller what to go and add."""
    source = (
        '//@version=6\nstrategy("S")\n'
        f"v = strategy.closedtrades.{field}(0)\n"
        "if v > 0\n    strategy.close()\n"
    )
    result = convert(source)
    assert not result.ok
    assert any(f"strategy.closedtrades.{field}()" in i for i in result.unsupported)


def test_more_than_one_bar_of_counter_history_is_reported():
    source = (
        '//@version=6\nstrategy("S")\n'
        "v = strategy.closedtrades[2]\n"
        "if v > 0\n    strategy.close()\n"
    )
    result = convert(source)
    assert not result.ok
    assert any("only the previous bar's value" in i for i in result.unsupported)


def test_open_trade_accessors_read_the_live_position():
    source = (
        '//@version=6\nstrategy("S")\n'
        "ma = ta.sma(close, 10)\n"
        "var int barsIn = 0\n"
        "if strategy.opentrades > 0\n"
        "    barsIn := bar_index - strategy.opentrades.entry_bar_index(0)\n"
        "if close > ma\n"
        '    strategy.entry("l", strategy.long)\n'
        "if barsIn > 3\n"
        "    strategy.close()\n"
    )
    strategy = _run_strategy(source)
    assert strategy.barsIn > 0
    assert strategy.analyzers.trades.get_analysis()["total"]["closed"] > 0


# --- ta.pivothigh / ta.pivotlow ----------------------------------------------

PIVOT_STRATEGY = """//@version=6
strategy("Pivots")
leftBars = input.int(5, "Left")
rightBars = input.int(5, "Right")
ph = ta.pivothigh(high, leftBars, rightBars)
pl = ta.pivotlow(low, leftBars, rightBars)
if not na(pl)
    strategy.entry("l", strategy.long)
if not na(ph)
    strategy.close()
"""


def _pivot_reference(values, index, left, right, is_high):
    """Brute force, over the raw list: is ``values[index]`` a pivot?"""
    if index - left < 0 or index + right >= len(values):
        return None
    candidate = values[index]
    window = values[index - left : index] + values[index + 1 : index + 1 + right]
    if is_high:
        found = all(candidate > value for value in window)
    else:
        found = all(candidate < value for value in window)
    return candidate if found else None


def _coarse_frame(bars=200, seed=11):
    """Integer prices, so ties are common and strictness gets exercised."""
    rng = random.Random(seed)
    closes = [float(rng.randint(90, 110)) for _ in range(bars)]
    start = datetime.datetime(2022, 1, 1)
    frame = pd.DataFrame(
        [
            {
                "datetime": start + datetime.timedelta(days=i),
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "volume": 100,
            }
            for i, close in enumerate(closes)
        ]
    ).set_index("datetime")
    return closes, frame


def _pivot_values(frame, left, right, is_high):
    """Every bar's pivot reading from the generated indicator."""
    source = "high" if is_high else "low"
    call = "ta.pivothigh" if is_high else "ta.pivotlow"
    result = convert(
        '//@version=6\nstrategy("S")\n'
        f"v = {call}({source}, {left}, {right})\n"
        "if not na(v)\n    strategy.close()\n"
    )
    assert result.ok, result.unsupported
    namespace = {}
    exec(compile(result.code, "<converted>", "exec"), namespace)

    seen = {}
    base = namespace[result.class_name]

    class Watched(base):
        def next(self):
            super().next()
            seen[len(self) - 1] = self._pivot_1[0]

    cerebro = bt.Cerebro()
    cerebro.adddata(bt.feeds.PandasData(dataname=frame))
    cerebro.addstrategy(Watched)
    cerebro.run()
    return seen


@pytest.mark.parametrize(
    "left,right,is_high",
    [(5, 5, True), (5, 5, False), (2, 3, True), (3, 1, False), (1, 1, True)],
)
def test_pivots_match_a_brute_force_reference(left, right, is_high):
    """Checked against a definition written out longhand, not against itself.

    Pine's rule is strict on both sides -- a flat top, two equal highs side by
    side, is not a pivot -- so the prices here are integers, which makes ties
    common enough that a `>=` would show up immediately.
    """
    closes, frame = _coarse_frame()
    seen = _pivot_values(frame, left, right, is_high)

    hits = 0
    for bar, got in seen.items():
        expected = _pivot_reference(closes, bar - right, left, right, is_high)
        if expected is None:
            assert got != got, f"bar {bar}: expected na, got {got}"
        else:
            assert got == expected, f"bar {bar}: expected {expected}, got {got}"
            hits += 1
    assert hits > 0, "no pivots found at all, so nothing was really checked"


def test_a_pivot_never_reads_a_bar_that_has_not_closed():
    """The `right` offset is what makes it causal, so that is what is tested.

    A pivot is reported only once `right` further bars have closed and
    confirmed it. If any future bar leaked in, adding more data to the end of
    the feed would change readings already taken.
    """
    _, long_frame = _coarse_frame(bars=200)
    _, short_frame = _coarse_frame(bars=150)
    assert long_frame.index[:150].equals(short_frame.index)

    full = _pivot_values(long_frame, 5, 5, True)
    truncated = _pivot_values(short_frame, 5, 5, True)

    for bar, value in truncated.items():
        other = full[bar]
        same = (value != value and other != other) or value == other
        assert same, f"bar {bar} changed from {value} to {other} when the feed grew"


@pytest.mark.parametrize(
    "call,expected",
    [
        ("ta.pivothigh(close, 4, 2)", "PinePivot(self.data.close, left=4, right=2"),
        ("ta.pivothigh(5, 3)", "PinePivot(self.data.high, left=5, right=3"),
        ("ta.pivotlow(5, 3)", "PinePivot(self.data.low, left=5, right=3"),
        (
            "ta.pivothigh(high, leftbars=4, rightbars=2)",
            "PinePivot(self.data.high, left=4, right=2",
        ),
        (
            "ta.pivothigh(high, 4, rightbars=2)",
            "PinePivot(self.data.high, left=4, right=2",
        ),
        (
            "ta.pivotlow(leftbars=4, rightbars=2)",
            "PinePivot(self.data.low, left=4, right=2",
        ),
    ],
)
def test_pivot_argument_forms(call, expected):
    """Pine writes these six ways, and the short form is not `close`."""
    result = convert(
        '//@version=6\nstrategy("S")\n'
        f"v = {call}\n"
        "if not na(v)\n    strategy.close()\n"
    )
    assert result.ok, result.unsupported
    assert expected in result.code


@pytest.mark.parametrize(
    "call,reason",
    [
        ("ta.pivothigh(osc, 5, 5)", "source argument is not a plain series"),
        ("ta.pivothigh(high, n, n)", "could not resolve its left/right bar counts"),
        ("ta.pivothigh(high, 1, 2, 3)", "expects (source, left, right)"),
        ("ta.pivothigh(high)", "expects (source, left, right)"),
    ],
)
def test_pivot_calls_outside_the_subset_are_reported(call, reason):
    """`osc` is a var, and `n` is a per-bar value where a number is needed."""
    source = (
        '//@version=6\nstrategy("S")\n'
        "var float osc = 0.0\n"
        "osc := high - low\n"
        "n = close > open ? 3 : 5\n"
        f"v = {call}\n"
        "if not na(v)\n    strategy.close()\n"
    )
    result = convert(source)
    assert not result.ok
    assert any(reason in item for item in result.unsupported)


def test_the_pivot_indicator_is_only_emitted_when_it_is_used():
    assert "class PinePivot" not in convert(DUAL_MA).code
    assert "class PinePivot" in convert(PIVOT_STRATEGY).code


def test_generated_pivot_strategy_runs_and_trades():
    value, closed = _run(PIVOT_STRATEGY)
    assert closed > 0
    assert value != 10_000.0


def test_generated_pivot_strategy_responds_to_its_bar_counts():
    """The window has to reach the params, or the knobs are decoration."""
    _, tight = _run(PIVOT_STRATEGY, leftBars=2, rightBars=2)
    _, wide = _run(PIVOT_STRATEGY, leftBars=15, rightBars=15)
    assert tight != wide


# --- computed values as indicator sources ------------------------------------

WAVETREND = """//@version=6
strategy("WaveTrend")
chLen = input.int(9, "Channel")
avgLen = input.int(12, "Average")
esa = ta.ema(hlc3, chLen)
d = ta.ema(math.abs(hlc3 - esa), chLen)
ci = (hlc3 - esa) / (0.015 * d)
wt1 = ta.ema(ci, avgLen)
wt2 = ta.sma(wt1, 4)
if ta.crossover(wt1, wt2)
    strategy.entry("l", strategy.long)
if ta.crossunder(wt1, wt2)
    strategy.close()
"""


@pytest.mark.parametrize(
    "source,expected",
    [
        ("hlc3", "(self.data.high + self.data.low + self.data.close) / 3"),
        ("hl2", "(self.data.high + self.data.low) / 2"),
        ("close - open", "(self.data.close - self.data.open)"),
        ("close * 2", "(self.data.close * 2)"),
        ("-close", "(-self.data.close)"),
        ("close[1]", "self.data.close(-1)"),
        ("math.abs(close - open)", "abs((self.data.close - self.data.open))"),
    ],
)
def test_an_expression_can_be_an_indicator_source(source, expected):
    """Backtrader overloads arithmetic on lines, so a composition is a line.

    Note `close[1]` becomes `close(-1)`, the line delayed by a bar, and not
    `close[-1]`, which would be a *read* -- and `__init__` runs before there
    is a bar to read.
    """
    result = convert(
        '//@version=6\nstrategy("S")\n'
        f"ma = ta.sma({source}, 10)\n"
        "if close > ma\n    strategy.close()\n"
    )
    assert result.ok, result.unsupported
    assert expected in result.code


def test_a_computed_local_is_promoted_to_a_line_when_one_is_needed():
    """`ci = ...` then `ta.ema(ci, n)` is the commonest shape in the corpus."""
    result = convert(WAVETREND)
    assert result.ok, result.unsupported
    setup = result.code.split("def __init__")[1].split("def ")[0]
    assert "self._line_ci_3 = ((((self.data.high" in setup
    assert "bt.indicators.EMA(self._line_ci_3, period=self.p.avgLen)" in setup


def test_the_promoted_line_and_the_scalar_agree():
    """The same expression is lowered twice, so the two must not diverge.

    One reads per-bar values in `next()`, the other is composed by Backtrader
    from the same lines. If they disagreed, which one a strategy saw would
    depend on whether anything happened to ask for a line.
    """
    result = convert(WAVETREND)
    assert result.ok, result.unsupported
    namespace = {}
    exec(compile(result.code, "<converted>", "exec"), namespace)

    seen = []
    base = namespace[result.class_name]

    class Watched(base):
        def next(self):
            super().next()
            if len(self) > 60:
                high, low, close = (
                    self.data.high[0],
                    self.data.low[0],
                    self.data.close[0],
                )
                hlc3 = (high + low + close) / 3
                hand = (hlc3 - self._ema_1[0]) / (0.015 * self._ema_2[0])
                seen.append((self._line_ci_3[0], hand))

    cerebro = bt.Cerebro()
    cerebro.adddata(bt.feeds.PandasData(dataname=_price_frame()))
    cerebro.addstrategy(Watched)
    cerebro.run()

    assert len(seen) > 100
    for line_value, hand_value in seen:
        assert line_value == pytest.approx(hand_value, rel=1e-12)


@pytest.mark.parametrize(
    "setup",
    [
        # Reassigned, so it is a different value later.
        "osc = 0.0\nif close > open\n    osc := high - low\n",
        # A var is one number carried forward, not a series.
        "var float osc = 0.0\nosc := high - low\n",
        "osc = high - low\nosc := osc * 2\n",
        # Written on some bars only: a line would compute it on every one.
        "if close > open\n    osc = high - low\n",
        # Two assignments mean two different things.
        "osc = high - low\nosc = close - open\n",
    ],
)
def test_a_value_that_is_not_one_series_is_not_promoted(setup):
    """Promotion has to be refused wherever a single line would be a lie.

    Each case here trips a *different* rule. An earlier version of this test
    looked broad but every case was rejected by the same one, and loosening
    either of the others passed the whole suite.
    """
    result = convert(
        '//@version=6\nstrategy("S")\n' + setup + "ma = ta.sma(osc, 10)\n"
        "if close > ma\n    strategy.close()\n"
    )
    assert not result.ok
    assert any("not a plain series" in item for item in result.unsupported)


def test_a_ternary_becomes_a_per_bar_expression_not_a_line_operation():
    """`bt.If` would compute both branches, which defeats the guard.

    `d != 0 ? x / d : 0` is written precisely so the division does not happen
    when `d` is zero. A line operation computes both arms every bar and
    Backtrader's line division raises, so the expression moves into a Python
    function where `if`/`else` is lazy again.
    """
    result = convert(
        '//@version=6\nstrategy("S")\n'
        "d = high - low\n"
        "safe = d != 0 ? (close - open) / d : 0.0\n"
        "ma = ta.sma(safe, 10)\n"
        "if close > ma\n    strategy.close()\n"
    )
    assert result.ok, result.unsupported
    assert "bt.If(" not in result.code
    assert "PineExpr(" in result.code
    assert "if (a0 != 0) else 0" in result.code


def test_a_guarded_division_does_not_raise_on_a_zero_divisor():
    """The point of the whole exercise, checked on a feed that hits zero."""
    result = convert(
        '//@version=6\nstrategy("S")\n'
        "d = high - low\n"
        "safe = d != 0 ? (close - open) / d : 0.0\n"
        "ma = ta.sma(safe, 5)\n"
        "if close > ma\n    strategy.close()\n"
    )
    assert result.ok, result.unsupported
    namespace = {}
    exec(compile(result.code, "<converted>", "exec"), namespace)

    start = datetime.datetime(2022, 1, 1)
    rows = []
    for i in range(60):
        close = 100.0
        # One bar with high == low makes the divisor exactly zero.
        high, low = (close, close) if i == 30 else (close * 1.01, close * 0.99)
        rows.append(
            {
                "datetime": start + datetime.timedelta(days=i),
                "open": close * 0.999,
                "high": high,
                "low": low,
                "close": close,
                "volume": 1000,
            }
        )
    frame = pd.DataFrame(rows).set_index("datetime")

    cerebro = bt.Cerebro()
    cerebro.adddata(bt.feeds.PandasData(dataname=frame))
    cerebro.addstrategy(namespace[result.class_name])
    cerebro.run()  # a ZeroDivisionError here is the failure


def test_a_pine_expression_reads_the_same_in_both_run_modes():
    """Backtrader runs indicators vectorised by default and stepwise on
    request, and a custom indicator has to mean the same thing in both.

    Leaving `once` to Backtrader's `next` emulation reads an indicator input a
    bar out of step -- silently, and only on some bars.
    """
    result = convert(
        '//@version=6\nstrategy("S")\n'
        "sm = ta.sma(close, 10)\n"
        "v = sm > close ? sm - close : 0.0\n"
        "ma = ta.sma(v, 5)\n"
        "p = ta.pivothigh(sm, 3, 3)\n"
        "if ma > 0 and not na(p)\n    strategy.close()\n"
    )
    assert result.ok, result.unsupported
    # Both custom indicators must be in play, and both over an *indicator*
    # input -- which is the case the emulation gets wrong.
    assert "PineExpr(" in result.code and "PinePivot(" in result.code

    def readings(runonce):
        namespace = {}
        exec(compile(result.code, "<converted>", "exec"), namespace)
        seen = {}
        base = namespace[result.class_name]

        class Watched(base):
            def next(self):
                super().next()
                seen[len(self)] = tuple(
                    getattr(self, name)[0]
                    for name in sorted(self.__dict__)
                    if name.startswith(("_expr_", "_pivot_"))
                )

        cerebro = bt.Cerebro(runonce=runonce)
        cerebro.adddata(bt.feeds.PandasData(dataname=_price_frame()))
        cerebro.addstrategy(Watched)
        cerebro.run()
        return seen

    vectorised, stepwise = readings(True), readings(False)
    assert len(vectorised) > 100
    for bar, pair in vectorised.items():
        other = stepwise[bar]
        for got, want in zip(pair, other):
            same = (got != got and want != want) or got == want
            assert same, f"bar {bar}: {got} != {want}"


@pytest.mark.parametrize(
    "length,expected",
    [
        ("20", "period=20"),
        ("n", "period=self.p.n"),
        ("n * 2", "period=(self.p.n * 2)"),
        ("int(n / 2)", "period=int((self.p.n / 2))"),
    ],
)
def test_an_indicator_length_may_be_computed_from_constants(length, expected):
    result = convert(
        '//@version=6\nstrategy("S")\n'
        'n = input.int(14, "N")\n'
        f"ma = ta.sma(close, {length})\n"
        "if close > ma\n    strategy.close()\n"
    )
    assert result.ok, result.unsupported
    assert expected in result.code


@pytest.mark.parametrize(
    "length", ["close > open ? 3 : 5", "high - low", "ta.sma(close, 5)"]
)
def test_a_length_that_is_only_known_per_bar_is_reported(length):
    """A period is read once, when the indicator is built.

    Lowering one of these as a line produced a class that converted cleanly
    and then died on the first bar, which is worse than reporting it.
    """
    result = convert(
        '//@version=6\nstrategy("S")\n'
        f"n = {length}\n"
        "ma = ta.sma(close, n)\n"
        "if close > ma\n    strategy.close()\n"
    )
    assert not result.ok
    assert any("length argument" in item for item in result.unsupported)


def test_the_two_derived_series_spellings_stay_in_step():
    """One definition, two renderings -- a drift between them is a wrong number."""
    from pwb_toolbox.converting.backtrader import DERIVED_LINES, DERIVED_SERIES

    assert set(DERIVED_LINES) == set(DERIVED_SERIES)
    for name, line in DERIVED_LINES.items():
        assert DERIVED_SERIES[name] == line.replace(".high", ".high[{i}]").replace(
            ".low", ".low[{i}]"
        ).replace(".close", ".close[{i}]").replace(".open", ".open[{i}]")


def test_generated_wavetrend_runs_and_trades():
    """The whole WaveTrend core, which none of this could convert before."""
    value, closed = _run(WAVETREND)
    assert closed > 0
    assert value != 10_000.0


def test_generated_wavetrend_responds_to_its_inputs():
    _, fast = _run(WAVETREND, chLen=4, avgLen=5)
    _, slow = _run(WAVETREND, chLen=30, avgLen=40)
    assert fast != slow


# --- moving averages Backtrader lacks, or spells differently ----------------


def _wma_reference(values, length):
    weights = range(1, length + 1)
    window = values[-length:]
    return sum(v * w for v, w in zip(window, weights)) / sum(weights)


def _hma_reference(values, length):
    """Pine: wma(2*wma(src, len/2) - wma(src, len), round(sqrt(len)))."""
    final = int(round(math.sqrt(length)))
    inner = []
    for back in range(final):
        segment = values[: len(values) - back]
        inner.append(
            2 * _wma_reference(segment, length // 2) - _wma_reference(segment, length)
        )
    inner.reverse()
    weights = range(1, final + 1)
    return sum(v * w for v, w in zip(inner, weights)) / sum(weights)


def _vwma_reference(values, volumes, length):
    traded = sum(v * u for v, u in zip(values[-length:], volumes[-length:]))
    return traded / sum(volumes[-length:])


def _alma_reference(values, length, offset=0.85, sigma=6.0):
    centre = offset * (length - 1)
    spread = length / sigma
    weights = [
        math.exp(-((i - centre) ** 2) / (2 * spread * spread)) for i in range(length)
    ]
    window = values[-length:]
    return sum(v * w for v, w in zip(window, weights)) / sum(weights)


def _volume_frame(bars=80, seed=4):
    rng = random.Random(seed)
    price = 100.0
    start = datetime.datetime(2022, 1, 1)
    closes, volumes, rows = [], [], []
    for i in range(bars):
        price *= 1 + rng.gauss(0, 0.02)
        volume = 1000 + rng.randint(0, 500)
        closes.append(price)
        volumes.append(float(volume))
        rows.append(
            {
                "datetime": start + datetime.timedelta(days=i),
                "open": price,
                "high": price * 1.01,
                "low": price * 0.99,
                "close": price,
                "volume": volume,
            }
        )
    return closes, volumes, pd.DataFrame(rows).set_index("datetime")


def _last_value(source, attribute, frame, at):
    """Run a converted strategy and read one attribute on a chosen bar."""
    result = convert(source)
    assert result.ok, result.unsupported
    namespace = {}
    exec(compile(result.code, "<converted>", "exec"), namespace)
    seen = {}
    base = namespace[result.class_name]

    class Watched(base):
        def next(self):
            super().next()
            seen[len(self)] = getattr(self, attribute)[0]

    cerebro = bt.Cerebro()
    cerebro.adddata(bt.feeds.PandasData(dataname=frame))
    cerebro.addstrategy(Watched)
    cerebro.run()
    return seen[at]


@pytest.mark.parametrize("length", [9, 13, 21, 30])
def test_hma_matches_pines_definition_not_backtraders(length):
    """Backtrader rounds the final window down where Pine rounds it off.

    `int(sqrt(len))` and `round(sqrt(len))` disagree for 24 of the first 59
    lengths -- 13 and 21 among them -- so `bt.indicators.HullMovingAverage`
    would be quietly a different indicator. This is composed from the weighted
    averages Pine says it is made of instead.
    """
    closes, _, frame = _volume_frame()
    source = (
        '//@version=6\nstrategy("S")\n'
        f"h = ta.hma(close, {length})\n"
        "if close > h\n    strategy.close()\n"
    )
    got = _last_value(source, "_hma_3", frame, 75)
    assert got == pytest.approx(_hma_reference(closes[:75], length), rel=1e-12)


def test_hma_differs_from_backtraders_own_hull():
    """Pinning the difference, so nobody later 'simplifies' this away."""
    _, _, frame = _volume_frame()
    source = (
        '//@version=6\nstrategy("S")\n'
        "h = ta.hma(close, 13)\n"
        "if close > h\n    strategy.close()\n"
    )
    result = convert(source)
    assert "HullMovingAverage" not in result.code

    ours = _last_value(source, "_hma_3", frame, 75)

    class Native(bt.Strategy):
        def __init__(self):
            self.hull = bt.indicators.HullMovingAverage(self.data.close, period=13)
            self.seen = {}

        def next(self):
            self.seen[len(self)] = self.hull[0]

    cerebro = bt.Cerebro()
    cerebro.adddata(bt.feeds.PandasData(dataname=frame))
    cerebro.addstrategy(Native)
    theirs = cerebro.run()[0].seen[75]

    assert ours != pytest.approx(theirs, rel=1e-6)


def test_vwma_matches_its_definition():
    closes, volumes, frame = _volume_frame()
    source = (
        '//@version=6\nstrategy("S")\n'
        "v = ta.vwma(close, 13)\n"
        "if close > v\n    strategy.close()\n"
    )
    got = _last_value(source, "_vwma_3", frame, 75)
    assert got == pytest.approx(
        _vwma_reference(closes[:75], volumes[:75], 13), rel=1e-12
    )


def test_vwma_answers_na_rather_than_dividing_by_zero():
    """A window with no volume at all is `na` in Pine, not an exception."""
    start = datetime.datetime(2022, 1, 1)
    rows = [
        {
            "datetime": start + datetime.timedelta(days=i),
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "volume": 0,
        }
        for i in range(40)
    ]
    frame = pd.DataFrame(rows).set_index("datetime")
    source = (
        '//@version=6\nstrategy("S")\n'
        "v = ta.vwma(close, 5)\n"
        "if close > v\n    strategy.close()\n"
    )
    got = _last_value(source, "_vwma_3", frame, 35)
    assert got != got  # NaN, and no ZeroDivisionError getting here


@pytest.mark.parametrize("length,offset,sigma", [(9, 0.85, 6.0), (20, 0.5, 3.0)])
def test_alma_matches_its_definition(length, offset, sigma):
    closes, _, frame = _volume_frame()
    source = (
        '//@version=6\nstrategy("S")\n'
        f"a = ta.alma(close, {length}, {offset}, {sigma})\n"
        "if close > a\n    strategy.close()\n"
    )
    got = _last_value(source, "_alma_1", frame, 75)
    want = _alma_reference(closes[:75], length, offset, sigma)
    assert got == pytest.approx(want, rel=1e-12)


def test_alma_defaults_match_tradingviews():
    result = convert(
        '//@version=6\nstrategy("S")\n'
        "a = ta.alma(close, 9)\n"
        "if close > a\n    strategy.close()\n"
    )
    assert result.ok, result.unsupported
    assert "PineAlma(self.data.close, period=9, offset=0.85, sigma=6.0)" in result.code


def test_the_new_averages_read_the_same_in_both_run_modes():
    """`PineAlma` writes `once` for the same reason the others do."""
    _, _, frame = _volume_frame()
    result = convert(
        '//@version=6\nstrategy("S")\n'
        "sm = ta.sma(close, 10)\n"
        "a = ta.alma(sm, 9)\n"
        "if close > a\n    strategy.close()\n"
    )
    assert result.ok, result.unsupported

    def readings(runonce):
        namespace = {}
        exec(compile(result.code, "<converted>", "exec"), namespace)
        seen = {}
        base = namespace[result.class_name]

        class Watched(base):
            def next(self):
                super().next()
                seen[len(self)] = self._alma_2[0]

        cerebro = bt.Cerebro(runonce=runonce)
        cerebro.adddata(bt.feeds.PandasData(dataname=frame))
        cerebro.addstrategy(Watched)
        cerebro.run()
        return seen

    vectorised, stepwise = readings(True), readings(False)
    assert len(vectorised) > 40
    for bar, got in vectorised.items():
        want = stepwise[bar]
        assert (got != got and want != want) or got == want, f"bar {bar}"


def test_the_new_indicators_are_only_emitted_when_used():
    assert "class PineAlma" not in convert(DUAL_MA).code


@pytest.mark.parametrize("call", ["ta.hma", "ta.vwma", "ta.alma"])
def test_a_length_only_known_per_bar_is_reported_for_the_new_averages(call):
    result = convert(
        '//@version=6\nstrategy("S")\n'
        "n = close > open ? 5 : 9\n"
        f"v = {call}(close, n)\n"
        "if close > v\n    strategy.close()\n"
    )
    assert not result.ok
    assert any("length argument" in item for item in result.unsupported)


def test_generated_hull_strategy_runs_and_trades():
    source = """//@version=6
strategy("Hull")
fast = input.int(9, "Fast")
slow = input.int(30, "Slow")
h = ta.hma(close, fast)
s = ta.hma(close, slow)
if h > s
    strategy.entry("l", strategy.long)
if h < s
    strategy.close()
"""
    value, closed = _run(source)
    assert closed > 0
    assert value != 10_000.0
    _, tight = _run(source, fast=4, slow=12)
    assert tight != closed


# --- the clock: timeframe.period and time() ---------------------------------


def _intraday_frame(bars=96, minutes=60, seed=11, start=None):
    """Hourly bars from midnight, so a 4-hour bucket turns over inside the run."""
    rng = random.Random(seed)
    price = 100.0
    start = datetime.datetime(2022, 1, 1, 0, 0) if start is None else start
    rows = []
    for i in range(bars):
        price *= 1 + rng.gauss(0, 0.01)
        rows.append(
            {
                "datetime": start + datetime.timedelta(minutes=minutes * i),
                "open": price,
                "high": price * 1.01,
                "low": price * 0.99,
                "close": price,
                "volume": 1000,
            }
        )
    return pd.DataFrame(rows).set_index("datetime")


def _instance(source, frame=None, **feed_kwargs):
    """Run a converted strategy and hand back the instance, for reading state."""
    result = convert(source)
    assert result.ok, f"conversion reported: {result.unsupported}"
    namespace = {}
    exec(compile(result.code, "<converted>", "exec"), namespace)

    cerebro = bt.Cerebro()
    frame = _intraday_frame() if frame is None else frame
    cerebro.adddata(bt.feeds.PandasData(dataname=frame, **feed_kwargs))
    cerebro.addstrategy(namespace[result.class_name])
    cerebro.broker.setcash(10_000.0)
    return cerebro.run()[0]


@pytest.mark.parametrize(
    "text,seconds",
    [("240", 14400), ("15", 900), ("D", 86400), ("2D", 172800), ("S", 1), ("30S", 30)],
)
def test_a_resolution_that_tiles_the_epoch_has_a_second_count(text, seconds):
    from pwb_toolbox.converting.backtrader import timeframe_seconds

    assert timeframe_seconds(text) == seconds


@pytest.mark.parametrize("text", ["", "W", "3W", "M", "12M", "nonsense", None])
def test_a_resolution_that_does_not_tile_the_epoch_has_none(text):
    """A week floored by modulo starts on a Thursday and a month has no fixed
    length, so both are refused rather than quietly rounded to the wrong bar."""
    from pwb_toolbox.converting.backtrader import timeframe_seconds

    assert timeframe_seconds(text) is None


def test_timeframe_period_reads_the_feed_it_was_given():
    """Pine asks the chart; the converted class asks the feed, which is the
    same question once the caller has chosen a timeframe for the data."""
    source = (
        '//@version=6\nstrategy("S")\n'
        'if timeframe.period == "60"\n    strategy.entry("L", strategy.long)\n'
    )
    strategy = _instance(source, timeframe=bt.TimeFrame.Minutes, compression=60)
    assert strategy.position.size > 0


def test_timeframe_period_on_a_daily_feed_spells_it_the_way_pine_does():
    """One of a unit is the bare letter in Pine: `D`, never `1D`."""
    source = (
        '//@version=6\nstrategy("S")\n'
        'if timeframe.period == "D"\n    strategy.entry("L", strategy.long)\n'
    )
    strategy = _instance(
        source, frame=_price_frame(bars=40), timeframe=bt.TimeFrame.Days, compression=1
    )
    assert strategy.position.size > 0


def test_change_of_time_fires_once_per_higher_timeframe_bar():
    """`ta.change(time(res)) != 0` is Pine's idiom for "a new res bar just
    started". On hourly bars a 4-hour bucket turns over every fourth bar."""
    source = (
        '//@version=6\nstrategy("S")\n'
        "var int seen = 0\n"
        'if ta.change(time("240")) != 0\n    seen := seen + 1\n'
    )
    strategy = _instance(source, frame=_intraday_frame(bars=96, minutes=60))
    # 96 hourly bars span 24 four-hour buckets. The first bar has no previous
    # bar to differ from, so Backtrader's `[1]` repeats it and it does not count.
    assert strategy.seen == 23


def test_time_floors_to_the_resolution_not_to_the_bar():
    """Every bar inside one 4-hour bucket reports that bucket's opening time."""
    source = (
        '//@version=6\nstrategy("S")\n' "var int stamp = 0\n" 'stamp := time("240")\n'
    )
    strategy = _instance(source, frame=_intraday_frame(bars=4, minutes=60))
    assert strategy.stamp == calendar.timegm((2022, 1, 1, 0, 0, 0, 0, 0, 0)) * 1000


def test_time_with_a_session_filters_by_the_bars_own_clock():
    """`not na(time(timeframe.period, sess))` is how Pine asks "is this bar in
    the session". The check runs on the feed's own timestamps -- the one clock
    a backtest has -- and hourly bars stamped 10:00 through 15:00 are the ones
    inside 0930-1600."""
    source = (
        '//@version=6\nstrategy("S")\n'
        "var int seen = 0\n"
        'if not na(time(timeframe.period, "0930-1600"))\n    seen := seen + 1\n'
    )
    strategy = _instance(source, frame=_intraday_frame(bars=48, minutes=60))
    assert strategy.seen == 12  # 6 in-session hourly bars on each of 2 days


def test_an_overnight_session_wraps_midnight():
    source = (
        '//@version=6\nstrategy("S")\n'
        "var int seen = 0\n"
        'if not na(time(timeframe.period, "2200-0400"))\n    seen := seen + 1\n'
    )
    strategy = _instance(source, frame=_intraday_frame(bars=48, minutes=60))
    # 22:00 and 23:00 on each day, plus 00:00 through 03:00 on each day.
    assert strategy.seen == 12


def test_a_session_day_suffix_counts_sundays_as_1():
    """The intraday frame starts Saturday 2022-01-01, so a Sunday-only session
    admits exactly the second day's morning bars."""
    source = (
        '//@version=6\nstrategy("S")\n'
        "var int seen = 0\n"
        'if not na(time(timeframe.period, "0000-1200:1"))\n    seen := seen + 1\n'
    )
    strategy = _instance(source, frame=_intraday_frame(bars=48, minutes=60))
    assert strategy.seen == 12  # 00:00-11:00 on the Sunday only


def test_a_session_composes_with_a_resolution_floor():
    """`time("240", sess)` still answers the 4-hour bucket's opening time on
    the bars the session admits, and na on the rest."""
    source = (
        '//@version=6\nstrategy("S")\n'
        "var int stamp = 0\n"
        'v = time("240", "0000-0300")\n'
        "if not na(v)\n    stamp := v\n"
    )
    strategy = _instance(source, frame=_intraday_frame(bars=8, minutes=60))
    # 00:00-02:00 are admitted and all floor to the day's first 4-hour bucket.
    assert strategy.stamp == calendar.timegm((2022, 1, 1, 0, 0, 0, 0, 0, 0)) * 1000


def test_a_time_timezone_checks_the_session_on_that_zones_clock():
    """January 2022 is EST, five hours behind UTC: a 0500-0700 New York
    session admits the bars stamped 10:00 and 11:00 UTC. The captured stamp
    is the proof -- an unconverted clock would have admitted 05:00 instead."""
    source = (
        '//@version=6\nstrategy("S")\n'
        "var int seen = 0\n"
        "var int stamp = 0\n"
        'v = time(timeframe.period, "0500-0700", "America/New_York")\n'
        "if not na(v)\n"
        "    seen := seen + 1\n"
        "    if stamp == 0\n"
        "        stamp := v\n"
    )
    strategy = _instance(source, frame=_intraday_frame(bars=48, minutes=60))
    assert strategy.seen == 4  # 10:00 and 11:00 UTC on each of 2 days
    assert strategy.stamp == calendar.timegm((2022, 1, 1, 10, 0, 0, 0, 0, 0)) * 1000


def test_a_timezone_session_moves_with_dst():
    """The same New York window sits four hours behind UTC in July, not five:
    the first admitted bar is 09:00 UTC where the winter test saw 10:00."""
    source = (
        '//@version=6\nstrategy("S")\n'
        "var int stamp = 0\n"
        'v = time(timeframe.period, "0500-0700", "America/New_York")\n'
        "if stamp == 0 and not na(v)\n    stamp := v\n"
    )
    july = _intraday_frame(bars=24, minutes=60, start=datetime.datetime(2022, 7, 1))
    strategy = _instance(source, frame=july)
    assert strategy.stamp == calendar.timegm((2022, 7, 1, 9, 0, 0, 0, 0, 0)) * 1000


def test_syminfo_timezone_is_dropped_as_the_feeds_own_clock():
    """`time(tf, sess, syminfo.timezone)` names the exchange's zone, which is
    exactly what the bare session check already assumes the feed's clock to
    be, so it filters like the two-argument form."""
    source = (
        '//@version=6\nstrategy("S")\n'
        "var int seen = 0\n"
        'if not na(time(timeframe.period, "0930-1600", syminfo.timezone))\n'
        "    seen := seen + 1\n"
    )
    strategy = _instance(source, frame=_intraday_frame(bars=48, minutes=60))
    assert strategy.seen == 12  # the same 6 hourly bars on each of 2 days


def test_history_of_a_session_check_reads_the_previous_bars_clock():
    """`inSess and not inSess[1]` is Pine's "first bar of the session". No
    line exists to read `inSess[1]` off -- time() is a per-bar read of the
    feed's clock -- but the definition holds on every bar, so the previous
    value is the same expression read one bar back."""
    source = (
        '//@version=6\nstrategy("S")\n'
        'inSess = not na(time(timeframe.period, "0930-1600"))\n'
        "var int opens = 0\n"
        "if inSess and not inSess[1]\n    opens := opens + 1\n"
    )
    strategy = _instance(source, frame=_intraday_frame(bars=48, minutes=60))
    assert strategy.opens == 2  # the session opens once on each of 2 days


def test_a_shifted_read_moves_every_read_in_the_definition_together():
    """A definition mixing a price read with the session check still shifts
    whole: `hot[1]` is yesterday's close against yesterday's clock, so the
    count is of in-session predecessors, and the price read costs the first
    bar to the lookback guard."""
    source = (
        '//@version=6\nstrategy("S")\n'
        'hot = close >= low and not na(time(timeframe.period, "0000-1200"))\n'
        "var int count = 0\n"
        "if hot[1]\n    count := count + 1\n"
    )
    strategy = _instance(source, frame=_intraday_frame(bars=48, minutes=60))
    assert strategy.count == 24  # 12 in-session predecessors on each of 2 days


def test_history_of_a_value_built_on_state_is_still_refused():
    """A var holds only its current value, so a definition reading one cannot
    be re-read a bar back; refusing beats answering with today's state."""
    source = (
        '//@version=6\nstrategy("S")\n'
        "var float anchor = 0.0\n"
        "anchor := close\n"
        "gap = close - anchor\n"
        "if gap[1] > 0\n    strategy.close()\n"
    )
    result = convert(source)
    assert not result.ok
    assert any("needs a Backtrader line" in item for item in result.unsupported)


def test_input_session_becomes_a_tunable_string_param():
    source = (
        '//@version=6\nstrategy("S")\n'
        'sess = input.session("0930-1600", "Session window")\n'
        "var int seen = 0\n"
        "if not na(time(timeframe.period, sess))\n    seen := seen + 1\n"
    )
    result = convert(source)
    assert result.ok, result.unsupported
    assert ("sess", "0930-1600") in result.params

    namespace = {}
    exec(compile(result.code, "<converted>", "exec"), namespace)
    cerebro = bt.Cerebro()
    cerebro.adddata(bt.feeds.PandasData(dataname=_intraday_frame(bars=48, minutes=60)))
    cerebro.addstrategy(namespace[result.class_name], sess="1300-1500")
    cerebro.broker.setcash(10_000.0)
    strategy = cerebro.run()[0]
    assert strategy.seen == 4  # 13:00 and 14:00, on each of 2 days


def test_a_malformed_session_string_raises_a_named_error():
    """`930-1600` is missing a digit. Pine rejects it at compile time; here it
    is a param, so the first bar that reads it says which string is wrong."""
    source = (
        '//@version=6\nstrategy("S")\n'
        'x = time(timeframe.period, "930-1600")\n'
        "if not na(x)\n    strategy.close()\n"
    )
    result = convert(source)
    assert result.ok, result.unsupported
    namespace = {}
    exec(compile(result.code, "<converted>", "exec"), namespace)
    cerebro = bt.Cerebro()
    cerebro.adddata(bt.feeds.PandasData(dataname=_intraday_frame(bars=4, minutes=60)))
    cerebro.addstrategy(namespace[result.class_name])
    with pytest.raises(ValueError, match="930-1600"):
        cerebro.run()


def test_a_weekly_resolution_for_time_is_reported():
    result = convert(
        '//@version=6\nstrategy("S")\n'
        'x = time("W")\n'
        "if x > 0\n    strategy.close()\n"
    )
    assert not result.ok
    assert any("does not floor" in item for item in result.unsupported)


def test_change_of_a_source_without_history_is_reported():
    """Regression. `ta.change` built its previous value by re-evaluating the
    source when that source was not a name, so the difference was the value
    minus itself: a condition that compiled, ran, and never once fired."""
    result = convert(
        '//@version=6\nstrategy("S")\n'
        "if ta.change(math.max(high, low)) != 0\n    strategy.close()\n"
    )
    assert not result.ok
    assert any("needs bar history" in item for item in result.unsupported)


# --- an `if` that picks a value across branches ------------------------------


def test_an_if_that_writes_one_name_folds_into_the_substitution():
    """The shape a Pine function uses to choose a value over several branches:
    assign a default, overwrite it in whichever branch applies, hand it back."""
    source = (
        '//@version=6\nstrategy("S")\n'
        "pick(x) =>\n"
        "    out = 0\n"
        "    if x > 2\n"
        "        out := 10\n"
        "    else if x > 1\n"
        "        out := 20\n"
        "    else\n"
        "        out := 30\n"
        "    out\n"
        "var int got = 0\n"
        "got := pick(2)\n"
    )
    strategy = _instance(source, frame=_price_frame(bars=20))
    assert strategy.got == 20


def test_an_if_with_no_else_leaves_the_name_alone():
    """Pine does not blank a variable when no branch runs, so neither does
    this. `_if_as_expression` would have to answer na here; binding the name
    to itself and substituting gives back whatever it last held."""
    source = (
        '//@version=6\nstrategy("S")\n'
        "pick(x) =>\n"
        "    out = 7\n"
        "    if x > 100\n"
        "        out := 1\n"
        "    out\n"
        "var int got = 0\n"
        "got := pick(2)\n"
    )
    strategy = _instance(source, frame=_price_frame(bars=20))
    assert strategy.got == 7


def test_a_later_branch_can_read_the_name_it_is_writing():
    """`out := out + 1` reads the value from before the block, because the
    whole conditional is substituted against the bindings as they stood."""
    source = (
        '//@version=6\nstrategy("S")\n'
        "pick(x) =>\n"
        "    out = 5\n"
        "    if x > 1\n"
        "        out := out + 1\n"
        "    else\n"
        "        out := out - 1\n"
        "    out\n"
        "var int got = 0\n"
        "got := pick(2)\n"
    )
    strategy = _instance(source, frame=_price_frame(bars=20))
    assert strategy.got == 6


def test_branches_writing_different_names_are_reported():
    """Two names is two assignments, and only one of them can be the value
    carried forward. Folding it would silently drop the other."""
    result = convert(
        '//@version=6\nstrategy("S")\n'
        "pick(x) =>\n"
        "    a = 0\n"
        "    b = 0\n"
        "    if x > 1\n"
        "        a := 1\n"
        "    else\n"
        "        b := 2\n"
        "    a\n"
        "if pick(2) > 0\n    strategy.close()\n"
    )
    assert not result.ok
    assert any("write one name in every branch" in i for i in result.unsupported)


def test_a_name_first_assigned_inside_an_if_is_reported():
    """Without a prior value there is nothing for the untaken branch to keep,
    and Pine's answer there is na rather than a number."""
    result = convert(
        '//@version=6\nstrategy("S")\n'
        "pick(x) =>\n"
        "    if x > 1\n"
        "        out := 1\n"
        "    else\n"
        "        out := 2\n"
        "    out\n"
        "if pick(2) > 0\n    strategy.close()\n"
    )
    assert not result.ok
    assert any("assigned only inside" in i for i in result.unsupported)


def test_a_branch_carrying_two_statements_is_reported():
    result = convert(
        '//@version=6\nstrategy("S")\n'
        "pick(x) =>\n"
        "    out = 0\n"
        "    if x > 1\n"
        "        tmp = x * 2\n"
        "        out := tmp\n"
        "    else\n"
        "        out := 3\n"
        "    out\n"
        "if pick(2) > 0\n    strategy.close()\n"
    )
    assert not result.ok
    assert any("one name in every branch" in i for i in result.unsupported)


def test_a_trailing_if_without_an_assignment_still_reads_as_the_value():
    """The older reading is still there for a block whose branches are bare
    expressions rather than assignments."""
    source = (
        '//@version=6\nstrategy("S")\n'
        "pick(x) =>\n"
        "    if x > 1\n"
        "        11\n"
        "    else\n"
        "        22\n"
        "var int got = 0\n"
        "got := pick(0)\n"
    )
    strategy = _instance(source, frame=_price_frame(bars=20))
    assert strategy.got == 22


# --- naming an intermediate instead of copying it ----------------------------


_BIG_LOCAL = " + ".join(f"close[{i}] * {i}" for i in range(1, 40))


def test_a_large_body_local_is_named_rather_than_copied():
    """Substitution has nowhere to put an intermediate, so a local read more
    than once used to be copied into every read and blow the node budget.
    Naming it is what the reject message was asking for all along."""
    source = (
        '//@version=6\nstrategy("S")\n'
        "f(p) =>\n"
        f"    d = {_BIG_LOCAL}\n"
        "    e = d + d + d\n"
        "    e\n"
        "if f(5) > 0\n    strategy.close()\n"
    )
    result = convert(source)
    assert result.ok, result.unsupported
    # One scalar assignment in next(), not three copies of a forty-term sum.
    body = result.code.split("def next(self):")[1]
    assert body.count("close[-39]") == 1


def test_a_named_intermediate_can_feed_an_indicator():
    """`ta.ema(d, p)` needs a line, and a substituted expression tree is not
    one. The name lands in `_computed`, which is what `_promote` reads, so the
    same expression is built a second time in `__init__` as an indicator
    source while `next()` keeps the scalar."""
    source = (
        '//@version=6\nstrategy("S")\n'
        "f(p) =>\n"
        f"    d = {_BIG_LOCAL}\n"
        "    e = ta.ema(d, p)\n"
        "    e\n"
        "if f(5) > 0\n    strategy.close()\n"
    )
    result = convert(source)
    assert result.ok, result.unsupported

    setup, body = result.code.split("def next(self):")
    # Built on the promoted line, not on a bare price series.
    assert "bt.indicators.EMA(self._line_" in setup
    assert "close(-39)" in setup
    # And it survives a real cerebro. An expression tree handed to an
    # indicator converts cleanly and then dies on the first bar, so running it
    # is the part of this test that would catch a wrong source.
    value, _ = _run(source)
    assert isinstance(value, float)


def test_a_named_intermediate_can_carry_history():
    """`d[1]` off an expression tree has no previous bar to read. Off a name
    it does, by the same route any computed value takes."""
    source = (
        '//@version=6\nstrategy("S")\n'
        "f() =>\n"
        f"    d = {_BIG_LOCAL}\n"
        "    e = d - d[1]\n"
        "    e\n"
        "var float got = 0.0\n"
        "got := f()\n"
    )
    strategy = _instance(source, frame=_price_frame(bars=60))
    assert strategy.got != 0.0


def test_a_named_intermediate_may_be_computed_under_an_if():
    """A value is not a state machine. `var` updates have to run every bar
    wherever the call sits, and are still refused under an `if`; a named
    intermediate computed only on the bars that read it is the same strategy."""
    source = (
        '//@version=6\nstrategy("S")\n'
        "f(p) =>\n"
        f"    d = {_BIG_LOCAL}\n"
        "    e = d + d\n"
        "    e\n"
        "if close > open\n"
        "    if f(5) > 0\n        strategy.close()\n"
    )
    assert convert(source).ok


def test_a_function_that_keeps_state_is_still_refused_under_an_if():
    source = (
        '//@version=6\nstrategy("S")\n'
        "f() =>\n"
        "    var int n = 0\n"
        "    n := n + 1\n"
        "    n\n"
        "if close > open\n"
        "    if f() > 0\n        strategy.close()\n"
    )
    result = convert(source)
    assert not result.ok
    assert any("every bar" in item for item in result.unsupported)


def test_history_of_an_indicator_call_reads_the_line_at_an_offset():
    """`ta.ema(close, 20)[1]` is the previous bar of a line that has to exist
    for the current bar to be readable at all."""
    source = (
        '//@version=6\nstrategy("S")\n'
        "var float got = 0.0\n"
        "got := ta.ema(close, 5)[1]\n"
    )
    strategy = _instance(source, frame=_price_frame(bars=60))
    assert strategy.got > 0.0


def test_history_of_an_indicator_call_is_the_previous_bar():
    """Pinned against the line read directly, so an off-by-one here shows up
    as a difference rather than as a plausible number."""
    source = (
        '//@version=6\nstrategy("S")\n'
        "var float now = 0.0\n"
        "var float before = 0.0\n"
        "now := ta.ema(close, 5)\n"
        "before := ta.ema(close, 5)[1]\n"
    )
    strategy = _instance(source, frame=_price_frame(bars=60))
    assert strategy.now != strategy.before


# --- lookahead ---------------------------------------------------------------


_SECURITY = (
    '//@version=6\nstrategy("S")\n'
    'x = request.security(syminfo.tickerid, "240", close, {settings})\n'
    "if x > 0\n    strategy.close()\n"
)


@pytest.mark.parametrize(
    "settings",
    [
        "lookahead=barmerge.lookahead_on",
        "barmerge.gaps_off, barmerge.lookahead_on",
        "barmerge.gaps_on, barmerge.lookahead_on",
    ],
)
def test_lookahead_on_is_refused_wherever_it_sits(settings):
    """Regression, and the worst kind this converter can have. Pine takes
    `lookahead` positionally as well as by name, and only the keyword form was
    checked -- so the positional spelling converted clean and read the
    higher-timeframe bar before it closed. That is a backtest that cannot lose,
    on data the strategy could not have had."""
    result = convert(_SECURITY.format(settings=settings))
    assert not result.ok
    assert any("before it closes" in item for item in result.unsupported)


@pytest.mark.parametrize(
    "settings",
    ["barmerge.gaps_off, barmerge.lookahead_off", "lookahead=barmerge.lookahead_off"],
)
def test_lookahead_off_is_the_normal_case_and_still_converts(settings):
    assert convert(_SECURITY.format(settings=settings)).ok


# --- request.security as a line, not just a reading --------------------------

HTF_INDICATOR_STRATEGY = """//@version=6
strategy("HTF")
fast = input.int(20, "Fast")
htfClose = request.security(syminfo.tickerid, "240", close)
htfMa = ta.ema(htfClose, fast)
if htfClose > htfMa
    strategy.entry("l", strategy.long)
if htfClose < htfMa
    strategy.close()
"""


def test_a_higher_timeframe_series_can_feed_an_indicator():
    """`request.security` answered only as this bar's number before.

    A resampled feed is a line like any other, so an indicator can be built on
    it -- but the lowering indexed it before handing it back, which left
    nothing an indicator could take.
    """
    result = convert(HTF_INDICATOR_STRATEGY)
    assert result.ok, result.unsupported
    assert "self._line_htfClose_1 = self.datas[1].close" in result.code
    assert "bt.indicators.EMA(self._line_htfClose_1, period=self.p.fast)" in result.code


def test_a_higher_timeframe_series_composes_before_it_is_used():
    source = (
        '//@version=6\nstrategy("S")\n'
        'htf = request.security(syminfo.tickerid, "240", close)\n'
        "spread = htf - close\n"
        "sig = ta.sma(spread, 5)\n"
        "if sig > 0\n    strategy.close()\n"
    )
    result = convert(source)
    assert result.ok, result.unsupported
    # The higher-timeframe read is promoted to its own handle first, so the
    # composition references that rather than repeating the feed.
    assert "self._line_htf_1 = self.datas[1].close" in result.code
    assert "self._line_spread_2 = (self._line_htf_1 - self.data.close)" in result.code
    assert "bt.indicators.SMA(self._line_spread_2, period=5)" in result.code


#: Reads a composed expression off the weekly feed as this bar's number.
COMPOSED_SECURITY = (
    '//@version=6\nstrategy("S")\n'
    'wkRange = request.security(syminfo.tickerid, "W", high - low)\n'
    "if wkRange > 0 and strategy.position_size == 0\n"
    '    strategy.entry("L", strategy.long)\n'
    "if strategy.position_size > 0 and close < open\n"
    "    strategy.close()\n"
)


def test_a_composed_security_expression_reads_through_a_hoisted_line():
    """Regression. `request.security(..., high - low)` lowered to
    `(self.datas[1].high - self.datas[1].low)[0]` inline in next() -- but
    line arithmetic inside next() runs on this bar's floats, so the read
    subscripted a float and the first bar raised TypeError. The composition
    has to be built once in __init__ and read through its handle."""
    result = convert(COMPOSED_SECURITY)
    assert result.ok, result.unsupported
    assert "(self.datas[1].high - self.datas[1].low)[0]" not in result.code
    assert "self._line_1 = (self.datas[1].high - self.datas[1].low)" in result.code


def test_a_composed_security_expression_survives_a_run():
    """The weekly range is positive on every bar it exists for, so the long
    keeps re-opening after every down-day close: round trips, not a crash."""
    value, closed = _run_htf(COMPOSED_SECURITY)
    assert closed > 0


def test_the_line_path_still_records_the_feed_to_supply():
    result = convert(HTF_INDICATOR_STRATEGY)
    assert "feed_spec" in result.code
    assert "needs 2 data feeds" in result.code


@pytest.mark.parametrize(
    "call,reason",
    [
        (
            'request.security(syminfo.tickerid, "240", high, barmerge.gaps_off,'
            " barmerge.lookahead_on)",
            "lookahead_on reads a bar before it closes",
        ),
        (
            'request.security(syminfo.tickerid, "240", high,'
            " lookahead=barmerge.lookahead_on)",
            "lookahead_on reads a bar before it closes",
        ),
        (
            'request.security(syminfo.tickerid, "zz", close)',
            "is not recognised",
        ),
    ],
)
def test_the_line_path_refuses_what_the_value_path_refuses(call, reason):
    """The checks moved when the two paths were split, so they are re-pinned.

    A lookahead read is the one thing this converter most needs to refuse: it
    is a backtest on data the strategy could not have had, and it looks like a
    very good strategy. Reaching `request.security` through an indicator must
    not be a way around that.
    """
    source = (
        '//@version=6\nstrategy("S")\n'
        f"h = {call}\n"
        "ma = ta.ema(h, 20)\n"
        "if close > ma\n    strategy.close()\n"
    )
    result = convert(source)
    assert not result.ok
    assert any(reason in item for item in result.unsupported)


def test_generated_htf_indicator_strategy_runs_and_trades():
    result = convert(HTF_INDICATOR_STRATEGY)
    assert result.ok, result.unsupported
    namespace = {}
    exec(compile(result.code, "<converted>", "exec"), namespace)
    strategy = namespace[result.class_name]

    cerebro = bt.Cerebro()
    frame = _price_frame(bars=600)
    cerebro.adddata(bt.feeds.PandasData(dataname=frame))
    for _symbol, timeframe, compression in strategy.feed_spec:
        cerebro.resampledata(
            bt.feeds.PandasData(dataname=frame),
            timeframe=timeframe,
            compression=compression,
        )
    cerebro.addstrategy(strategy)
    cerebro.broker.setcash(10_000.0)
    cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name="trades")
    result_strategy = cerebro.run()[0]
    closed = (
        result_strategy.analyzers.trades.get_analysis().get("total", {}).get("total", 0)
    )
    assert closed > 0


# --- priced entries: strategy.entry with limit/stop, from_entry, cancel ------
#
# A priced `strategy.entry` is a standing order in Pine: it works until it
# fills, until the same id is re-issued -- which moves it -- or until
# `strategy.cancel` withdraws it, and the `strategy.exit` that names it via
# `from_entry` only becomes live orders when it fills. The generated class
# says that with a Backtrader bracket, and these tests fill, move, and cancel
# one against bars spelled out by hand, because "the order was submitted" says
# nothing about what it does to a position.


def _explicit_frame(rows):
    """Daily bars spelled out as (open, high, low, close), for fill scenarios."""
    start = datetime.datetime(2022, 3, 7)  # a Monday
    out = []
    for i, (o, h, l, c) in enumerate(rows):
        out.append(
            {
                "datetime": start + datetime.timedelta(days=i),
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "volume": 1000,
            }
        )
    return pd.DataFrame(out).set_index("datetime")


def _fill_run(source, rows, **params):
    """Run a conversion over explicit bars and hand back the strategy."""
    result = convert(source)
    assert result.ok, f"conversion reported: {result.unsupported}"
    namespace = {}
    exec(compile(result.code, "<converted>", "exec"), namespace)
    cerebro = bt.Cerebro()
    cerebro.adddata(bt.feeds.PandasData(dataname=_explicit_frame(rows)))
    cerebro.addstrategy(namespace[result.class_name], **params)
    cerebro.broker.setcash(10_000.0)
    return cerebro.run()[0]


#: Arms once on the first bar and rests a limit buy below the market.
LIMIT_ENTRY = (
    '//@version=6\nstrategy("S")\n'
    "var bool armed = true\n"
    "if armed\n"
    "    armed := false\n"
    '    strategy.entry("L", strategy.long, limit=99.0)\n'
)


def test_a_limit_entry_waits_on_the_book_for_its_price():
    rows = [
        (100, 101, 99.5, 100.5),  # arms: limit buy at 99 goes out at the close
        (100, 100.5, 99.8, 100),  # never trades down to it
        (100, 100.5, 98.5, 100),  # touches 99: fills at the limit, not at market
        (100, 100.5, 99.5, 100),
    ]
    strategy = _fill_run(LIMIT_ENTRY, rows)
    assert strategy.position.size == 1
    assert strategy.position.price == 99.0


def test_an_untouched_limit_entry_never_opens_a_position():
    rows = [(100, 101, 99.5, 100.5)] + [(100, 100.5, 99.2, 100)] * 4
    strategy = _fill_run(LIMIT_ENTRY, rows)
    assert strategy.position.size == 0


BRACKET_ENTRY = (
    '//@version=6\nstrategy("S")\n'
    "var bool armed = true\n"
    "if armed\n"
    "    armed := false\n"
    '    strategy.entry("L", strategy.long, limit=99.0)\n'
    '    strategy.exit("Lx", "L", stop=95.0, limit=105.0)\n'
)


def test_exit_levels_arm_with_the_pending_entry_and_fire_on_its_fill():
    """The script issues its exit on the bar that issues the entry, while the
    position is still size 0. Translating that call against the *position*
    would place nothing, the entry would later fill unprotected, and the
    target would never be hit -- which is exactly what the first cut of this
    translation did. The levels have to ride with the entry."""
    rows = [
        (100, 101, 99.5, 100.5),  # arms the bracket
        (100, 100.5, 98.5, 100),  # entry fills at 99
        (100.5, 106, 100, 105.5),  # target leg fills at 105
        (105, 105.5, 104, 105),
    ]
    strategy = _fill_run(BRACKET_ENTRY, rows)
    assert strategy.position.size == 0  # the stop leg cancelled with the fill
    assert strategy.broker.getvalue() == pytest.approx(10_006.0)


def test_the_stop_leg_of_a_filled_bracket_protects_the_position():
    rows = [
        (100, 101, 99.5, 100.5),  # arms the bracket
        (100, 100.5, 98.5, 100),  # entry fills at 99
        (98, 98.5, 94, 94.5),  # stop leg fills at 95
        (95, 95.5, 94, 95),
    ]
    strategy = _fill_run(BRACKET_ENTRY, rows)
    assert strategy.position.size == 0
    assert strategy.broker.getvalue() == pytest.approx(9_996.0)


def test_reissuing_an_entry_tag_moves_the_order_rather_than_stacking():
    """Pine keeps one order per id. Five bars of re-arming must still open a
    one-unit position, and at the last level asked for, not the first."""
    source = (
        '//@version=6\nstrategy("S")\n'
        "if strategy.position_size == 0\n"
        '    strategy.entry("L", strategy.long, limit=close - 1.0)\n'
    )
    rows = [
        (100, 101, 99.5, 100.5),  # limit rests at 99.5
        (101, 102, 100.5, 101.5),  # moved to 100.5
        (102, 103, 101.6, 102.5),  # moved to 101.5
        (102, 103, 101.0, 102.5),  # fills at 101.5 -- one unit, latest level
        (102, 103, 101.2, 102.5),
    ]
    strategy = _fill_run(source, rows)
    assert strategy.position.size == 1
    assert strategy.position.price == pytest.approx(101.5)


def test_cancel_withdraws_a_pending_entry_before_it_can_fill():
    """The control run proves the dip would have filled the order; the cancel
    run proves `strategy.cancel` is why it did not."""
    source = (
        '//@version=6\nstrategy("S")\n'
        "var bool armed = true\n"
        "if armed\n"
        "    armed := false\n"
        '    strategy.entry("L", strategy.long, limit=99.0)\n'
        "if bar_index == 3\n"
        '    strategy.cancel("L")\n'
    )
    rows = [
        (100, 101, 99.5, 100.5),  # arms
        (100, 100.5, 99.8, 100),  # resting, untouched
        (100, 100.5, 99.6, 100),  # bar_index 3: cancelled
        (100, 100.5, 98.0, 100),  # the dip that would have filled it
        (100, 100.5, 99.5, 100),
    ]
    strategy = _fill_run(source, rows)
    assert strategy.position.size == 0

    control = source.replace('    strategy.cancel("L")\n', "    strategy.close()\n")
    strategy = _fill_run(control, rows)
    assert strategy.position.size == 1


def test_cancel_all_withdraws_a_resting_entry_without_naming_it():
    source = LIMIT_ENTRY + "if bar_index == 3\n    strategy.cancel_all()\n"
    rows = [
        (100, 101, 99.5, 100.5),  # arms
        (100, 100.5, 99.8, 100),  # resting, untouched
        (100, 100.5, 99.6, 100),  # bar_index 3: everything cancelled
        (100, 100.5, 98.0, 100),  # the dip that would have filled it
        (100, 100.5, 99.5, 100),
    ]
    strategy = _fill_run(source, rows)
    assert strategy.position.size == 0
    assert _fill_run(LIMIT_ENTRY, rows).position.size == 1  # the control


def test_cancel_all_takes_the_standing_exits_off_an_open_position():
    """strategy.cancel leaves a filled entry's exits protecting the position;
    strategy.cancel_all withdraws those too, as Pine's does. The tape dips
    through where the stop rested: a surviving stop would fill and flatten,
    so the position still being open is the proof it was cancelled."""
    source = (
        '//@version=6\nstrategy("S")\n'
        "if bar_index == 1\n"
        '    strategy.entry("L", strategy.long)\n'
        "if strategy.position_size > 0 and bar_index < 4\n"
        '    strategy.exit("Lx", stop=98.0)\n'
        "if bar_index == 4\n"
        "    strategy.cancel_all()\n"
    )
    rows = [
        (100, 100.5, 99.5, 100),  # bar 1: market entry placed
        (100, 100.6, 99.6, 100.2),  # fills at the open; stop 98 goes out
        (100.2, 100.6, 99.0, 100),  # above the stop, nothing fills
        (100, 100.4, 99.2, 100.1),  # bar 4: cancel_all takes the stop away
        (100, 100.2, 97.0, 99.0),  # through 98, which must fill nothing
        (99, 99.5, 98.5, 99.2),
    ]
    strategy = _fill_run(source, rows)
    assert strategy.position.size == 1

    control = source.replace("if bar_index == 4\n    strategy.cancel_all()\n", "")
    strategy = _fill_run(control, rows)
    assert strategy.position.size == 0


def test_exit_reissued_after_the_fill_moves_the_bracket_legs():
    """A `strategy.exit` that keeps running once the position is open has to
    move the bracket's legs, not stack a second pair beside them: two pairs
    would fill twice and flip the strategy short."""
    source = (
        '//@version=6\nstrategy("S")\n'
        "var bool armed = true\n"
        "if armed\n"
        "    armed := false\n"
        '    strategy.entry("L", strategy.long, limit=99.0)\n'
        'strategy.exit("Lx", "L", stop=95.0, limit=close + 6.0)\n'
    )
    rows = [
        (100, 101, 99.5, 100),  # arms: bracket target rests at 106
        (100, 100.5, 98.5, 100),  # entry fills at 99; levels unchanged, kept
        (101, 102, 100.5, 101.5),  # target moves to 107.5: legs resubmitted
        (102, 108, 101.5, 107),  # fills at 107.5, and only once
        (107, 107.5, 106, 107),
    ]
    strategy = _fill_run(source, rows)
    assert strategy.position.size == 0
    assert strategy.broker.getvalue() == pytest.approx(10_008.5)


def test_a_dynamic_cancel_id_is_reported():
    result = convert(
        '//@version=6\nstrategy("S")\n'
        'id = close > open ? "L" : "S"\n'
        "strategy.cancel(id)\n"
    )
    assert not result.ok
    assert any("strategy.cancel" in item for item in result.unsupported)


# --- syminfo.mintick ----------------------------------------------------------


def test_syminfo_mintick_becomes_a_param_with_a_note():
    source = (
        '//@version=6\nstrategy("S")\n'
        "buf = 2 * syminfo.mintick\n"
        "if close > open + buf\n"
        '    strategy.entry("L", strategy.long)\n'
    )
    result = convert(source)
    assert result.ok, result.unsupported
    assert ("mintick", 0.01) in result.params
    assert any("property of the instrument" in item for item in result.ignored)


def test_the_mintick_param_is_live_rather_than_baked_in():
    """Set to something absurd it must change behavior, or it is not a param."""
    source = (
        '//@version=6\nstrategy("S")\n'
        "gap = 4 * syminfo.mintick\n"
        "if close - open > gap and strategy.position_size == 0\n"
        '    strategy.entry("L", strategy.long)\n'
    )
    rows = [(100, 102, 99.5, 101.5)] * 5  # every bar closes 1.5 above its open
    assert _fill_run(source, rows).position.size == 1
    assert _fill_run(source, rows, mintick=10.0).position.size == 0


# --- the ICT order-block strategy, end to end ---------------------------------
#
# The script that drove priced entries, sessions and syminfo.mintick into the
# converter, kept whole: a fair-value-gap detector that rests a limit at the
# gap's top, brackets it behind the order block and at an R multiple, and
# withdraws it after `zoneLife` bars untouched.

ICT_OB_FVG = """//@version=6
strategy("ICT OB + FVG", shorttitle="OB_FVG", overlay=true,
     initial_capital=25000, default_qty_type=strategy.fixed, default_qty_value=1,
     margin_long=0, margin_short=0, pyramiding=0,
     commission_type=strategy.commission.cash_per_order, commission_value=0)

grpS = "Setup"
useLong     = input.bool(true,  "Trade longs",            group=grpS)
useShort    = input.bool(true,  "Trade shorts",           group=grpS)
minGapTicks = input.float(4,    "Min FVG size (ticks)",   minval=0, group=grpS)
zoneLife    = input.int(20,     "Entry valid for N bars", minval=1, group=grpS)

grpR = "Risk / Reward"
rr           = input.float(2.0, "Target R multiple",   minval=0.5, step=0.5, group=grpR)
stopBufTicks = input.float(4,   "Stop buffer (ticks)", minval=0, group=grpR)

grpT = "Session"
useSession = input.bool(false, "Limit entries to session", group=grpT)
sess       = input.session("0930-1600", "Session window (chart tz)", group=grpT)

grpV = "Visuals"
showZones = input.bool(true, "Draw FVG zones", group=grpV)

inSess = not useSession or not na(time(timeframe.period, sess))
buf    = stopBufTicks * syminfo.mintick
minGap = minGapTicks * syminfo.mintick

bullFVG = (low > high[2]) and (low - high[2] >= minGap) and close[1] > open[1]
bearFVG = (high < low[2]) and (low[2] - high >= minGap) and close[1] < open[1]

var int longArmBar  = na
var int shortArmBar = na

if bullFVG and useLong and inSess and strategy.position_size == 0
    entryP = low
    stopP  = high[2] - buf
    risk   = entryP - stopP
    tgtP   = entryP + rr * risk
    if risk > 0
        strategy.entry("L", strategy.long, limit=entryP)
        strategy.exit("Lx", "L", stop=stopP, limit=tgtP)
        longArmBar := bar_index
        if showZones
            box.new(bar_index, low, bar_index + zoneLife, high[2], border_color=color.new(color.teal,40), bgcolor=color.new(color.teal,85))

if not na(longArmBar) and strategy.position_size == 0 and bar_index - longArmBar >= zoneLife
    strategy.cancel("L")
    longArmBar := na

if bearFVG and useShort and inSess and strategy.position_size == 0
    entryP = high
    stopP  = low[2] + buf
    risk   = stopP - entryP
    tgtP   = entryP - rr * risk
    if risk > 0
        strategy.entry("S", strategy.short, limit=entryP)
        strategy.exit("Sx", "S", stop=stopP, limit=tgtP)
        shortArmBar := bar_index
        if showZones
            box.new(bar_index, low[2], bar_index + zoneLife, high, border_color=color.new(color.maroon,40), bgcolor=color.new(color.maroon,85))

if not na(shortArmBar) and strategy.position_size == 0 and bar_index - shortArmBar >= zoneLife
    strategy.cancel("S")
    shortArmBar := na
"""


def test_ict_ob_fvg_converts_clean():
    result = convert(ICT_OB_FVG)
    assert result.ok, result.unsupported
    assert ("mintick", 0.01) in result.params
    assert ("sess", "0930-1600") in result.params
    assert any("box.new" in item for item in result.ignored)


def test_ict_ob_fvg_trades_a_bullish_gap_to_its_target():
    """A three-bar fair value gap, a retrace into it, and a run to the target.

    The gap bar's low is 102 over a two-bars-ago high of 100.5, so the limit
    rests at 102, the stop at 100.46 (the order block less four ticks), and
    the 2R target at 105.08. The retrace fills the limit; the rally fills the
    target; the profit is exactly the 3.08 the geometry promises.
    """
    rows = [
        (100, 100.5, 99.5, 100),  # the order block: high[2] for the gap bar
        (100, 103, 100, 102.9),  # displacement up (close > open)
        (103, 104, 102, 103.5),  # gap bar: low 102 > 100.5, entry goes out
        (103.5, 104, 102.5, 103),  # no retrace yet
        (103, 103.2, 101.8, 102.5),  # trades through 102: entry fills
        (103, 105.5, 102.8, 105.2),  # trades through 105.08: target fills
        (105.2, 105.4, 103.0, 105.0),
        (105, 105.3, 103.1, 105.1),
    ]
    strategy = _fill_run(ICT_OB_FVG, rows)
    assert strategy.position.size == 0
    assert strategy.broker.getvalue() == pytest.approx(10_003.08)


def test_ict_ob_fvg_trades_a_bearish_gap_to_its_target():
    rows = [
        (100, 100.5, 99.5, 100),  # low[2] = 99.5 for the gap bar
        (100, 100.2, 97, 97.1),  # displacement down (close < open)
        (97, 98.5, 96.5, 98),  # gap bar: high 98.5 < 99.5, short rests at 98.5
        (97.5, 98.0, 96.8, 97.2),  # no retrace yet
        (98, 98.6, 97.5, 98.2),  # trades through 98.5: short fills
        (97.5, 97.8, 96.3, 96.5),  # trades through the 96.42 target
        (96.5, 97.6, 96, 96.6),
        (96.6, 97.2, 96.2, 96.8),
    ]
    strategy = _fill_run(ICT_OB_FVG, rows)
    assert strategy.position.size == 0
    # entry 98.5, stop 99.54, risk 1.04, 2R target 96.42: profit 2.08.
    assert strategy.broker.getvalue() == pytest.approx(10_002.08)


def test_ict_ob_fvg_withdraws_an_entry_the_market_never_retraces_to():
    """zoneLife bars without a retrace and the resting order must be gone: the
    later dip through the limit price proves it, by filling nothing."""
    rows = [
        (100, 100.5, 99.5, 100),
        (100, 103, 100, 102.9),
        (103, 104, 102, 103.5),  # gap bar: limit rests at 102
        (103.5, 104, 102.6, 103.2),
        (103.2, 103.8, 102.7, 103.5),
        (103.5, 104.2, 102.9, 104),  # three bars armed: zoneLife=3 cancels here
        (104, 104.5, 103.0, 104.2),
        (104.2, 104.8, 101.5, 104.5),  # through 102, which must fill nothing
        (104.5, 105, 103.4, 104.8),
    ]
    strategy = _fill_run(ICT_OB_FVG, rows, zoneLife=3)
    assert strategy.position.size == 0
    assert strategy.broker.getvalue() == 10_000.0


def test_ict_ob_fvg_session_filter_gates_entries():
    """With the session turned on and a window nothing trades in, the same
    tape that produced the winning long must produce no trade at all."""
    rows = [
        (100, 100.5, 99.5, 100),
        (100, 103, 100, 102.9),
        (103, 104, 102, 103.5),
        (103.5, 104, 102.5, 103),
        (103, 103.2, 101.8, 102.5),
        (103, 105.5, 102.8, 105.2),
        (105.2, 105.4, 103.0, 105.0),
        (105, 105.3, 103.1, 105.1),
    ]
    strategy = _fill_run(ICT_OB_FVG, rows, useSession=True, sess="0930-1600")
    # Daily bars are stamped midnight, which 0930-1600 never contains.
    assert strategy.position.size == 0
    assert strategy.broker.getvalue() == 10_000.0


# --- the ICT AM continuation strategy, end to end ------------------------------
#
# The script that drove time()'s timezone argument, history of a computed
# session check and strategy.cancel_all into the converter, kept whole: an
# opening-drive FVG detector that only trades the New York morning, rests a
# limit at the gap's midpoint bracketed at an R multiple, and goes flat into
# the close.

ICT_AM_OB = """//@version=6
strategy("ICT AM OB Continuation", shorttitle="AM_OB", overlay=true,
     initial_capital=25000, default_qty_type=strategy.fixed, default_qty_value=1,
     margin_long=0, margin_short=0, pyramiding=0,
     commission_type=strategy.commission.cash_per_order, commission_value=1.0, slippage=1)

// ===== Session (ET) =====
grpS = "Session (ET)"
entrySess = input.session("0930-1130", "AM entry window", group=grpS)
flatSess  = input.session("1555-1600", "Force-flat window", group=grpS)
tz = "America/New_York"

// ===== Direction / bias =====
grpD = "Direction / bias"
useLong  = input.bool(true, "Longs (above the open)",     group=grpD)
useShort = input.bool(true, "Shorts (below the open)",    group=grpD)
useBias  = input.bool(true, "Require opening-drive bias", group=grpD)

// ===== Setup quality =====
grpF = "Setup quality"
useDisp     = input.bool(true, "Require strong displacement", group=grpF)
dispMult    = input.float(1.5, "Displacement x avg range(10)", minval=0.1, step=0.1, group=grpF)
minGapTicks = input.float(8,   "Min FVG size (ticks)", minval=0, group=grpF)
zoneLife    = input.int(12,    "Entry valid for N bars", minval=1, group=grpF)

// ===== Risk / Reward =====
grpR = "Risk / Reward"
rr           = input.float(2.0, "Target R multiple", minval=0.5, step=0.5, group=grpR)
stopBufTicks = input.float(2,   "Stop buffer (ticks)", minval=0, group=grpR)

grpV = "Visuals"
showZones = input.bool(true, "Draw FVG zones", group=grpV)

// ===== Helpers =====
inEntry = not na(time(timeframe.period, entrySess, tz))
inFlat  = not na(time(timeframe.period, flatSess, tz))
buf     = stopBufTicks * syminfo.mintick
minGap  = minGapTicks * syminfo.mintick

var float amOpen = na
if inEntry and not inEntry[1]
    amOpen := open
plot(amOpen, "AM open", color=color.blue, style=plot.style_stepline)

longBias  = not useBias or (not na(amOpen) and close > amOpen)
shortBias = not useBias or (not na(amOpen) and close < amOpen)

avgRange   = ta.sma(high - low, 10)
dispRange  = high[1] - low[1]
strongDisp = not useDisp or dispRange >= dispMult * avgRange[2]

bullFVG = (low > high[2]) and (low - high[2] >= minGap) and close[1] > open[1] and strongDisp
bearFVG = (high < low[2]) and (low[2] - high >= minGap) and close[1] < open[1] and strongDisp

var int longArm  = na
var int shortArm = na

// ---- LONG: buy the FVG 50% in an up-drive ----
if bullFVG and useLong and longBias and inEntry and strategy.position_size == 0
    gTop = low
    gBot = high[2]
    ce   = (gTop + gBot) / 2
    stopP = gBot - buf
    risk  = ce - stopP
    if risk > 0
        strategy.entry("L", strategy.long, limit=ce)
        strategy.exit("Lx", "L", stop=stopP, limit=ce + rr * risk)
        longArm := bar_index
        if showZones
            box.new(bar_index, gTop, bar_index + zoneLife, gBot, border_color=color.new(color.teal,40), bgcolor=color.new(color.teal,88))

// ---- SHORT: sell the FVG 50% in a down-drive ----
if bearFVG and useShort and shortBias and inEntry and strategy.position_size == 0
    gBot = high
    gTop = low[2]
    ce   = (gTop + gBot) / 2
    stopP = gTop + buf
    risk  = stopP - ce
    if risk > 0
        strategy.entry("S", strategy.short, limit=ce)
        strategy.exit("Sx", "S", stop=stopP, limit=ce - rr * risk)
        shortArm := bar_index
        if showZones
            box.new(bar_index, gTop, bar_index + zoneLife, gBot, border_color=color.new(color.maroon,40), bgcolor=color.new(color.maroon,88))

// ---- cancel stale pending entries ----
if not na(longArm) and strategy.position_size == 0 and (bar_index - longArm >= zoneLife or not inEntry)
    strategy.cancel("L")
    longArm := na
if not na(shortArm) and strategy.position_size == 0 and (bar_index - shortArm >= zoneLife or not inEntry)
    strategy.cancel("S")
    shortArm := na

// ---- force flat at end of day ----
if inFlat
    strategy.cancel_all()
    strategy.close_all()
"""


def _minute_frame(rows, start, minutes=15):
    """Intraday bars spelled out as (open, high, low, close), stamped from
    ``start`` (UTC) every ``minutes``, for session-gated fill scenarios."""
    out = []
    for i, (o, h, l, c) in enumerate(rows):
        out.append(
            {
                "datetime": start + datetime.timedelta(minutes=minutes * i),
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "volume": 1000,
            }
        )
    return pd.DataFrame(out).set_index("datetime")


def _minute_fill_run(source, rows, start, minutes=15, **params):
    """_fill_run over intraday bars, for scripts whose sessions gate entries."""
    result = convert(source)
    assert result.ok, f"conversion reported: {result.unsupported}"
    namespace = {}
    exec(compile(result.code, "<converted>", "exec"), namespace)
    cerebro = bt.Cerebro()
    cerebro.adddata(
        bt.feeds.PandasData(
            dataname=_minute_frame(rows, start, minutes),
            timeframe=bt.TimeFrame.Minutes,
            compression=minutes,
        )
    )
    cerebro.addstrategy(namespace[result.class_name], **params)
    cerebro.broker.setcash(10_000.0)
    return cerebro.run()[0]


def test_ict_am_ob_converts_clean():
    result = convert(ICT_AM_OB)
    assert result.ok, result.unsupported
    assert ("entrySess", "0930-1130") in result.params
    assert ("flatSess", "1555-1600") in result.params
    assert ("mintick", 0.01) in result.params
    assert any("box.new" in item for item in result.ignored)


#: A New York morning of 15-minute bars, stamped in UTC on 2022-03-07 -- EST,
#: so 09:30 ET is 14:30 UTC. Ten quiet premarket bars from 12:00 warm the
#: displacement SMA, the opening bar captures amOpen at 100, a displacement
#: bar drives up, and the gap bar leaves a bullish FVG whose midpoint 101.25
#: the entry rests at -- stop 100.48 behind the order block, 2R target 102.79.
_AM_START = datetime.datetime(2022, 3, 7, 12, 0)
_AM_MORNING = [(100, 100.5, 99.5, 100)] * 10 + [  # premarket, 12:00-14:15 UTC
    (100, 100.5, 99.5, 100.2),  # 14:30, 09:30 ET: amOpen captured at 100
    (100.2, 103, 100, 102.9),  # 14:45: the opening drive (close > open)
    (103, 104, 102, 103.5),  # 15:00: gap bar, low 102 > 100.5: entry rests
    (103.5, 104, 102.5, 103),  # 15:15: no retrace yet
    (102.6, 102.7, 100.9, 101.5),  # 15:30: trades through 101.25, entry fills
]


def test_ict_am_ob_trades_a_bullish_am_gap_to_its_target():
    rows = _AM_MORNING + [
        (101.5, 103.5, 101.4, 103.2),  # trades through the 102.79 target
        (103, 103.1, 102.4, 102.8),
    ]
    strategy = _minute_fill_run(ICT_AM_OB, rows, start=_AM_START)
    assert strategy.position.size == 0
    # entry 101.25, stop 100.48, risk 0.77, 2R target 102.79: profit 1.54.
    assert strategy.broker.getvalue() == pytest.approx(10_001.54)


def test_ict_am_ob_goes_flat_into_its_window_with_nothing_left_resting():
    """The flat window cancels the bracket and closes at market. The bar after
    it dips through where the stop rested; a surviving stop would sell a
    second time and flip the book short, so flat is the proof."""
    rows = _AM_MORNING + [
        (101.5, 101.8, 101.0, 101.3),  # 15:45: drifting inside the bracket
        (101.3, 101.6, 100.9, 101.2),  # 16:00, 11:00 ET: force-flat fires
        (101.0, 101.4, 100.0, 100.8),  # closed at the open; the dip fills nothing
        (100.8, 101.0, 100.4, 100.6),
    ]
    strategy = _minute_fill_run(ICT_AM_OB, rows, start=_AM_START, flatSess="1100-1130")
    assert strategy.position.size == 0
    # entry 101.25, market close at the 101.0 open: a 0.25 loss, once.
    assert strategy.broker.getvalue() == pytest.approx(9_999.75)


def test_history_reads_on_the_first_bars_cannot_see_the_future():
    """Regression. Backtrader preloads the feed into flat arrays, and a read
    past the start -- `high[2]` on the first bar -- wraps around to the *end*
    of the feed: bars from the future. Pine answers na there, and a condition
    on na never fires. The tape below is built so only the wraparound can
    satisfy the condition: the cheap final bars are visible to the first bar's
    `high[2]` only through the wrap, and no honest read ever qualifies."""
    source = (
        '//@version=6\nstrategy("S")\n'
        "if low > high[2] and strategy.position_size == 0\n"
        '    strategy.entry("L", strategy.long)\n'
    )
    rows = [
        (100, 101, 99, 100),
        (100, 101, 99, 100),
        (100, 101, 99, 100),
        (10, 11, 9, 10),
        (10, 11, 9, 10),
    ]
    strategy = _fill_run(source, rows)
    assert strategy.position.size == 0


# --- history at an offset only known per bar ---------------------------------


def test_a_computed_offset_reads_history():
    """`close[rsPeriod]` where rsPeriod is a param, not a constant."""
    result = convert(
        '//@version=6\nstrategy("S")\n'
        'n = input.int(10, "N")\n'
        "v = close[n]\n"
        "if v > close\n    strategy.close()\n"
    )
    assert result.ok, result.unsupported
    assert "v = self._pine_back(self.data.close, self.p.n)" in result.code


def test_a_computed_offset_reads_an_indicators_history():
    result = convert(
        '//@version=6\nstrategy("S")\n'
        'n = input.int(3, "N")\n'
        "m = ta.sma(close, 20)\n"
        "v = m[n]\n"
        "if v > close\n    strategy.close()\n"
    )
    assert result.ok, result.unsupported
    assert "v = self._pine_back(self._sma_1, self.p.n)" in result.code


def test_reading_back_past_the_start_answers_na_not_the_future():
    """Backtrader's `line[-step]` past the start returns a bar from the *end*.

    The series is preloaded, so Python's negative indexing wraps: on bar 1 of
    a thirty-bar feed, `close[5]` hands back bar 26. Not an error, not `na` --
    a price the strategy could not have seen. A constant offset is handled by
    sitting the early bars out; a computed one has to be checked where it is
    read, and this is that check.
    """
    result = convert(
        '//@version=6\nstrategy("S")\n'
        'n = input.int(5, "N")\n'
        "var float seen = 0.0\n"
        "var int leaks = 0\n"
        "seen := close[n]\n"
        "if na(close[n])\n"
        "    leaks := leaks\n"
        "else\n"
        "    leaks := leaks + 1\n"
        "if close > open\n    strategy.close()\n"
    )
    assert result.ok, result.unsupported

    namespace = {}
    exec(compile(result.code, "<converted>", "exec"), namespace)
    base = namespace[result.class_name]

    closes = [100.0 + i for i in range(30)]
    start = datetime.datetime(2022, 1, 1)
    frame = pd.DataFrame(
        [
            {
                "datetime": start + datetime.timedelta(days=i),
                "open": close,
                "high": close * 1.01,
                "low": close * 0.99,
                "close": close,
                "volume": 1000,
            }
            for i, close in enumerate(closes)
        ]
    ).set_index("datetime")

    readings = {}

    class Watched(base):
        def next(self):
            super().next()
            readings[len(self)] = self.seen

    cerebro = bt.Cerebro()
    cerebro.adddata(bt.feeds.PandasData(dataname=frame))
    cerebro.addstrategy(Watched)
    cerebro.run()

    # The first five bars cannot reach five back, and must say so.
    for bar in range(1, 6):
        assert readings[bar] != readings[bar], f"bar {bar} answered {readings[bar]}"
    # From the sixth they are ordinary reads, and never a future price.
    for bar in range(6, len(closes) + 1):
        assert readings[bar] == closes[bar - 1 - 5]
    assert max(v for v in readings.values() if v == v) < closes[-1]


def test_a_computed_offset_on_something_that_is_not_a_series_is_reported():
    result = convert(
        '//@version=6\nstrategy("S")\n'
        'n = input.int(5, "N")\n'
        "var float acc = 0.0\n"
        "acc := acc + close\n"
        "v = acc[n]\n"
        "if v > 0\n    strategy.close()\n"
    )
    assert not result.ok
    assert any("needs a series to read back over" in i for i in result.unsupported)


def test_the_back_helper_is_only_emitted_when_it_is_used():
    assert "_pine_back" not in convert(DUAL_MA).code


# --- a second instrument -----------------------------------------------------

RELATIVE_STRENGTH = """//@version=6
strategy("RS")
peerSymbol = input.symbol("SPY", "Comparative Symbol")
lookback = input.int(20, "Lookback")
base = request.security(syminfo.tickerid, timeframe.period, close)
peer = request.security(peerSymbol, timeframe.period, close)
rs = (base / base[lookback]) / (peer / peer[lookback]) - 1
if rs > 0
    strategy.entry("l", strategy.long)
if rs < 0
    strategy.close()
"""


def test_a_named_symbol_becomes_a_feed_to_load():
    result = convert(RELATIVE_STRENGTH)
    assert result.ok, result.unsupported
    assert "('SPY', None, None)," in result.code
    assert "peer = self.datas[1].close[0]" in result.code


def test_input_symbol_is_a_param_that_cannot_move_the_feed():
    """Same reasoning as a timeframe: the feed is chosen before the strategy.

    The param stays, because the script may read it elsewhere, and the
    conversion says plainly that it is no longer wired to the data.
    """
    result = convert(RELATIVE_STRENGTH)
    assert "('peerSymbol', 'SPY')," in result.code
    assert any("peerSymbol" in item and "feed_spec" in item for item in result.ignored)


def test_a_symbol_and_a_timeframe_are_separate_feeds():
    result = convert(
        '//@version=6\nstrategy("S")\n'
        "peer = request.security('SPY', timeframe.period, close)\n"
        "htf = request.security(syminfo.tickerid, '1D', close)\n"
        "both = request.security('SPY', '1D', close)\n"
        "if peer > htf and both > 0\n    strategy.close()\n"
    )
    assert result.ok, result.unsupported
    assert "('SPY', None, None)," in result.code
    assert "(None, bt.TimeFrame.Days, 1)," in result.code
    assert "('SPY', bt.TimeFrame.Days, 1)," in result.code
    assert "needs 4 data feeds" in result.code


def test_two_reads_of_one_symbol_share_a_feed():
    result = convert(
        '//@version=6\nstrategy("S")\n'
        "a = request.security('SPY', 'D', close)\n"
        "b = request.security('SPY', 'D', high)\n"
        "if a > b\n    strategy.close()\n"
    )
    assert result.ok, result.unsupported
    assert result.code.count("('SPY', bt.TimeFrame.Days, 1),") == 1
    assert "self.datas[2]" not in result.code


def test_a_symbol_that_is_not_a_constant_is_reported():
    """The feed is loaded before the strategy, so the name has to be known."""
    result = convert(
        '//@version=6\nstrategy("S")\n'
        "sym = close > open ? 'SPY' : 'QQQ'\n"
        "peer = request.security(sym, 'D', close)\n"
        "if peer > close\n    strategy.close()\n"
    )
    assert not result.ok
    assert any("must be a literal string" in i for i in result.unsupported)


def test_a_second_instrument_still_refuses_lookahead():
    """Naming another symbol must not become a way around the lookahead guard."""
    result = convert(
        '//@version=6\nstrategy("S")\n'
        "peer = request.security('SPY', '240', close, barmerge.gaps_off,"
        " barmerge.lookahead_on)\n"
        "if peer > close\n    strategy.close()\n"
    )
    assert not result.ok
    assert any("lookahead_on reads a bar" in i for i in result.unsupported)


def test_generated_relative_strength_strategy_runs_on_two_instruments():
    """The whole point: a strategy that compares one symbol against another."""
    result = convert(RELATIVE_STRENGTH)
    assert result.ok, result.unsupported
    namespace = {}
    exec(compile(result.code, "<converted>", "exec"), namespace)
    strategy_cls = namespace[result.class_name]
    assert strategy_cls.feed_spec == (("SPY", None, None),)

    cerebro = bt.Cerebro()
    cerebro.adddata(bt.feeds.PandasData(dataname=_price_frame(seed=1)))
    for _symbol, timeframe, compression in strategy_cls.feed_spec:
        peer = bt.feeds.PandasData(dataname=_price_frame(seed=99))
        if timeframe is None:
            cerebro.adddata(peer)
        else:
            cerebro.resampledata(peer, timeframe=timeframe, compression=compression)
    cerebro.addstrategy(strategy_cls)
    cerebro.broker.setcash(10_000.0)
    cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name="trades")
    strategy = cerebro.run()[0]

    closed = strategy.analyzers.trades.get_analysis().get("total", {}).get("total", 0)
    assert closed > 0


# --- the date window, and the risk rules that stop a strategy ----------------


def test_pine_timestamp_spellings_fold_to_epoch_milliseconds():
    """Every spelling in the corpus, and the ones nothing there happens to use.

    A timestamp is a constant, so it is folded here rather than parsed again on
    every bar. Getting the fold wrong would move a strategy's start date by
    hours or years without anything failing, so the numbers are pinned.
    """
    from pwb_toolbox.converting.backtrader import parse_timestamp

    assert parse_timestamp("01 Jan 2020 00:00 +0000") == 1577836800000
    assert parse_timestamp("31 Dec 2030 23:59 +0000") == 1924991940000
    assert parse_timestamp("01 Jan 2024") == 1704067200000
    assert parse_timestamp("2023-01-01") == 1672531200000
    assert parse_timestamp("2025-08-20 00:00") == 1755648000000
    assert parse_timestamp("2024-06-01T12:30:00") == 1717245000000
    # No offset means UTC, which is what Pine assumes when none is given.
    assert parse_timestamp("01 Jan 2020 00:00") == parse_timestamp(
        "01 Jan 2020 00:00 +0000"
    )
    # An offset is honoured rather than ignored.
    assert parse_timestamp("01 Jan 2020 00:00 +0200") == 1577836800000 - 2 * 3600 * 1000


def test_a_timestamp_that_is_not_a_literal_date_is_reported():
    """The value has to be known before the run, so a shape that cannot be
    folded is reported rather than guessed at.

    ``timestamp("GMT+0", 2025, 2, 1, 0, 0)`` -- the numeric form with a
    timezone -- is the one shape in the corpus this does not read. It appears
    once, in an indicator, so it is a named gap rather than a silent one.
    """
    result = convert(
        '//@version=6\nstrategy("S")\n'
        'start = timestamp("GMT+0", 2025, 2, 1, 0, 0)\n'
        "if time > start\n    strategy.close()\n"
    )
    assert not result.ok
    assert any("only a literal date string" in i for i in result.unsupported)


def test_a_timestamp_is_folded_rather_than_parsed_on_every_bar():
    result = convert(
        '//@version=6\nstrategy("S")\n'
        'dFrom = input.time(timestamp("01 Jan 2020 00:00 +0000"), "From")\n'
        "if time > dFrom\n    strategy.close()\n"
    )
    assert result.ok, result.unsupported
    assert "('dFrom', 1577836800000)," in result.code
    assert "timestamp(" not in result.code
    assert "strptime" not in result.code


def test_input_time_becomes_a_param_the_run_can_override():
    """The date window is the most-overridden input there is -- walking a
    strategy forward means moving it -- so it has to be a live param.
    """
    source = (
        '//@version=6\nstrategy("S")\n'
        'dFrom = input.time(timestamp("09 Mar 2022 00:00 +0000"), "From")\n'
        'dTo = input.time(timestamp("11 Mar 2022 00:00 +0000"), "To")\n'
        "inRange = time >= dFrom and time <= dTo\n"
        "if inRange\n"
        '    strategy.entry("L", strategy.long)\n'
    )
    rows = [(100, 101, 99, 100)] * 7
    strategy = _fill_run(source, rows)
    assert strategy.p.dFrom == 1646784000000  # 9 March 2022, UTC
    assert strategy.p.dTo == 1646956800000  # 11 March 2022, UTC
    assert strategy.position.size == 1

    moved = _fill_run(source, rows, dFrom=0, dTo=1)
    assert moved.p.dFrom == 0
    # A window that closed before the feed starts lets nothing through, which
    # it could not do if the dates had been folded into the body.
    assert moved.position.size == 0


def _window_sizes(source, rows, **params):
    """Position size at the close of every bar."""
    result = convert(source)
    assert result.ok, f"conversion reported: {result.unsupported}"
    namespace = {}
    exec(compile(result.code, "<converted>", "exec"), namespace)

    seen = []
    base = namespace[result.class_name]

    class Watched(base):
        def next(self):
            super().next()
            seen.append(self.position.size)

    cerebro = bt.Cerebro()
    cerebro.adddata(bt.feeds.PandasData(dataname=_explicit_frame(rows)))
    cerebro.addstrategy(Watched, **params)
    cerebro.broker.setcash(10_000.0)
    cerebro.run()
    return seen


def test_a_date_window_gates_the_entries_and_shuts_the_position_after_it():
    """The shape nine of the corpus strategies are written in: an input.time
    pair, a `time` comparison, and every order behind it.

    The feed runs 7 March to 13 March; the window is 9 March to 11 March. An
    entry inside it fills on the next bar's open, and the bar after the window
    shuts closes what is left.
    """
    source = (
        '//@version=6\nstrategy("S")\n'
        'dFrom = input.time(timestamp("09 Mar 2022 00:00 +0000"), "From")\n'
        'dTo = input.time(timestamp("11 Mar 2022 00:00 +0000"), "To")\n'
        "inRange = time >= dFrom and time <= dTo\n"
        "if inRange\n"
        '    strategy.entry("L", strategy.long)\n'
        "if not inRange\n"
        '    strategy.close("L")\n'
    )
    rows = [(100, 101, 99, 100)] * 7
    #        7th 8th 9th 10th 11th 12th 13th
    assert _window_sizes(source, rows) == [0, 0, 0, 1, 1, 1, 0]
    # The control: with the window opened wide the same script is long from
    # the second bar and never lets go, so the window is what shaped the run.
    assert _window_sizes(source, rows, dFrom=0, dTo=99999999999999) == [
        0,
        1,
        1,
        1,
        1,
        1,
        1,
    ]


def test_a_risk_rule_reads_its_limit_and_its_basis():
    """`strategy.percent_of_equity` and `strategy.cash` mean different numbers,
    and Pine's default when neither is named is percent."""

    def emitted(line):
        result = convert(
            '//@version=6\nstrategy("S")\n'
            + line
            + "\nif close > 0\n    strategy.close()\n"
        )
        assert result.ok, result.unsupported
        return [
            l.strip()
            for l in result.code.splitlines()
            if "_pine_risk(" in l and not l.strip().startswith("def ")
        ]

    assert emitted("strategy.risk.max_drawdown(20)") == [
        "self._pine_risk(20, True, False)"
    ]
    assert emitted("strategy.risk.max_drawdown(20, strategy.percent_of_equity)") == [
        "self._pine_risk(20, True, False)"
    ]
    assert emitted("strategy.risk.max_drawdown(40, strategy.cash)") == [
        "self._pine_risk(40, False, False)"
    ]
    assert emitted(
        "strategy.risk.max_intraday_loss(5, strategy.percent_of_equity)"
    ) == ["self._pine_risk(5, True, True)"]
    assert emitted("strategy.risk.max_intraday_loss(250, strategy.cash)") == [
        "self._pine_risk(250, False, True)"
    ]


def test_a_risk_rule_without_a_limit_is_reported():
    result = convert(
        '//@version=6\nstrategy("S")\n'
        "strategy.risk.max_drawdown()\n"
        "if close > 0\n    strategy.close()\n"
    )
    assert not result.ok
    assert any("needs a limit" in i for i in result.unsupported)


def test_a_strategy_with_no_risk_rule_carries_no_halt_check():
    """The halt flags exist only alongside the rule that sets them, so the
    check that reads them has to be emitted alongside it too. Emitting it
    unconditionally made every entry raise AttributeError.
    """
    plain = convert(
        '//@version=6\nstrategy("S")\n'
        "if close > open\n"
        '    strategy.entry("L", strategy.long)\n'
    )
    assert plain.ok, plain.unsupported
    assert "_pine_entry" in plain.code
    assert "_pine_halted" not in plain.code

    guarded = convert(
        '//@version=6\nstrategy("S")\n'
        "strategy.risk.max_drawdown(20)\n"
        "if close > open\n"
        '    strategy.entry("L", strategy.long)\n'
    )
    assert guarded.ok, guarded.unsupported
    assert "if self._pine_halted or self._pine_day_halted:" in guarded.code


#: Enters every bar it can, so anything that stops it showing up as flat is
#: the risk rule rather than the entry condition running out.
ALWAYS_ENTERS = (
    '//@version=6\nstrategy("S")\n'
    "strategy.risk.max_drawdown(40, strategy.cash)\n"
    "if close > 0\n"
    '    strategy.entry("L", strategy.long)\n'
)

#: 1 unit bought near 100 against 10,000 of cash, then a collapse to 55. The
#: equity drop is 45 -- past a 40-of-cash limit, nowhere near 20% of equity.
COLLAPSE = [(100, 101, 99, 100)] * 3 + [(55, 56, 54, 55)] * 3


def _risk_run(source, rows, mutate=None, **params):
    """Run a conversion, optionally mutating the generated source first.

    Returns the strategy and the position size at the close of every bar. The
    mutation hook is how each guard gets tested on its own: take one line out
    and the run has to change.
    """
    result = convert(source)
    assert result.ok, f"conversion reported: {result.unsupported}"
    code = result.code if mutate is None else mutate(result.code)
    namespace = {}
    exec(compile(code, "<converted>", "exec"), namespace)

    seen = []
    base = namespace[result.class_name]

    class Watched(base):
        def next(self):
            super().next()
            seen.append(self.position.size)

    cerebro = bt.Cerebro()
    cerebro.adddata(bt.feeds.PandasData(dataname=_explicit_frame(rows)))
    cerebro.addstrategy(Watched, **params)
    cerebro.broker.setcash(10_000.0)
    return cerebro.run()[0], seen


def test_a_drawdown_breach_flattens_the_position_and_keeps_it_flat():
    strategy, sizes = _risk_run(ALWAYS_ENTERS, COLLAPSE)
    assert sizes == [0, 1, 1, 1, 0, 0]
    assert strategy._pine_halted is True

    # The control: the same tape with the rule taken out holds the position to
    # the end, so the collapse is not what closed it -- the rule is.
    control = ALWAYS_ENTERS.replace(
        "strategy.risk.max_drawdown(40, strategy.cash)\n", ""
    )
    _, unguarded = _risk_run(control, COLLAPSE)
    assert unguarded == [0, 1, 1, 1, 1, 1]


def test_the_halt_flag_is_what_refuses_the_entries_after_a_breach():
    """Flattening is only half of it. Pine places no new orders after a breach,
    and this script asks for one on every bar -- so with the check taken out of
    `_pine_entry` it walks straight back in on the next bar.
    """
    guard = (
        "        if self._pine_halted or self._pine_day_halted:\n"
        "            # A risk rule stopped trading; Pine places no new orders.\n"
        "            return\n"
    )
    _, sizes = _risk_run(
        ALWAYS_ENTERS, COLLAPSE, mutate=lambda c: c.replace(guard, "", 1)
    )
    assert sizes[-1] == 1
    assert sizes != [0, 1, 1, 1, 0, 0]


def test_a_percent_limit_is_measured_against_equity_not_currency():
    """Reading the basis backwards is silent: the number is the same, and only
    the tape says which one was meant. On this collapse a 40-of-cash limit
    halts and a 20-percent limit does not, so the pair pins the reading.
    """
    cash_rule, cash_sizes = _risk_run(ALWAYS_ENTERS, COLLAPSE)
    assert cash_rule._pine_halted is True
    assert cash_sizes[-1] == 0

    percent = ALWAYS_ENTERS.replace(
        "40, strategy.cash", "20, strategy.percent_of_equity"
    )
    percent_rule, percent_sizes = _risk_run(percent, COLLAPSE)
    assert percent_rule._pine_halted is False
    assert percent_sizes[-1] == 1


def _hourly_frame(days):
    """Hourly bars, `days` being a list of per-day (open, high, low, close)."""
    out = []
    for day, rows in enumerate(days):
        stamp = datetime.datetime(2022, 3, 7 + day, 10, 0)
        for o, h, l, c in rows:
            out.append(
                {
                    "datetime": stamp,
                    "open": o,
                    "high": h,
                    "low": l,
                    "close": c,
                    "volume": 1000,
                }
            )
            stamp += datetime.timedelta(hours=1)
    return pd.DataFrame(out).set_index("datetime")


def _intraday_run(source, days, mutate=None):
    result = convert(source)
    assert result.ok, f"conversion reported: {result.unsupported}"
    code = result.code if mutate is None else mutate(result.code)
    namespace = {}
    exec(compile(code, "<converted>", "exec"), namespace)

    seen = []
    base = namespace[result.class_name]

    class Watched(base):
        def next(self):
            super().next()
            seen.append(self.position.size)

    cerebro = bt.Cerebro()
    cerebro.adddata(
        bt.feeds.PandasData(
            dataname=_hourly_frame(days),
            timeframe=bt.TimeFrame.Minutes,
            compression=60,
        )
    )
    cerebro.addstrategy(Watched)
    cerebro.broker.setcash(10_000.0)
    return cerebro.run()[0], seen


INTRADAY_RULE = ALWAYS_ENTERS.replace(
    "strategy.risk.max_drawdown(40, strategy.cash)",
    "strategy.risk.max_intraday_loss(40, strategy.cash)",
)

#: Six hourly bars over two days: the collapse and the halt on the first, a
#: fresh start on the second.
TWO_DAYS = [
    [(100, 101, 99, 100)] * 3 + [(55, 56, 54, 55)] * 3,
    [(55, 56, 54, 55)] * 3,
]


def test_an_intraday_loss_halt_lifts_when_the_day_turns():
    """`max_intraday_loss` stops the day, not the backtest. The difference is
    the whole reason it is a separate rule from `max_drawdown`.
    """
    strategy, sizes = _intraday_run(INTRADAY_RULE, TWO_DAYS)
    assert sizes == [0, 1, 1, 1, 0, 0, 0, 1, 1]
    # Neither flag survives the day boundary: the run is trading again.
    assert strategy._pine_halted is False
    assert strategy._pine_day_halted is False


def test_without_the_day_reset_an_intraday_halt_never_lifts():
    _, sizes = _intraday_run(
        INTRADAY_RULE,
        TWO_DAYS,
        mutate=lambda c: c.replace(
            "            self._pine_day_halted = False\n", "", 1
        ),
    )
    assert sizes[-1] == 0


def test_a_halt_withdraws_orders_still_resting_on_the_book():
    """A breach is not just 'stop entering'. Anything already working has to
    come off, or the tape fills it afterwards and the strategy is in a trade
    the risk rule was there to prevent.

    The sell-stop rests at 50 from the second bar. The collapse to 55 breaches
    the limit and the halt cancels it; the tape then goes through 50, which an
    order left on the book would have filled.
    """
    source = (
        '//@version=6\nstrategy("S")\n'
        "strategy.risk.max_drawdown(40, strategy.cash)\n"
        "if bar_index == 1\n"
        '    strategy.entry("S", strategy.short, stop=50.0)\n'
        "if bar_index == 2\n"
        '    strategy.entry("L", strategy.long)\n'
    )
    rows = [
        (100, 101, 99, 100),  # bar_index 1: the sell-stop goes out at the close
        (100, 101, 99, 100),  # bar_index 2: a market long is placed
        (100, 101, 99, 100),  # the long fills at the open
        (55, 56, 54, 55),  # the breach: cancel the stop, close the long
        (55, 56, 54, 55),  # the close fills
        (45, 46, 44, 45),  # through 50 -- a surviving sell-stop fires here
        (45, 46, 44, 45),
    ]
    strategy, sizes = _risk_run(source, rows)
    assert strategy._pine_halted is True
    assert sizes == [0, 0, 1, 1, 0, 0, 0]

    cancel = (
        "        for order in list(self.broker.get_orders_open()):\n"
        "            if order.owner is self:\n"
        "                self.cancel(order)\n"
    )
    _, uncancelled = _risk_run(source, rows, mutate=lambda c: c.replace(cancel, "", 1))
    assert uncancelled[-1] == -1  # the short the halt was supposed to prevent


# --- the chart's own timeframe, as a number ----------------------------------


def test_timeframe_in_seconds_reads_the_feed_it_was_given():
    """`timeframe.in_seconds()` asks the feed, not a string, which is what lets
    it be answered from `__init__` as readily as from `next()`.
    """
    source = (
        '//@version=6\nstrategy("S")\n'
        "secs = timeframe.in_seconds()\n"
        "if secs > 3600\n"
        '    strategy.entry("L", strategy.long)\n'
    )
    result = convert(source)
    assert result.ok, result.unsupported
    assert "self._pine_tf_seconds()" in result.code

    namespace = {}
    exec(compile(result.code, "<converted>", "exec"), namespace)
    strategy_cls = namespace[result.class_name]

    seen = {}

    class Watched(strategy_cls):
        def next(self):
            super().next()
            seen["secs"] = self._pine_tf_seconds()

    for timeframe, compression, expected in (
        (bt.TimeFrame.Minutes, 60, 3600),
        (bt.TimeFrame.Minutes, 240, 14400),
        (bt.TimeFrame.Days, 1, 86400),
        (bt.TimeFrame.Weeks, 1, 604800),
    ):
        cerebro = bt.Cerebro()
        cerebro.adddata(
            bt.feeds.PandasData(
                dataname=_price_frame(bars=30),
                timeframe=timeframe,
                compression=compression,
            )
        )
        cerebro.addstrategy(Watched)
        cerebro.broker.setcash(10_000.0)
        cerebro.run()
        assert seen["secs"] == expected, (timeframe, compression)


def test_timeframe_in_seconds_spells_the_written_timeframes_pines_way():
    """The literal and input forms take the text, so the numbers have to match
    Pine's: a month is a flat 30 days there, not a calendar one."""
    source = (
        '//@version=6\nstrategy("S")\n'
        'htf = input.timeframe("240", "HTF")\n'
        'a = timeframe.in_seconds("15")\n'
        'b = timeframe.in_seconds("D")\n'
        "c = timeframe.in_seconds(htf)\n"
        "if a + b + c > 0\n"
        '    strategy.entry("L", strategy.long)\n'
    )
    result = convert(source)
    assert result.ok, result.unsupported
    assert "self._pine_tf_seconds('15')" in result.code
    assert "self._pine_tf_seconds('D')" in result.code
    # An input keeps its param: unlike a feed's timeframe, this one is only
    # read, so overriding it at addstrategy still moves the answer.
    assert "self._pine_tf_seconds(self.p.htf)" in result.code

    namespace = {}
    exec(compile(result.code, "<converted>", "exec"), namespace)
    strategy_cls = namespace[result.class_name]

    seen = {}

    class Watched(strategy_cls):
        def next(self):
            super().next()
            seen["got"] = [
                self._pine_tf_seconds(t) for t in ("15", "D", "240", "W", "M", "1S")
            ]

    cerebro = bt.Cerebro()
    cerebro.adddata(bt.feeds.PandasData(dataname=_price_frame(bars=30)))
    cerebro.addstrategy(Watched)
    cerebro.broker.setcash(10_000.0)
    cerebro.run()
    assert seen["got"] == [900, 86400, 14400, 604800, 2592000, 1]


def test_timeframe_period_and_the_bare_call_agree():
    """`timeframe.in_seconds(timeframe.period)` is the bare call written out,
    so the two must not lower to different things."""

    def lowered(argument):
        result = convert(
            '//@version=6\nstrategy("S")\n'
            f"secs = timeframe.in_seconds({argument})\n"
            "if secs > 0\n"
            '    strategy.entry("L", strategy.long)\n'
        )
        assert result.ok, result.unsupported
        return [l.strip() for l in result.code.splitlines() if "secs =" in l]

    assert (
        lowered("") == lowered("timeframe.period") == ["secs = self._pine_tf_seconds()"]
    )


def test_a_timeframe_only_known_per_bar_is_reported():
    """A feed is not built from a value that only exists once bars are running,
    and neither is the number of seconds in one."""
    result = convert(
        '//@version=6\nstrategy("S")\n'
        'tf = close > open ? "60" : "240"\n'
        "secs = timeframe.in_seconds(tf)\n"
        "if secs > 0\n"
        '    strategy.entry("L", strategy.long)\n'
    )
    assert not result.ok
    assert any("timeframe.in_seconds" in i for i in result.unsupported)


def test_timeframe_in_seconds_answers_before_the_first_bar():
    """The reason it reads the feed rather than a string: an indicator chosen
    by the chart's timeframe has to be chosen when the indicator is built, and
    `__init__` runs before any bar has arrived.
    """
    source = (
        '//@version=6\nstrategy("S")\n'
        "secs = timeframe.in_seconds()\n"
        "if secs > 0\n"
        '    strategy.entry("L", strategy.long)\n'
    )
    result = convert(source)
    assert result.ok, result.unsupported
    namespace = {}
    exec(compile(result.code, "<converted>", "exec"), namespace)

    asked = {}

    class Watched(namespace[result.class_name]):
        def __init__(self):
            asked["in_init"] = self._pine_tf_seconds()
            super().__init__()

    cerebro = bt.Cerebro()
    cerebro.adddata(
        bt.feeds.PandasData(
            dataname=_price_frame(bars=30),
            timeframe=bt.TimeFrame.Minutes,
            compression=240,
        )
    )
    cerebro.addstrategy(Watched)
    cerebro.broker.setcash(10_000.0)
    cerebro.run()
    assert asked["in_init"] == 14400


# --- choosing between indicators before the first bar ------------------------


#: The commonest shape in the corpus: a `switch` picking a moving average,
#: reached through a function and keyed off an input.
MA_SELECTOR = (
    '//@version=6\nstrategy("S")\n'
    'mode = input.string("HMA", "Mode")\n'
    'avgLen = input.int(21, "Length")\n'
    "f_smooth(src, length, m) =>\n"
    "    switch m\n"
    "        'SMA' => ta.sma(src, length)\n"
    "        'RMA' => ta.rma(src, length)\n"
    "        'HMA' => ta.hma(src, length)\n"
    "        =>       ta.ema(src, length)\n"
    "wt1 = f_smooth(close, avgLen, mode)\n"
    "wt2 = ta.sma(wt1, 4)\n"
    "if ta.crossover(wt1, wt2)\n"
    '    strategy.entry("L", strategy.long)\n'
)


def test_a_switch_between_indicators_becomes_one_construction():
    result = convert(MA_SELECTOR)
    assert result.ok, result.unsupported
    # One conditional expression, not one hoisted average per branch.
    assert result.code.count("bt.indicators.SMA(") == 2  # the choice, and wt2
    assert "if (self.p.mode == 'SMA')" in result.code
    # The per-bar read comes off the line the choice built rather than
    # lowering the whole switch a second time.
    assert "wt1 = self._choice_1[0]" in result.code


def test_only_the_chosen_branch_is_built():
    """The reason this is a conditional *expression* rather than a selector
    over pre-built branches.

    Backtrader's minimum period is the maximum over every indicator the
    strategy holds, read or not -- an unused 80-period average delays the
    first `next()` to bar 80 exactly as a used one would. So building the
    branch not taken would move the bar a converted strategy starts trading
    on, and nothing in the generated source would look wrong.
    """
    source = (
        '//@version=6\nstrategy("S")\n'
        'mode = input.string("FAST", "Mode")\n'
        "f_pick(source) =>\n"
        "    switch mode\n"
        "        'FAST' => ta.sma(source, 5)\n"
        "        =>       ta.sma(source, 80)\n"
        "ma = f_pick(close)\n"
        "if close > ma\n"
        '    strategy.entry("L", strategy.long)\n'
    )
    result = convert(source)
    assert result.ok, result.unsupported
    namespace = {}
    exec(compile(result.code, "<converted>", "exec"), namespace)
    base = namespace[result.class_name]

    def first_bar(mode):
        seen = {}

        class Watched(base):
            def next(self):
                super().next()
                seen.setdefault("first", len(self.data))

        cerebro = bt.Cerebro()
        cerebro.adddata(bt.feeds.PandasData(dataname=_price_frame(bars=150)))
        cerebro.addstrategy(Watched, mode=mode)
        cerebro.broker.setcash(10_000.0)
        cerebro.run()
        return seen["first"]

    assert first_bar("FAST") == 5
    assert first_bar("SLOW") == 80


def test_a_conditional_between_numbers_stays_a_number():
    """Only a choice between *lines* is a line. `mode == 'A' ? 5 : 20` is a
    number, and a number cannot be read at `[0]`."""
    result = convert(
        '//@version=6\nstrategy("S")\n'
        'mode = input.string("A", "Mode")\n'
        "len = mode == 'A' ? 5 : 20\n"
        "ma = ta.sma(close, len)\n"
        "if close > ma\n"
        '    strategy.entry("L", strategy.long)\n'
    )
    assert result.ok, result.unsupported
    assert "_choice" not in result.code

    namespace = {}
    exec(compile(result.code, "<converted>", "exec"), namespace)
    cerebro = bt.Cerebro()
    cerebro.adddata(bt.feeds.PandasData(dataname=_price_frame(bars=60)))
    cerebro.addstrategy(namespace[result.class_name])
    cerebro.broker.setcash(10_000.0)
    cerebro.run()  # the regression: this used to raise on `(5 if c else 20)[0]`


def test_a_per_bar_condition_is_still_lazy():
    """`d != 0 ? x / d : 0` is a guard, not a choice between indicators.

    Its condition moves per bar, so it cannot be settled in `__init__` and
    must not become a `_choice`. Fed to an indicator it needs a line, and the
    line it gets is a `PineExpr` -- a Python lambda, evaluated per bar and
    lazily, which is the whole reason `bt.If` is not used here.
    """
    result = convert(
        '//@version=6\nstrategy("S")\n'
        "d = ta.stdev(close, 20)\n"
        "z = d != 0 ? (close - ta.sma(close, 20)) / d : 0.0\n"
        "smoothed = ta.sma(z, 5)\n"
        "if smoothed > 1\n"
        '    strategy.entry("L", strategy.long)\n'
    )
    assert result.ok, result.unsupported
    assert "PineExpr" in result.code
    assert "_choice" not in result.code


def test_a_branch_that_will_not_lower_declines_without_inventing_a_reason():
    """The attempt is speculative, so a branch that cannot be a line must not
    leave its own rejection behind -- the fallback path is what decides what
    to report, and two messages for one gap reads as two gaps."""
    source = (
        '//@version=6\nstrategy("S")\n'
        'mode = input.string("SMA", "Mode")\n'
        "f_state(src) =>\n"
        "    var float carried = 0.0\n"
        "    carried := carried + src\n"
        "    carried\n"
        "pick = mode == 'SMA' ? ta.sma(close, 10) : f_state(close)\n"
        "if close > pick\n"
        '    strategy.entry("L", strategy.long)\n'
    )
    result = convert(source)
    # Whatever it reports, it must not report the same gap twice.
    assert len(result.unsupported) == len(set(result.unsupported))
