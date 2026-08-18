"""Assemble a daily market-close script with Eleven v3 audio tags.

Three rules shape everything below.

**The generator reports moves; it never invents causes.** It has prices, not
press releases, so no line here asserts *why* anything happened. That sounds
like a limitation and is actually the joke: financial media's house style is
confident post-hoc explanation, so the humour lives in refusing to supply one.
"Somebody will tell you why — whoever tells you fastest will be the least
sure" needs no facts beyond the move itself, and so cannot go stale, be wrong,
or quietly turn into defamation on a day this runs unattended.

**The straight beat never rotates.** Every other segment picks from a bank so
a week of episodes doesn't repeat itself, but the disclaimer is a fixed
string. A show that reads real price levels in a comic register needs one, and
the persona already has a slot where it drops the act — which makes the
compliance requirement and the writing want the same thing.

**A line break in the output is a beat.** v3 treats whitespace as timing, so
none of the banks below wrap for source readability; they use implicit string
concatenation instead. Where a break appears in a rendered script it is there
because somebody wanted the pause.

Rotation is seeded by the session date, so a given day always renders the same
script (re-runnable, reviewable, testable) while consecutive days differ.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date

from . import spoken
from .market import MarketFacts, Quote

# --------------------------------------------------------------------------
# rotation
# --------------------------------------------------------------------------


def pick(options: list[str], seed: date, salt: str) -> str:
    """Choose deterministically from a bank, keyed by session date.

    Hashing rather than ``random`` so the choice survives process restarts and
    Python versions: the same date always renders the same script.
    """
    if not options:
        raise ValueError("empty option bank")
    digest = hashlib.sha256(f"{seed.isoformat()}:{salt}".encode()).digest()
    return options[int.from_bytes(digest[:8], "big") % len(options)]


# --------------------------------------------------------------------------
# banks
# --------------------------------------------------------------------------

COLD_OPEN = {
    "up": [
        "Stocks went up today. [pause] We'll spend the next four minutes "
        "pretending we know why.",
        "Green across the board tonight. [pause] Somewhere, a strategist is "
        "quietly deleting a note.",
        "Stocks rose today, and the explanations have already been written. "
        "[pause] They were written last week.",
        "Good news tonight, if you own things. [pause] Which is more or less "
        "the entire business model.",
    ],
    "down": [
        "Stocks fell today. [pause] The reasons will arrive tomorrow, fully "
        "formed and very confident.",
        "A red session tonight. [pause] Nobody saw it coming — according to "
        "the people who said they saw it coming.",
        "Stocks dropped today. [pause] This is called a healthy correction, "
        "[pause] by people who are not selling.",
        "Down day. [pause] I'd remind everyone the market has recovered from "
        "a hundred percent of previous declines… [pause] so far.",
    ],
    "mixed": [
        "Stocks closed mixed today, which is the phrase we use when we have "
        "no idea what happened.",
        "A mixed session tonight. Some things up, some things down. [pause] "
        "Riveting.",
        "Stocks went sideways today. [pause] Four minutes. [pause] Let's see "
        "what I can do with that.",
        "Mixed close tonight. [pause] Which means every headline you read "
        "about today will be technically true.",
    ],
}

BREADTH_NARROW = [
    "Volume was light, breadth was narrow, and the move was carried by a "
    'handful of names. [pause] Which we\'re calling "broad-based." [pause] '
    "Because we always do.",
    "More names fell than rose. [pause] On a day the index finished higher. "
    "[pause] Try not to think about that one too hard.",
    "The advance was narrow. [pause] A few very large companies had a nice "
    "afternoon, and everybody else came along for the photograph.",
    "Breadth was poor, which is the part that doesn't make the headline "
    "[pause] because it doesn't fit in the headline.",
]

BREADTH_EVEN = [
    "Roughly as many names fell as rose. [pause] Which tomorrow's headline will "
    "call either a rally or a selloff, [pause] depending entirely on the "
    "headline.",
    "It was an even split. [pause] Half the market disagreed with the other "
    "half, and the index quietly averaged them into one number.",
    "Breadth was a coin flip. [pause] There is probably a lesson in that. "
    "[exhales] I'm not going to go looking for it.",
]

BREADTH_BROAD = [
    "Breadth was healthy — most names participated. [pause] That's rarer "
    "than it sounds, and nobody will mention it tomorrow.",
    "The move was broad. [pause] Genuinely broad. [exhales] I'm as surprised "
    "as you are.",
    "Most things went the same direction today, which we call conviction "
    "[pause] and which is usually just everyone reading the same screen.",
    "Participation was wide tonight. [pause] Enjoy it. [pause] It doesn't " "last.",
]

BREADTH_DECLINING = [
    "More fell than rose, which on a down day is at least internally "
    "consistent. [pause] Enjoy that. [pause] It's the only consistency you're "
    "getting tonight.",
    "The decline was broad. [pause] Nowhere to hide, as people say — [pause] "
    "usually right before telling you where to hide.",
    "Most things went down. [pause] I'd like to tell you it was orderly. "
    "[exhales] It was just uniform.",
    "That one was wide. [pause] It wasn't a rotation, it wasn't a reallocation, "
    "[pause] it was a Tuesday.",
]

BREADTH_DIVERGENT = [
    "More names rose than fell — [pause] on a day the index finished lower. "
    "[pause] A few very large companies had a bad afternoon and took the "
    "average down with them.",
    "The index fell. [pause] Most stocks didn't. [pause] Which tells you "
    "roughly what the index is these days.",
    "Most of the market went up today, and the number on the screen went down. "
    "[pause] Both of those are true, [pause] which is the problem with the "
    "number on the screen.",
]

BREADTH_BANKS = {
    "narrow": BREADTH_NARROW,
    "advancing": BREADTH_BROAD,
    "declining": BREADTH_DECLINING,
    "divergent": BREADTH_DIVERGENT,
    "even": BREADTH_EVEN,
}

# Is today's move actually large? Every outlet reports the number; none of them
# say whether it is big for this market this month. That comparison is the one
# thing here a viewer cannot get from the news, so it leads the tape.
SCALE_QUIET = [
    "Which sounds like something. [pause] It isn't. [pause] This market moves "
    "about that much on an ordinary day, just clearing its throat.",
    "That is a nothing move. [pause] Smaller than a normal session. [exhales] "
    "Nobody is going to report it that way.",
    "Below average, for this month. [pause] A quiet day wearing a headline.",
    "That is less than this market does on a typical day. [pause] Which makes "
    "it, technically, not news. [starts laughing] And yet here I am.",
]

SCALE_ORDINARY = [
    "Which is about a normal day for this market. [pause] Not a story. "
    "[pause] Just the market being open.",
    "About average. [exhales] The least reportable thing that can happen, "
    "[pause] and the thing that happens most.",
    "Ordinary. [pause] Genuinely, boringly ordinary. [pause] I checked.",
]

# ``{multiple}`` substitutes mid-sentence, ``{Multiple}`` at a sentence start.
# Explicit rather than inferred: guessing from the preceding characters would
# have to reason about audio tags, and getting it wrong reads as a typo.
SCALE_NOTABLE = [
    "That one is bigger than usual — {multiple} what this market does on a "
    "normal day. [pause] Worth noticing, which is more than most sessions "
    "manage.",
    "Above average. [pause] {Multiple} a typical day. [pause] Not dramatic. "
    "[pause] But real.",
]

SCALE_BIG = [
    "That is a real move. [pause] {Multiple} what this market does on an "
    "ordinary day.",
    "That one counts. [pause] {Multiple} a normal session. [pause] Rare enough "
    "to remember, [pause] and rare enough that everybody will over-explain it "
    "by morning.",
]

SCALE_BANKS = {
    "quiet": SCALE_QUIET,
    "ordinary": SCALE_ORDINARY,
    "notable": SCALE_NOTABLE,
    "big": SCALE_BIG,
}

GAINER_JOKES = [
    "[pause] By tomorrow morning there will be nine explanations for that, "
    "[pause] all written by people who did not own it yesterday.",
    "[pause] Somebody will tell you why. [pause] Whoever tells you fastest "
    "will be the least sure.",
    "[pause] The company has said nothing at all. [pause] This has slowed "
    "nobody down.",
    "[pause] [sarcastic] And of course everyone saw that coming. [pause] " "Obviously.",
]

LOSER_JOKES = [
    "[pause] The stock has an opinion. [pause] The press release will not.",
    "[pause] Nobody rings a bell at the top. [pause] They do, however, issue "
    "a statement about eleven hours later.",
    "[pause] I'm told there's a reason. [exhales] There's always a reason, "
    "and it always arrives after the move.",
    "[pause] If you held that today — [pause] I'm sorry. [pause] And also: "
    "position sizing.",
]

# Split by size: "a move of approximately nothing" is a good joke on three
# basis points and a wrong one on twenty.
RATE_JOKES_SMALL = [
    "[pause] That is a move of approximately nothing, [pause] which will not "
    "stop anybody writing four hundred words about it.",
    "[pause] Rates went essentially nowhere. [exhales] I've stopped asking.",
    "[pause] Traders will read something into that. [pause] Traders read "
    "something into EVERYTHING.",
]

RATE_JOKES_LARGE = [
    "[pause] The bond market spent the afternoon disagreeing with the stock "
    "market. [pause] One of them is wrong. [pause] Historically, it is not "
    "the bond market.",
    "[pause] That is a real move, [pause] and the people explaining it to you "
    "tonight will not agree with each other by morning.",
    "[pause] Somebody repriced something. [exhales] We'll find out what in "
    "about a week.",
]

CRUDE_JOKES = [
    '[pause] Analysts cited "demand concerns." [pause] There are always '
    "demand concerns. [pause] It's oil. [pause] Somebody is always concerned.",
    "[pause] They cite supply when it rises and demand when it falls, "
    "[pause] and nobody has ever once made them pick.",
    "[pause] Oil moved. [pause] Somewhere a very serious man is drawing a "
    "triangle on a chart about it.",
]

CRYPTO_JOKES = [
    "[pause] It was large, it happened quickly, and by the time this airs it "
    "will have happened again in the other direction.",
    "[pause] I am contractually obliged to tell you the number, [pause] and "
    "spiritually obliged to tell you it will be different by breakfast.",
    "[exhales] I have no further analysis. [pause] I'm not certain anybody " "does.",
]

# Closers. Each states the show's proposition and then undercuts it, so the
# call to action never has to be begged for.
SIGN_OFF = [
    "[pause] Tomorrow I'll tell you whether tomorrow was real, [pause] or just "
    "more breathing.\n[exhales] Most days it's breathing.",
    "[pause] Same time tomorrow, [pause] where I will read you different "
    "numbers in an identical tone of voice.",
    "[pause] Nothing that happened today will matter in ten years — [pause] "
    "which is either comforting or upsetting, [pause] depending entirely on "
    "your time horizon.",
    "[pause] Go and do something that isn't this. [exhales] The screen will "
    "still be here, [pause] being wrong at you.",
]

# The ask. Never "smash that subscribe" — each of these states what the show is
# for and then declines to say the word, which is the joke and the pitch at once.
CALL_TO_ACTION = [
    "[pause] If you'd rather know which days matter than hear about all of "
    "them — [starts laughing] you know where the button is. [pause] I'm not "
    "going to say it. [pause] We both know.",
    "[pause] Follow along if you like being told when nothing happened. "
    "[exhales] It's a small market, that one. [starts laughing] I'm building "
    "it anyway.",
    "[pause] There's one of these every weekday. [pause] You know how this "
    "works. [starts laughing] I'm not doing the voice.",
]

# Fixed. See the module docstring: this is the one block that never rotates.
#
# Note what it does NOT say: "because this actually matters". Announcing that
# something is important is the tell of copy written to sound sincere rather
# than to be it. The weight here comes from [sighs], the short sentences and
# the flat delivery — the performance carries it, not a label on top.
STRAIGHT_BEAT = """[sighs] One straight thing, then I'll go.

