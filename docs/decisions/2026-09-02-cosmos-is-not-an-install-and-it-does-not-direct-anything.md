# Cosmos is not an install, and it does not direct anything

*Decided 2026-09-02.*

**Decision:** do not adopt NVIDIA Cosmos into this repository, and install
nothing from it. Separately, keep the *hosted* Cosmos 3 Super video endpoints on
the table for the content business, which is a different vendor, a different
budget and a different question.

The trigger was a link — `https://github.com/NVIDIA/cosmos.git` — with "install
what I don't already have", and a question worth more than the install: *does
this direct everything going forward, or do we have a say in where we use this
and other agents or skills?*

## There was nothing to install, on three counts

`NVIDIA/cosmos` is a documentation repository. Cloned in full it contains
`README.md`, `RELEASE.md`, `inference_benchmarks.md`, `cookbooks/cosmos3/` and
`evaluation/cosmos3/`. No `setup.py`, no `pyproject.toml`, no package. The
installable code is a second repository, `NVIDIA/cosmos-framework`, whose stated
system requirements are:

| Requirement | Ours |
| --- | --- |
| NVIDIA GPU, Ampere or newer (H100/B200 recommended) | none |
| CUDA >= 12.8 | n/a |
| Linux x86-64/aarch64, glibc >= 2.35 | Windows |
| ~150 GiB free disk for a first run | not budgeted |

Three independent failures, any one of which is fatal. The install command
itself opens `sudo apt-get install`, which does not parse in PowerShell 5.1, so
the failure would have arrived as a confusing shell error rather than an honest
"wrong hardware".

The model family does not rescue this at the small end. The smallest member,
Cosmos3-Edge at 4B, is specified for Jetson AGX Orin / Thor or an RTX Pro 6000.
The other two are Cosmos3-Nano at 16B and Cosmos3-Super at 64B.

## It is also aimed somewhere else

Cosmos 3 is a family of world models for Physical AI — robots, autonomous
vehicles, smart infrastructure. It exposes a **Reasoner** (vision in, text out:
physical plausibility, grounding, action forecasting) and a **Generator**
(text/image/action in, video, sound and action sequences out). Nothing in it is
aimed at charts, options or backtests. The desk already reads images through
`pwb_toolbox.vision`, and that need is met.

## The direction question, which is the part worth keeping

**No, it does not direct anything, and the reasons are structural rather than
reassuring.**

**Cosmos ships its own agent skills, and they are repo-local.** There are five,
in `cosmos-framework/.agents/skills/` and mirrored to `.claude/skills/`:
`cosmos3-setup`, `cosmos3-codebase-nav`, `cosmos3-inference`,
`cosmos3-post-training`, `cosmos3-env-troubleshoot`. They load for a session
working *inside that clone*. They cannot reach this repository, cannot override
the skills in `.claude/skills/` here, and every one of them is about installing
or training on GPUs we do not have. A vendor shipping skills alongside a model
is a pattern worth recognising, not a claim on the stack.

**The vendor is a URL and the model is a string.** `pwb_toolbox/vision/nvidia.py`
posts to `integrate.api.nvidia.com` with a `--model` flag whose default is
`moonshotai/kimi-k3` — not an NVIDIA model at all. That shape is what keeps the
say local: swapping vendor or model is an argument, not a migration. Preserve it
in anything added later.

**The only real lock-in here is hardware.** Adopting Cosmos seriously means
buying or renting a GPU. That is a money decision, and it is the one to say no
to — not the software, which is Apache-licensed and costs nothing to ignore.

## What survives: the video route, which needs no GPU and no NVIDIA key

Cosmos 3 Super is open-weights, and third-party serverless hosts run it. That
makes the Generator reachable from a Windows machine with no GPU, billed per
use:

| Route | Reported price | Notes |
| --- | --- | --- |
| Cosmos 3 Super image-to-video (fal) | ~$0.05 per second of video | rounded up per second |
| Cosmos 3 Super text-to-image (fal) | ~$0.04 per image | +$0.02 for prompt expansion |
| Cosmos 3 Super image-to-video (WaveSpeed) | from ~$0.05 per run | scales with size/length |

For scale, the same surveys put Kling 3.0 near $0.07/s, Sora 2 near $0.10/s and
Veo 3.1 near $0.40/s. Cosmos is at or below the cheap end of that field.

**Every number in those two paragraphs is unverified.** They come from
comparison articles, not vendor pages, and they disagree with each other — one
source puts Kling on fal at $0.029/s against another's $0.07/s. The egress proxy
in a Claude Code cloud session blocks `fal.ai`, `build.nvidia.com`,
`huggingface.co` and `integrate.api.nvidia.com` outright (403 on the CONNECT
tunnel), so none of it could be confirmed at the source from here. Confirm on
the vendor's own pricing page before any card is attached.

**The honest caveat is capability, not price.** Cosmos 3 Super is trained for
physical plausibility — how objects fall, how a manipulator moves, how a scene
evolves under real dynamics. That is not the same skill as cinematic or
stylised content, which is what Veo, Kling and Sora are tuned for. Cheapest per
second is the wrong metric if the output is the wrong kind of video.

**This route does not touch the NVIDIA API key.** fal and WaveSpeed are separate
vendors with separate billing. The ledger's open `nvidia-key` item is unrelated
to it and is not a blocker for it — which also means the obvious check, "list
the NVIDIA catalog and grep for cosmos", could not have been run: there is no
key yet. Reading the ledger before writing the handover is what caught that.

## What this does not decide

Whether to spend anything on Cosmos-generated video. That is a money decision
and it stays with the owner. This entry records only that the *install* is
closed and the *hosted* route is real, priced in the region above, and reachable
without hardware.
