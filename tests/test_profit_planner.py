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
    POSITIONS,
    SECTORS,
    build_workbook,
)

EXPECTED_SHEETS = [
    "Start Here",
    "Dashboard",
    "Positions",
    "Exit Ladder",
    "Sell Calculator",
    "Gain Table",
    "Trade Log",
    "Settings",
    "Lists",
]


@pytest.fixture(scope="module")
def wb():
    return build_workbook()


def test_expected_sheets_present(wb):
    assert set(wb.sheetnames) == set(EXPECTED_SHEETS)
    assert wb.sheetnames[0] == "Start Here"
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
    assert ws.cell(row=POS_TOTAL, column=1).value == "TOTAL"
    assert ws.cell(row=POS_TOTAL, column=6).value == f"=SUM(F{POS_FIRST}:F{POS_LAST})"
    assert ws.cell(row=POS_TOTAL, column=11).value == f"=SUM(K{POS_FIRST}:K{POS_LAST})"


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
        formula = ws.cell(row=row, column=9).value
        assert "GOOGLEFINANCE" in formula
        assert formula.startswith(f'=IF($D{row}="","",IFERROR(')
        assert formula.endswith(f"$H{row}))")


def test_workbook_saves(wb, tmp_path):
    out = tmp_path / "planner.xlsx"
    wb.save(out)
    assert out.stat().st_size > 10_000
    reopened = openpyxl.load_workbook(out)
    assert reopened.sheetnames == wb.sheetnames