None of this is advice. I'm not an advisor. I'm a bloke reading a data feed out
loud.
[pause] If you've got real money on the line, two things survive contact with
reality. [pause] How much you put in. [pause] And how long you leave it.
[pause] Everything else — [pause] me very much included — [pause] is noise with
a personality."""

# The handoff out of the opening story and into the market. Fixed: the story
# above it is different every night, so the seam has to be the same every night
# or the show has no recognisable shape at all.
COLD_OPEN_HANDOFF = "[pause] I'm {anchor}. [pause] Let's look at today."


GAIN_VERBS = ("closed up", "gained", "added", "advanced")
LOSS_VERBS = ("closed down", "slipped", "shed", "gave up")

# Below this, a yield move is noise rather than news.
QUIET_RATE_MOVE_BP = 5.0


@dataclass
class ScriptOptions:
    # Spelled for the voice, not the channel art. The screen name is
    # "Toadchu Yall"; v3 has to be handed the apostrophe or it guesses at
    # "Yall", and a host who fumbles his own name in the opening seconds is
    # finished. Same rule as spoken.py applies to numbers.
    anchor: str = "Toadchu Y'all"
    show: str = "the Market Close"
    kicker: str | None = None


def _capitalize(text: str) -> str:
    """Upper-case the opening letter without touching the rest.

    ``str.capitalize`` would lower-case "S and P"; index names arrive
    sentence-cased for mid-sentence use and only the first one needs lifting.
    """
    return text[:1].upper() + text[1:] if text else text


# --------------------------------------------------------------------------
# segments
# --------------------------------------------------------------------------


def _index_clause(quote: Quote, position: int) -> str:
    """One index's move, in the unit that index is actually quoted in."""
    pct = quote.percent_change
    if abs(pct) < 0.05:
        return f"{quote.name} finished essentially flat"

    verbs = GAIN_VERBS if pct > 0 else LOSS_VERBS
    verb = verbs[position % len(verbs)]

    # The Dow is read in points on air; everything else in percent.
    if quote.symbol == "INDU":
        return f"{quote.name} {verb} {spoken.say_points(quote.point_change)}"

    magnitude = spoken.say_percent(pct)
    # An anchor states the unit once and then drops it: "the S and P closed up
    # six tenths of a percent. The Nasdaq gained nine tenths."
    if position > 0 and magnitude.endswith(" of a percent"):
        magnitude = magnitude[: -len(" of a percent")]
    return f"{quote.name} {verb} {magnitude}"


