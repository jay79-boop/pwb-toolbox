"""The sweep is what picks the next converter fix, so its ranking has to be
honest about what a count means.

Every number it prints is a frontier: the converter reports the gaps it
reaches, and one it never reaches it never reports. A reason in front of nine
scripts converts none of them unless it is also the last thing in the way of
one. These tests pin that distinction, because losing it is how an afternoon
gets spent on a fix that moves nothing.
"""

import pathlib

import pytest

from tools.pine_sweep import main, normalise, sweep

_CLEAN = '//@version=6\nstrategy("Clean")\nif close > open\n    strategy.close()\n'

#: One gap, and it is the only thing in the way.
_ONE_GAP = (
    '//@version=6\nstrategy("One")\n'
    "for i = 0 to 3\n    x = i\n"
    "if close > open\n    strategy.close()\n"
)

#: Two gaps at once, so neither is the last one.
_TWO_GAPS = (
    '//@version=6\nstrategy("Two")\n'
    "for i = 0 to 3\n    x = i\n"
    "a = array.new_float(0)\n"
    "if close > open\n    strategy.close()\n"
)


def _corpus(tmp_path, **files):
    for name, source in files.items():
        (tmp_path / f"{name}.pine").write_text(source, encoding="utf-8")
    return tmp_path


def test_a_reason_in_front_of_a_script_is_not_a_reason_that_blocks_it_alone(tmp_path):
    """`for` is in front of both scripts. It is the last gap on only one."""
    root = _corpus(tmp_path, one=_ONE_GAP, two=_TWO_GAPS)
    _, _, _, reasons, sole, _ = sweep(root, strategies_only=True)

    loop = "for block is not supported"
    assert reasons[loop] == 2
    assert sole[loop] == 1


def test_per_script_rows_come_back_closest_first(tmp_path):
    root = _corpus(tmp_path, one=_ONE_GAP, two=_TWO_GAPS, clean=_CLEAN)
    considered, clean, crashes, _, _, per_script = sweep(root, strategies_only=True)

    assert (considered, clean, crashes) == (3, 1, [])
    assert [count for count, _, _ in per_script] == [1, 2]
    assert per_script[0][1] == "one.pine"


def test_a_gap_that_is_the_last_one_ranks_above_a_commoner_frontier(tmp_path, capsys):
    """Sorting by raw count would put the frontier first and send the reader
    at a fix that converts nothing."""
    root = _corpus(tmp_path, one=_ONE_GAP, two=_TWO_GAPS)
    main([str(root), "--strategies-only"])
    lines = [l for l in capsys.readouterr().out.split("\n") if l.startswith("  ")]

    ranked = [l for l in lines if "supported" in l]
    assert "for block" in ranked[0]
    assert "the last gap on 1" in ranked[0]


def test_a_corpus_where_no_fix_converts_anything_says_so(tmp_path, capsys):
    """The state this corpus was actually in, and the thing worth knowing
    before starting: every visible gap has another one behind it."""
    root = _corpus(tmp_path, two=_TWO_GAPS)
    main([str(root), "--strategies-only"])

    assert "no single fix" in capsys.readouterr().out


def test_by_script_lists_the_gaps_and_warns_they_are_not_a_total(tmp_path, capsys):
    root = _corpus(tmp_path, two=_TWO_GAPS)
    main([str(root), "--strategies-only", "--by-script"])
    out = capsys.readouterr().out

    assert "another may sit behind" in out
    assert "two.pine" in out
    assert "for block is not supported" in out


def test_an_empty_corpus_reports_rather_than_dividing_by_zero(tmp_path, capsys):
    assert main([str(tmp_path), "--strategies-only"]) == 1
    assert "no .pine files" in capsys.readouterr().out


@pytest.mark.parametrize(
    "reason,collapsed",
    [
        ("unknown identifier 'h4Val'", "unknown identifier '<name>'"),
        ("unknown identifier 'timeframe.period'", "unknown identifier 'timeframe.*'"),
        ("var entryPrice: needs a literal", "var <name>: needs a literal"),
    ],
)
def test_reasons_collapse_to_the_gap_they_describe(reason, collapsed):
    """`var entryPrice` and `var stopPrice` are one gap, not two, or the
    ranking splits a single fix across as many rows as it has identifiers."""
    assert normalise(reason) == collapsed
