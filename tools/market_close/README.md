# market_close

Generates a daily market-close broadcast script for a text-to-speech talking-head
avatar. It reads a session out of `pwb_toolbox.datasets`, reduces it to facts, and
renders a script with Eleven v3 audio tags — with every number already spelled the
way it should be spoken.

```bash
python -m tools.market_close --demo                        # canned session, no credentials
python -m tools.market_close --free --preview              # live data, no credentials
python -m tools.market_close --preview                     # tape and movers only
python -m tools.market_close --kicker-file story.txt --out close.txt
python -m tools.market_close --full                         # adds rates and commodities
python -m tools.market_close --segments render/            # one file per block
```

## Credentials, and doing without them

By default the data comes from `pwb_toolbox.datasets`, which wants a `PWB_API_KEY`
or an `HF_ACCESS_TOKEN`. Without one, `load_dataset` falls back to yfinance for only
four dataset families — stocks, ETFs, crypto and forex — so the tape, the rates line
and crude have no free path at all, and movers costs one full-history request per
symbol.

`--free` skips that layer and goes to Yahoo directly, in two batched requests:

| segment | default source | `--free` source |
|---|---|---|
| tape | SPX, CCMP, INDU | SPY, QQQ, DIA |
| movers | S&P 500 | 40 mega caps |
| rates | US10Y | `^TNX` |
| crude | CL1 | `CL=F` |
| bitcoin | BTC | `BTC-USD` |

**Free mode names the proxies for what they are.** SPY is not the S&P 500 — it tracks
it, and on a given day the two differ by a basis point or two, so the script says "the
S and P five hundred E T F" rather than quoting a fund's move as the index's. Same rule
as everywhere else here: report what you measured. It's a cheaper seat and it says so.

## The daily loop

1. `python -m tools.market_close --preview` — check the figures read correctly
   before committing to anything.
2. Write the opening story into `story.txt`. One human-scale thing that actually
   happened to you, no numbers, landing on `[starts laughing]`.
3. `python -m tools.market_close --kicker-file story.txt --segments render/`
4. Paste `render/01-…` through `render/05-…` into ElevenLabs Text to Speech one at a
   time, on **Eleven v3**, stability **Natural**.
5. Stitch the clips, drop the track onto a HeyGen avatar as uploaded audio.

## Five segments, in this order

| # | segment | what it is |
|---|---------|------------|
| 1 | `[COLD OPEN]` | your story, then a fixed handoff into the market |
| 2 | `[THE TAPE]` | the move, what it means, and how wide it was |
| 3 | `[MOVERS]` | the day's biggest single move, and the heuristic behind it |
| 4 | `[STRAIGHT]` | the disclaimer |
| 5 | `[SIGN-OFF]` | the proposition, then the ask |

Three decisions in that shape are worth stating, because each is the opposite of
what a newscast does.

**The story opens the show.** It used to close it, after four minutes of numbers
nobody had a reason to sit through. It leads now because it is the only part of the
broadcast a stranger has any reason to care about in the first ten seconds — and
because a story about being confidently wrong hands straight over to a show whose
whole thesis is that nobody knows why anything moved.

**`--full` is the long version, and everything it adds is unasked for.** A bond quote,
an oil quote, a Bitcoin quote, and a second single-stock move, and the raw advancer/decliner tally. That density is the
information overload that makes every one of these channels skippable; cutting it is
what buys the attention the rest of the script needs.

Two of those cuts are worth spelling out. A gainer *and* a loser every night is a
format rather than a reason — it fills the same twenty seconds whether or not either
move was worth mentioning, so only the larger one gets said. And the advancer/decliner
tally is exactly the recitation the breadth line was written to replace: "thirteen
names rose, twenty-seven fell" tells you nothing that "most things went down" does not,
and costs eight seconds to say.

**Nothing announces how long it will take.** Naming a duration turns the video into a
commitment the viewer has to weigh before pressing play, and "here's what I'll cover
in the next four minutes" spends the time it is describing.

`--preview` earns its place at the top of that loop because the tape and movers carry
nearly every number in the broadcast — index levels, breadth counts, two percentage
moves, two closing prices — and they change most between sessions, so an unfamiliar
ticker spelling or an odd-sounding level surfaces there first. It prints those two
blocks exactly as they will be performed, jokes included, so what you audition is
what ships. It exits non-zero when there is nothing to show, which makes it usable
as a guard in a scheduled run.

Rendering segment by segment is not fussiness either. v3 holds a performance together
better across a few sentences than across a whole broadcast, and a bad take on the
opening story should cost you one re-roll rather than the night's work.

## Noise or news

The one number here a viewer cannot get anywhere else.

Every outlet reports that the index fell half a percent. None of them say whether
half a percent is *large for this market this month* — which is the only part that
tells you whether to care. So the tape leads with that comparison:

> The S and P five hundred E T F closed down half a percent.
> [pause] Which sounds like something. [pause] It isn't. [pause] This market moves
> about that much on an ordinary day, just clearing its throat.

`typical_moves()` computes the baseline: the mean absolute daily percent change over
the trailing twenty sessions. Today is measured against it and lands in one of four
bands — `quiet`, `ordinary`, `notable`, `big` — each with its own bank.

Three choices in that calculation are deliberate:

- **Today is excluded from its own baseline.** Otherwise a large session inflates the
  average it is being judged against, and the days most worth flagging are exactly
  the ones the show would understate.
- **Mean, not standard deviation.** The claim is about a typical day, not about a
  distribution, and one outlier moves a standard deviation far more than an average.
