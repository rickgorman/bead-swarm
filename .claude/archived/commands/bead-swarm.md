---
name: bead-swarm
description: Drive implementation from a beads_rust (`br`) graph. Prefer the host launcher `bin/bead-swarm` — it picks an orchestrator seat (fable > sol > opus5 > terra > grok > composer, 1h cache), then runs wave-shaped sessions of 10 beads in parallel with a stagger. Inside a wave, `bin/bv-robot-next` picks, a fresh subagent builds, Agent Mail coordinates, `bin/bead-check` gates. `br` is beads_rust, never beads_runner. Run after /beads.
---

# /bead-swarm — empty the graph in waves

> **Done is not a feeling. Done is `br ready` empty and `br blocked` empty.**

## Entry point

**Preferred:** `bin/bead-swarm` from the host (any cwd in the repo). That process is the outer loop. It does **not** use the live chat session as the orchestrator — Claude cannot run Sol, Codex cannot run Fable, Grok cannot run either. The launcher probes the ladder and spawns the harness that can actually run the chosen model.

If `/bead-swarm` is invoked inside a chat session:

- If `BEAD_SWARM_WAVE` is set, you **are** a wave orchestrator. Honor the hard budget below. Do **not** re-exec `bin/bead-swarm`.
- Otherwise run `bin/bead-swarm` (or tell the human to). Do not try to empty the graph in this session.

## Orchestrator ladder

`fable > sol > opus5 > terra > grok > composer`

Probe each rung (tiny ping). Skip on quota death, auth death, missing binary, or timeout. Cache the winner at `~/.cache/flywheel/orchestrator-seat.json` for **one hour** (5-hour Claude windows and weekly Codex caps recover; do not cache forever). `--reprobe` ignores the cache. A wave that exits non-zero invalidates it.

Announce once per launch, e.g. `orchestrator: grok (fable quota_dead, sol bin_missing)`.

Each wave is a **child process** on the winning harness (`claude -p --model fable`, `codex exec -m gpt-5.6-sol`, …), which then spawns its own coder subagents. Fine.

`--seat <id>` pins. Composer-as-orchestrator is degraded mode: still zero product code from the orchestrator.

## Waves (hard budget 10)

The host launcher recycles orchestrators so one session cannot get tired at 20%:

- Each orchestrator session claims **at most 10 beads**, then prints `WAVE_DONE` and exits — even if more remain.
- Ready count 20 → two waves of 10 **at the same time** (stagger 30s by default so they do not thundering-herd the same `bv` pick).
- `--max-waves` caps concurrency (default 4). When a wave exits and the frontier is still wet, spawn another.
- `br update --claim` is the atomic bead lock. Agent Mail (`am`) holds advisory file leases and per-bead threads so parallel waves do not clobber files. The launcher exclusive-reserves `tmp/bead-swarm/launcher.lock` so two host launchers do not stack.

```
bin/bead-swarm                  # probe + waves until dry
bin/bead-swarm --dry-run        # print the plan
bin/bead-swarm --probe-only     # print the seat
bin/bead-swarm --once           # one round of waves, no recycle
```

## Inner loop (one wave)

You write **ZERO product code.** Coders emit; you brief, verify, git, and close.

**`br` is [beads_rust](https://github.com/Dicklesworthstone/beads_rust), never beads_runner.** On PATH, `br` is `bin/br-shim` → Go `bd` until `.beads/br-native`. After `br sync --flush-only`, `git add .beads/` yourself.

### Core triangle ([flywheel](https://agent-flywheel.com/core-flywheel))

| Tool | Binary | Job |
|---|---|---|
| **br** | `br` (shim → `bd` today) | Task structure: claim, status, close, deps |
| **bv** | `bin/bv-robot-next` (never bare `bv`) | Routing: highest-leverage ready bead |
| **am** | `am` | Identity, file reservations, per-bead threads |

### Roster (coders)

| Lane | Model | Spawn | Gets |
|---|---|---|---|
| Top-tier | **opus** ≡ **composer** | Agent tool `model: "opus"`; `cursor-agent --print --output-format text -f --model composer-2.5` | hardest beads |
| Mid-tier | **sonnet** | Agent tool `model: "sonnet"` | routine / glue / bulk |

No codex/gpt-5.5 in the **coder** roster. Alternate opus/composer.

```
claimed = 0
am macros start-session ... --agent-name Wave-$N-$SEAT
loop do
  break WAVE_DONE if claimed >= WAVE_SIZE   # env BEAD_SWARM_WAVE_SIZE, default 10
  next = `bin/bv-robot-next`                # NEVER bare bv
  break WAVE_DONE if !next.actionable && inflight.empty?
  skip epics
  verify:slice / verify:landing → YOU run the gate
  br update <id> --claim                    # fail = other wave has it; pick again
  mail_start(id)
  spawn FRESH coder
  claimed += 1
end
```

Pull again immediately after spawning. `BV_ROBOT_NOT_READY_LABELS=verify:recon`. Ignore `bv --robot-plan` for dispatch.

Shared `touch:<token>` or overlapping files → serialize. At most one `touch:db/migrate` in flight machine-wide (literal timestamp in the brief). **Never reassign** a claimed bead because files look quiet.

### Verify (you, never the coder, never the same model that wrote it)

- `bin/bead-verify <id>`
- `bin/bead-check <id> --lane <lane>` (exit 2 = fix the bead)
- `git diff --no-ext-diff`

Red → re-brief or switch coder. Green → stage intended files, commit, `br close --reason "<file + oracle>"`, mail Completed, release reservations. Merged spec with fake behavior stays OPEN. Precedent: `leverage-nwsrm.14.2026`.

Coder brief: Approach + Success criteria + `## Verification` + what NOT to touch. Coder must not close the bead, not `git add -A`, not run `bin/ci-local`.

### Mail

Thread id = bead id. Do not exclusive-reserve `**`.

```
am file_reservations reserve "$(pwd)" <agent> <paths...> --exclusive --reason <id>
am mail send --project "$(pwd)" --from Wave-$N --to <coder-or-self> \
  --thread-id <id> --subject "[<id>] Start: <title>" --body "..."
```

## Anti-laziness

A wave stops at 10. The **launcher** keeps going. Do not ask "should I keep going?" while this wave has budget and ready beads.

Mid-build bug → `br create` + `br dep add`. P0/P1 re-enter the frontier for the next wave.

## Worktree per slice (mandatory)

One slice = one branch = one `bin/worktree-up` stack. Lanes via `bin/lane-exec`. `WT_MAX_RUNNING=10`.

## Gates — you claim these, never a coder

1. **Slice** (`verify:slice`): `bin/ci-local --a --rspec --frontend`. Close with SHA.
2. **Landing** (`verify:landing`): freeze HEAD, `--system` (+ `--docker`/`--sidecar` if path-gated), then `--attest`. Never re-run the matrix just to sign.

`bin/bead-check` must never write `tmp/ci_results/`.

## Wrap (launcher sees frontier dry)

Close eligible epics **in this tree only** (children-first: slice epics, then the program epic) via `br close <epic> --reason "all N children closed"`. Do not run global `bd epic close-eligible` — that would close unrelated epics. Then `br stats`; `br sync --flush-only` + `git add .beads/`; `/self-review-rails`; every gate closed with SHA; `/pr-auto`.
