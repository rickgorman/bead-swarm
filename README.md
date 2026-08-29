# bead-swarm

Dummy graphs and tests for the host `bin/bead-swarm` launcher: waves, `bd` claims, Agent Mail file leases, and epic wrap.

This is not the Leverage app. Each scenario materializes into a throwaway git+`bd` directory so the live beads graph stays untouched.

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

## Surprises the tests pin

- Agent Mail exclusive conflicts return **exit 0** plus JSON `conflicts`. Do not trust the process status.
- Parallel `am` CLIs on one mailbox also hit a **storage-root activity lock** (`temporarily busy`). Retry that the same way as a file conflict.
- `bd update --claim` is per **actor**. Two workers as the same OS user both win; pass `--actor BlueLake` vs `--actor CoralPeak`. A failed claim can still exit 0 with `already claimed`.
- `bd ready --unassigned` hides a reopened bead that still has an assignee. Unclaim with `--assignee ""`.
- `am file_reservations reserve --ttl 1` is **one minute**, not one second.
- `bd dep add` refuses cycles. A stuck `in_progress` blocker is the realistic dry-frontier trap.

## Run

Needs `bd` (Go beads), `am` (MCP Agent Mail), `git`, and Python 3.11+.

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
