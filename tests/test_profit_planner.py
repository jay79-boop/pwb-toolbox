"""Checks on the generated Profit & Exit Planner workbook.

These pin the specific defects that the workbook exists to fix — duplicated
holdings, an exit ladder selling more than it holds, an all-time high rounded
away to zero — so a future edit to the seed data cannot quietly reintroduce
them. They also audit every formula's sheet and range references, which is the
failure mode a spreadsheet hides best: a wrong reference still calculates, it
just calculates something else.
"""

import re

import pytest

openpyxl = pytest.importorskip("openpyxl")

from tools.build_profit_planner import (  # noqa: E402
    PLAN_COUNT,
    POS_FIRST,
    POS_LAST,
    POS_TOTAL,
    CLASSES,
    LOG_FIRST,
    LOG_LAST,
    POSITIONS,
    SECTORS,
    STATUSES,
    TP_HEADER,
    TP_INPUT,
    TP_RUNG_FIRST,
    TP_RUNG_LAST,
    TP_RUNGS,
    build_workbook,
)

EXPECTED_SHEETS = (
    ["Start Here"]
    + [f"Plan {i}" for i in range(1, PLAN_COUNT + 1)]
    + ["Positions", "Dashboard", "Trade Log", "Watch", "Settings", "Lists"]
)


@pytest.fixture(scope="module")
def wb():
    return build_workbook()


def test_expected_sheets_present(wb):
    assert wb.sheetnames == EXPECTED_SHEETS
    assert wb["Lists"].sheet_state == "hidden"


def test_position_sectors_are_in_the_dropdown_list():
    for p in POSITIONS:
        assert p.sector in SECTORS, p.asset
        assert p.asset_class in CLASSES, p.asset


def test_positions_seed_fits_the_sheet(wb):
    assert len(POSITIONS) <= POS_LAST - POS_FIRST + 1
    ws = wb["Positions"]
    assert ws.cell(row=POS_TOTAL, column=1).value.startswith("TOTAL")
    assert ws.cell(row=POS_TOTAL, column=14).value == f"=SUM(N{POS_FIRST}:N{POS_LAST})"
    assert ws.cell(row=POS_TOTAL, column=15).value == f"=SUM(O{POS_FIRST}:O{POS_LAST})"


def test_defined_names_point_at_settings(wb):
    for name in ("TaxRate", "FeeRate", "MaxWeight"):
        assert name in wb.defined_names
        assert wb.defined_names[name].attr_text.startswith("Settings!$C$")
    ws = wb["Settings"]
    for ref in ("C4", "C5", "C6"):
        assert isinstance(ws[ref].value, (int, float)), ref


def _formulas(wb):
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                if isinstance(cell.value, str) and cell.value.startswith("="):
                    yield ws.title, cell.coordinate, cell.value


SHEET_REF = re.compile(r"(?:'([^']+)'|\b([A-Za-z ]+))!\$?[A-Z]{1,2}\$?\d")


def test_cross_sheet_references_name_real_sheets(wb):
    """A misquoted sheet name is silently wrong, never loudly broken."""
    names = set(wb.sheetnames)
    for sheet, coord, formula in _formulas(wb):
        for quoted, bare in SHEET_REF.findall(formula):
            ref = quoted or bare
            assert ref in names, f"{sheet}!{coord} references unknown sheet {ref!r}"


def test_sheet_names_with_spaces_are_quoted(wb):
    """'Exit Ladder'!A4 parses; Exit Ladder!A4 does not."""
    spaced = [n for n in wb.sheetnames if " " in n]
    for sheet, coord, formula in _formulas(wb):
        for name in spaced:
            for match in re.finditer(re.escape(name) + "!", formula):
                start = match.start()
                assert (
                    start > 0 and formula[start - 1] == "'"
                ), f"{sheet}!{coord} uses unquoted {name}!"


def test_no_dynamic_array_functions(wb):
    """These survive neither the .xlsx round trip nor older engines."""
    banned = ("XLOOKUP", "SORT(", "FILTER(", "LET(", "LAMBDA(", "_xlfn")
    for sheet, coord, formula in _formulas(wb):
        upper = formula.upper()
        for name in banned:
            assert name not in upper, f"{sheet}!{coord} uses {name}"


