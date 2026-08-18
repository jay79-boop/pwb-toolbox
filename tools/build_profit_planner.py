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

# Row geography of the Ticker Plan sheet. It is the tab that gets used daily,
# so the layout is fixed here and every formula on it refers back to these.
# The position reads across one strip (labels on TP_LABEL, values on TP_INPUT)
# rather than down a column, which is what keeps the ladder above the fold.
TP_LABEL = 4
TP_INPUT = 5
TP_TILE_LABEL = 7
TP_TILE_VALUE = 8
TP_BANNER = 11
TP_HEADER = 12
TP_RUNG_FIRST = 13
TP_RUNG_LAST = 26

# (gain above your average cost, share of the position sold at that rung).
# Sums to 95%, leaving a deliberate 5% riding rather than mechanically
# closing the whole position at the top rung.
TP_RUNGS = [
    (0.05, 0.10),
    (0.10, 0.15),
    (0.15, 0.15),
    (0.20, 0.15),
    (0.25, 0.10),
    (0.30, 0.10),
    (0.40, 0.05),
    (0.50, 0.05),
    (0.75, 0.05),
    (1.00, 0.05),
]


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
    asset_class: str = "Crypto"


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

# The workbook is one book for everything, not one per asset type: a stock and
# a coin differ only in which ticker the price feed wants.
CLASSES = ["Crypto", "Stock", "ETF", "Option", "Cash", "Other"]

