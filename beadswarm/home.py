"""Locate the bead-swarm checkout. Never copy bin scripts off this tree."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

BINS = ("bead-swarm", "bead-swarm-lab-setup", "bead-swarm-lab-worker")
SKILL_REL = Path(".claude") / "skills" / "bead-swarm"
CANONICAL_SKILL = SKILL_REL / "SKILL.md"


def swarm_home(script_file: str | None = None) -> Path:
    env = os.environ.get("BEAD_SWARM_HOME")
    if env:
        return Path(env).expanduser().resolve()
    if script_file:
        return Path(script_file).resolve().parent.parent
    return Path(__file__).resolve().parent.parent


def bin_dir(script_file: str | None = None) -> Path:
    return swarm_home(script_file) / "bin"


def bin_path(name: str, script_file: str | None = None) -> Path:
    return bin_dir(script_file) / name


def skill_dir(script_file: str | None = None) -> Path:
    return swarm_home(script_file) / SKILL_REL


def which_swarm() -> Path | None:
    found = shutil.which("bead-swarm")
    return Path(found).resolve() if found else None