def test_price_falls_back_when_the_feed_is_missing(wb):
    """GOOGLEFINANCE does not carry every altcoin; an unpriced row would
    poison every total on the Dashboard."""
    ws = wb["Positions"]
    for row in range(POS_FIRST, POS_LAST + 1):
        formula = ws.cell(row=row, column=12).value
        assert "GOOGLEFINANCE" in formula
        assert formula.startswith(f'=IF($A{row}="","",IFERROR(')
        assert formula.endswith(f"$K{row}))")


def test_workbook_saves(wb, tmp_path):
    out = tmp_path / "planner.xlsx"
    wb.save(out)
    assert out.stat().st_size > 10_000
    reopened = openpyxl.load_workbook(out)
    assert reopened.sheetnames == wb.sheetnames


def _riding(pcts):
    """What is left after every rung takes its share of the remainder."""
    left = 1.0
    for pct in pcts:
        left *= 1 - pct
    return left


def test_ticker_plan_rungs_rise_and_do_not_oversell():
    gains = [gain for gain, _ in TP_RUNGS]
    assert gains == sorted(gains)
    assert all(gain > 0 for gain in gains)
    shares = [pct for _, pct in TP_RUNGS]
    assert all(0 < pct <= 1 for pct in shares), "a share of the remainder"
    riding = _riding(shares)
    assert riding < 0.5, f"the seeded plan leaves {riding:.0%} riding"
    assert riding > 0, "a plan that closes the position completely keeps no tail"


def test_ticker_plan_fits_its_rows():
    assert len(TP_RUNGS) <= TP_RUNG_LAST - TP_RUNG_FIRST + 1


def test_closed_positions_leave_the_portfolio_totals(wb):
    """A closed round keeps its realised result and stops counting.

    Market value, unrealised and weight blank out unless the row says Active,
    and the cost total is a SUMIFS on Active — which is what lets a finished
    position stay on the register without inflating what you appear to hold.
    """
    ws = wb["Positions"]
    for row in range(POS_FIRST, POS_LAST + 1):
        for col in (14, 23):  # market value, if-ATH-returns
            assert f'$E{row}<>"Active"' in ws.cell(row=row, column=col).value
    assert (
        ws.cell(row=POS_TOTAL, column=9).value
        == f'=SUMIFS(I{POS_FIRST}:I{POS_LAST},E{POS_FIRST}:E{POS_LAST},"Active")'
    )
    # Realised is summed across every status, closed rounds included.
    assert ws.cell(row=POS_TOTAL, column=18).value == f"=SUM(R{POS_FIRST}:R{POS_LAST})"


def test_realised_profit_is_scoped_to_the_round(wb):
    """Re-entering a position must not disturb the round before it.

    The realised figure joins the log on name *and* round, so buying a coin
    again after closing it starts from its own average cost and leaves the
    banked result alone.
    """
    ws = wb["Positions"]
    for row in range(POS_FIRST, POS_LAST + 1):
        formula = ws.cell(row=row, column=18).value
        assert "SUMIFS('Trade Log'!" in formula
        assert f"$A{row}" in formula, "must match on the position name"
        assert f"$F{row}" in formula, "must match on the round as well"


def test_trade_log_averages_only_that_round(wb):
    ws = wb["Trade Log"]
    for row in range(LOG_FIRST, LOG_LAST + 1):
        formula = ws.cell(row=row, column=11).value
        assert formula.count("SUMIFS(") == 2, "cost over units, both filtered"
        assert f"$B{row}" in formula and f"$C{row}" in formula


def test_statuses_and_classes_are_offered_as_dropdowns(wb):
    ws = wb["Lists"]
    assert [
        ws.cell(row=r, column=5).value for r in range(2, 2 + len(CLASSES))
    ] == CLASSES
    assert [
        ws.cell(row=r, column=6).value for r in range(2, 2 + len(STATUSES))
    ] == STATUSES
    sources = {dv.formula1 for dv in wb["Positions"].data_validations.dataValidation}
    assert "=Lists!$E$2:$E$7" in sources, "Class column needs its dropdown"
    assert "=Lists!$F$2:$F$4" in sources, "Status column needs its dropdown"


def test_workbook_recalculates_on_open(wb):
    """openpyxl saves no cached values.

    A consumer that trusts what is stored rather than recomputing would show
    every formula as blank, which reads as "the sheet does not work" rather
    than as a missing flag.
    """
    assert wb.calculation.fullCalcOnLoad is True


