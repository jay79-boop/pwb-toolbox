# The NVIDIA catalog is GPU-shaped, and exactly one skill survived the filter

*Decided 2026-09-02.*

**Decision:** install `cuopt-numerical-optimization-formulation` from
`NVIDIA/skills`, and reject the other 349 — most importantly
`portfolio-optimization`, which is the one a quant desk would reach for first
and the one that would fail hardest here.

The owner asked for "what is helpful that I don't already have" against the
public catalogue at `https://github.com/NVIDIA/skills`. The catalogue was
cloned and swept rather than sampled: 350 skills with a `SKILL.md`, scored by
how often each body names hardware or an orchestration stack it cannot get
here (`nvidia-smi`, `nvcc`, CUDA, GPU, Jetson, BlueField, Slurm, Kubernetes,
NIM, H100). **40 came back with zero mentions**, and reading those 40 showed
the zero to be mostly an artefact of vocabulary — NeMo Relay, TAO, i4h and
DICOM skills name no GPU because they name a *product* the owner does not run.

## Why `portfolio-optimization` was rejected, though it is the obvious fit

It is the single most topically relevant skill in the catalogue: Mean-CVaR,
efficient frontiers, scenario generation, backtesting, rebalancing. It is also
the most dangerous one to install, and the reason is worth writing down.

The skill hard-requires the cuOpt GPU solver and **explicitly forbids the
fallback**: *"Never fall back to CLARABEL, SCS, ECOS, or another CPU solver.
If cuOpt is absent, finish validation/setup and report that the GPU/cuOpt
runtime is missing."* `cuopt-install` puts a floor of NVIDIA Compute
Capability ≥ 7.0 under that — a physical Volta-or-newer card.

The owner's machine has no GPU. So the skill would fire on the most common
question they ask — *how should this portfolio be weighted* — and then decline
to answer, having displaced whatever would otherwise have answered it. That is
worse than not installing it.

**The check worth repeating: cuOpt has no hosted path.** It was tempting to
assume one, because the owner already holds an `NVIDIA_API_KEY` for
`tools/nvidia_vision.py` and NVIDIA hosts plenty else on build.nvidia.com.
Searched for it across `cuopt-install`, `cuopt-server-api-python` and
`portfolio-optimization`: the only "hosted" in any of them is
`pypi.nvidia.com` hosting the *wheel*. The server skill's endpoint is
`http://localhost:8000` throughout. An API key does not substitute for a card.

## Why the one that survived, survived

`cuopt-numerical-optimization-formulation` is 277 lines of concepts and names
no API: LP vs MILP vs QP, which shape a problem actually is, what duals and
reduced costs mean and when they do not exist, and the modelling patterns
(binary linking constraints, blending with a shared mixing step, capacity
timing). Zero CUDA/GPU/Docker mentions, nothing to install, no network at fire
time. Its one gesture at a sibling skill — "see the language-specific API
skills for how to retrieve them after a solve" — is a pointer, not a
dependency, so it does not dead-end on a skill we chose not to have.

It costs 18 words of always-loaded description. Total for our skills went
754 → 772 against a 1,000 cap.

That is a smaller haul than "install what's helpful" implies, and the smallness
is the finding: this catalogue is built for datacenter, robotics and edge work,
and a GPU-less Windows trading desk is not its audience. The skills that *look*
transferable are the ones that fail loudest.

## What was considered and left out

- **`nvidia-skill-finder`** — a catalogue router; 60 words of always-loaded
  description to fire on any mention of NVIDIA, CUDA or a GPU, and then mostly
  surface skills this machine cannot run. `docs/skills.md` already names this
  shape: the collision problem in a costume. A cloud session can clone and
  sweep the catalogue in minutes when the question actually arises — which is
  what produced this record.
- **`aiq-deploy`** — the companion that would stand up the backend
  `aiq-research` is waiting on. Docker Compose or Kubernetes, on Windows, for a
  skill already documented as expected never to fire. The open ledger question
  is whether to retire `aiq-research`, not to grow it a second half.
- **`nemo-rl-session-memory`** — writes session state under `./session/`.
  Duplicates the ledger and `CLAUDE.md`, in a second place, with a competing
  trigger.
- **`data-designer`**, **`nemo-retriever`** — both plausible on paper
  (synthetic test data; document search) and both needing a CLI installed and a
  model endpoint reachable before they do anything.

The sweep is reproducible: clone `NVIDIA/skills`, count hardware terms per
`SKILL.md` body, read the low tail. It cost one clone and no round trip to the
owner.
