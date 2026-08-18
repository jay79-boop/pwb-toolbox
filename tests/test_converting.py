"""Tests for `pwb_toolbox.converting`.

The important ones are at the bottom: generated strategies are compiled and run
through a real Backtrader `cerebro` on synthetic bars, so "it converted" is
never mistaken for "it works".
"""

import datetime
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
    assert "self.buy()" in next_body
    assert "self.close()" in next_body


def test_convert_maps_short_entry_to_sell():
    source = '//@version=5\nstrategy("S")\nif close > open\n    strategy.entry("s", strategy.short)\n'
    assert "self.sell()" in convert(source).code


def test_convert_passes_entry_quantity_as_size():
    source = (
        '//@version=5\nstrategy("S")\nif close > open\n'
        '    strategy.entry("l", strategy.long, qty=5)\n'
    )
    assert "self.buy(size=5)" in convert(source).code


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
        ("s = request.security('AAPL', '1D', close)\n", "syminfo.tickerid"),
        ("varip count = 0\n", "varip count"),
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
    code = convert('//@version=5\nstrategy("S")\nvarip c = 0\n').code
    assert "Not translated" in code and "varip c" in code


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
    for timeframe, compression in (
        feeds if feeds is not None else strategy_cls.resample_spec
    ):
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
        ("D", "(bt.TimeFrame.Days, 1)"),
        ("1D", "(bt.TimeFrame.Days, 1)"),
        ("W", "(bt.TimeFrame.Weeks, 1)"),
        ("240", "(bt.TimeFrame.Minutes, 240)"),
        ("30S", "(bt.TimeFrame.Seconds, 30)"),
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
    assert "resample_spec" not in result.code
    assert "h = self.data.close[0]" in result.code


def test_security_on_another_symbol_is_reported():
    result = convert(
        "//@version=6\nstrategy(\"S\")\nh = request.security('AAPL', 'D', close)\n"
    )
    assert not result.ok
    assert any("syminfo.tickerid" in item for item in result.unsupported)


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
    assert any("htfTF" in item and "resample_spec" in item for item in result.ignored)


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


def test_varip_is_still_refused():
    """varip updates intrabar; a bar-close run has no ticks to update on."""
    result = convert('//@version=6\nstrategy("S")\nvarip int n = 0\n')
    assert not result.ok
    assert any("intrabar" in item for item in result.unsupported)


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
    assert "self.buy()" in result.code and "self.close()" in result.code


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


def test_var_inside_a_body_is_reported():
    """Pine keeps a `var` per call site, which substitution cannot give it."""
    source = (
        '//@version=6\nstrategy("S")\n'
        "acc(x) =>\n"
        "    var float total = 0.0\n"
        "    total := total + x\n"
        "    total\n"
        "y = acc(close)\n"
    )
    result = convert(source)
    assert not result.ok
    assert any("keeps state per call site" in item for item in result.unsupported)


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
