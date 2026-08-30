# AGENTS.md

This checkout **is** the bead-swarm launcher (`bin/bead-swarm`). Read `README.md` first: clone, `bin/install`, then `bead-swarm --epic <id> --cwd <repo-with-.beads>`. Do not empty a graph in this chat — exec the binary. `--cwd` is the beads project; it is not this checkout unless the human said so.

## How to invoke

Read `.claude/skills/bead-swarm/SKILL.md` first. The live chat session is not the orchestrator — exec the binary.

Find it, in order: `$BEAD_SWARM_HOME/bin/bead-swarm`, `./bin/bead-swarm`, `command -v bead-swarm`. Never copy `bin/*` out of this tree; the lab worker imports `beadswarm` from the checkout. `bin/install --status` if anything is missing.

```
./bin/bead-swarm --help
./bin/bead-swarm-lab-setup --list
./bin/install --status
```

## Global vs per-repo

| | Per-repo (`bin/install --local`) | Global (`bin/install --global`) |
|---|---|---|
| Binary | `./bin/bead-swarm` | `bead-swarm` shim on `PATH` (`~/.local/bin`) that sets `BEAD_SWARM_HOME` and execs this checkout |
| Skill | `.claude/skills`, `.grok/skills`, `.agents/skills` in **this** repo | same skill symlinked into `~/.<vendor>/skills/bead-swarm` |
| `--cwd` / beads / AM project | the directory you pass (default: cwd) | same — still per target repo |
| Seat cache | `~/.cache/flywheel/orchestrator-seat.json` unless `BEAD_SWARM_SEAT_CACHE` is set | same |

`bd`, `am`, `git`, Python stay machine-global either way.

## Tests

```
python3 -m unittest discover -s tests -v
```

Needs `bd`, `am`, `git`. Lab tests create isolated mailboxes (`DATABASE_URL` / `STORAGE_ROOT`).

## Conventions

- `br` on PATH may be a shim; tests pin `BEAD_SWARM_BR_BIN` to Go `bd`.
- Claim with `--actor AdjectiveNoun`. Unclaim with `--status open --assignee ""` or `bd ready --unassigned` will hide the bead.
- Reserve **before** write; retry exclusive JSON conflicts **and** mailbox `temporarily busy`; then release.
- Close epics only when every child is closed; never `bd epic close-eligible` repo-wide.
- Scenario graphs: `scenarios/*.json`. What they look like is in README.md.

## Wave nest

If `BEAD_SWARM_WAVE` is set, do not re-exec the launcher. Inner loop: `.claude/archived/commands/bead-swarm.md`.
