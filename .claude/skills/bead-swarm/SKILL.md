---
name: bead-swarm
description: Launch the host bead-swarm loop. Runs `bin/bead-swarm` (orchestrator ladder + 10-bead waves). Pass --epic <id> or let the CLI list open epics to pick. Do not empty the graph in this chat session.
---

# /bead-swarm

This command is a shim. Inner-loop doctrine lives at `~/.claude/archived/commands/bead-swarm.md` (repo copy: `.claude/archived/commands/bead-swarm.md`).

Run the host launcher from the repo (or worktree) root. Forward `$ARGUMENTS` as CLI flags.

```
bin/bead-swarm
bin/bead-swarm --epic <id>
bin/bead-swarm --dry-run --epic <id>
```

If the user named an epic, pass `--epic <id>`. If they did not, run `bin/bead-swarm` with no epic flag: a TTY shows a numbered list to pick from; a non-TTY prints the list and exits 1 — then show that list and ask them to pick, and re-run with `--epic`.

If `BEAD_SWARM_WAVE` is already set, you are inside a wave. Do **not** re-exec `bin/bead-swarm`. Follow `.claude/archived/commands/bead-swarm.md`.

When the worked frontier is dry, `bin/bead-swarm` closes the chosen epic (and descendant epics) if every child is closed. It does not run a repo-wide `bd epic close-eligible`.