def cold_open(facts: MarketFacts, options: ScriptOptions) -> str:
    """Open on the human story, then hand off to the market.

    The personal beat used to close the show, after four minutes of numbers
    nobody had a reason to sit through. It opens now because it is the only
    part of the broadcast a stranger has any reason to care about in the first
    ten seconds — and because a story about being wrong hands straight over to
    a show whose whole thesis is that nobody knows why anything moved.

    Falls back to the rotating market-shaped opener when no story is supplied,
    so an unattended run still produces something sayable.
    """
    handoff = COLD_OPEN_HANDOFF.format(anchor=options.anchor)

    if options.kicker:
        return f"[COLD OPEN]\n\n{options.kicker.strip()}\n\n{handoff}"

    line = pick(COLD_OPEN[facts.direction], facts.session_date, "cold-open")
    return f"[COLD OPEN]\n\n{line}\n\n{handoff}"


def scale_line(quote: Quote, session: date) -> str | None:
    """Say whether today's move was large, measured against a normal day.

    ``None`` when there was not enough history to compute a baseline — the one
    line in this show that must never be a guess, since its whole value is that
    it is the number nobody else reports.
    """
    scale = quote.move_scale
    if scale is None:
        return None

    line = pick(SCALE_BANKS[scale], session, "scale")
    multiple = spoken.say_multiple(quote.move_ratio)
    # ``replace`` rather than ``format``: a stray brace in a bank line should
    # never raise on a live run.
    return line.replace("{multiple}", multiple).replace(
        "{Multiple}", _capitalize(multiple)
    )


