# Reading an image with a hosted vision model

`tools/nvidia_vision.py` sends an image and a question to a vision model on
NVIDIA's hosted API (`integrate.api.nvidia.com`) and returns the answer. It
takes a local file, an `http(s)` URL, or a chart it renders itself off bar data.

    python tools/nvidia_vision.py ask chart.png --prompt "What is in this image?"
    python tools/nvidia_vision.py ask https://example.com/x.jpg --stream
    python tools/nvidia_vision.py chart NQ=F --keep nq.png
    python tools/nvidia_vision.py models --filter kimi

The key comes from `NVIDIA_API_KEY` in the environment (`.env.example` lists
it). Get one at <https://build.nvidia.com/>.

## The four things NVIDIA's own snippet gets wrong

The catalog page hands out a copy-paste `requests` example. Each of these costs
a failed request to discover, and all four are handled in the module.

**`"Bearer $NVIDIA_API_KEY"` is a string literal.** Python does not interpolate
`$VAR` inside a string, so that header goes out with the dollar sign and the
variable name still in it and the API answers 401. The key is read from the
environment here, and a value that still *looks* like an unexpanded variable is
rejected at construction rather than sent — that is the same bug arriving one
layer later, when the placeholder got stored instead of expanded.

**An inline image is capped.** A base64 image embedded in the request body is
accepted only up to `MAX_INLINE_BYTES` (180,000); past that NVIDIA wants the
image uploaded through their asset API instead, which this module does not
implement. It downscales to fit instead, and `--max-bytes` overrides the cap
because it is NVIDIA's number and can move.

**The shrink ladder is PNG-first, and that is deliberate.** A chart is line art.
JPEG at the quality needed to hit a size target smears thin candle wicks and
axis text — exactly the content a chart read depends on. So the image is scaled
down as PNG at every step first, and only re-encoded as JPEG when no scale at or
above 320px a side fits. A photograph degrades gracefully either way; a chart
does not.

**A stream is not JSON.** With `--stream` the response is `text/event-stream` —
`data:` lines carrying JSON fragments, ending in `data: [DONE]`. Calling
`.json()` on it fails. Reasoning models additionally split their output across
two delta keys, `reasoning_content` for the thinking and `content` for the
answer; concatenating them returns a reply with the model's scratchpad pasted on
the front. The two are kept apart, and `--show-reasoning` prints the thinking to
stderr so a pipe still carries only the answer.

## Two things that were not verifiable when this was written

Stated rather than assumed, because a confident wrong answer here is worse than
an open question.

**Whether `moonshotai/kimi-k3` exists on the catalog, and whether it accepts
images.** It is the model in the snippet this was built from and it is the
default, but it was never called. Every NVIDIA host is blocked from Claude Code
on the web by the environment's network policy — `integrate.api.nvidia.com`,
`build.nvidia.com` and `docs.api.nvidia.com` all answer 403 at the proxy — so
the catalog could not be listed from here.

That is what the `models` subcommand is for, and it is the first thing to run
with a working key:

    python tools/nvidia_vision.py models --filter kimi
    python tools/nvidia_vision.py models --filter vision

It reads `/v1/models` and prints the ids, which costs no completion and settles
the question outright. An id absent from that list is why a call comes back 400.
`--model` overrides the default, and the 400 carries the API's own message,
which the CLI prints.

**The 180,000-byte cap.** It is NVIDIA's documented figure for inline assets,
carried over rather than re-measured, for the same reason.

Both are why nothing here asserts a model's capabilities, and why the first live
call has to happen somewhere with network access to NVIDIA — the owner's Windows
machine, or a cloud environment with those domains allowed.

## Reading a desk chart

The `chart` subcommand renders the desk's own candlestick chart for a symbol
through `tools/desk_levels.py` — prior-day range, session levels, unmitigated
fair value gaps — and then asks the model to read it:

    python tools/nvidia_vision.py chart NQ=F --interval 15m --keep nq.png

The PNG is deleted afterwards unless `--keep` names a path. This is a second
opinion on a picture, not a source of levels: the numbers on that chart were
computed from bars by `desk_levels`, and a model reading them back off the image
can only be less accurate than the arithmetic that drew them. The default prompt
says so — it asks for structure and forbids inventing numbers that are not
legible.

## Tests

`tests/test_nvidia_vision.py` runs entirely offline: the HTTP layer against a
fake `requests.Session`, every image built in memory, no `NVIDIA_API_KEY`
needed. Following the pattern in `tests/test_docs_examples.py`, the size-cap
tests calibrate their caps off the image under test rather than hard-coding a
round number — a gradient PNG compresses so well that a plausible-looking
constant silently stopped testing anything, which is how the first cut of two of
them passed while asserting nothing.
