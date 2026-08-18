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


def test_varip_in_a_body_is_refused_for_being_intrabar():
    """The objection to varip is ticks, not inlining -- say the right one."""
    source = (
        '//@version=6\nstrategy("S")\n'
        "f(x) =>\n"
        "    varip float a = 0.0\n"
        "    a := a + x\n"
        "    a\n"
        "y = f(close)\n"
    )
    result = convert(source)
    assert not result.ok
    assert any("updates intrabar" in i for i in result.unsupported)


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