def tape(facts: MarketFacts, counts: bool = False) -> str | None:
    """The session's move, what its size means, and how wide it was.

    ``counts`` adds the raw advancer/decliner tally. Off by default: the
    breadth *line* already says which way the market leaned and what that is
    worth, and "thirteen names rose, twenty-seven fell" is the recitation the
    interpretation was written to replace. Every bank line stands on its own
    without the numbers in front of it.
    """
    if not facts.indices:
        return None

    clauses = [
        _index_clause(quote, position) for position, quote in enumerate(facts.indices)
    ]
    body = ". ".join(_capitalize(clause) for clause in clauses) + "."

    # Led by the first index: that one is "the market" for this purpose.
    scale = scale_line(facts.indices[0], facts.session_date)
    if scale:
        body = f"{body}\n[pause] {scale}"

    state = facts.breadth_state
    if state is None:
        # Too few names to characterise breadth. Drop the line rather than
        # assert something the data doesn't support.
        return f"[THE TAPE]\n\n{body}"

    tally = ""
    if counts:
        tally = (
            f"[pause] {_capitalize(spoken.int_to_words(facts.advancers))} names "
            f"rose, {spoken.int_to_words(facts.decliners)} fell.\n"
        )
    joke = pick(BREADTH_BANKS[state], facts.session_date, "breadth")

    return f"[THE TAPE]\n\n{body}\n{tally}[pause] {joke}"


