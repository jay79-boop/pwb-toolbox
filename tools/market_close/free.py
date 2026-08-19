"""Credential-free market data, straight from yfinance.

``pwb_toolbox.datasets`` falls back to yfinance for only four dataset families —
stocks, ETFs, crypto and forex — and never for indices, bonds or commodities. So
without a ``PWB_API_KEY`` or an ``HF_ACCESS_TOKEN`` the tape, the rates line and
crude have no free path through ``load_dataset`` at all, and the movers segment
that does work costs one full-history request per symbol.

This module goes to Yahoo directly instead: proxies that Yahoo serves to anybody,
pulled in a single batched request over a short window rather than a
symbol-at-a-time loop over all history.

**The proxies are named for what they are.** SPY is not the S&P 500 — it tracks
it, and on any given day the two differ by a basis point or two. The script says
"the S and P five hundred E T F" rather than quoting a fund's move as the index's,
which is the same rule the rest of this tool runs on: report what you measured.
Free mode is a cheaper seat, and it says so out loud rather than pretending.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from .market import (
    COMPANY_NAMES,
    MarketFacts,
    Quote,
    breadth,
    latest_changes,
    movers,
    session_is_open,
    typical_moves,
)

# Yahoo tickers, paired with the name an anchor would say. The "E T F" spacing
# is for the voice model — see spoken.say_ticker.
FREE_INDEX_NAMES = {
    "SPY": "the S and P five hundred E T F",
    "QQQ": "the Nasdaq one hundred E T F",
    "DIA": "the Dow E T F",
}

FREE_INDICES = tuple(FREE_INDEX_NAMES)

# ^TNX is Yahoo's ten-year yield index; CL=F is WTI front-month, which is the
# actual barrel price the script quotes rather than an oil fund's NAV.
FREE_RATE_SYMBOL = "^TNX"
FREE_CRUDE_SYMBOL = "CL=F"
FREE_CRYPTO_SYMBOL = "BTC-USD"

# Forty names rather than five hundred. Breadth needs twenty-plus to be
# characterised at all (see MarketFacts.breadth_state) and every one of these
# already has a spoken name in COMPANY_NAMES, so movers stays pronounceable.
FREE_UNIVERSE = (
    "AAPL",
    "ABBV",
    "AMD",
    "AMZN",
    "AVGO",
    "BA",
    "BAC",
    "CAT",
    "CRM",
    "CSCO",
    "CVX",
    "DIS",
    "GE",
    "GOOGL",
    "GS",
    "HD",
    "IBM",
    "INTC",
    "JNJ",
    "JPM",
    "KO",
    "LLY",
    "MA",
    "META",
    "MRK",
    "MSFT",
    "MU",
    "NFLX",
    "NKE",
    "NVDA",
    "ORCL",
    "PEP",
    "PFE",
    "PG",
    "QCOM",
    "TSLA",
    "UNH",
    "V",
    "WMT",
    "XOM",
)

# The reducers only ever want the last two observations, but a month is the
# smallest window that comfortably survives a long weekend, a market holiday and
# a thinly-traded symbol that skipped a print.
DEFAULT_PERIOD = "1mo"

# Yahoo has quoted ^TNX both as a percentage (4.09) and as tenths of a percent
# (40.9) over the years. No real ten-year yield reaches twenty, so a close above
# that is the older convention rather than a genuine rate.
_TNX_TENTHS_THRESHOLD = 20.0


def _default_downloader(symbols: list[str], period: str) -> pd.DataFrame:
    import yfinance as yf

    return yf.download(
        symbols,
        period=period,
        auto_adjust=False,
        progress=False,
        threads=True,
    )


def to_long_frame(raw: pd.DataFrame, symbols: list[str]) -> pd.DataFrame:
    """Reduce a yfinance download to the ``date``/``symbol``/``close`` shape.

    That is the same shape ``load_dataset`` returns, so every reducer in
    ``market.py`` works on it unchanged — free mode swaps the source, not the
    pipeline.
    """
    if raw is None or raw.empty:
        return pd.DataFrame(columns=["date", "symbol", "close"])

    if isinstance(raw.columns, pd.MultiIndex):
        if "Close" not in raw.columns.get_level_values(0):
            return pd.DataFrame(columns=["date", "symbol", "close"])
        close = raw["Close"]
    else:
        # Single-symbol downloads come back with flat columns.
        if "Close" not in raw.columns:
            return pd.DataFrame(columns=["date", "symbol", "close"])
        close = raw[["Close"]].rename(columns={"Close": symbols[0]})

    # Name the index rather than trusting yfinance to; an unnamed one reset_index
    # calls "index", and the melt below would look for a column that isn't there.
    close = close.copy()
    close.index.name = "date"

    frame = (
        close.reset_index()
        .melt(id_vars="date", var_name="symbol", value_name="close")
        .dropna(subset=["close"])
    )
    frame["date"] = pd.to_datetime(frame["date"]).dt.date
    frame["symbol"] = frame["symbol"].astype(str)
    return frame[["date", "symbol", "close"]].sort_values(["symbol", "date"])


def fetch(symbols, period: str = DEFAULT_PERIOD, downloader=None) -> pd.DataFrame:
    """Download the symbols in one batched request, as a long frame."""
    symbols = list(symbols)
    if not symbols:
        return pd.DataFrame(columns=["date", "symbol", "close"])
    download = downloader or _default_downloader
    return to_long_frame(download(symbols, period), symbols)


def _quote_from(frame: pd.DataFrame, symbol: str, name: str) -> Quote | None:
    changes = latest_changes(frame)
    row = changes[changes["symbol"] == symbol]
    if row.empty:
        return None
    row = row.iloc[0]
    return Quote(
        symbol=symbol,
        name=name,
        close=float(row["close"]),
        previous_close=float(row["previous_close"]),
    )


def normalize_tnx(quote: Quote | None) -> Quote | None:
    """Put ^TNX on a percent footing regardless of which convention Yahoo used."""
    if quote is None or quote.close < _TNX_TENTHS_THRESHOLD:
        return quote
    return Quote(
        symbol=quote.symbol,
        name=quote.name,
        close=quote.close / 10.0,
        previous_close=quote.previous_close / 10.0,
    )


def collect_free(
    period: str = DEFAULT_PERIOD,
    names: dict[str, str] | None = None,
    downloader=None,
) -> MarketFacts:
    """Build :class:`MarketFacts` from Yahoo alone — no key, no login.

    One request for the index proxies, rate, crude and crypto; a second for the
    movers universe. A symbol Yahoo declines to serve costs its segment, not the
    broadcast.
    """
    facts = MarketFacts(session_date=date.today())

    market_symbols = [
        *FREE_INDICES,
        FREE_RATE_SYMBOL,
        FREE_CRUDE_SYMBOL,
        FREE_CRYPTO_SYMBOL,
    ]

    try:
        frame = fetch(market_symbols, period, downloader)
    except Exception as exc:  # noqa: BLE001 - a dead feed is not fatal
        print(f"warning: skipping index and macro data ({exc})")
        frame = pd.DataFrame(columns=["date", "symbol", "close"])

    if not frame.empty:
        # DEFAULT_PERIOD is a month precisely so this baseline has something to
        # average over; the reducers themselves only want the last two closes.
        baselines = typical_moves(frame)
        found = []
        for symbol in FREE_INDICES:
            quote = _quote_from(frame, symbol, FREE_INDEX_NAMES[symbol])
            if quote is None:
                continue
            quote.typical_move = baselines.get(symbol)
            found.append(quote)
        facts.indices = found

        latest = pd.to_datetime(frame["date"]).max()
        if not pd.isna(latest):
            facts.session_date = latest.date()
            facts.session_open = session_is_open(facts.session_date)

        facts.rate = normalize_tnx(_quote_from(frame, FREE_RATE_SYMBOL, "the ten-year"))
        facts.crude = _quote_from(frame, FREE_CRUDE_SYMBOL, "crude")
        facts.crypto = _quote_from(frame, FREE_CRYPTO_SYMBOL, "Bitcoin")

    try:
        stocks = fetch(FREE_UNIVERSE, period, downloader)
    except Exception as exc:  # noqa: BLE001
        print(f"warning: skipping movers ({exc})")
        stocks = pd.DataFrame(columns=["date", "symbol", "close"])

    if not stocks.empty:
        table = {**COMPANY_NAMES, **(names or {})}
        facts.gainer, facts.loser = movers(stocks, table)
        facts.advancers, facts.decliners = breadth(stocks)

    return facts