- **Fewer than ten usable observations and the line is dropped.** Same rule as breadth:
  say nothing rather than characterise a month you cannot see.

The spoken multiple stays coarse — "about twice", "about three times", never "two
point three times". A decimal would imply a precision twenty sessions don't support.

**`ordinary` means "typical", never "nothing".** The two are the same only in a calm
month. A one percent drop can be perfectly average for a volatile market and still be
the most interesting fact on offer, so the band's lines report the measured baseline
instead of waving the day away:

> About average — [pause] and average around here is nine tenths of a percent a day,
> at the moment. [pause] Make of that what you like. [starts laughing] I've stopped.

A test forbids "not a story", "least reportable" and "nothing" from ever appearing in
that bank.

## Writing before the bell

Writing the script and recording it both happen before the close — you need the words
in hand to render them. So "the S and P five hundred **closed** down one percent" is
false at eleven in the morning, and it is the one claim in the show a viewer could
catch outright.

`session_is_open()` compares the latest bar's date against the clock in New York. When
the session is still running, every verb switches to the present:

| after the bell | still open |
|---|---|
| "closed up six tenths of a percent" | "is up six tenths of a percent" |
| "led the tape, up fourteen percent, closing at…" | "are leading the tape, up fourteen percent, at…" |
| "The biggest move today went the wrong way" | "The biggest move so far has gone the wrong way" |
| "the ten-year yield eased three basis points" | "the ten-year yield has eased three basis points" |
| "Crude settled at seventy-one dollars" | "Crude is at seventy-one dollars" |

The CLI also says so on stderr, because the figures will all have moved by the close.

Timezone detection needs an IANA database, which Windows does not ship; `zoneinfo`
finds one there only because pandas pulls in `tzdata`. If that ever stops being true,
`_now_eastern()` returns `None` and the check falls back to comparing dates alone
rather than raising.

Two smaller things still read as past tense mid-session: the breadth banks ("the
advance was narrow") and the fallback cold open used when no story is supplied. Both
describe what has happened so far, which is true either way — unlike "closed", which
names an event.

## Why there are no digits in the output

ElevenLabs reads numerals by its own rules, and on a markets script that is most of
the runtime: `4.09` comes back as "four point zero nine" when the desk says
"four-oh-nine", `&` is a coin flip, `$71.40` invites "dollar seventy-one point four".

So `spoken.py` spells everything before it reaches the page, in broadcast idiom
rather than arithmetic — "six tenths of a percent", not "zero point six percent";
"a hundred and forty points", not "one hundred forty". A test asserts the rendered
script contains no digit at all, and the CLI warns if one survives, which in
practice means you typed one into the kicker.

## Why it never says why

The generator has prices. It does not have press releases, and it will not pretend
to: no line asserts a *cause* for any move. This matters more the more automated the
show gets — a template that fills in "after the company beat expectations" is a
template that will eventually broadcast something false about a real company, on a
day nobody is reading the output.

It also happens to be funnier. Financial media's house style *is* confident post-hoc
explanation, so the joke writes itself out of the refusal:

> Shares of Nvidia led the tape, up fourteen percent.
> [pause] Somebody will tell you why. [pause] Whoever tells you fastest will be the
> least sure.

That needs no facts beyond the move, cannot go stale, and cannot become defamation.

## The straight beat

One segment never rotates: the disclaimer. A show that reads real price levels in a
comic register needs one, and burying it in on-screen small print is the version
nobody hears. The persona already has a slot where it drops the act for fifteen
seconds — putting it there means the compliance requirement and the writing want the
same thing, and the disclaimer lands as the most sincere moment in the episode.

If you change one string in this package, don't let it be that one.

## Rotation

Every other segment picks from a bank of three or four lines, seeded by a hash of
the session date. So a given day always renders the same script — re-runnable,
reviewable, diffable — while a working week doesn't repeat itself. The jokes live in
the transitions rather than in the numbers, which is what lets fresh data drop into
the same skeleton without the comedy going with it.

Add lines to the banks in `script.py` freely; they're plain lists. Keep them as
single strings — a line break in the output is a beat, so the banks use implicit
string concatenation rather than wrapping for source readability.

## Options

| flag | effect |
|------|--------|
| `--demo` | canned session; no network, no `PWB_API_KEY` |
| `--free` | live data from Yahoo; no API key or login needed |
| `--date YYYY-MM-DD` | override the session date, which also reseeds the rotation |
| `--kicker-file PATH` | hand-written story that opens the show |
| `--full` | the long version: rates, commodities, a second mover, the tally |
| `--names PATH` | JSON `{"TICKER": "spoken name"}`, merged over the built-ins |
| `--anchor`, `--show` | rename the anchor and the programme |
| `--preview` | tape and movers only; exits `1` when neither has data |
| `--out PATH` | write the script (default: stdout) |
| `--segments DIR` | also write one numbered file per block, in render order |

`COMPANY_NAMES` in `market.py` covers about sixty large caps. Anything absent gets
its ticker spelled out — "Z Z Z Z" — which is also how a desk reads an unfamiliar
one, but `--names` is there for when you want fuller coverage.

## Layout

- `spoken.py` — numbers to broadcast English. No dependencies, heavily tested.
- `market.py` — dataset loading and reduction. `collect()` is the only function that
  touches the network; everything else is pure and takes a DataFrame, which is what
  keeps the suite offline.
- `script.py` — the template, the joke banks, and the rotation.
- `cli.py` — argument handling and the digit check.