def _gainer_block(quote: Quote, session: date) -> str:
    joke = pick(GAINER_JOKES, session, "gainer")
    return (
        f"Shares of {quote.name} led the tape, up "
        f"{spoken.say_percent(quote.percent_change)}, closing at "
        f"{spoken.say_dollars(quote.close)}.\n{joke}"
    )


def _loser_block(quote: Quote, session: date, lead: bool) -> str:
    joke = pick(LOSER_JOKES, session, "loser")
    # "Going the other way" needs something to be the other way from; standing
    # alone it opens the segment mid-thought.
    opening = (
        f"The biggest move today went the wrong way. {quote.name} finished down"
        if lead
        else f"Going the other way — {quote.name} finished down"
    )
    return (
        f"{opening} {spoken.say_percent(quote.percent_change)}, at "
        f"{spoken.say_dollars(quote.close)}.\n{joke}"
    )


def movers(facts: MarketFacts, both: bool = False) -> str | None:
    """The session's biggest move, or both extremes when ``both``.

    One name by default. A gainer *and* a loser every night is a format rather
    than a reason — it fills the same twenty seconds whether or not either move
    was worth mentioning. The larger of the two is the actual story, so that is
    what gets said, and the segment halves.
    """
    if facts.gainer is None and facts.loser is None:
        return None

    if both:
        blocks = []
        if facts.gainer is not None:
            blocks.append(_gainer_block(facts.gainer, facts.session_date))
        if facts.loser is not None:
            blocks.append(
                _loser_block(facts.loser, facts.session_date, lead=not blocks)
            )
        return "[MOVERS]\n\n" + "\n\n".join(blocks)

    if facts.loser is None:
        lead_gainer = True
    elif facts.gainer is None:
        lead_gainer = False
    else:
        lead_gainer = abs(facts.gainer.percent_change) >= abs(
            facts.loser.percent_change
        )

    block = (
        _gainer_block(facts.gainer, facts.session_date)
        if lead_gainer
        else _loser_block(facts.loser, facts.session_date, lead=True)
    )
    return f"[MOVERS]\n\n{block}"


def rates(facts: MarketFacts) -> str | None:
    if facts.rate is None:
        return None

    # Bond data arrives as a yield in percent, so a "point change" here is
    # percentage points and a basis point is a hundredth of one.
    basis_points = facts.rate.point_change * 100.0
    quiet = abs(basis_points) < QUIET_RATE_MOVE_BP

    if abs(basis_points) < 0.5:
        movement = f"was effectively unchanged, at {spoken.say_yield(facts.rate.close)}"
    else:
        direction = "eased" if basis_points < 0 else "rose"
        movement = (
            f"{direction} {spoken.say_basis_points(basis_points)} "
            f"to {spoken.say_yield(facts.rate.close)}"
        )

    bank = RATE_JOKES_SMALL if quiet else RATE_JOKES_LARGE
    joke = pick(bank, facts.session_date, "rates")
    return f"[RATES]\n\nTo the bond market. The ten-year yield {movement}.\n{joke}"