def test_staleness_is_measured_in_money_not_in_rows(wb):
    """One stale holding was a third of the book while reading as one row of
    twelve. A count understates the damage; the dashboard has to weigh it."""
    band = wb["Dashboard"]["B7"].value
    assert "SUMIF(Positions!$M" in band
    assert '"<>live"' in band, "must select the rows with no live feed"
    assert f"Positions!$N${POS_FIRST}:$N${POS_LAST}" in band, "must sum market value"
    assert f"Positions!$N${POS_TOTAL}" in band, "must express it as a share"
    assert "priced by hand" in band


def test_the_stale_band_sits_under_the_portfolio_value(wb):
    """It qualifies that tile, so it has to be read in the same glance."""
    ws = wb["Dashboard"]
    merged = {str(r) for r in ws.merged_cells.ranges}
    assert "B7:P7" in merged
    # The two tiles above it are the split the band explains.
    assert '"live"' in ws["B5"].value, "left tile is the live-priced value"
    assert "SUMIF(Positions!$M" in ws["F5"].value, "right tile is hand-priced"


def test_a_plan_reads_the_register_rather_than_repeating_it(wb):
    """XRP once sat in the workbook at two different average costs.

    A plan picks a holding by name and pulls quantity, cost and ticker from
    Positions, so the two cannot drift apart by accident. They stay typeable —
    and the tab then reports that it no longer matches.
    """
    for index in range(1, PLAN_COUNT + 1):
        ws = wb[f"Plan {index}"]
        for column in (3, 4, 5):
            formula = ws.cell(row=TP_INPUT, column=column).value
            assert "Positions!" in formula, f"Plan {index} column {column}"
            assert "IFERROR(" in formula, "an unknown holding must not error"
        matches = ws.cell(row=TP_INPUT, column=14).value
        assert '"EDITED"' in matches and "Positions!" in matches


def test_a_plan_only_ever_looks_at_positions(wb):
    """Duplicating a tab has to give a working plan, not a mirror of this one."""
    for index in range(1, PLAN_COUNT + 1):
        name = f"Plan {index}"
        others = [n for n in wb.sheetnames if n not in (name, "Positions")]
        for row in wb[name].iter_rows():
            for cell in row:
                if not (isinstance(cell.value, str) and cell.value.startswith("=")):
                    continue
                for other in others:
                    assert f"'{other}'!" not in cell.value, f"{name}!{cell.coordinate}"
                    assert f"{other}!" not in cell.value, f"{name}!{cell.coordinate}"


def test_an_unfinished_row_says_what_it_wants(wb):
    """A holding with a name and nothing else used to contribute silently.

    It reported no value, no weight and no warning — the register simply
    ignored it, and the portfolio total was wrong by whatever it held.
    """
    ws = wb["Positions"]
    for row in range(POS_FIRST, POS_LAST + 1):
        needs = ws.cell(row=row, column=25).value
        assert needs.startswith(f'=IF($A{row}="","",')
        for field in ("Status", "Quantity", "Avg cost", "Class", "Ticker"):
            assert f'"set {field}"' in needs, f"row {row} never asks for {field}"


def test_the_register_ships_without_pretending(wb):
    """No holding on screen may be one the owner does not own."""
    example = [p for p in POSITIONS if p.asset.startswith("EXAMPLE")]
    assert len(example) == 1, "exactly one row shows the shape"
    assert example[0].qty > 0 and example[0].avg_cost > 0, "and it is complete"
    for position in POSITIONS:
        if position is example[0]:
            continue
        assert position.qty == 0, f"{position.asset} must ship empty of quantity"
        assert position.ticker.startswith("CURRENCY:"), "a feed Google carries"


def test_watch_covers_every_plan_and_every_register_row(wb):
    ws = wb["Watch"]
    for index in range(1, PLAN_COUNT + 1):
        assert f"'Plan {index}'!" in ws.cell(row=3 + index, column=2).value
    first_holding = 3 + PLAN_COUNT + 3 + 1
    assert f"Positions!$A${POS_FIRST}" in ws.cell(row=first_holding, column=1).value


def test_the_next_rung_never_lands_on_a_blank_row(wb):
    """A price past every rung used to report no target at all.

    MATCH walked off the end of the seeded rungs onto an empty one, so the
    Watch tab published a blank target and the watcher had nothing to say
    about the position that had run furthest.
    """
    for index in range(1, PLAN_COUNT + 1):
        which = wb[f"Plan {index}"].cell(row=TP_RUNG_FIRST + 9, column=18).value
        assert "MATCH(0," in which
        assert f"COUNT($C${TP_RUNG_FIRST}" in which, "cap on rungs, not on rows"
        assert "IFERROR(" in which, "no rung passed yet is not an error"
