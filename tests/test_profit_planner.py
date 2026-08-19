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
    LADDER_FIRST,
    LADDER_LAST,
    LADDERS,
    POS_FIRST,
    POS_LAST,
    POS_TOTAL,
    CLASSES,
    LOG_FIRST,
    NO_GOOGLE_FEED,
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

EXPECTED_SHEETS = [
    "Start Here",
    "Ticker Plan",
    "Positions",
    "Exit Ladder",
    "Dashboard",
    "Trade Log",
    "Settings",
    "Lists",
]


@pytest.fixture(scope="module")
def wb():
    return build_workbook()


def test_expected_sheets_present(wb):
    assert wb.sheetnames == EXPECTED_SHEETS
    assert wb["Lists"].sheet_state == "hidden"


def test_positions_are_unique():
    """The old sheet listed Decentraland and Chainlink twice each."""
    names = [p.asset for p in POSITIONS]
    assert len(names) == len(set(names)), "duplicate holding carried over"


def test_no_placeholder_rows():
    """Two 'Future Asset' template rows sat among the real holdings."""
    assert not [p for p in POSITIONS if "future asset" in p.asset.lower()]


def test_every_position_has_a_real_quantity_and_cost():
    for p in POSITIONS:
        assert p.qty > 0, p.asset
        assert p.avg_cost > 0, p.asset
        assert p.manual_price > 0, p.asset


def test_all_time_highs_are_not_rounded_away():
    """Shiba Inu's high displayed as $0.00, which made X-to-ATH meaningless."""
    for p in POSITIONS:
        if p.ath is not None:
            assert p.ath > 0, p.asset
            assert p.ath >= p.manual_price, p.asset


def test_position_sectors_are_in_the_dropdown_list():
    for p in POSITIONS:
        assert p.sector in SECTORS, p.asset
        assert p.asset_class in CLASSES, p.asset


def test_ladders_never_sell_more_than_the_position():
    """The old Gala ladder sold 3,563 coins with 891 left."""
    for ladder in LADDERS:
        total = sum(rung.pct for rung in ladder.rungs)
        assert total == pytest.approx(1.0), f"{ladder.asset} sells {total:.0%}"


def test_ladder_targets_rise_monotonically():
    for ladder in LADDERS:
        targets = [rung.target for rung in ladder.rungs]
        assert targets == sorted(targets), ladder.asset


def test_ladder_assets_exist_in_positions():
    held = {p.asset for p in POSITIONS}
    for ladder in LADDERS:
        assert ladder.asset in held, ladder.asset


def test_ladder_capacity_fits_the_seeded_rungs(wb):
    rungs = sum(len(ladder.rungs) for ladder in LADDERS)
    assert rungs <= LADDER_LAST - LADDER_FIRST + 1


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


def test_ticker_plan_rungs_rise_and_do_not_oversell():
    gains = [gain for gain, _ in TP_RUNGS]
    assert gains == sorted(gains)
    assert all(gain > 0 for gain in gains)
    sold = sum(pct for _, pct in TP_RUNGS)
    assert sold <= 1.0, f"the seeded plan sells {sold:.0%} of the position"
    assert sold > 0.5, "a plan that sells almost nothing is not a plan"


def test_ticker_plan_fits_its_rows():
    assert len(TP_RUNGS) <= TP_RUNG_LAST - TP_RUNG_FIRST + 1


def test_ticker_plan_is_self_contained(wb):
    """Duplicating the tab has to give a working plan for the next coin.

    A reference to another sheet would survive the copy and quietly keep
    pointing at the original, so the copy would show the first coin's numbers
    under the second coin's ticker. Only the two Settings rates may leak in,
    and they come through defined names rather than cell references.
    """
    others = [n for n in wb.sheetnames if n != "Ticker Plan"]
    ws = wb["Ticker Plan"]
    for row in ws.iter_rows():
        for cell in row:
            if not (isinstance(cell.value, str) and cell.value.startswith("=")):
                continue
            for name in others:
                assert (
                    f"{name}!" not in cell.value
                ), f"Ticker Plan!{cell.coordinate} references {name}"


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


def test_ticker_plan_ladder_is_not_below_the_fold(wb):
    """An earlier version froze twenty-one rows.

    On a laptop that left nothing visible under the frozen block, so the sheet
    looked like it ended at the tiles and the ladder appeared not to exist.
    """
    ws = wb["Ticker Plan"]
    frozen_rows = int(ws.freeze_panes[1:]) - 1
    assert frozen_rows == TP_HEADER, "the header row should be the last frozen one"
    assert frozen_rows <= 14, f"{frozen_rows} frozen rows buries the ladder"


def test_exit_ladder_survives_an_asset_it_has_never_heard_of(wb):
    """Typing a ticker of your own used to turn the whole row into #N/A.

    A name that is not on the register is not a mistake — it is something you
    are sizing up before you own it — so the lookups fall back to blank and
    the quantity and cost are yours to fill in.
    """
    ws = wb["Exit Ladder"]
    for row in range(LADDER_FIRST, LADDER_LAST + 1):
        for col in (2, 3):
            formula = ws.cell(row=row, column=col).value
            assert formula.startswith(f'=IF($A{row}="","",IFERROR(')
            assert formula.endswith('""))')
        assert (
            ws.cell(row=row, column=4).value
            == f'=IF(OR($B{row}="",$C{row}=""),"",$B{row}*$C{row})'
        ), "cost basis must follow from the quantity and cost on the row"


