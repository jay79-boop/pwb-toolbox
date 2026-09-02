# Reading images: `pwb_toolbox.vision`

Some of what this desk produces is a picture and nothing else. A chart
screenshot has the levels drawn on it but no CSV behind it. A trade-journal
shot is the only record of what the screen looked like at entry. A statement or
a filing arrives as a scan, and `tools/analyze_trades.py` needs a table.

`pwb_toolbox.vision` sends those to a vision-language model hosted on NVIDIA's
catalog and gets text back. It is a thin client — the endpoint speaks the
OpenAI chat-completions dialect — and almost all of the code is the three
things that go wrong on a first attempt.

## Getting a key

`NVIDIA_API_KEY`, from [build.nvidia.com](https://build.nvidia.com/) → **Get API
Key**. It is listed in `.env.example`. Nothing in the test suite reads it: HTTP
runs against a fake session, so `pytest` passes with no key and no network.

## The shortest thing that works

```python
from pwb_toolbox.vision import VisionClient

client = VisionClient()
answer = client.describe("chart.png", prompt="Read the session levels off this")
```

`describe` takes one image or a list of them, as paths, `http(s)` URLs, or raw
`bytes`. A URL is passed through for the endpoint to fetch; anything local is
read, sniffed and inlined as a `data:` URI.

From a shell, so you can prove the key works before writing any code:

```
python -m pwb_toolbox.vision chart.png --prompt "Read the session levels off this" --verbose
```

## Three traps, each of which cost a debugging session

**The key does not expand itself.** NVIDIA's catalog page shows

```
"Authorization": "Bearer $NVIDIA_API_KEY"
```

which is a shell idiom. Pasted into Python it sends those eighteen characters
literally and the endpoint answers `401` with nothing that points at the cause.
`resolve_api_key` refuses a value that still looks like an unexpanded variable
and says so in the exception.

**The inline size limit counts base64 characters, not bytes.** NVIDIA documents
roughly 180,000 characters for an inline image, and base64 inflates by 4/3 — so
a 135 KB screenshot is already at the ceiling while `ls` still says it is small.
Checking the file size passes locally and fails on the wire. `prepare` measures
the encoded string and reports the number back:

```python
from pwb_toolbox.vision import prepare

ready = prepare("statement.png", 180_000, False)
print(ready.media_type, ready.encoded_chars, ready.scale)
```

**Reasoning arrives on its own channel.** A model given `reasoning_effort` puts
its working in `delta.reasoning_content` and its answer in `delta.content`.
Joining every delta returns the scratch work with the answer buried after it.
`stream()` yields the answer only; `events()` keeps both, labelled:

```python
from pwb_toolbox.vision import VisionClient

client = VisionClient(model="moonshotai/kimi-k3")
for channel, text in client.events("Why?", "chart.png", reasoning_effort="max"):
    print(channel, text)
```

## Downscaling is a decision, not a default to inherit

An oversized image is shrunk to fit by default, losslessly where it can be and
as JPEG when it cannot. For a chart screenshot that costs nothing you were
reading anyway.

For a **scanned statement it can cost the digits**, which is the one case where
a wrong answer looks exactly like a right one. Pass `downscale=False` there and
handle `ImageTooLarge` by splitting the scan or re-scanning it smaller:

```python
from pwb_toolbox.vision import VisionClient

client = VisionClient(downscale=False)
for chunk in client.stream("Summarise this statement", ["page1.png", "page2.png"]):
    print(chunk, end="")
```

`--verbose` on the CLI prints what each image became on the wire — media type,
encoded length, and the scale factor — which is the fastest way to notice that
something was resized when you did not want it to be.

## Errors

`NvidiaError` carries `status` and `body`, because the useful half of an NVIDIA
error is in the response body and `raise_for_status` throws it away. It is
raised for any `4xx`/`5xx`, for a `200` whose body is an `error` object rather
than a completion, and for a body that is not JSON at all — a gateway HTML page
is the usual cause of the last one.

## A note for sessions running in the cloud

`integrate.api.nvidia.com` is **not reachable from a Claude Code web container**:
the egress proxy answers `403` to the CONNECT. The code and its tests run there
happily, since neither needs the network, but a live call has to run on the
owner's machine or from an environment with that domain allowlisted.

## Where the code is

- `pwb_toolbox/vision/client.py` — key resolution, request building, the SSE reader
- `pwb_toolbox/vision/images.py` — media-type sniffing, the base64 budget, downscaling
- `pwb_toolbox/vision/cli.py` — `python -m pwb_toolbox.vision`
- `tests/test_vision.py` — the suite; no network, no key, no Pillow required

Pillow is used for downscaling when it is present — it arrives with
`matplotlib` rather than being required outright — and its absence turns an
oversized image into an `ImageTooLarge` rather than a crash.