def commodities(facts: MarketFacts) -> str | None:
    if facts.crude is None and facts.crypto is None:
        return None

    blocks = []

    if facts.crude is not None:
        joke = pick(CRUDE_JOKES, facts.session_date, "crude")
        move = "up" if facts.crude.percent_change >= 0 else "down"
        blocks.append(
            f"Crude settled at {spoken.say_dollars(facts.crude.close)} a barrel, "
            f"{move} {spoken.say_percent(facts.crude.percent_change)}.\n{joke}"
        )

    if facts.crypto is not None:
        joke = pick(CRYPTO_JOKES, facts.session_date, "crypto")
        move = "higher" if facts.crypto.percent_change >= 0 else "lower"
        blocks.append(
            f"And Bitcoin — [exhales] Bitcoin went {move}, "
            f"{spoken.say_percent(facts.crypto.percent_change)}, to "
            f"{spoken.say_dollars(facts.crypto.close)}.\n{joke}"
        )

    return "[COMMODITIES]\n\n" + "\n\n".join(blocks)


def straight_beat() -> str:
    return f"[STRAIGHT]\n\n{STRAIGHT_BEAT}"


def sign_off(facts: MarketFacts, options: ScriptOptions) -> str:
    line = pick(SIGN_OFF, facts.session_date, "sign-off")
    ask = pick(CALL_TO_ACTION, facts.session_date, "cta")
    return f"[SIGN-OFF]\n\n{line}\n\n{ask}\n\n[pause] {options.anchor}. See you after the bell."


def render(
    facts: MarketFacts,
    options: ScriptOptions | None = None,
    full: bool = False,
) -> str:
    """Build the script. Segments with no data are dropped, not faked.

    Five segments by default, running under two minutes: the story, the tape,
    one mover, the disclaimer, the ask.

    ``full`` is the long version, and everything it adds is something a viewer
    who came for a market read did not ask for — a bond quote, an oil quote, a
    Bitcoin quote, a second single-stock move, and the raw advancer/decliner
    tally. That density is the information overload that makes every one of
    these channels skippable; cutting it is what buys the attention the rest of
    the script needs.
    """
    options = options or ScriptOptions()

    segments = [
        cold_open(facts, options),
        tape(facts, counts=full),
        movers(facts, both=full),
    ]
    if full:
        segments += [rates(facts), commodities(facts)]
    segments += [straight_beat(), sign_off(facts, options)]

    return "\n\n\n".join(segment for segment in segments if segment) + "\n"


def preview(facts: MarketFacts) -> str:
    """Just the tape and the movers — the segments carrying the figures.

    What is worth checking before you spend renders is the numbers, and nearly
    all of them live in these two blocks: index levels, breadth counts, two
    percentage moves and two closing prices. The jokes are the same either way,
    so a preview that dropped them would read differently from what ships;
    these are whole segments, exactly as they will be performed.

    Empty when neither segment has data, which the caller should treat as
    nothing to show rather than an empty script.
    """
    blocks = [block for block in (tape(facts), movers(facts)) if block]
    return "\n\n\n".join(blocks) + "\n" if blocks else ""


def split_segments(text: str) -> list[tuple[str, str]]:
    """Split a rendered script into ``(name, body)`` pairs.

    v3 holds a performance together better across a few sentences than across
    a whole broadcast, and a re-roll should cost one segment rather than the
    night's work — so the render workflow is segment-by-segment, and this is
    what feeds it.
    """
    pairs: list[tuple[str, str]] = []
    for block in text.split("\n\n\n"):
        block = block.strip()
        if not block:
            continue
        header, _, body = block.partition("\n")
        name = header.strip().strip("[]").lower().replace(" ", "-")
        pairs.append((name, body.strip()))
    return pairs