def test_exit_ladder_quantity_and_cost_are_editable(wb):
    """They arrive filled in, but typing over them has to be allowed.

    Amber is the workbook's promise that a cell can be typed in; these carry a
    lookup and still need to accept an override.
    """
    ws = wb["Exit Ladder"]
    for row in range(LADDER_FIRST, LADDER_LAST + 1):
        for col in (2, 3):
            assert ws.cell(row=row, column=col).fill.fgColor.rgb.endswith(
                "FEF3C7"
            ), f"Exit Ladder row {row} column {col} should read as typeable"


def test_every_ladder_column_waits_for_its_inputs(wb):
    """No column may compute from a half-filled row."""
    ws = wb["Exit Ladder"]
    for row in range(LADDER_FIRST, LADDER_LAST + 1):
        for col in range(7, 19):
            if col == 8:  # the percentage to sell is typed
                continue
            formula = ws.cell(row=row, column=col).value
            for ref in (f'$B{row}=""', f'$C{row}=""', f'$F{row}=""', f'$H{row}=""'):
                assert ref in formula, f"column {col} ignores {ref}"


def test_unpriced_positions_say_so_on_the_row(wb):
    """Google Finance carries the majors and nothing else.

    A holding it cannot price sits at whatever was typed into it, so the row
    has to admit that rather than presenting a 2022 number as today's.
    """
    noted = {p.asset for p in POSITIONS if "does not carry this symbol" in p.note}
    assert noted == NO_GOOGLE_FEED
    for position in POSITIONS:
        if position.asset not in NO_GOOGLE_FEED:
            assert "does not carry this symbol" not in position.note, position.asset


def test_staleness_is_measured_in_money_not_in_rows(wb):
    """One stale holding was a third of the book while reading as one row of
    twelve. A count understates the damage; the dashboard has to weigh it."""
    band = wb["Dashboard"]["B7"].value
    assert band.startswith("=IF(SUMIF(Positions!$M")
    assert '"<>live"' in band, "must select the rows with no live feed"
    assert f"Positions!$N${POS_FIRST}:$N${POS_LAST}" in band, "must sum market value"
    assert f"Positions!$N${POS_TOTAL}" in band, "must express it as a share"
    assert "priced by hand" in band


def test_the_stale_band_sits_under_the_portfolio_value(wb):
    """It qualifies that tile, so it has to be read in the same glance."""
    ws = wb["Dashboard"]
    merged = {str(r) for r in ws.merged_cells.ranges}
    assert "B7:P7" in merged
    assert (
        ws["B5"].value == f"=Positions!$N${POS_TOTAL}"
    ), "tile above must be the value"


def test_a_pinned_price_beats_the_feed(wb):
    """Manual price was a fallback only.

    For a coin the feed does carry, typing a price beside it did nothing and
    nothing said why. Price mode decides, exactly as it does on Positions.
    """
    live = wb["Ticker Plan"].cell(row=TP_INPUT, column=7).value
    assert live.startswith(f'=IF($E${TP_INPUT}="Manual",$F${TP_INPUT},')
    assert "GOOGLEFINANCE" in live
    feed = wb["Ticker Plan"].cell(row=TP_INPUT, column=8).value
    assert "pinned by you" in feed, "the sheet has to say the feed is overruled"
    modes = {dv.formula1 for dv in wb["Ticker Plan"].data_validations.dataValidation}
    assert '"Auto,Manual"' in modes


def test_you_type_units_and_read_percentages(wb):
    """Typing units is how the decision is made — take four hundred off here.

    A percentage that has to be worked out first is a step in the way, so the
    unit cells carry a live default and the percentage is derived from them.
    """
    for sheet, qty_col, pct_col in (("Ticker Plan", 8, 9), ("Exit Ladder", 8, 9)):
        ws = wb[sheet]
        first = TP_RUNG_FIRST if sheet == "Ticker Plan" else LADDER_FIRST
        last = TP_RUNG_LAST if sheet == "Ticker Plan" else LADDER_LAST
        seeded = 0
        for row in range(first, last + 1):
            qty = ws.cell(row=row, column=qty_col)
            assert qty.fill.fgColor.rgb.endswith("FEF3C7"), f"{sheet} {row} qty"
            if qty.value:
                assert qty.value.startswith("=ROUND("), "a live default, not a constant"
                seeded += 1
            pct = ws.cell(row=row, column=pct_col).value
            assert pct.startswith("=IF("), f"{sheet} {row} percentage must be derived"
            assert "MIN(" in pct, f"{sheet} {row} percentage must read the capped units"
        assert seeded, f"{sheet} should arrive with a plan in it"


def test_no_rung_sells_what_the_rungs_above_it_sold(wb):
    """The fault the old workbook's Gala ladder had.

    Over-committing a plan used to keep booking cash on units that were
    already gone. The quantity each rung moves is capped at what is left.
    """
    for sheet, first, last, held in (
        ("Ticker Plan", TP_RUNG_FIRST, TP_RUNG_LAST, f"$C${TP_INPUT}"),
        ("Exit Ladder", LADDER_FIRST, LADDER_LAST, None),
    ):
        ws = wb[sheet]
        for row in range(first, last + 1):
            gross = ws.cell(row=row, column=10).value
            assert "MIN($H%d,MAX(0," % row in gross, f"{sheet} row {row} is uncapped"
            if held:
                assert held in gross
            left = ws.cell(row=row, column=13 if sheet == "Ticker Plan" else 17).value
            assert "MAX(0," in left, f"{sheet} row {row} can show negative units left"


def test_an_oversold_plan_says_so(wb):
    ws = wb["Ticker Plan"]
    for row in range(TP_RUNG_FIRST, TP_RUNG_LAST + 1):
        assert '"Oversold"' in ws.cell(row=row, column=14).value