# Closed keeps a finished round on the register with its realised result and
# out of every portfolio total; Watchlist is a position sized at zero.
STATUSES = ["Active", "Closed", "Watchlist"]

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
    for col, (header, values) in enumerate(
        [
            ("Sector", SECTORS),
            ("Price mode", ["Auto", "Manual"]),
            ("Side", ["Buy", "Sell"]),
            ("Yes/No", ["Yes", "No"]),
            ("Class", CLASSES),
            ("Status", STATUSES),
        ],
        start=1,
    ):
        ws.cell(row=1, column=col, value=header)
        for offset, value in enumerate(values, start=2):
            ws.cell(row=offset, column=col, value=value)
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
        "The register: everything you hold, everything you used to hold, and "
        "anything you are watching. Closed rows keep their realised result and "
        "stop counting toward the portfolio.",
        24,
    )
    headers = [
        "Asset",
        "Ticker",
        "Class",
        "Sector",
        "Status",
        "Round",
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
        "Realised $",
        "Total result",
        "All-time high",
        "% of ATH",
        "X to ATH",
        "If ATH returns",
        "Note",
    ]
    widths = [
        28,
        19,
        10,
        18,
        10,
        8,
        14,
        13,
        13,
        11,
        13,
        13,
        12,
        14,
        13,
        12,
        9,
        13,
        13,
        13,
        9,
        9,
        14,
        58,
    ]
    header_row(ws, 3, headers, widths)
    ws.freeze_panes = "C4"

    dropdowns = {
        3: "=Lists!$E$2:$E$7",
        4: "=Lists!$A$2:$A$14",
        5: "=Lists!$F$2:$F$4",
        10: "=Lists!$B$2:$B$3",
    }
    validators = {}
    for col, source in dropdowns.items():
        dv = DataValidation(type="list", formula1=source, allow_blank=True)
        ws.add_data_validation(dv)
        validators[col] = dv

    for row in range(POS_FIRST, POS_LAST + 1):
        seed = POSITIONS[row - POS_FIRST] if row - POS_FIRST < len(POSITIONS) else None
        banded = (row - POS_FIRST) % 2 == 1

        for col in range(1, 25):
            cell = ws.cell(row=row, column=col)
            style_body(cell)
            if banded:
                cell.fill = BAND_FILL
        for col in (1, 2, 3, 4, 5, 6, 7, 8, 10, 11, 20, 24):
            style_input(ws.cell(row=row, column=col))

        fine = seed is not None and seed.manual_price < 0.01
        ws.cell(row=row, column=1, value=seed.asset if seed else None)
        ws.cell(row=row, column=2, value=seed.ticker if seed else None)
        ws.cell(row=row, column=3, value=seed.asset_class if seed else None)
        ws.cell(row=row, column=4, value=seed.sector if seed else None)
        ws.cell(row=row, column=5, value="Active" if seed else None)
        ws.cell(row=row, column=6, value=1 if seed else None).alignment = Alignment(
            horizontal="center"
        )
        qty = ws.cell(row=row, column=7, value=seed.qty if seed else None)
        qty.number_format = QTY
        avg = ws.cell(row=row, column=8, value=seed.avg_cost if seed else None)
        avg.number_format = MONEY_FINE if fine else MONEY
        ws.cell(row=row, column=10, value="Auto" if seed else None)
        manual = ws.cell(row=row, column=11, value=seed.manual_price if seed else None)
        manual.number_format = MONEY_FINE if fine else MONEY
        ath = ws.cell(row=row, column=20, value=seed.ath if seed else None)
        ath.number_format = MONEY_FINE if fine else MONEY
        note = ws.cell(row=row, column=24, value=seed.note if seed else None)
        note.font = SMALL_F
        note.alignment = Alignment(vertical="top", wrap_text=True)

        for col, dv in validators.items():
            dv.add(ws.cell(row=row, column=col))

        # Guarded on the name, not the quantity: a closed position keeps its
        # quantity as a record of what it was, and still has to compute a
        # realised result.
        blank = f'$A{row}=""'
        inactive = f'OR($A{row}="",$E{row}<>"Active")'
        ws.cell(
            row=row, column=9, value=f'=IF({blank},"",$G{row}*$H{row})'
        ).number_format = MONEY
        ws.cell(
            row=row,
            column=12,
            value=(
                f'=IF({blank},"",IFERROR(IF($J{row}="Manual",$K{row},'
                f"GOOGLEFINANCE($B{row})),$K{row}))"
            ),
        ).number_format = (
            MONEY_FINE if fine else MONEY
        )
        ws.cell(
            row=row,
            column=13,
            value=(
                f'=IF({blank},"",IF($J{row}="Manual","manual",'
                f'IF(ISERROR(GOOGLEFINANCE($B{row})),"no feed","live")))'
            ),
        ).font = SMALL_F
        # Everything from here to Weight is deliberately blank unless the
        # position is Active, which is what keeps a closed round out of the
        # portfolio totals without deleting it.
        ws.cell(
            row=row, column=14, value=f'=IF({inactive},"",$G{row}*$L{row})'
        ).number_format = MONEY
        ws.cell(
            row=row, column=15, value=f'=IF($N{row}="","",$N{row}-$I{row})'
        ).number_format = MONEY
        ws.cell(
            row=row,
            column=16,
            value=f'=IF(OR($O{row}="",$I{row}=0),"",$O{row}/$I{row})',
        ).number_format = PCT
        ws.cell(
            row=row,
            column=17,
            value=f'=IF(OR($N{row}="",$N${POS_TOTAL}=0),"",$N{row}/$N${POS_TOTAL})',
        ).number_format = PCT
        ws.cell(
            row=row,
            column=18,
            value=(
                f"=IF({blank},\"\",SUMIFS('Trade Log'!$L${LOG_FIRST}:$L${LOG_LAST},"
                f"'Trade Log'!$B${LOG_FIRST}:$B${LOG_LAST},$A{row},"
                f"'Trade Log'!$C${LOG_FIRST}:$C${LOG_LAST},$F{row}))"
            ),
        ).number_format = MONEY
        ws.cell(
            row=row, column=19, value=f'=IF({blank},"",N($O{row})+N($R{row}))'
        ).number_format = MONEY
        ws.cell(
            row=row,
            column=21,
            value=f'=IF(OR({blank},$T{row}=""),"",$L{row}/$T{row})',
        ).number_format = PCT
        ws.cell(
            row=row,
            column=22,
            value=f'=IF(OR({blank},$T{row}="",$L{row}=0),"",$T{row}/$L{row})',
        ).number_format = MULT
        ws.cell(
            row=row,
            column=23,
            value=f'=IF(OR({inactive},$T{row}=""),"",$G{row}*$T{row})',
        ).number_format = MONEY

    for col in range(1, 25):
        cell = ws.cell(row=POS_TOTAL, column=col)
        cell.fill = PatternFill("solid", fgColor=INK)
        cell.font = Font(name="Calibri", size=11, bold=True, color=WHITE)
        cell.border = BOX
    total = ws.cell(
        row=POS_TOTAL, column=1, value="TOTAL — active only, except realised"
    )
    total.alignment = Alignment(indent=1)
    ws.merge_cells(start_row=POS_TOTAL, start_column=1, end_row=POS_TOTAL, end_column=6)
    ws.cell(
        row=POS_TOTAL,
        column=9,
        value=f'=SUMIFS(I{POS_FIRST}:I{POS_LAST},E{POS_FIRST}:E{POS_LAST},"Active")',
    ).number_format = MONEY
    for col, letter in ((14, "N"), (15, "O"), (18, "R"), (19, "S"), (23, "W")):
        ws.cell(
            row=POS_TOTAL,
            column=col,
            value=f"=SUM({letter}{POS_FIRST}:{letter}{POS_LAST})",
        ).number_format = MONEY
    ws.cell(
        row=POS_TOTAL,
        column=16,
        value=f'=IF($I${POS_TOTAL}=0,"",$O${POS_TOTAL}/$I${POS_TOTAL})',
    ).number_format = PCT

    for span in (f"O{POS_FIRST}:P{POS_LAST}", f"R{POS_FIRST}:S{POS_LAST}"):
        ws.conditional_formatting.add(
            span,
            CellIsRule(
                operator="lessThan",
                formula=["0"],
                font=Font(color=BAD),
                fill=PatternFill("solid", fgColor=BAD_SOFT),
            ),
        )
        ws.conditional_formatting.add(
            span,
            CellIsRule(
                operator="greaterThan",
                formula=["0"],
                font=Font(color=GOOD),
                fill=PatternFill("solid", fgColor=GOOD_SOFT),
            ),
        )
    ws.conditional_formatting.add(
        f"Q{POS_FIRST}:Q{POS_LAST}",
        DataBarRule(
            start_type="num", start_value=0, end_type="num", end_value=1, color=ACCENT
        ),
    )
    ws.conditional_formatting.add(
        f"Q{POS_FIRST}:Q{POS_LAST}",
        FormulaRule(
            formula=[f'AND($Q{POS_FIRST}<>"",$Q{POS_FIRST}>MaxWeight)'],
            fill=PatternFill("solid", fgColor=WARN_SOFT),
        ),
    )
    ws.conditional_formatting.add(
        f"M{POS_FIRST}:M{POS_LAST}",
        CellIsRule(
            operator="equal", formula=['"no feed"'], font=Font(color=BAD, size=9)
        ),
    )
    ws.conditional_formatting.add(
        f"A{POS_FIRST}:X{POS_LAST}",
        FormulaRule(formula=[f'$E{POS_FIRST}="Closed"'], font=Font(color=MUTED)),
    )
    ws.conditional_formatting.add(
        f"E{POS_FIRST}:E{POS_LAST}",
        CellIsRule(
            operator="equal",
            formula=['"Watchlist"'],
            font=Font(color="1D4ED8", bold=True),
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
        # Quantity and average cost are amber, not grey: they arrive filled in
        # from the register, and typing over either one is a legitimate
        # what-if — or the only way to plan an asset you do not hold yet.
        for col in (1, 2, 3, 5, 6, 8):
            style_input(ws.cell(row=row, column=col))
        style_derived(ws.cell(row=row, column=4))

        asset_cell = ws.cell(row=row, column=1, value=seed[0] if seed else None)
        asset_dv.add(asset_cell)
        ws.cell(row=row, column=5, value=f"Rung {seed[1]}" if seed else None)
        target = ws.cell(row=row, column=6, value=seed[2].target if seed else None)
        target.number_format = MONEY
        pct = ws.cell(row=row, column=8, value=seed[2].pct if seed else None)
        pct.number_format = PCT0

        blank = f'$A{row}=""'
        # IFERROR, because a name that is not on the register is not a mistake
        # — it is a coin you are sizing up. Without it the whole row collapsed
        # to #N/A the moment you typed a ticker of your own.
        ws.cell(
            row=row,
            column=2,
            value=f'=IF({blank},"",IFERROR({pos_lookup("G", f"$A{row}")},""))',
        ).number_format = QTY
        ws.cell(
            row=row,
            column=3,
            value=f'=IF({blank},"",IFERROR({pos_lookup("H", f"$A{row}")},""))',
        ).number_format = MONEY
        ws.cell(
            row=row,
            column=4,
            value=f'=IF(OR($B{row}="",$C{row}=""),"",$B{row}*$C{row})',
        ).number_format = MONEY

        empty = f'OR({blank},$B{row}="",$C{row}="",$F{row}="",$H{row}="")'
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
    how = ws.cell(
        row=row,
        column=1,
        value=(
            "Pick an asset and its quantity and average cost arrive from the "
            "register — both are amber, so type over either one for a what-if, "
            "or type a name of your own and fill them in yourself to plan "
            "something you do not hold yet. Cost basis follows from the two of "
            "them, and every column to the right follows from that."
        ),
    )
    how.font = SMALL_F
    how.alignment = Alignment(vertical="top", wrap_text=True)
    ws.merge_cells(start_row=row, start_column=1, end_row=row + 1, end_column=10)
    ws.row_dimensions[row].height = 28

    row = LADDER_LAST + 5
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


def build_ticker_plan(wb: Workbook):
    """One ticker, one screen, the whole exit plan.

    The position reads across a single strip rather than down a column, so the
    ladder starts near the top of the sheet. An earlier version stacked the
    inputs vertically and froze twenty-one rows, which on a laptop left the
    ladder entirely below the fold — the sheet looked like it ended at the
    tiles.

    Deliberately self-contained: every formula refers to a cell on this sheet
    (plus the two rates from Settings), so right-click → Duplicate gives a
    working plan for the next coin instead of a mirror of this one.
    """
    ws = wb.create_sheet("Ticker Plan")
    ws.sheet_properties.tabColor = ACCENT
    ws.sheet_view.showGridLines = False
    sheet_title(
        ws,
        "Ticker Plan",
        "Fill in the four amber cells. Everything below them is the plan. "
        "Right-click the tab and Duplicate it for the next coin.",
        20,
    )

    ws.column_dimensions["A"].width = 3
    ws.column_dimensions["B"].width = 18
    for col in "CDEFGHIJKLMNO":
        ws.column_dimensions[col].width = 14
    ws.column_dimensions["P"].width = 2
    for col in "QRST":
        ws.column_dimensions[col].width = 15

    # ---- the position, read across ----------------------------------------
    strip = [
        (2, "Ticker", "CURRENCY:XRPUSD", None, True),
        (3, "Units held", 1000, QTY, True),
        (4, "Average cost", 1.00, MONEY, True),
        (5, "Manual price", 1.00, MONEY, True),
        (
            6,
            "Live price",
            f"=IFERROR(GOOGLEFINANCE($B${TP_INPUT}),$E${TP_INPUT})",
            MONEY,
            False,
        ),
        (
            7,
            "Feed",
            f'=IF(ISERROR(GOOGLEFINANCE($B${TP_INPUT})),"manual","live")',
            None,
            False,
        ),
        (8, "Cost basis", f"=$C${TP_INPUT}*$D${TP_INPUT}", MONEY, False),
        (9, "Market value", f"=$C${TP_INPUT}*$F${TP_INPUT}", MONEY, False),
        (10, "Open profit", f"=$I${TP_INPUT}-$H${TP_INPUT}", MONEY, False),
        (
            11,
            "Open profit %",
            f'=IF($H${TP_INPUT}=0,"",$J${TP_INPUT}/$H${TP_INPUT})',
            PCT,
            False,
        ),
    ]
    for col, label, value, fmt, typed in strip:
        head = ws.cell(row=TP_LABEL, column=col, value=label.upper())
        head.font = KPI_LABEL_F
        head.alignment = Alignment(vertical="center", wrap_text=True, indent=1)
        head.fill = PatternFill("solid", fgColor=INPUT_BG if typed else DERIVED)
        head.border = BOX
        cell = ws.cell(row=TP_INPUT, column=col, value=value)
        if typed:
            style_input(cell)
        else:
            style_derived(cell)
        if fmt:
            cell.number_format = fmt
    ws.row_dimensions[TP_LABEL].height = 26
    ws.row_dimensions[TP_INPUT].height = 20

    for ref in (f"J{TP_INPUT}", f"K{TP_INPUT}"):
        ws.conditional_formatting.add(
            ref,
            CellIsRule(
                operator="lessThan", formula=["0"], font=Font(bold=True, color=BAD)
            ),
        )
        ws.conditional_formatting.add(
            ref,
            CellIsRule(
                operator="greaterThan", formula=["0"], font=Font(bold=True, color=GOOD)
            ),
        )

    # ---- what the whole plan adds up to ------------------------------------
    first, last = TP_RUNG_FIRST, TP_RUNG_LAST
    tiles = [
        (2, "Net cash it returns", f"=SUM($K${first}:$K${last})", MONEY0),
        (
            4,
            "Average exit price",
            f'=IF(SUM($I${first}:$I${last})=0,"—",'
            f"SUM($J${first}:$J${last})/SUM($I${first}:$I${last}))",
            MONEY,
        ),
        (
            6,
            "% of position sold",
            f'=IF($C${TP_INPUT}=0,"",SUM($I${first}:$I${last})/$C${TP_INPUT})',
            PCT,
        ),
        (8, "Units still riding", f"=$C${TP_INPUT}-SUM($I${first}:$I${last})", QTY),
        (
            10,
            "Free ride from",
            f'=IFERROR("+"&TEXT(INDEX($B${first}:$B${last},'
            f'MATCH("Free ride",$N${first}:$N${last},0)),"0%"),"not with this plan")',
            None,
        ),
    ]
    for col, label, formula, fmt in tiles:
        ws.merge_cells(
            start_row=TP_TILE_LABEL,
            start_column=col,
            end_row=TP_TILE_LABEL,
            end_column=col + 1,
        )
        head = ws.cell(row=TP_TILE_LABEL, column=col, value=label.upper())
        head.font = KPI_LABEL_F
        head.fill = PatternFill("solid", fgColor=DERIVED)
        head.alignment = Alignment(vertical="center", indent=1)
        ws.merge_cells(
            start_row=TP_TILE_VALUE,
            start_column=col,
            end_row=TP_TILE_VALUE + 1,
            end_column=col + 1,
        )
        val = ws.cell(row=TP_TILE_VALUE, column=col, value=formula)
        val.font = Font(name="Calibri", size=15, bold=True, color=INK)
        val.alignment = Alignment(vertical="center", indent=1)
        if fmt:
            val.number_format = fmt
        for offset in range(2):
            for r in (TP_TILE_LABEL, TP_TILE_VALUE, TP_TILE_VALUE + 1):
                ws.cell(row=r, column=col + offset).border = BOX
    ws.row_dimensions[TP_TILE_VALUE].height = 20

    ws.conditional_formatting.add(
        f"G{TP_TILE_VALUE}",
        CellIsRule(
            operator="greaterThan",
            formula=["1"],
            font=Font(size=15, bold=True, color=BAD),
            fill=PatternFill("solid", fgColor=BAD_SOFT),
        ),
    )

    # ---- the ladder --------------------------------------------------------
    for start, end, text, colour in [
        (2, 5, "The rung", SLATE),
        (6, 7, "If you sold the whole position here", "1D4ED8"),
        (8, 15, "Or take a slice at each rung", ACCENT),
    ]:
        ws.merge_cells(
            start_row=TP_BANNER, start_column=start, end_row=TP_BANNER, end_column=end
        )
        for col in range(start, end + 1):
            ws.cell(row=TP_BANNER, column=col).fill = PatternFill(
                "solid", fgColor=colour
            )
        cell = ws.cell(row=TP_BANNER, column=start, value=text)
        cell.font = HEAD_F
        cell.alignment = Alignment(vertical="center", horizontal="center")
    ws.row_dimensions[TP_BANNER].height = 20

    headers = [
        "Gain from your cost",
        "Target price",
        "vs today's price",
        "Profit per unit",
        "Net cash",
        "Net profit",
        "% to sell here",
        "Units sold",
        "Gross",
        "Net cash",
        "Cash so far",
        "Units left",
        "Status",
        "Break-even of the rest",
    ]
    for offset, label in enumerate(headers):
        cell = ws.cell(row=TP_HEADER, column=2 + offset, value=label)
        cell.font = HEAD_F
        cell.fill = HEAD_FILL
        cell.border = BOX
        cell.alignment = Alignment(vertical="center", wrap_text=True, indent=1)
    ws.row_dimensions[TP_HEADER].height = 32
    ws.freeze_panes = f"B{TP_RUNG_FIRST}"

    for offset in range(last - first + 1):
        row = first + offset
        seed = TP_RUNGS[offset] if offset < len(TP_RUNGS) else None
        for col in range(2, 16):
            cell = ws.cell(row=row, column=col)
            style_body(cell)
            if offset % 2 == 1:
                cell.fill = BAND_FILL
        for col in (2, 8):
            style_input(ws.cell(row=row, column=col))

        gain = ws.cell(row=row, column=2, value=seed[0] if seed else None)
        gain.number_format = PCT0
        slice_pct = ws.cell(row=row, column=8, value=seed[1] if seed else None)
        slice_pct.number_format = PCT0

        blank = f'$B{row}=""'
        noslice = f'$I{row}=""'
        units, cost, live, basis = (
            f"$C${TP_INPUT}",
            f"$D${TP_INPUT}",
            f"$F${TP_INPUT}",
            f"$H${TP_INPUT}",
        )
        ws.cell(
            row=row, column=3, value=f'=IF({blank},"",{cost}*(1+$B{row}))'
        ).number_format = MONEY
        ws.cell(
            row=row,
            column=4,
            value=f'=IF(OR({blank},{live}=0),"",$C{row}/{live}-1)',
        ).number_format = PCT
        ws.cell(
            row=row, column=5, value=f'=IF({blank},"",$C{row}-{cost})'
        ).number_format = MONEY
        ws.cell(
            row=row,
            column=6,
            value=(
                f'=IF({blank},"",{units}*$C{row}*(1-FeeRate)'
                f"-MAX(0,($C{row}-{cost})*{units})*TaxRate)"
            ),
        ).number_format = MONEY
        ws.cell(
            row=row, column=7, value=f'=IF({blank},"",$F{row}-{basis})'
        ).number_format = MONEY
        ws.cell(
            row=row,
            column=9,
            value=f'=IF(OR({blank},$H{row}=""),"",$H{row}*{units})',
        ).number_format = QTY
        ws.cell(
            row=row, column=10, value=f'=IF({noslice},"",$I{row}*$C{row})'
        ).number_format = MONEY
        ws.cell(
            row=row,
            column=11,
            value=(
                f'=IF({noslice},"",$J{row}*(1-FeeRate)'
                f"-MAX(0,($C{row}-{cost})*$I{row})*TaxRate)"
            ),
        ).number_format = MONEY
        ws.cell(
            row=row, column=12, value=f'=IF({noslice},"",SUM($K${first}:$K{row}))'
        ).number_format = MONEY
        ws.cell(
            row=row,
            column=13,
            value=f'=IF({noslice},"",{units}-SUM($I${first}:$I{row}))',
        ).number_format = QTY
        # The rung where the cash taken off the table covers what you put in.
        ws.cell(
            row=row,
            column=14,
            value=f'=IF({noslice},"",IF($L{row}>={basis},"Free ride","Still exposed"))',
        )
        ws.cell(
            row=row,
            column=15,
            value=(
                f'=IF({noslice},"",IF($M{row}<=0,"—",'
                f"MAX(0,{basis}-$L{row})/$M{row}))"
            ),
        ).number_format = MONEY

    ws.conditional_formatting.add(
        f"N{first}:N{last}",
        CellIsRule(
            operator="equal",
            formula=['"Free ride"'],
            font=Font(color=GOOD, bold=True),
            fill=PatternFill("solid", fgColor=GOOD_SOFT),
        ),
    )
    ws.conditional_formatting.add(
        f"D{first}:D{last}",
        CellIsRule(
            operator="lessThanOrEqual",
            formula=["0"],
            font=Font(color=GOOD, bold=True),
            fill=PatternFill("solid", fgColor=GOOD_SOFT),
        ),
    )
    ws.conditional_formatting.add(
        f"M{first}:M{last}",
        CellIsRule(
            operator="lessThan",
            formula=["0"],
            font=Font(color=BAD, bold=True),
            fill=PatternFill("solid", fgColor=BAD_SOFT),
        ),
    )
    ws.conditional_formatting.add(
        f"L{first}:L{last}",
        DataBarRule(start_type="min", end_type="max", color=ACCENT),
    )

    # ---- recovery math, beside the ladder rather than below it -------------
    ws.merge_cells(
        start_row=TP_BANNER, start_column=17, end_row=TP_BANNER, end_column=20
    )
    for col in range(17, 21):
        ws.cell(row=TP_BANNER, column=col).fill = PatternFill("solid", fgColor=BAD)
    rec = ws.cell(row=TP_BANNER, column=17, value="If it goes the other way")
    rec.font = HEAD_F
    rec.alignment = Alignment(vertical="center", horizontal="center")
    for offset, label in enumerate(
        [
            "If you are down",
            "Price becomes",
            "Position worth",
            "Gain needed to get back",
        ]
    ):
        cell = ws.cell(row=TP_HEADER, column=17 + offset, value=label)
        cell.font = HEAD_F
        cell.fill = HEAD_FILL
        cell.border = BOX
        cell.alignment = Alignment(vertical="center", wrap_text=True, indent=1)

    for offset, drawdown in enumerate([i / 100 for i in range(10, 81, 10)]):
        row = TP_RUNG_FIRST + offset
        for col in range(17, 21):
            cell = ws.cell(row=row, column=col)
            style_body(cell)
            if offset % 2 == 1:
                cell.fill = BAND_FILL
        ws.cell(row=row, column=17, value=round(drawdown, 2)).number_format = PCT0
        ws.cell(
            row=row, column=18, value=f"=$D${TP_INPUT}*(1-$Q{row})"
        ).number_format = MONEY
        ws.cell(row=row, column=19, value=f"=$R{row}*$C${TP_INPUT}").number_format = (
            MONEY
        )
        ws.cell(row=row, column=20, value=f"=1/(1-$Q{row})-1").number_format = PCT0
    ws.conditional_formatting.add(
        f"T{TP_RUNG_FIRST}:T{TP_RUNG_FIRST + 7}",
        CellIsRule(
            operator="greaterThanOrEqual",
            formula=["1"],
            font=Font(color=BAD, bold=True),
            fill=PatternFill("solid", fgColor=BAD_SOFT),
        ),
    )

    note = ws.cell(
        row=last + 2,
        column=2,
        value=(
            'Green under "vs today\'s price" means the market is already past '
            'that rung. "Break-even of the rest" is what the units you still '
            "hold have to be worth for the whole trade to come out flat — once "
            "a rung reads Free ride, the position cannot lose you money "
            "whatever happens next."
        ),
    )
    note.font = SMALL_F
    note.alignment = Alignment(vertical="top", wrap_text=True)
    ws.merge_cells(start_row=last + 2, start_column=2, end_row=last + 3, end_column=11)
    return ws


def build_trade_log(wb: Workbook):
    ws = wb.create_sheet("Trade Log")
    ws.sheet_properties.tabColor = "1D4ED8"
    sheet_title(
        ws,
        "Trade Log",
        "Every fill you actually made. Pick the position, give it a round "
        "number, and each sell works out what it realised against what that "
        "round's buys averaged.",
        17,
    )
    headers = [
        "Date",
        "Position",
        "Round",
        "Side",
        "Units",
        "Price",
        "Fees",
        "Cash flow",
        "Buy cost",
        "Buy units",
        "Round avg cost",
        "Realised $",
        "Realised %",
        "Thesis at entry",
        "What would prove me wrong",
        "Exit reason",
        "Followed the plan?",
    ]
    widths = [12, 26, 8, 9, 14, 13, 10, 14, 12, 11, 15, 13, 12, 40, 36, 28, 15]
    header_row(ws, 4, headers, widths)
    ws.freeze_panes = "C5"

    asset_dv = DataValidation(
        type="list",
        formula1=f"=Positions!$A${POS_FIRST}:$A${POS_LAST}",
        allow_blank=True,
    )
    side_dv = DataValidation(type="list", formula1="=Lists!$C$2:$C$3", allow_blank=True)
    yesno_dv = DataValidation(
        type="list", formula1="=Lists!$D$2:$D$3", allow_blank=True
    )
    for dv in (asset_dv, side_dv, yesno_dv):
        ws.add_data_validation(dv)

    for row in range(LOG_FIRST, LOG_LAST + 1):
        for col in range(1, 18):
            cell = ws.cell(row=row, column=col)
            style_body(cell)
            if (row - LOG_FIRST) % 2 == 1:
                cell.fill = BAND_FILL
        for col in (1, 2, 3, 4, 5, 6, 7, 14, 15, 16, 17):
            style_input(ws.cell(row=row, column=col))
        for col in (9, 10, 11):
            style_derived(ws.cell(row=row, column=col))

        ws.cell(row=row, column=1).number_format = "yyyy-mm-dd"
        ws.cell(row=row, column=3).alignment = Alignment(horizontal="center")
        ws.cell(row=row, column=5).number_format = QTY
        for col in (6, 7):
            ws.cell(row=row, column=col).number_format = MONEY
        asset_dv.add(ws.cell(row=row, column=2))
        side_dv.add(ws.cell(row=row, column=4))
        yesno_dv.add(ws.cell(row=row, column=17))

        empty = f'OR($E{row}="",$F{row}="")'
        ws.cell(
            row=row,
            column=8,
            value=(
                f'=IF({empty},"",IF($D{row}="Buy",-($E{row}*$F{row})-N($G{row}),'
                f"($E{row}*$F{row})-N($G{row})))"
            ),
        ).number_format = MONEY
        # These two exist so the round average below can be a SUMIFS rather
        # than an array formula. Nothing else reads them.
        ws.cell(
            row=row,
            column=9,
            value=f'=IF(OR($D{row}<>"Buy",{empty}),"",$E{row}*$F{row}+N($G{row}))',
        ).number_format = MONEY
        ws.cell(
            row=row,
            column=10,
            value=f'=IF(OR($D{row}<>"Buy",{empty}),"",$E{row})',
        ).number_format = QTY
        # Average cost of this position's buys in this round only, which is
        # what makes re-entering a closed position safe: round 2 never sees
        # round 1's prices.
        ws.cell(
            row=row,
            column=11,
            value=(
                f'=IF($B{row}="","",IFERROR('
                f"SUMIFS($I${LOG_FIRST}:$I${LOG_LAST},$B${LOG_FIRST}:$B${LOG_LAST},$B{row},"
                f"$C${LOG_FIRST}:$C${LOG_LAST},$C{row})/"
                f"SUMIFS($J${LOG_FIRST}:$J${LOG_LAST},$B${LOG_FIRST}:$B${LOG_LAST},$B{row},"
                f'$C${LOG_FIRST}:$C${LOG_LAST},$C{row}),""))'
            ),
        ).number_format = MONEY
        ws.cell(
            row=row,
            column=12,
            value=(
                f'=IF(OR($D{row}<>"Sell",{empty},$K{row}=""),"",'
                f"$E{row}*$F{row}-N($G{row})-$E{row}*$K{row})"
            ),
        ).number_format = MONEY
        ws.cell(
            row=row,
            column=13,
            value=f'=IF(OR($L{row}="",$K{row}=0),"",$L{row}/($E{row}*$K{row}))',
        ).number_format = PCT
        for col in (14, 15, 16):
            ws.cell(row=row, column=col).alignment = Alignment(
                vertical="top", wrap_text=True
            )

    for span in (f"L{LOG_FIRST}:M{LOG_LAST}",):
        ws.conditional_formatting.add(
            span,
            CellIsRule(
                operator="greaterThan",
                formula=["0"],
                font=Font(color=GOOD, bold=True),
                fill=PatternFill("solid", fgColor=GOOD_SOFT),
            ),
        )
        ws.conditional_formatting.add(
            span,
            CellIsRule(
                operator="lessThan",
                formula=["0"],
                font=Font(color=BAD, bold=True),
                fill=PatternFill("solid", fgColor=BAD_SOFT),
            ),
        )
    ws.conditional_formatting.add(
        f"Q{LOG_FIRST}:Q{LOG_LAST}",
        CellIsRule(
            operator="equal",
            formula=['"No"'],
            font=Font(color=BAD, bold=True),
            fill=PatternFill("solid", fgColor=BAD_SOFT),
        ),
    )
    return ws


def build_dashboard(wb: Workbook):
    ws = wb.create_sheet("Dashboard")
    ws.sheet_properties.tabColor = INK
    ws.sheet_view.showGridLines = False
    sheet_title(
        ws, "Dashboard", "Everything here is calculated. Nothing here is typed.", 18
    )
    ws.cell(
        row=2,
        column=1,
        value='=Settings!$C$7&" — everything here is calculated, nothing here is typed."',
    ).font = SUB_F

    ws.column_dimensions["A"].width = 3
    for col in "BCDFGHJKLNOP":
        ws.column_dimensions[col].width = 12
    for col in "EIM":
        ws.column_dimensions[col].width = 2

    status = f"Positions!$E${POS_FIRST}:$E${POS_LAST}"
    realised = f"Positions!$R${POS_FIRST}:$R${POS_LAST}"

    def tiles(label_row: int, entries):
        for start_col, label, formula, fmt, size in entries:
            col = ws[f"{start_col}1"].column
            ws.merge_cells(
                start_row=label_row,
                start_column=col,
                end_row=label_row,
                end_column=col + 2,
            )
            head = ws.cell(row=label_row, column=col, value=label.upper())
            head.font = KPI_LABEL_F
            head.alignment = Alignment(vertical="center", indent=1)
            head.fill = PatternFill("solid", fgColor=DERIVED)
            ws.merge_cells(
                start_row=label_row + 1,
                start_column=col,
                end_row=label_row + 2,
                end_column=col + 2,
            )
            val = ws.cell(row=label_row + 1, column=col, value=formula)
            val.font = Font(name="Calibri", size=size, bold=True, color=INK)
            val.number_format = fmt or "General"
            val.alignment = Alignment(vertical="center", indent=1)
            for offset in range(3):
                for r in (label_row, label_row + 1, label_row + 2):
                    ws.cell(row=r, column=col + offset).border = BOX
        ws.row_dimensions[label_row + 1].height = 22
        ws.row_dimensions[label_row + 2].height = 16

    tiles(
        4,
        [
            ("B", "Portfolio value", f"=Positions!$N${POS_TOTAL}", MONEY0, 20),
            ("F", "Cost at risk", f"=Positions!$I${POS_TOTAL}", MONEY0, 20),
            ("J", "Unrealised", f"=Positions!$O${POS_TOTAL}", MONEY0, 20),
            (
                "N",
                "Unrealised %",
                f"=IFERROR(Positions!$O${POS_TOTAL}/Positions!$I${POS_TOTAL},0)",
                PCT,
                20,
            ),
        ],
    )
    # The second row is the half the old sheet never had: what you have
    # actually banked, and what the two halves add up to.
    tiles(
        8,
        [
            ("B", "Realised, all time", f"=Positions!$R${POS_TOTAL}", MONEY0, 18),
            ("F", "Total result", f"=Positions!$S${POS_TOTAL}", MONEY0, 18),
            (
                "J",
                "Active / closed",
                f'=COUNTIF({status},"Active")&" / "&COUNTIF({status},"Closed")',
                None,
                18,
            ),
            (
                "N",
                "Closed rounds in profit",
                f'=IFERROR(COUNTIFS({status},"Closed",{realised},">0")'
                f'/COUNTIF({status},"Closed"),"—")',
                PCT0,
                18,
            ),
        ],
    )
    for span in ("J5:P7", "B9:H11"):
        ws.conditional_formatting.add(
            span,
            CellIsRule(
                operator="lessThan",
                formula=["0"],
                font=Font(size=18, bold=True, color=BAD),
            ),
        )
        ws.conditional_formatting.add(
            span,
            CellIsRule(
                operator="greaterThan",
                formula=["0"],
                font=Font(size=18, bold=True, color=GOOD),
            ),
        )

    def breakdown(col: int, heading: str, keys, key_range: str):
        title = ws.cell(row=12, column=col, value=heading)
        title.font = BOLD_F
        for offset, (label, width) in enumerate(
            [("", 22), ("Value", 14), ("Share", 10)]
        ):
            cell = ws.cell(
                row=13, column=col + offset, value=label or heading.split()[-1]
            )
            cell.font = HEAD_F
            cell.fill = HEAD_FILL
            cell.border = BOX
            cell.alignment = Alignment(vertical="center", indent=1)
            ws.column_dimensions[get_column_letter(col + offset)].width = width
        letter = get_column_letter(col)
        value_letter = get_column_letter(col + 1)
        for offset, key in enumerate(keys):
            row = 14 + offset
            for c in range(col, col + 3):
                cell = ws.cell(row=row, column=c)
                style_body(cell)
                if offset % 2 == 1:
                    cell.fill = BAND_FILL
            ws.cell(row=row, column=col, value=key)
            ws.cell(
                row=row,
                column=col + 1,
                value=(
                    f"=SUMIF({key_range},${letter}{row},"
                    f"Positions!$N${POS_FIRST}:$N${POS_LAST})"
                ),
            ).number_format = MONEY0
            ws.cell(
                row=row,
                column=col + 2,
                value=f'=IFERROR(${value_letter}{row}/Positions!$N${POS_TOTAL},"")',
            ).number_format = PCT
        last = 14 + len(keys) - 1
        ws.conditional_formatting.add(
            f"{get_column_letter(col + 2)}14:{get_column_letter(col + 2)}{last}",
            DataBarRule(
                start_type="num",
                start_value=0,
                end_type="num",
                end_value=1,
                color=ACCENT,
            ),
        )
        return last

    class_last = breakdown(
        2,
        "Split by class",
        CLASSES,
        f"Positions!$C${POS_FIRST}:$C${POS_LAST}",
    )
    sector_last = breakdown(
        6,
        "Split by sector",
        SECTORS,
        f"Positions!$D${POS_FIRST}:$D${POS_LAST}",
    )

    chart = BarChart()
    chart.type = "bar"
    chart.title = "Where the money actually is"
    chart.height = 10
    chart.width = 16
    chart.add_data(
        Reference(ws, min_col=7, min_row=13, max_row=sector_last), titles_from_data=True
    )
    chart.set_categories(Reference(ws, min_col=6, min_row=14, max_row=sector_last))
    chart.legend = None
    ws.add_chart(chart, "J13")

    row = max(class_last, sector_last) + 3
    ws.cell(row=row, column=2, value="Checks").font = BOLD_F
    checks = [
        (
            "Active positions with no exit plan",
            f'=SUMPRODUCT(({status}="Active")*'
            f"(COUNTIF('Exit Ladder'!$A${LADDER_FIRST}:$A${LADDER_LAST},"
            f"Positions!$A${POS_FIRST}:$A${POS_LAST})=0))",
            "Every holding should have rungs before it needs them.",
        ),
        (
            "Positions priced by hand",
            f'=COUNTIF(Positions!$M${POS_FIRST}:$M${POS_LAST},"manual")'
            f'+COUNTIF(Positions!$M${POS_FIRST}:$M${POS_LAST},"no feed")',
            "These do not update themselves. Everything about them is as stale as the day you typed it.",
        ),
        (
            "Trades logged against nothing",
            f"=SUMPRODUCT(('Trade Log'!$B${LOG_FIRST}:$B${LOG_LAST}<>\"\")*"
            f"(COUNTIF(Positions!$A${POS_FIRST}:$A${POS_LAST},"
            f"'Trade Log'!$B${LOG_FIRST}:$B${LOG_LAST})=0))",
            "A logged trade whose position name does not match the register earns nothing — its realised profit lands nowhere.",
        ),
        (
            "Names used twice on the register",
            f'=SUMPRODUCT((Positions!$A${POS_FIRST}:$A${POS_LAST}<>"")*'
            f"(COUNTIF(Positions!$A${POS_FIRST}:$A${POS_LAST},"
            f"Positions!$A${POS_FIRST}:$A${POS_LAST})>1))",
            "Names are the key everything else joins on. Re-entering a position needs a name you can tell apart — Cardano, round 2 — not a second row called Cardano.",
        ),
        (
            "Trades logged without a thesis",
            f"=SUMPRODUCT(('Trade Log'!$B${LOG_FIRST}:$B${LOG_LAST}<>\"\")"
            f"*('Trade Log'!$N${LOG_FIRST}:$N${LOG_LAST}=\"\"))",
            "A trade with no written reason cannot be reviewed later. It is just a number.",
        ),
    ]
    for offset, (label, formula, note) in enumerate(checks, start=1):
        r = row + offset
        ws.cell(row=r, column=2, value=label).font = BODY_F
        ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=3)
        val = ws.cell(row=r, column=4, value=formula)
        val.font = BOLD_F
        val.number_format = "0"
        val.alignment = Alignment(horizontal="center")
        val.border = BOX
        hint = ws.cell(row=r, column=6, value=note)
        hint.font = SMALL_F
        ws.merge_cells(start_row=r, start_column=6, end_row=r, end_column=14)
        ws.conditional_formatting.add(
            f"D{r}",
            CellIsRule(
                operator="greaterThan",
                formula=["0"],
                font=Font(color=BAD, bold=True),
                fill=PatternFill("solid", fgColor=WARN_SOFT),
            ),
        )
        ws.conditional_formatting.add(
            f"D{r}",
            CellIsRule(
                operator="equal",
                formula=["0"],
                font=Font(color=GOOD, bold=True),
                fill=PatternFill("solid", fgColor=GOOD_SOFT),
            ),
        )
    return ws


def build_start_here(wb: Workbook):
    ws = wb.create_sheet("Start Here")
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

    row = section(row, "Start on Ticker Plan")
    row = line(
        row,
        "Ticker Plan",
        "Type a ticker, what you paid and how much you hold. It shows the live "
        "price and every exit rung measured from your cost: what the price has "
        "to reach, what you make per unit, what selling the lot would return, "
        "and what taking a slice at each rung leaves you still holding. "
        "Right-click the tab and choose Duplicate to make one per coin — it "
        "links to no other tab, so the copy works on its own.",
    )
    row += 1

    row = section(row, "The rest")
    row = line(
        row,
        "Positions",
        "Every holding in one table — the portfolio view Ticker Plan does not "
        "give you. Type the amber columns; the rest follows.",
    )
    row = line(
        row,
        "Exit Ladder",
        "The same rungs, but for every coin at once, so nothing sits unplanned. "
        "The Dashboard counts the holdings missing from it.",
    )
    row = line(
        row,
        "Dashboard",
        "Totals and three checks that go red when something needs attention.",
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

    row = section(row, "Adding to it as the portfolio grows")
    row = line(
        row,
        "A new coin or stock",
        "First empty row on Positions. Name, ticker, Class, Sector, Status = "
        "Active, Round = 1, then quantity and average cost. Nothing else needs "
        "touching.",
    )
    row = line(
        row,
        "A buy or a sell",
        "One row on Trade Log. Pick the position from the dropdown and give it "
        "the same Round number. A sell works out what it realised against what "
        "that round's buys averaged — you do not enter the profit yourself.",
    )
    row = line(
        row,
        "Closing a position out",
        "Set Status to Closed. The row stays where it is with the realised "
        "profit the log worked out, and stops counting toward portfolio value, "
        "weight and unrealised profit. Nothing is deleted and nothing is lost.",
    )
    row = line(
        row,
        "Buying it again later",
        "Leave the closed row alone. Add a new row with a name you can tell "
        "apart — Cardano, round 2 — and set Round to 2. The old round's result "
        "is banked and does not move, and the new round starts from its own "
        "average cost. Reusing the same name is the one thing that breaks this, "
        "so the Dashboard counts names used twice.",
    )
    row = line(
        row,
        "Something you only watch",
        "Same as a position, but Status = Watchlist. It shows a live price and "
        "counts toward nothing.",
    )
    row = line(
        row,
        "Stocks and ETFs",
        "Same table, same tabs. Set Class to Stock and use the plain symbol as "
        "the ticker — NVDA, not CURRENCY:NVDA. The Dashboard splits the "
        "portfolio by class so you can see crypto and equity apart.",
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
            "About 1,900 hand-typed cells across 14 blocks, all of them the same arithmetic. It is now four inputs on Ticker Plan, and the rungs move when you change what you paid.",
        ),
        (
            "Six tabs became one",
            "Working out an exit used to mean reading a percentage table, a sell-profit table and a pull-profits block that did not agree with each other. Ticker Plan answers all three questions in one row: what the price has to reach, what selling the lot returns, and what selling a slice leaves you holding.",
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
            "Was 121 dated rows with four side-by-side ticker blocks and nothing filled in. Now one row per fill, with the thesis and the invalidation captured at entry, and each sell measured against what that round's buys averaged.",
        ),
        (
            "Profit you have actually banked",
            "The old sheet only ever showed what a position might be worth. Closing one out now leaves its realised result on the register, out of the portfolio totals but inside the lifetime figure — so selling something does not erase it from your record.",
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
    wb.calculation.fullCalcOnLoad = True
    # Created in the order they should appear: the daily driver first, the
    # reference tabs behind it.
    build_start_here(wb)
    build_ticker_plan(wb)
    build_positions(wb)
    build_ladder(wb)
    build_dashboard(wb)
    build_trade_log(wb)
    build_settings(wb)
    build_lists(wb)
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
