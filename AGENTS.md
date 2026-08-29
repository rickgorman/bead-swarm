# bead-swarm lab

Isolated dummy graphs for pressure-testing `bin/bead-swarm` with `bd`, `bv`, and Agent Mail.

- Never write into a live Leverage `.beads` database.
- `br` on PATH may be a shim; lab tests pin `BEAD_SWARM_BR_BIN` to Go `bd`.
- Agent Mail is isolated per test via `DATABASE_URL` + `STORAGE_ROOT`.
- Lab workers reserve **before** they write, retry exclusive conflicts, then release.
- Epics are completion gates: close them only when every child is closed, and never with repo-wide `bd epic close-eligible`.
