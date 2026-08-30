# bead-swarm

Dummy graphs and tests for the host `bin/bead-swarm` launcher: waves, `bd` claims, Agent Mail file leases, and epic wrap.

This is not the Leverage app. Each scenario materializes into a throwaway git+`bd` directory so the live beads graph stays untouched.

## Prerequisites

Install these on the host. `bin/install --status` reports which ones it can see.

**Required for the lab tests** (`python3 -m unittest discover -s tests`):

| Binary | What it is | Link |
|--------|------------|------|
| `python3` | Python 3.11+ | [python.org](https://www.python.org/downloads/) |
| `git` | Isolated lab repos | [git-scm.com](https://git-scm.com/) |
| `bd` | Go [Beads](https://github.com/steveyegge/beads) — graph issue tracker (`bd ready`, `--claim`, `--json`) | [steveyegge/beads](https://github.com/steveyegge/beads) |
| `am` | [Agent Mail](https://github.com/Dicklesworthstone/mcp_agent_mail) CLI — identities and file reservations | [Dicklesworthstone/mcp_agent_mail](https://github.com/Dicklesworthstone/mcp_agent_mail) |

**Used by the launcher / workers** (tests pin `BEAD_SWARM_BR_BIN` to Go `bd` because `br` on PATH is often a shim):

| Binary | What it is | Link |
|--------|------------|------|
| `br` | [Beads Rust](https://github.com/Dicklesworthstone/beads_rust) — Rust port of Beads; same graph, `br` CLI | [Dicklesworthstone/beads_rust](https://github.com/Dicklesworthstone/beads_rust) |
| `bv` | [Beads Viewer](https://github.com/Dicklesworthstone/beads_viewer) — TUI + robot-next. **Never run bare `bv`** (it opens a TUI). Lab workers call `bv-robot-next` / `bv --robot-next` only, and skip BV unless `BEAD_SWARM_SKIP_BV=0`. | [Dicklesworthstone/beads_viewer](https://github.com/Dicklesworthstone/beads_viewer) |

**Optional, live waves only** (not the `--lab` worker). The launcher probes this seat ladder and caches the winner for an hour: Fable / Sol / Opus / Terra / Grok / Composer — via `claude`, `codex`, `grok`, and `cursor-agent`. Lab runs pass `--lab --seat grok` and never need those harnesses.

## The angles

Baseline plus overlap, then ten more combinations of dependencies, parallelism, and Agent Mail.

| # | Scenario | What it stresses |
|---|---|---|
| 00 | `width6` | 20 beads, max ready width 6, independent files. Wave recycle. |
| 01 | `overlap-exclusive` | Two ready beads exclusive-append the same file. Graph says parallel; AM serializes. |
| 02 | `touch-label` | `touch:files/hotspot.txt` with no `blocks` edge (the migrate-file convention). |
| 03 | `blocks-same-file` | Same file **with** a `blocks` edge. Ready width 1; no AM conflict. |
| 04 | `fan-in-merge` | Three parallel writers, then a merge bead blocked by all three. |
| 05 | `fan-out-shared` | Seed exclusive-writes; children take a **shared** lease on the seed. |
| 06 | `two-clusters` | Two disjoint chains in one epic. Parallel waves, one wrap. |
| 07 | `claim-race` | Two workers, one bead, `bd update --claim`. Exactly one writer. |
| 08 | `abandoned-ttl` | Claim + exclusive lease, then die. AM TTL expires; the bead stays `in_progress` until reopen. |
| 09 | `nway-log` | Six parallel exclusive appends to `files/log.txt`. No lost lines. |
| 10 | `nested-epics` | Program → two slices, cross-slice overlap, close-eligible inside-out. |
| 11 | `cycle-trap` | `bd` rejects A↔B. A stuck `in_progress` blocker leaves ready empty; swarm exits 1 and does not wrap. |
| 12 | `two-epics-leak` | Two programs in one db. `--epic A` must not claim or close B. |
| 13 | `partial-wrap` | Alpha slice done, beta still claimed. Only alpha closes. |
| 14 | `diamond` | A → B,C → D. Ready 1 then 2 then 1. |
| 15 | `partial-overlap` | A/B share one file; C is disjoint. |
| 16 | `shared-vs-exclusive` | Shared reader holds seed; exclusive writer waits. |
| 17 | `no-am-rmw` | Negative: two rmw-appends without AM lose an update. |
| 18 | `second-launcher` | Second process flocks `launcher.lock` and bounces. |
| 19 | `once-vs-recycle` | `--once` leaves layer 2; a second full run finishes. |
| 20 | `off-epic-bv` | Fake `bv` recommends a distractor; worker only claims the allowed id. |
| 21 | `foreign-actor` | OtherFox holds the claim. Swarm does not steal. |
| 22–30 | crash/hang | Finish-without-close, SIGKILL before/after write, live hang (with and without heartbeat), hang-then-succeed, two incomplete log beads, close-without-release, idempotent replay. |
| 31 | `relates` | `relates_to` must not shrink ready width. |
| 32 | `p0-vs-p4` | Both priorities ready; `--once` takes **P0** (`high.txt`); full run takes both. |
| 33 | `idempotent-rerun` | Second swarm on a closed epic is a no-op. |
| 34 | `tiny-hang` | One hang bead. A second swarm skip-lives (ready is empty so it never takes `launcher.lock`); SIGTERM then lets a third swarm spawn. |

## Surprises the tests pin

- Agent Mail exclusive conflicts return **exit 0** plus JSON `conflicts`. Do not trust the process status.
- Parallel `am` CLIs on one mailbox also hit a **storage-root activity lock** (`temporarily busy` / sqlite busy), even on distinct paths. Retry that the same way as a file conflict.
- `bd update --claim` is per **actor**. Two workers as the same OS user both win; pass `--actor BlueLake` vs `--actor CoralPeak`. A failed claim can still exit 0 with `already claimed`.
- `bd ready --unassigned` hides a reopened bead that still has an assignee. Unclaim with `--assignee ""`.
- `am file_reservations reserve --ttl 1` is **one minute**, not one second (the CLI help still says seconds).
- `bd children` hides closed kids; wrap uses `bd list --parent --all`. A wrap that listed one still-open closer exits 1; the next swarm closes the epic.
- `bd dep add` refuses cycles. A stuck `in_progress` blocker is the realistic dry-frontier trap.

## Agent setup (global vs per-repo)

Do **not** copy `bin/bead-swarm` somewhere else. Lab workers import `beadswarm` from this checkout; a copied bin will break. Use a shim that sets `BEAD_SWARM_HOME` and execs the original, or call `./bin/bead-swarm`.

**Per-repo** (this checkout, or a submodule in another app):

```bash
git clone git@github.com:rickgorman/bead-swarm.git
cd bead-swarm
bin/install --local          # executable bins + .grok/.agents skill links
./bin/bead-swarm --help
```

Agents pick up `.claude/skills/bead-swarm` (and the `.grok` / `.agents` links). Invoke `./bin/bead-swarm --cwd <target> --epic <id>`.

**Global** (one checkout, call `bead-swarm` from any directory):

```bash
bin/install --global         # shims in ~/.local/bin + user-level skills
hash -r
command -v bead-swarm
bin/install --status
```

`--cwd` still selects the beads/AM project. A repo that already has its own `.claude/skills/bead-swarm` (Leverage) keeps that copy while you are inside it.

Undo global: `bin/install --uninstall`.

The skill (`.claude/skills/bead-swarm/SKILL.md`) is what tells an agent **when** to call the binary and **how** to find it.

## Run

See **Prerequisites**. Then:

```bash
python3 -m unittest discover -s tests -v
```

One scenario, no tests:

```bash
bin/bead-swarm-lab-setup --scenario 01-overlap-exclusive --dir /tmp/ovx --force
bin/bead-swarm --lab --seat grok --epic "$(cat /tmp/ovx/EPIC)" --cwd /tmp/ovx --wave-size 1 --max-waves 2 --stagger-seconds 0
```

## Layout

```
bin/bead-swarm              # host wave launcher (copied from Leverage, AM-conflict + unfinished-tree fixes)
bin/bead-swarm-lab-setup    # materialize a scenario JSON into an isolated bd repo
bin/bead-swarm-lab-worker   # claim → AM reserve → write → release → close
scenarios/*.json            # the corpus
tests/                      # launcher fakes + live bd/am scenario tests
```
