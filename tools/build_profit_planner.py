"""Build the Profit & Exit Planner workbook.

This replaces a hand-maintained Google Sheet ("Profit Gain Percentage") in which
every number was typed rather than computed: prices frozen at their October 2022
values, positions duplicated across rows, an exit ladder that sold more coins
than the position held, and a 1,900-cell percentage grid that was a single
formula repeated by hand.

Everything here is generated, so the workbook can be rebuilt from scratch
instead of patched in place:

    python tools/build_profit_planner.py --out profit_planner.xlsx

Upload the result to Google Drive and it converts to a Google Sheet with the
formulas live. Price cells call GOOGLEFINANCE and fall back to a manually
entered price when the feed does not carry the symbol, so the sheet degrades to
"still correct, just stale" rather than to "#N/A everywhere".
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field

from openpyxl import Workbook
from openpyxl.chart import BarChart, Reference
from openpyxl.formatting.rule import CellIsRule, DataBarRule, FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.workbook.defined_name import DefinedName
from openpyxl.worksheet.datavalidation import DataValidation

# --------------------------------------------------------------------------
# Palette. Ink for structure, amber for "you type here", grey for "pulled from
# somewhere else". The legend on Start Here documents this contract; keep the
# two in step.
# --------------------------------------------------------------------------

INK = "0F172A"
SLATE = "334155"
MUTED = "64748B"
LINE = "CBD5E1"
BAND = "F8FAFC"
DERIVED = "F1F5F9"
INPUT_BG = "FEF3C7"
INPUT_LINE = "F59E0B"
ACCENT = "0D9488"
ACCENT_SOFT = "CCFBF1"
GOOD = "15803D"
GOOD_SOFT = "DCFCE7"
BAD = "B91C1C"
BAD_SOFT = "FEE2E2"
WARN_SOFT = "FEF9C3"
WHITE = "FFFFFF"

MONEY = '"$"#,##0.00'
MONEY0 = '"$"#,##0'
MONEY_FINE = '"$"#,##0.00000000'
QTY = "#,##0.########"
PCT = "0.0%"
PCT0 = "0%"
MULT = '0.00"x"'

TITLE_F = Font(name="Calibri", size=16, bold=True, color=WHITE)
SUB_F = Font(name="Calibri", size=10, italic=True, color=MUTED)
HEAD_F = Font(name="Calibri", size=10, bold=True, color=WHITE)
BODY_F = Font(name="Calibri", size=11, color=INK)
BOLD_F = Font(name="Calibri", size=11, bold=True, color=INK)
SMALL_F = Font(name="Calibri", size=9, color=MUTED)
DERIVED_F = Font(name="Calibri", size=11, italic=True, color=SLATE)
KPI_F = Font(name="Calibri", size=20, bold=True, color=INK)
KPI_LABEL_F = Font(name="Calibri", size=9, bold=True, color=MUTED)

TITLE_FILL = PatternFill("solid", fgColor=INK)
HEAD_FILL = PatternFill("solid", fgColor=SLATE)
INPUT_FILL = PatternFill("solid", fgColor=INPUT_BG)
DERIVED_FILL = PatternFill("solid", fgColor=DERIVED)
BAND_FILL = PatternFill("solid", fgColor=BAND)
ACCENT_FILL = PatternFill("solid", fgColor=ACCENT_SOFT)

THIN = Side(style="thin", color=LINE)
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
INPUT_SIDE = Side(style="thin", color=INPUT_LINE)
INPUT_BORDER = Border(
    left=INPUT_SIDE, right=INPUT_SIDE, top=INPUT_SIDE, bottom=INPUT_SIDE
)

# Row geography of the Positions sheet. Several other sheets index into it, so
# it is stated once here rather than spelled out at each call site.
POS_FIRST = 4
POS_LAST = 33
POS_TOTAL = 35

LADDER_FIRST = 4
LADDER_LAST = 43

LOG_FIRST = 5
LOG_LAST = 204


@dataclass
class Position:
    """A holding carried over from the old sheet.

    ``manual_price`` is the last price the old sheet recorded. It is both the
    seed value and the fallback the price formula lands on when GOOGLEFINANCE
    does not carry the symbol.
    """

    asset: str
    ticker: str
    sector: str
    qty: float
    avg_cost: float
    manual_price: float
    ath: float | None
    note: str = ""


@dataclass
class Rung:
    target: float
    pct: float


@dataclass
class Ladder:
    asset: str
    rungs: list[Rung] = field(default_factory=list)
    note: str = ""


# --------------------------------------------------------------------------
# Carried-over data.
#
# Sourced from the old workbook's "Crypto Gaines" tab, which was the only tab
# that read as a holdings list rather than a plan. Duplicate rows (Decentraland,
# Chainlink) and the two "Future Asset" placeholders are dropped. Average cost is
# the old Cost column divided by the old Amount column, so it survives as one
# number instead of two that could disagree.
# --------------------------------------------------------------------------

POSITIONS: list[Position] = [
    Position(
        "Unknown — old sheet said '10162008'",
        "",
        "Review",
        100,
        7.26,
        7.26,
        45.01,
        "Name was a number in the old sheet. The $45.01 all-time high matches "
        "Uniswap (UNI) — confirm, then set the name and ticker CURRENCY:UNIUSD.",
    ),
    Position("Bitcoin", "CURRENCY:BTCUSD", "Store of value", 0.26, 22151, 22151, 68780),
    Position(
        "Ethereum", "CURRENCY:ETHUSD", "L1 / Smart contract", 3, 1496, 1496, 4861.29
    ),
    Position(
        "Cardano", "CURRENCY:ADAUSD", "L1 / Smart contract", 250, 0.48, 0.48, 3.09
    ),
    Position("Chainlink", "CURRENCY:LINKUSD", "DeFi / Oracle", 100, 6.67, 6.67, 52.85),
    Position(
        "Gala",
        "CURRENCY:GALAUSD",
        "Gaming",
        13545,
        0.044,
        0.04,
        0.74,
        "Avg cost is $595.98 / 13,545 coins.",
    ),
    Position(
        "The Sandbox",
        "CURRENCY:SANDUSD",
        "Metaverse",
        10000,
        1.31,
        1.31,
        8.35,
        "Old sheet listed this twice with different sizes: 10,000 units on the "
        "holdings tab and 100 on a planning tab. The larger is carried here — "
        "correct it if the planning tab was right.",
    ),
    Position("Decentraland", "CURRENCY:MANAUSD", "Metaverse", 150, 0.834, 0.83, 5.87),
    Position("Theta", "CURRENCY:THETAUSD", "Media / NFT", 100, 1.25, 1.25, 15.71),
    Position(
        "Harmony",
        "CURRENCY:ONEUSD",
        "Low cap",
        2500,
        0.0223644,
        0.02,
        0.38,
        "Avg cost is $55.91 / 2,500 coins.",
    ),
    Position("Audius", "CURRENCY:AUDIOUSD", "Low cap", 200, 0.35, 0.35, 4.94),
    Position(
        "Shiba Inu",
        "CURRENCY:SHIBUSD",
        "Meme",
        10000000,
        0.000011,
        0.000011,
        0.00008845,
        "Old sheet showed the all-time high as $0.00 — it was the real number "
        "rounded away by the cell format. Corrected here.",
    ),
]

LADDERS: list[Ladder] = [
    Ladder(
        "Gala",
        [
            Rung(0.06, 0.10),
            Rung(0.08, 0.20),
            Rung(0.10, 0.25),
            Rung(0.12, 0.25),
            Rung(0.14, 0.20),
        ],
        "Price targets are the old sheet's. The old ladder sold 3,563 coins at "
        "the last rung when only 891 were left, booking ~$374 of profit on "
        "coins it had already sold; percentages here are of the original "
        "position and column P checks they total 100%.",
    ),
    Ladder(
        "Cardano",
        [
            Rung(1.25, 0.10),
            Rung(2.00, 0.20),
            Rung(2.50, 0.25),
            Rung(2.75, 0.25),
            Rung(3.09, 0.20),
        ],
        "Price targets are the old sheet's. The old sheet held three identical "
        "copies of this ladder on one tab; this is the one copy.",
    ),
]

SECTORS = [
    "Store of value",
    "L1 / Smart contract",
    "L2 / Scaling",
    "DeFi / Oracle",
    "Gaming",
    "Metaverse",
    "Media / NFT",
    "Exchange",
    "Low cap",
    "Meme",
    "Equity",
    "Review",
    "Other",
]


# --------------------------------------------------------------------------
# Small styling helpers.
# --------------------------------------------------------------------------


def sheet_title(ws, text: str, subtitle: str, span: int) -> None:
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=span)
    cell = ws.cell(row=1, column=1, value=text)
    cell.font = TITLE_F
    cell.fill = TITLE_FILL
    cell.alignment = Alignment(vertical="center", indent=1)
    ws.row_dimensions[1].height = 30
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=span)
    sub = ws.cell(row=2, column=1, value=subtitle)
    sub.font = SUB_F
    sub.alignment = Alignment(vertical="center", indent=1)
    ws.row_dimensions[2].height = 18


def header_row(ws, row: int, headers: list[str], widths: list[int]) -> None:
    for idx, (label, width) in enumerate(zip(headers, widths), start=1):
        cell = ws.cell(row=row, column=idx, value=label)
        cell.font = HEAD_F
        cell.fill = HEAD_FILL
        cell.alignment = Alignment(vertical="center", wrap_text=True, indent=1)
        cell.border = BOX
        ws.column_dimensions[get_column_letter(idx)].width = width
    ws.row_dimensions[row].height = 30


def style_input(cell) -> None:
    cell.fill = INPUT_FILL
    cell.border = INPUT_BORDER
    cell.font = BODY_F


def style_derived(cell) -> None:
    cell.fill = DERIVED_FILL
    cell.border = BOX
    cell.font = DERIVED_F


def style_body(cell) -> None:
    cell.border = BOX
    cell.font = BODY_F


def label_value(ws, row: int, label: str, value, fmt=None, note: str = ""):
    """One `label | value | note` line, with the value marked as an input."""
    lab = ws.cell(row=row, column=2, value=label)
    lab.font = BOLD_F
    cell = ws.cell(row=row, column=3, value=value)
    style_input(cell)
    if fmt:
        cell.number_format = fmt
    if note:
        hint = ws.cell(row=row, column=4, value=note)
        hint.font = SMALL_F
        hint.alignment = Alignment(vertical="center", wrap_text=True)
    return cell


def pos_lookup(column: str, key_cell: str) -> str:
    """INDEX/MATCH into Positions.

    Deliberately not XLOOKUP: this workbook is written as .xlsx and imported,
    and INDEX/MATCH survives that trip everywhere.
    """
    return (
        f"INDEX(Positions!${column}${POS_FIRST}:${column}${POS_LAST},"
        f"MATCH({key_cell},Positions!$A${POS_FIRST}:$A${POS_LAST},0))"
    )


# --------------------------------------------------------------------------
# Sheets.
# --------------------------------------------------------------------------


def build_lists(wb: Workbook):
    ws = wb.create_sheet("Lists")
    ws.sheet_state = "hidden"
    ws["A1"] = "Sector"
    ws["B1"] = "Price mode"
    ws["C1"] = "Side"
    ws["D1"] = "Yes/No"
    for i, sector in enumerate(SECTORS, start=2):
        ws.cell(row=i, column=1, value=sector)
    ws["B2"], ws["B3"] = "Auto", "Manual"
    ws["C2"], ws["C3"] = "Buy", "Sell"
    ws["D2"], ws["D3"] = "Yes", "No"
    return ws


def build_settings(wb: Workbook):
    ws = wb.create_sheet("Settings")
    ws.sheet_properties.tabColor = MUTED
    ws.sheet_view.showGridLines = False
    sheet_title(
        ws,
        "Settings",
        "Four numbers the rest of the workbook reads. Change them here and "
        "every tab follows.",
        8,
    )
    ws.column_dimensions["A"].width = 3
    ws.column_dimensions["B"].width = 34
    ws.column_dimensions["C"].width = 16
    ws.column_dimensions["D"].width = 78

    label_value(
        ws,
        4,
        "Capital gains tax rate",
        0.24,
        PCT,
        "Used to show every exit after tax, not before. A guess is fine — a "
        "wrong rate here is still closer than ignoring tax entirely.",
    )
    label_value(
        ws,
        5,
        "Exchange / broker fee",
        0.005,
        "0.000%",
        "Charged on each sale in the ladder and calculator. 0.5% is Coinbase's "
        "rough taker fee; a broker with free equity trades is 0%.",
    )
    label_value(
        ws,
        6,
        "Max weight per position",
        0.20,
        PCT,
        "Positions above this are flagged amber on the Positions tab and "
        "counted on the Dashboard. It is a tripwire, not a rule.",
    )
    label_value(
        ws,
        7,
        "Portfolio name",
        "Main book",
        None,
        "Shown on the Dashboard.",
    )

    ws["B9"] = "Colour code"
    ws["B9"].font = BOLD_F
    swatches = [
        (
            INPUT_BG,
            INPUT_LINE,
            "Amber = type here. Nothing else expects to be typed in.",
        ),
        (
            DERIVED,
            LINE,
            "Grey italic = pulled from another tab. Overwriting it breaks the link.",
        ),
        (WHITE, LINE, "White = calculated. Leave it alone and it stays right."),
    ]
    for offset, (bg, edge, text) in enumerate(swatches):
        row = 10 + offset
        chip = ws.cell(row=row, column=2, value="")
        chip.fill = PatternFill("solid", fgColor=bg)
        side = Side(style="thin", color=edge)
        chip.border = Border(left=side, right=side, top=side, bottom=side)
        desc = ws.cell(row=row, column=3, value=text)
        desc.font = SMALL_F
        ws.merge_cells(start_row=row, start_column=3, end_row=row, end_column=4)

    wb.defined_names["TaxRate"] = DefinedName("TaxRate", attr_text="Settings!$C$4")
    wb.defined_names["FeeRate"] = DefinedName("FeeRate", attr_text="Settings!$C$5")
    wb.defined_names["MaxWeight"] = DefinedName("MaxWeight", attr_text="Settings!$C$6")
    return ws


def build_positions(wb: Workbook):
    ws = wb.create_sheet("Positions")
    ws.sheet_properties.tabColor = ACCENT
    sheet_title(
        ws,
        "Positions",
        "One row per holding. Type the four amber columns; the other fifteen "
        "compute themselves.",
        19,
    )
    headers = [
        "Asset",
        "Ticker",
        "Sector",
        "Quantity",
        "Avg cost",
        "Cost basis",
        "Price mode",
        "Manual price",
        "Price",
        "Feed",
        "Market value",
        "Unrealised $",
        "Unrealised %",
        "Weight",
        "All-time high",
        "% of ATH",
        "X to ATH",
        "If ATH returns",
        "Note",
    ]
    widths = [30, 20, 19, 15, 13, 13, 11, 13, 13, 14, 14, 13, 12, 9, 13, 9, 9, 14, 62]
    header_row(ws, 3, headers, widths)
    ws.freeze_panes = "D4"

    sector_dv = DataValidation(
        type="list", formula1="=Lists!$A$2:$A$14", allow_blank=True
    )
    mode_dv = DataValidation(type="list", formula1="=Lists!$B$2:$B$3", allow_blank=True)
    ws.add_data_validation(sector_dv)
    ws.add_data_validation(mode_dv)

    for row in range(POS_FIRST, POS_LAST + 1):
        seed = POSITIONS[row - POS_FIRST] if row - POS_FIRST < len(POSITIONS) else None
        banded = (row - POS_FIRST) % 2 == 1

        for col in range(1, 20):
            cell = ws.cell(row=row, column=col)
            style_body(cell)
            if banded:
                cell.fill = BAND_FILL

        for col in (1, 2, 3, 4, 5, 7, 8, 15, 19):
            style_input(ws.cell(row=row, column=col))

        ws.cell(row=row, column=1, value=seed.asset if seed else None)
        ws.cell(row=row, column=2, value=seed.ticker if seed else None)
        ws.cell(row=row, column=3, value=seed.sector if seed else None)
        qty = ws.cell(row=row, column=4, value=seed.qty if seed else None)
        qty.number_format = QTY
        avg = ws.cell(row=row, column=5, value=seed.avg_cost if seed else None)
        avg.number_format = MONEY_FINE if seed and seed.avg_cost < 0.01 else MONEY
        mode = ws.cell(row=row, column=7, value="Auto" if seed else None)
        manual = ws.cell(row=row, column=8, value=seed.manual_price if seed else None)
        manual.number_format = (
            MONEY_FINE if seed and seed.manual_price < 0.01 else MONEY
        )
        ath = ws.cell(row=row, column=15, value=seed.ath if seed else None)
        ath.number_format = (
            MONEY_FINE if seed and seed.ath and seed.ath < 0.01 else MONEY
        )
        note = ws.cell(row=row, column=19, value=seed.note if seed else None)
        note.font = SMALL_F
        note.alignment = Alignment(vertical="top", wrap_text=True)

        sector_dv.add(ws.cell(row=row, column=3))
        mode_dv.add(mode)

        blank = f'$D{row}=""'
        ws.cell(
            row=row, column=6, value=f'=IF({blank},"",$D{row}*$E{row})'
        ).number_format = MONEY
        # Manual price is both an override and a safety net: GOOGLEFINANCE does
        # not carry every altcoin, and an unpriced row would otherwise poison
        # every total on the Dashboard.
        ws.cell(
            row=row,
            column=9,
            value=(
                f'=IF({blank},"",IFERROR(IF($G{row}="Manual",$H{row},'
                f"GOOGLEFINANCE($B{row})),$H{row}))"
            ),
        ).number_format = (
            MONEY_FINE if seed and seed.manual_price < 0.01 else MONEY
        )
        ws.cell(
            row=row,
            column=10,
            value=(
                f'=IF({blank},"",IF($G{row}="Manual","manual",'
                f'IF(ISERROR(GOOGLEFINANCE($B{row})),"no feed","live")))'
            ),
        ).font = SMALL_F
        ws.cell(
            row=row, column=11, value=f'=IF({blank},"",$D{row}*$I{row})'
        ).number_format = MONEY
        ws.cell(
            row=row, column=12, value=f'=IF({blank},"",$K{row}-$F{row})'
        ).number_format = MONEY
        ws.cell(
            row=row,
            column=13,
            value=f'=IF(OR({blank},$F{row}=0),"",$L{row}/$F{row})',
        ).number_format = PCT
        ws.cell(
            row=row,
            column=14,
            value=f'=IF(OR({blank},$K${POS_TOTAL}=0),"",$K{row}/$K${POS_TOTAL})',
        ).number_format = PCT
        ws.cell(
            row=row,
            column=16,
            value=f'=IF(OR({blank},$O{row}=""),"",$I{row}/$O{row})',
        ).number_format = PCT
        ws.cell(
            row=row,
            column=17,
            value=f'=IF(OR({blank},$O{row}="",$I{row}=0),"",$O{row}/$I{row})',
        ).number_format = MULT
        ws.cell(
            row=row,
            column=18,
            value=f'=IF(OR({blank},$O{row}=""),"",$D{row}*$O{row})',
        ).number_format = MONEY

    total = ws.cell(row=POS_TOTAL, column=1, value="TOTAL")
    for col in range(1, 20):
        cell = ws.cell(row=POS_TOTAL, column=col)
        cell.fill = PatternFill("solid", fgColor=INK)
        cell.font = Font(name="Calibri", size=11, bold=True, color=WHITE)
        cell.border = BOX
    total.alignment = Alignment(indent=1)
    ws.cell(
        row=POS_TOTAL, column=6, value=f"=SUM(F{POS_FIRST}:F{POS_LAST})"
    ).number_format = MONEY
    ws.cell(
        row=POS_TOTAL, column=11, value=f"=SUM(K{POS_FIRST}:K{POS_LAST})"
    ).number_format = MONEY
    ws.cell(
        row=POS_TOTAL, column=12, value=f"=SUM(L{POS_FIRST}:L{POS_LAST})"
    ).number_format = MONEY
    ws.cell(
        row=POS_TOTAL,
        column=13,
        value=f'=IF($F${POS_TOTAL}=0,"",$L${POS_TOTAL}/$F${POS_TOTAL})',
    ).number_format = PCT
    ws.cell(
        row=POS_TOTAL, column=18, value=f"=SUM(R{POS_FIRST}:R{POS_LAST})"
    ).number_format = MONEY

    body = f"L{POS_FIRST}:M{POS_LAST}"
    ws.conditional_formatting.add(
        body,
        CellIsRule(
            operator="lessThan",
            formula=["0"],
            font=Font(color=BAD),
            fill=PatternFill("solid", fgColor=BAD_SOFT),
        ),
    )
    ws.conditional_formatting.add(
        body,
        CellIsRule(
            operator="greaterThan",
            formula=["0"],
            font=Font(color=GOOD),
            fill=PatternFill("solid", fgColor=GOOD_SOFT),
        ),
    )
    ws.conditional_formatting.add(
        f"N{POS_FIRST}:N{POS_LAST}",
        DataBarRule(
            start_type="num", start_value=0, end_type="num", end_value=1, color=ACCENT
        ),
    )
    ws.conditional_formatting.add(
        f"N{POS_FIRST}:N{POS_LAST}",
        FormulaRule(
            formula=[f'AND($N{POS_FIRST}<>"",$N{POS_FIRST}>MaxWeight)'],
            fill=PatternFill("solid", fgColor=WARN_SOFT),
        ),
    )
    ws.conditional_formatting.add(
        f"J{POS_FIRST}:J{POS_LAST}",
        CellIsRule(
            operator="equal", formula=['"no feed"'], font=Font(color=BAD, size=9)
        ),
    )
    return ws


def build_ladder(wb: Workbook):
    ws = wb.create_sheet("Exit Ladder")
    ws.sheet_properties.tabColor = "7C3AED"
    sheet_title(
        ws,
        "Exit Ladder",
        "Decide what you sell at what price before the price gets there. "
        "Percentages are of the original position, so the rungs always add to "
        "100% or less.",
        18,
    )
    headers = [
        "Asset",
        "Qty held",
        "Avg cost",
        "Cost basis",
        "Rung",
        "Target price",
        "Multiple",
        "% of position",
        "Qty sold",
        "Gross",
        "Fees",
        "Est. tax",
        "Net proceeds",
        "Cumulative net",
        "Cost recovered",
        "% sold so far",
        "Qty remaining",
        "Remainder value",
    ]
    widths = [26, 14, 12, 13, 8, 14, 10, 14, 14, 13, 11, 11, 14, 15, 16, 13, 14, 15]
    header_row(ws, 3, headers, widths)
    ws.freeze_panes = "E4"

    asset_dv = DataValidation(
        type="list",
        formula1=f"=Positions!$A${POS_FIRST}:$A${POS_LAST}",
        allow_blank=True,
    )
    ws.add_data_validation(asset_dv)

    seeded: list[tuple[str, int, Rung]] = []
    for ladder in LADDERS:
        for idx, rung in enumerate(ladder.rungs, start=1):
            seeded.append((ladder.asset, idx, rung))

    for row in range(LADDER_FIRST, LADDER_LAST + 1):
        seed = seeded[row - LADDER_FIRST] if row - LADDER_FIRST < len(seeded) else None
        for col in range(1, 19):
            cell = ws.cell(row=row, column=col)
            style_body(cell)
        for col in (1, 5, 6, 8):
            style_input(ws.cell(row=row, column=col))
        for col in (2, 3, 4):
            style_derived(ws.cell(row=row, column=col))

        asset_cell = ws.cell(row=row, column=1, value=seed[0] if seed else None)
        asset_dv.add(asset_cell)
        ws.cell(row=row, column=5, value=f"Rung {seed[1]}" if seed else None)
        target = ws.cell(row=row, column=6, value=seed[2].target if seed else None)
        target.number_format = MONEY
        pct = ws.cell(row=row, column=8, value=seed[2].pct if seed else None)
        pct.number_format = PCT0

        blank = f'$A{row}=""'
        ws.cell(
            row=row, column=2, value=f'=IF({blank},"",{pos_lookup("D", f"$A{row}")})'
        ).number_format = QTY
        ws.cell(
            row=row, column=3, value=f'=IF({blank},"",{pos_lookup("E", f"$A{row}")})'
        ).number_format = MONEY
        ws.cell(
            row=row, column=4, value=f'=IF({blank},"",{pos_lookup("F", f"$A{row}")})'
        ).number_format = MONEY

        empty = f'OR({blank},$F{row}="",$H{row}="")'
        ws.cell(
            row=row, column=7, value=f'=IF(OR({empty},$C{row}=0),"",$F{row}/$C{row})'
        ).number_format = MULT
        ws.cell(
            row=row, column=9, value=f'=IF({empty},"",$H{row}*$B{row})'
        ).number_format = QTY
        ws.cell(
            row=row, column=10, value=f'=IF({empty},"",$I{row}*$F{row})'
        ).number_format = MONEY
        ws.cell(
            row=row, column=11, value=f'=IF({empty},"",$J{row}*FeeRate)'
        ).number_format = MONEY
        ws.cell(
            row=row,
            column=12,
            value=f'=IF({empty},"",MAX(0,($F{row}-$C{row})*$I{row})*TaxRate)',
        ).number_format = MONEY
        ws.cell(
            row=row, column=13, value=f'=IF({empty},"",$J{row}-$K{row}-$L{row})'
        ).number_format = MONEY
        # Running totals scoped to the asset, so rungs for different assets can
        # be interleaved without the arithmetic bleeding across.
        ws.cell(
            row=row,
            column=14,
            value=f'=IF({empty},"",SUMIFS($M${LADDER_FIRST}:$M{row},$A${LADDER_FIRST}:$A{row},$A{row}))',
        ).number_format = MONEY
        ws.cell(
            row=row,
            column=15,
            value=f'=IF({empty},"",IF($N{row}>=$D{row},"Yes — free ride","Not yet"))',
        )
        ws.cell(
            row=row,
            column=16,
            value=f'=IF({empty},"",SUMIFS($H${LADDER_FIRST}:$H{row},$A${LADDER_FIRST}:$A{row},$A{row}))',
        ).number_format = PCT0
        ws.cell(
            row=row,
            column=17,
            value=f'=IF({empty},"",$B{row}-SUMIFS($I${LADDER_FIRST}:$I{row},$A${LADDER_FIRST}:$A{row},$A{row}))',
        ).number_format = QTY
        ws.cell(
            row=row, column=18, value=f'=IF({empty},"",$Q{row}*$F{row})'
        ).number_format = MONEY

    ws.conditional_formatting.add(
        f"O{LADDER_FIRST}:O{LADDER_LAST}",
        CellIsRule(
            operator="equal",
            formula=['"Yes — free ride"'],
            font=Font(color=GOOD, bold=True),
            fill=PatternFill("solid", fgColor=GOOD_SOFT),
        ),
    )
    # The failure the old sheet actually made: a ladder that sells more than it
    # holds. Flagged loudly rather than left to arithmetic.
    ws.conditional_formatting.add(
        f"P{LADDER_FIRST}:P{LADDER_LAST}",
        CellIsRule(
            operator="greaterThan",
            formula=["1"],
            font=Font(color=BAD, bold=True),
            fill=PatternFill("solid", fgColor=BAD_SOFT),
        ),
    )
    ws.conditional_formatting.add(
        f"Q{LADDER_FIRST}:Q{LADDER_LAST}",
        CellIsRule(
            operator="lessThan",
            formula=["0"],
            font=Font(color=BAD, bold=True),
            fill=PatternFill("solid", fgColor=BAD_SOFT),
        ),
    )

    row = LADDER_LAST + 2
    ws.cell(row=row, column=1, value="Notes carried from the old sheet").font = BOLD_F
    for offset, ladder in enumerate(LADDERS, start=1):
        cell = ws.cell(
            row=row + offset, column=1, value=f"{ladder.asset}: {ladder.note}"
        )
        cell.font = SMALL_F
        cell.alignment = Alignment(vertical="top", wrap_text=True)
        ws.merge_cells(
            start_row=row + offset, start_column=1, end_row=row + offset, end_column=10
        )
        ws.row_dimensions[row + offset].height = 42
    return ws


def build_sell_calculator(wb: Workbook):
    ws = wb.create_sheet("Sell Calculator")
    ws.sheet_properties.tabColor = "0891B2"
    ws.sheet_view.showGridLines = False
    sheet_title(
        ws,
        "Sell Calculator",
        "Pick one position and a price. The grid answers the only question "
        "that matters: after selling this slice, is the rest of it free?",
        13,
    )
    ws.column_dimensions["A"].width = 3
    ws.column_dimensions["B"].width = 26

    asset_dv = DataValidation(
        type="list",
        formula1=f"=Positions!$A${POS_FIRST}:$A${POS_LAST}",
        allow_blank=True,
    )
    ws.add_data_validation(asset_dv)

    asset = label_value(
        ws, 4, "Position", "Bitcoin", None, "Pick from the Positions tab."
    )
    asset_dv.add(asset)
    ws.column_dimensions["C"].width = 18
    ws.column_dimensions["D"].width = 60

    for row, (label, formula, fmt, note) in enumerate(
        [
            (
                "Quantity held",
                f"=IFERROR({pos_lookup('D', '$C$4')},0)",
                QTY,
                "From Positions.",
            ),
            (
                "Average cost",
                f"=IFERROR({pos_lookup('E', '$C$4')},0)",
                MONEY,
                "From Positions.",
            ),
            ("Cost basis", "=$C$5*$C$6", MONEY, "What you have at risk."),
            (
                "Current price",
                f"=IFERROR({pos_lookup('I', '$C$4')},0)",
                MONEY,
                "From Positions.",
            ),
            (
                "Sell at this price",
                "=ROUND($C$8*2,2)",
                MONEY,
                "The one number to play with. Seeded at 2x the current price.",
            ),
        ],
        start=5,
    ):
        lab = ws.cell(row=row, column=2, value=label)
        lab.font = BOLD_F
        cell = ws.cell(row=row, column=3, value=formula)
        cell.number_format = fmt
        if row == 9:
            style_input(cell)
        else:
            style_derived(cell)
        hint = ws.cell(row=row, column=4, value=note)
        hint.font = SMALL_F

    headers = [
        "",
        "% sold",
        "Qty sold",
        "Gross",
        "Fees",
        "Cost of qty sold",
        "Realised gain",
        "Est. tax",
        "Net cash",
        "Qty left",
        "Cost still at risk",
        "Break-even of remainder",
        "Status",
    ]
    widths = [3, 26, 16, 14, 11, 17, 15, 12, 14, 14, 17, 22, 22]
    header_row(ws, 11, headers, widths)

    steps = [0.05 * i for i in range(5, 21)]
    for offset, pct in enumerate(steps):
        row = 12 + offset
        for col in range(2, 14):
            cell = ws.cell(row=row, column=col)
            style_body(cell)
            if offset % 2 == 1:
                cell.fill = BAND_FILL
        ws.cell(row=row, column=2, value=round(pct, 2)).number_format = PCT0
        ws.cell(row=row, column=3, value=f"=$C$5*$B{row}").number_format = QTY
        ws.cell(row=row, column=4, value=f"=$C{row}*$C$9").number_format = MONEY
        ws.cell(row=row, column=5, value=f"=$D{row}*FeeRate").number_format = MONEY
        ws.cell(row=row, column=6, value=f"=$C{row}*$C$6").number_format = MONEY
        ws.cell(row=row, column=7, value=f"=$D{row}-$F{row}").number_format = MONEY
        ws.cell(row=row, column=8, value=f"=MAX(0,$G{row})*TaxRate").number_format = (
            MONEY
        )
        ws.cell(row=row, column=9, value=f"=$D{row}-$E{row}-$H{row}").number_format = (
            MONEY
        )
        ws.cell(row=row, column=10, value=f"=$C$5-$C{row}").number_format = QTY
        ws.cell(row=row, column=11, value=f"=MAX(0,$C$7-$I{row})").number_format = MONEY
        # The number the old sheet never computed: what the leftovers now have
        # to be worth for the whole trade to be flat.
        ws.cell(
            row=row,
            column=12,
            value=f'=IF($J{row}<=0,"—",$K{row}/$J{row})',
        ).number_format = MONEY
        ws.cell(
            row=row,
            column=13,
            value=(
                f'=IF($J{row}<=0,"Fully closed",'
                f'IF($I{row}>=$C$7,"Free ride — cost is back",'
                f'"Still exposed"))'
            ),
        )

    last = 12 + len(steps) - 1
    ws.conditional_formatting.add(
        f"M12:M{last}",
        CellIsRule(
            operator="equal",
            formula=['"Free ride — cost is back"'],
            font=Font(color=GOOD, bold=True),
            fill=PatternFill("solid", fgColor=GOOD_SOFT),
        ),
    )
    ws.conditional_formatting.add(
        f"M12:M{last}",
        CellIsRule(operator="equal", formula=['"Fully closed"'], fill=ACCENT_FILL),
    )
    ws.conditional_formatting.add(
        f"L12:L{last}",
        DataBarRule(start_type="min", end_type="max", color="93C5FD"),
    )

    note = ws.cell(
        row=last + 2,
        column=2,
        value=(
            "Break-even of remainder is the cost you have not yet recovered "
            "divided by the units you still hold. Once it drops below zero the "
            "position cannot lose you money, whatever the price does next — "
            "that is the row the old sheet's 60%–100% tables were groping for."
        ),
    )
    note.font = SMALL_F
    note.alignment = Alignment(vertical="top", wrap_text=True)
    ws.merge_cells(start_row=last + 2, start_column=2, end_row=last + 3, end_column=9)
    return ws


def build_gain_table(wb: Workbook):
    ws = wb.create_sheet("Gain Table")
    ws.sheet_properties.tabColor = "B45309"
    ws.sheet_view.showGridLines = False
    sheet_title(
        ws,
        "Gain Table",
        "The old sheet's percentage grid, rebuilt as two inputs and a column "
        "of formulas. Change the entry price and the whole table moves.",
        14,
    )
    ws.column_dimensions["A"].width = 3
    ws.column_dimensions["B"].width = 22
    ws.column_dimensions["C"].width = 16
    ws.column_dimensions["D"].width = 4

    label_value(ws, 4, "Entry price", 2.17, MONEY, "")
    label_value(ws, 5, "Units held", 276, QTY, "")
    lab = ws.cell(row=6, column=2, value="Position cost")
    lab.font = BOLD_F
    cost = ws.cell(row=6, column=3, value="=$C$4*$C$5")
    cost.number_format = MONEY
    style_derived(cost)
    seednote = ws.cell(
        row=7,
        column=2,
        value="Seeded with the $2.17 / 276-unit example from the old sheet's percentage tab.",
    )
    seednote.font = SMALL_F
    ws.merge_cells(start_row=7, start_column=2, end_row=7, end_column=8)

    headers = [
        "",
        "Gain",
        "Sell price",
        "",
        "Position value",
        "Gross profit",
        "Fees + tax",
        "Net profit",
        "Multiple",
        "Units to sell to get your cost back",
    ]
    widths = [3, 10, 14, 4, 16, 14, 13, 14, 11, 32]
    header_row(ws, 9, headers, widths)
    ws.cell(row=9, column=4).fill = PatternFill("solid", fgColor=WHITE)
    ws.cell(row=9, column=4).border = Border()

    gains = (
        [i / 100 for i in range(1, 31)]
        + [i / 100 for i in range(35, 101, 5)]
        + [i / 100 for i in range(110, 301, 10)]
        + [4.0, 5.0, 7.0, 9.0]
    )
    for offset, gain in enumerate(gains):
        row = 10 + offset
        for col in list(range(2, 4)) + list(range(5, 11)):
            cell = ws.cell(row=row, column=col)
            style_body(cell)
            if offset % 2 == 1:
                cell.fill = BAND_FILL
        ws.cell(row=row, column=2, value=round(gain, 4)).number_format = PCT0
        ws.cell(row=row, column=3, value=f"=$C$4*(1+$B{row})").number_format = MONEY
        ws.cell(row=row, column=5, value=f"=$C{row}*$C$5").number_format = MONEY
        ws.cell(row=row, column=6, value=f"=$E{row}-$C$6").number_format = MONEY
        ws.cell(
            row=row, column=7, value=f"=$E{row}*FeeRate+MAX(0,$F{row})*TaxRate"
        ).number_format = MONEY
        ws.cell(row=row, column=8, value=f"=$F{row}-$G{row}").number_format = MONEY
        ws.cell(row=row, column=9, value=f"=1+$B{row}").number_format = MULT
        ws.cell(
            row=row, column=10, value=f'=IF($C{row}=0,"—",MIN($C$5,$C$6/$C{row}))'
        ).number_format = QTY

    last = 10 + len(gains) - 1
    ws.conditional_formatting.add(
        f"H10:H{last}", DataBarRule(start_type="min", end_type="max", color=ACCENT)
    )

    # The half of the arithmetic the old sheet never had: what a drawdown costs
    # to undo. It is not symmetric, and the table makes that impossible to miss.
    ws.merge_cells(start_row=9, start_column=12, end_row=9, end_column=15)
    rec = ws.cell(
        row=9, column=12, value="Recovery math — down is not the same distance as up"
    )
    rec.font = HEAD_F
    rec.fill = PatternFill("solid", fgColor=BAD)
    rec.alignment = Alignment(vertical="center", indent=1)
    for col, (label, width) in enumerate(
        [
            ("If you are down", 16),
            ("Price becomes", 14),
            ("Value becomes", 15),
            ("Gain needed to get back", 24),
        ],
        start=12,
    ):
        cell = ws.cell(row=10, column=col, value=label)
        cell.font = HEAD_F
        cell.fill = HEAD_FILL
        cell.border = BOX
        cell.alignment = Alignment(vertical="center", wrap_text=True, indent=1)
        ws.column_dimensions[get_column_letter(col)].width = width
    ws.row_dimensions[10].height = 30

    for offset, dd in enumerate([i / 100 for i in range(5, 96, 5)]):
        row = 11 + offset
        for col in range(12, 16):
            cell = ws.cell(row=row, column=col)
            style_body(cell)
            if offset % 2 == 1:
                cell.fill = BAND_FILL
        ws.cell(row=row, column=12, value=round(dd, 2)).number_format = PCT0
        ws.cell(row=row, column=13, value=f"=$C$4*(1-$L{row})").number_format = MONEY
        ws.cell(row=row, column=14, value=f"=$M{row}*$C$5").number_format = MONEY
        ws.cell(row=row, column=15, value=f"=1/(1-$L{row})-1").number_format = PCT0

    ws.conditional_formatting.add(
        f"O11:O{11 + 18}",
        CellIsRule(
            operator="greaterThan",
            formula=["1"],
            font=Font(color=BAD, bold=True),
            fill=PatternFill("solid", fgColor=BAD_SOFT),
        ),
    )
    return ws


def build_trade_log(wb: Workbook):
    ws = wb.create_sheet("Trade Log")
    ws.sheet_properties.tabColor = "334155"
    sheet_title(
        ws,
        "Trade Log",
        "One row per fill, and the two columns nobody wants to fill in: what "
        "you believed, and what would prove you wrong.",
        12,
    )
    headers = [
        "Date",
        "Ticker",
        "Side",
        "Quantity",
        "Price",
        "Fees",
        "Cash flow",
        "Thesis at entry",
        "What would prove me wrong",
        "Exit reason",
        "Followed the plan?",
        "Tags",
    ]
    widths = [13, 14, 9, 14, 13, 10, 14, 46, 40, 30, 15, 18]
    header_row(ws, 4, headers, widths)
    ws.freeze_panes = "C5"

    side_dv = DataValidation(type="list", formula1="=Lists!$C$2:$C$3", allow_blank=True)
    yesno_dv = DataValidation(
        type="list", formula1="=Lists!$D$2:$D$3", allow_blank=True
    )
    ws.add_data_validation(side_dv)
    ws.add_data_validation(yesno_dv)

    for row in range(LOG_FIRST, LOG_LAST + 1):
        for col in range(1, 13):
            cell = ws.cell(row=row, column=col)
            style_body(cell)
            if (row - LOG_FIRST) % 2 == 1:
                cell.fill = BAND_FILL
        for col in (1, 2, 3, 4, 5, 6, 8, 9, 10, 11, 12):
            style_input(ws.cell(row=row, column=col))
        ws.cell(row=row, column=1).number_format = "yyyy-mm-dd"
        ws.cell(row=row, column=4).number_format = QTY
        ws.cell(row=row, column=5).number_format = MONEY
        ws.cell(row=row, column=6).number_format = MONEY
        ws.cell(
            row=row,
            column=7,
            value=(
                f'=IF(OR($D{row}="",$E{row}=""),"",'
                f'IF($C{row}="Buy",-($D{row}*$E{row})-N($F{row}),'
                f"($D{row}*$E{row})-N($F{row})))"
            ),
        ).number_format = MONEY
        for col in (8, 9, 10):
            ws.cell(row=row, column=col).alignment = Alignment(
                vertical="top", wrap_text=True
            )
        side_dv.add(ws.cell(row=row, column=3))
        yesno_dv.add(ws.cell(row=row, column=11))

    ws.conditional_formatting.add(
        f"G{LOG_FIRST}:G{LOG_LAST}",
        CellIsRule(operator="greaterThan", formula=["0"], font=Font(color=GOOD)),
    )
    ws.conditional_formatting.add(
        f"K{LOG_FIRST}:K{LOG_LAST}",
        CellIsRule(
            operator="equal",
            formula=['"No"'],
            font=Font(color=BAD, bold=True),
            fill=PatternFill("solid", fgColor=BAD_SOFT),
        ),
    )
    return ws


def build_dashboard(wb: Workbook):
    ws = wb.create_sheet("Dashboard", 1)
    ws.sheet_properties.tabColor = INK
    ws.sheet_view.showGridLines = False
    sheet_title(
        ws, "Dashboard", "Everything below is calculated. Nothing here is typed.", 17
    )
    ws.cell(
        row=2,
        column=1,
        value='=Settings!$C$7&" — everything below is calculated, nothing here is typed."',
    ).font = SUB_F

    ws.column_dimensions["A"].width = 3
    for col in "BCDFGHJKLNOP":
        ws.column_dimensions[col].width = 12
    for col in "EIM":
        ws.column_dimensions[col].width = 2

    tiles = [
        ("B", "Portfolio value", f"=Positions!$K${POS_TOTAL}", MONEY0),
        ("F", "Cost basis", f"=Positions!$F${POS_TOTAL}", MONEY0),
        ("J", "Unrealised P/L", f"=Positions!$L${POS_TOTAL}", MONEY0),
        (
            "N",
            "Unrealised %",
            f"=IFERROR(Positions!$L${POS_TOTAL}/Positions!$F${POS_TOTAL},0)",
            PCT,
        ),
    ]
    for start_col, label, formula, fmt in tiles:
        col = ws[f"{start_col}1"].column
        ws.merge_cells(start_row=4, start_column=col, end_row=4, end_column=col + 2)
        head = ws.cell(row=4, column=col, value=label.upper())
        head.font = KPI_LABEL_F
        head.alignment = Alignment(vertical="center", indent=1)
        head.fill = PatternFill("solid", fgColor=DERIVED)
        ws.merge_cells(start_row=5, start_column=col, end_row=6, end_column=col + 2)
        val = ws.cell(row=5, column=col, value=formula)
        val.font = KPI_F
        val.number_format = fmt
        val.alignment = Alignment(vertical="center", indent=1)
        for offset in range(3):
            for r in (4, 5, 6):
                ws.cell(row=r, column=col + offset).border = BOX
    ws.row_dimensions[5].height = 22
    ws.row_dimensions[6].height = 16
    ws.conditional_formatting.add(
        "J5:P6",
        CellIsRule(
            operator="lessThan", formula=["0"], font=Font(size=20, bold=True, color=BAD)
        ),
    )
    ws.conditional_formatting.add(
        "J5:P6",
        CellIsRule(
            operator="greaterThan",
            formula=["0"],
            font=Font(size=20, bold=True, color=GOOD),
        ),
    )

    second = [
        (
            "B",
            "Positions held",
            f"=COUNTA(Positions!$A${POS_FIRST}:$A${POS_LAST})",
            "0",
        ),
        (
            "F",
            "Largest weight",
            f"=IFERROR(MAX(Positions!$N${POS_FIRST}:$N${POS_LAST}),0)",
            PCT,
        ),
        (
            "J",
            "Over the weight limit",
            f'=COUNTIF(Positions!$N${POS_FIRST}:$N${POS_LAST},">"&MaxWeight)',
            "0",
        ),
        (
            "N",
            "Net cash from the log",
            f"=IFERROR(SUM('Trade Log'!$G${LOG_FIRST}:$G${LOG_LAST}),0)",
            MONEY0,
        ),
    ]
    for start_col, label, formula, fmt in second:
        col = ws[f"{start_col}1"].column
        ws.merge_cells(start_row=8, start_column=col, end_row=8, end_column=col + 2)
        head = ws.cell(row=8, column=col, value=label.upper())
        head.font = KPI_LABEL_F
        head.alignment = Alignment(vertical="center", indent=1)
        head.fill = PatternFill("solid", fgColor=DERIVED)
        ws.merge_cells(start_row=9, start_column=col, end_row=10, end_column=col + 2)
        val = ws.cell(row=9, column=col, value=formula)
        val.font = Font(name="Calibri", size=16, bold=True, color=SLATE)
        val.number_format = fmt
        val.alignment = Alignment(vertical="center", indent=1)
        for offset in range(3):
            for r in (8, 9, 10):
                ws.cell(row=r, column=col + offset).border = BOX

    ws.cell(row=12, column=2, value="Allocation by sector").font = BOLD_F
    for col, (label, width) in enumerate(
        [("Sector", 22), ("Market value", 15), ("Weight", 10)], start=2
    ):
        cell = ws.cell(row=13, column=col, value=label)
        cell.font = HEAD_F
        cell.fill = HEAD_FILL
        cell.border = BOX
        cell.alignment = Alignment(vertical="center", indent=1)
        ws.column_dimensions[get_column_letter(col)].width = width
    for offset, sector in enumerate(SECTORS):
        row = 14 + offset
        for col in range(2, 5):
            cell = ws.cell(row=row, column=col)
            style_body(cell)
            if offset % 2 == 1:
                cell.fill = BAND_FILL
        ws.cell(row=row, column=2, value=sector)
        ws.cell(
            row=row,
            column=3,
            value=f"=SUMIF(Positions!$C${POS_FIRST}:$C${POS_LAST},$B{row},Positions!$K${POS_FIRST}:$K${POS_LAST})",
        ).number_format = MONEY0
        ws.cell(
            row=row,
            column=4,
            value=f'=IFERROR($C{row}/Positions!$K${POS_TOTAL},"")',
        ).number_format = PCT
    last_sector = 14 + len(SECTORS) - 1
    ws.conditional_formatting.add(
        f"D14:D{last_sector}",
        DataBarRule(
            start_type="num", start_value=0, end_type="num", end_value=1, color=ACCENT
        ),
    )

    chart = BarChart()
    chart.type = "bar"
    chart.title = "Where the money actually is"
    chart.y_axis.title = None
    chart.x_axis.title = None
    chart.height = 10
    chart.width = 18
    data = Reference(ws, min_col=3, min_row=13, max_row=last_sector)
    cats = Reference(ws, min_col=2, min_row=14, max_row=last_sector)
    chart.add_data(data, titles_from_data=True)
    chart.set_categories(cats)
    chart.legend = None
    ws.add_chart(chart, "F13")

    row = last_sector + 3
    ws.cell(row=row, column=2, value="Checks").font = BOLD_F
    checks = [
        (
            "Positions with no exit plan",
            f'=SUMPRODUCT((Positions!$A${POS_FIRST}:$A${POS_LAST}<>"")'
            f"*(COUNTIF('Exit Ladder'!$A${LADDER_FIRST}:$A${LADDER_LAST},"
            f"Positions!$A${POS_FIRST}:$A${POS_LAST})=0))",
            "Every holding should have rungs on the Exit Ladder before it needs them.",
        ),
        (
            "Positions priced by hand",
            f'=COUNTIF(Positions!$J${POS_FIRST}:$J${POS_LAST},"manual")'
            f'+COUNTIF(Positions!$J${POS_FIRST}:$J${POS_LAST},"no feed")',
            "These do not update themselves. Everything about them is as stale as the day you typed it.",
        ),
        (
            "Trades logged without a thesis",
            f"=SUMPRODUCT((('Trade Log'!$B${LOG_FIRST}:$B${LOG_LAST}<>\"\")*('Trade Log'!$H${LOG_FIRST}:$H${LOG_LAST}=\"\")))",
            "A trade with no written reason cannot be reviewed later. It is just a number.",
        ),
    ]
    for offset, (label, formula, note) in enumerate(checks, start=1):
        r = row + offset
        lab = ws.cell(row=r, column=2, value=label)
        lab.font = BODY_F
        val = ws.cell(row=r, column=3, value=formula)
        val.font = BOLD_F
        val.number_format = "0"
        val.alignment = Alignment(horizontal="center")
        val.border = BOX
        hint = ws.cell(row=r, column=4, value=note)
        hint.font = SMALL_F
        ws.merge_cells(start_row=r, start_column=4, end_row=r, end_column=12)
        ws.conditional_formatting.add(
            f"C{r}",
            CellIsRule(
                operator="greaterThan",
                formula=["0"],
                font=Font(color=BAD, bold=True),
                fill=PatternFill("solid", fgColor=WARN_SOFT),
            ),
        )
        ws.conditional_formatting.add(
            f"C{r}",
            CellIsRule(
                operator="equal",
                formula=["0"],
                font=Font(color=GOOD, bold=True),
                fill=PatternFill("solid", fgColor=GOOD_SOFT),
            ),
        )
    return ws


def build_start_here(wb: Workbook):
    ws = wb.create_sheet("Start Here", 0)
    ws.sheet_properties.tabColor = ACCENT
    ws.sheet_view.showGridLines = False
    ws.column_dimensions["A"].width = 3
    ws.column_dimensions["B"].width = 26
    ws.column_dimensions["C"].width = 104

    ws.merge_cells("B1:C1")
    title = ws.cell(row=1, column=2, value="Profit & Exit Planner")
    title.font = Font(name="Calibri", size=22, bold=True, color=WHITE)
    title.fill = TITLE_FILL
    title.alignment = Alignment(vertical="center", indent=1)
    ws.row_dimensions[1].height = 44

    def section(row: int, heading: str) -> int:
        cell = ws.cell(row=row, column=2, value=heading)
        cell.font = Font(name="Calibri", size=12, bold=True, color=ACCENT)
        return row + 1

    def line(row: int, left: str, right: str) -> int:
        lab = ws.cell(row=row, column=2, value=left)
        lab.font = BOLD_F
        lab.alignment = Alignment(vertical="top")
        body = ws.cell(row=row, column=3, value=right)
        body.font = BODY_F
        body.alignment = Alignment(vertical="top", wrap_text=True)
        ws.row_dimensions[row].height = max(16, 14 * (1 + len(right) // 95))
        return row + 1

    row = 3
    row = section(row, "What this is")
    row = line(
        row,
        "",
        "A rebuild of the old 'Profit Gain Percentage' sheet. Same job — decide "
        "what to sell, at what price, and what it leaves you with — but the "
        "numbers are calculated instead of typed, so they stop being wrong the "
        "moment the market moves.",
    )
    row += 1

    row = section(row, "The three tabs you actually use")
    row = line(
        row,
        "Positions",
        "What you hold. Type the amber columns; everything else follows.",
    )
    row = line(
        row,
        "Exit Ladder",
        "What you sell, at what price, decided in advance. The point of the whole workbook.",
    )
    row = line(
        row,
        "Sell Calculator",
        "One position, one price, sixteen ways to slice it — and whether the rest is free afterwards.",
    )
    row += 1

    row = section(row, "The rest")
    row = line(
        row,
        "Dashboard",
        "Totals and three checks that go red when something needs attention.",
    )
    row = line(
        row,
        "Gain Table",
        "The old percentage grid as formulas, plus the recovery table it never had.",
    )
    row = line(
        row,
        "Trade Log",
        "One row per fill, with the thesis and the invalidation written down at the time.",
    )
    row = line(
        row,
        "Settings",
        "Tax rate, fees, weight limit. Four numbers the other tabs read.",
    )
    row += 1

    row = section(row, "How prices work")
    row = line(
        row,
        "",
        "Each position calls GOOGLEFINANCE on its ticker. When the feed does not "
        "carry a symbol — it does not carry every altcoin — the price falls back "
        "to the manual price beside it and the Feed column says so. Set Price "
        "mode to Manual to pin a price deliberately. The Dashboard counts how "
        "many rows are running on hand-typed prices.",
    )
    row += 1

    row = section(row, "Colour code")
    for bg, edge, text in (
        (INPUT_BG, INPUT_LINE, "Amber — type here."),
        (
            DERIVED,
            LINE,
            "Grey italic — pulled from another tab. Overwrite it and the link breaks.",
        ),
        (WHITE, LINE, "White — calculated. Leave it alone and it stays right."),
    ):
        chip = ws.cell(row=row, column=2, value="")
        chip.fill = PatternFill("solid", fgColor=bg)
        side = Side(style="thin", color=edge)
        chip.border = Border(left=side, right=side, top=side, bottom=side)
        desc = ws.cell(row=row, column=3, value=text)
        desc.font = BODY_F
        row += 1
    row += 1

    row = section(row, "What changed from the old sheet")
    changes = [
        (
            "Duplicates removed",
            "Decentraland and Chainlink were listed twice, Cardano's exit plan three times, and two 'Future Asset' placeholder rows sat among the real ones. Sixteen rows became twelve holdings.",
        ),
        (
            "A ladder that oversold",
            "The Gala plan sold 3,563 coins on its last rung with 891 left, booking roughly $374 of profit on coins already gone. Rungs are now percentages of the original position and column P turns red if they pass 100%.",
        ),
        (
            "The percentage grid",
            "About 1,900 hand-typed cells across 14 blocks, all of them the same arithmetic. Now two inputs and eight formulas.",
        ),
        (
            "Prices unfroze",
            "Every price was a value typed in October 2022. They are formulas now.",
        ),
        (
            "An all-time high of $0.00",
            "Shiba Inu's real high, $0.00008845, had been rounded away by the cell format, which made its X-to-ATH meaningless.",
        ),
        (
            "Cost basis is one number",
            "The old sheet stored price and cost separately and they had drifted apart. Average cost is stored once and cost basis is derived.",
        ),
        (
            "Tax and fees exist",
            "Every exit figure is now shown after both. The old sheet's profits were gross.",
        ),
        (
            "Recovery math added",
            "Being down 50% needs +100% to undo. The old sheet only ever counted upward.",
        ),
        (
            "Break-even of the remainder",
            "After selling a slice, what the rest has to be worth for the trade to be flat — and when it drops through zero, the position can no longer lose you money.",
        ),
        (
            "The trading log",
            "Was 121 dated rows with four side-by-side ticker blocks and nothing filled in. Now one row per fill, with the thesis and the invalidation captured at entry.",
        ),
        (
            "Credentials removed",
            "The old sheet had a recovery key and a password sitting in plain cells. They were not copied here. Treat them as exposed and rotate them.",
        ),
        (
            "Not carried over",
            "Avalanche, Polygon and Cronos appeared only on planning tabs, never on the holdings tab. They are not here as positions — add them if you hold them.",
        ),
    ]
    for label, text in changes:
        row = line(row, label, text)
    row += 1

    row = section(row, "The disclaimer, kept from the old sheet")
    row = line(
        row,
        "",
        "This spreadsheet is for informational and educational purposes ONLY. I "
        "am NOT a financial advisor and this is NOT financial advice :-)",
    )
    row += 1
    footer = ws.cell(
        row=row,
        column=3,
        value="Generated by tools/build_profit_planner.py in pwb-toolbox. Rebuild it rather than patching it.",
    )
    footer.font = SMALL_F
    return ws


def build_workbook() -> Workbook:
    wb = Workbook()
    wb.remove(wb.active)
    build_positions(wb)
    build_ladder(wb)
    build_sell_calculator(wb)
    build_gain_table(wb)
    build_trade_log(wb)
    build_settings(wb)
    build_lists(wb)
    build_dashboard(wb)
    build_start_here(wb)
    wb.active = 0
    return wb


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        default="profit_planner.xlsx",
        help="path to write the workbook to (default: profit_planner.xlsx)",
    )
    args = parser.parse_args()
    build_workbook().save(args.out)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
