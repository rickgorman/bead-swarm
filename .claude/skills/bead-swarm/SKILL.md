---
name: bead-swarm
description: >
  Launch the host bead-swarm binary — wave-shaped orchestrator sessions that
  empty a bd epic using br/bd claims, Agent Mail file leases, and a 10-bead
  budget. Use when the user says bead-swarm, /bead-swarm, empty the graph,
  drain an epic, run a lab scenario, or pressure-test overlap/AM/parallel
  waves. Do not empty the graph in this chat; exec the launcher.
---

# /bead-swarm

You are **not** the swarm. This chat session is not the orchestrator (Claude cannot run Sol, Codex cannot run Fable). Exec the host binary and let it spawn waves.

If `BEAD_SWARM_WAVE` is set, you **are** a wave. Do not re-exec the launcher. Follow `.claude/archived/commands/bead-swarm.md` (or `$BEAD_SWARM_HOME/.claude/archived/commands/bead-swarm.md`).

## Find the binary (do not copy it)

Never copy `bin/bead-swarm` to another directory. Lab workers import the `beadswarm` package from the checkout; a stray copy will spawn a worker that cannot find it.

Resolve in this order:

1. `$BEAD_SWARM_HOME/bin/bead-swarm` if the env var is set
2. `./bin/bead-swarm` if cwd (or the git root) is this checkout
3. `command -v bead-swarm` — a **shim** from `bin/install --global`, which exports `BEAD_SWARM_HOME` and execs the checkout copy
4. Else tell the human to clone `rickgorman/bead-swarm` and run `bin/install --local` (this repo) or `bin/install --global` (PATH + user skills)

Confirm with `bead-swarm --help` or `./bin/bead-swarm --help`. `bin/install --status` prints home, shims, skills, and `bd`/`am`/`git`/`python3`.

Sibling tools live next to it: `bead-swarm-lab-setup`, `bead-swarm-lab-worker`. Same rule — shims or `./bin/…`, never copies.

## When to call it

| User intent | Command |
|---|---|
| Drain an epic in some repo | `bead-swarm --epic <id> --cwd <repo>` |
| Pick an epic (TTY) | `bead-swarm --cwd <repo>` |
| Plan only | `bead-swarm --dry-run --epic <id> --cwd <repo>` |
| Seat probe | `bead-swarm --probe-only` |
| Lab / dummy graph | `bead-swarm-lab-setup --list` then setup + `bead-swarm --lab --seat grok --epic <id> --cwd <lab>` |
| Already inside a wave | do **not** call it |

Forward extra flags the user named (`--wave-size`, `--max-waves`, `--stagger-seconds`, `--seat`, `--once`, `--no-am`).

Non-TTY with no `--epic`: the binary lists open epics and exits 1. Show that list, ask, re-run with `--epic`.

## Global vs per-repo

**Tooling** (`bd`, `am`, `git`, Python) is always machine-global.

**Swarm code** is either:

- **Per-repo** — this checkout (or a submodule). Call `./bin/bead-swarm`. Skills live at `.claude/skills/bead-swarm`, `.grok/skills/bead-swarm`, `.agents/skills/bead-swarm`. `bin/install --local` wires the extra skill links.
- **Global** — one checkout, `bin/install --global` writes shims to `~/.local/bin` and symlinks the skill into `~/.claude/skills`, `~/.grok/skills`, `~/.agents/skills`. Call `bead-swarm` from anywhere. A **project** skill of the same name (e.g. Leverage's `.claude/skills/bead-swarm`) still wins inside that repo.

**Beads, files, Agent Mail project identity** always follow `--cwd` (default: current directory). They are never stored in the swarm checkout. Isolate a run with a dedicated `--cwd`, not by copying bins.

**Seat cache** defaults to `~/.cache/flywheel/orchestrator-seat.json` (shared). For a private ladder per repo: `BEAD_SWARM_SEAT_CACHE=$PWD/tmp/bead-swarm/seat-cache.json`.

Lab tests isolate Agent Mail with `DATABASE_URL` + `STORAGE_ROOT` + `ALLOW_EPHEMERAL_PROJECTS_IN_DEFAULT_STORAGE=1`. Do not point those at the human's default mailbox.

## Hard rules

- `--cwd` must contain `.beads`. Never run against Leverage's live graph unless the human named that repo and epic.
- `bd update --claim --actor <AdjectiveNoun>` — same OS user without `--actor` both win. Failed claims can exit 0 with `already claimed`.
- Agent Mail exclusive conflicts often return exit 0 plus JSON `conflicts`; mailbox `temporarily busy` is retryable.
- Epics are completion gates. The launcher closes the **chosen tree** when every child is closed. It does not run repo-wide `bd epic close-eligible`.
- Dry frontier with open (blocked/stuck) beads is exit 1, not success.
- Need `bd` (Go beads), `am`, `git`, Python 3.11+.

## Lab corpus

```
bead-swarm-lab-setup --list
bead-swarm-lab-setup --scenario 01-overlap-exclusive --dir /tmp/ovx --force
bead-swarm --lab --seat grok --epic "$(cat /tmp/ovx/EPIC)" --cwd /tmp/ovx --wave-size 1 --max-waves 2 --stagger-seconds 0
```

Scenario ids and what they look like: `AGENTS.md` in the checkout (or `$BEAD_SWARM_HOME/AGENTS.md`).
