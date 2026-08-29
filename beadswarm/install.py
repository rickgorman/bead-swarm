"""Global shims + skill links, or per-repo skill links. Never copy the Python bins."""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

from beadswarm.home import BINS, CANONICAL_SKILL, swarm_home, which_swarm

SHIM = """#!/bin/sh
export BEAD_SWARM_HOME="{home}"
exec "{home}/bin/{name}" "$@"
"""

REPO_SKILL_LINKS = (
    Path(".grok") / "skills" / "bead-swarm",
    Path(".agents") / "skills" / "bead-swarm",
)

USER_SKILL_TARGETS = (
    Path(".claude") / "skills" / "bead-swarm",
    Path(".grok") / "skills" / "bead-swarm",
    Path(".agents") / "skills" / "bead-swarm",
)


def default_bin_dir() -> Path:
    override = os.environ.get("BEAD_SWARM_BIN_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".local" / "bin"


def user_home() -> Path:
    override = os.environ.get("BEAD_SWARM_USER_HOME")
    if override:
        return Path(override).expanduser().resolve()
    return Path.home()


def write_shim(dest: Path, home: Path, name: str) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(SHIM.format(home=home, name=name))
    dest.chmod(0o755)


def link(src: Path, dest: Path, *, force: bool) -> str:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_symlink() or dest.exists():
        if dest.is_symlink() and dest.resolve() == src.resolve():
            return f"ok {dest} -> {src}"
        if not force:
            raise SystemExit(f"refusing to overwrite {dest} (pass --force)")
        if dest.is_dir() and not dest.is_symlink():
            raise SystemExit(f"refusing to replace directory {dest}")
        dest.unlink()
    dest.symlink_to(src)
    return f"linked {dest} -> {src}"


def install_local(home: Path, *, force: bool) -> list[str]:
    skill = home / ".claude" / "skills" / "bead-swarm"
    if not (skill / "SKILL.md").is_file():
        raise SystemExit(f"missing {skill / 'SKILL.md'}")
    notes = [f"canonical skill: {skill / 'SKILL.md'}"]
    for rel in REPO_SKILL_LINKS:
        notes.append(link(skill, home / rel, force=force))
    for name in BINS:
        path = home / "bin" / name
        if not path.is_file():
            raise SystemExit(f"missing {path}")
        path.chmod(path.stat().st_mode | 0o111)
        notes.append(f"executable {path}")
    return notes


def install_global(home: Path, *, bin_dir: Path, force: bool) -> list[str]:
    notes = install_local(home, force=force)
    skill_src = home / ".claude" / "skills" / "bead-swarm"
    bin_dir.mkdir(parents=True, exist_ok=True)
    for name in BINS:
        write_shim(bin_dir / name, home, name)
        notes.append(f"shim {bin_dir / name}")
    for rel in USER_SKILL_TARGETS:
        notes.append(link(skill_src, user_home() / rel, force=force))
    notes.append(f"BEAD_SWARM_HOME={home}")
    return notes


def uninstall_global(home: Path, *, bin_dir: Path) -> list[str]:
    notes = []
    for name in BINS:
        path = bin_dir / name
        if path.is_file():
            text = path.read_text() if path.is_file() else ""
            if str(home) in text or path.is_symlink():
                path.unlink()
                notes.append(f"removed {path}")
    skill_src = (home / ".claude" / "skills" / "bead-swarm").resolve()
    for rel in USER_SKILL_TARGETS:
        dest = user_home() / rel
        if dest.is_symlink() and dest.resolve() == skill_src:
            dest.unlink()
            notes.append(f"removed {dest}")
    return notes


def status(home: Path, *, bin_dir: Path) -> list[str]:
    lines = [
        f"home: {home}",
        f"canonical skill: {home / CANONICAL_SKILL} ({'ok' if (home / CANONICAL_SKILL).is_file() else 'MISSING'})",
    ]
    for name in BINS:
        path = home / "bin" / name
        lines.append(f"repo bin: {path} ({'ok' if path.is_file() else 'MISSING'})")
    on_path = which_swarm()
    lines.append(f"PATH bead-swarm: {on_path or 'not found'}")
    shim = bin_dir / "bead-swarm"
    lines.append(f"global shim: {shim} ({'ok' if shim.is_file() else 'absent'})")
    for rel in REPO_SKILL_LINKS:
        dest = home / rel
        state = "ok" if dest.exists() else "absent"
        lines.append(f"repo skill link: {dest} ({state})")
    for rel in USER_SKILL_TARGETS:
        dest = user_home() / rel
        state = "ok" if dest.exists() else "absent"
        lines.append(f"user skill: {dest} ({state})")
    for tool in ("bd", "am", "git", "python3"):
        found = shutil.which(tool)
        lines.append(f"tool {tool}: {found or 'MISSING'}")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install bead-swarm bins and agent skills")
    parser.add_argument("--global", dest="do_global", action="store_true")
    parser.add_argument("--local", dest="do_local", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--home", help="checkout to install from (default: this repo)")
    parser.add_argument("--bin-dir", help="shim directory for --global (default: ~/.local/bin)")
    args = parser.parse_args(argv)
    home = Path(args.home).expanduser().resolve() if args.home else swarm_home()
    bin_dir = Path(args.bin_dir).expanduser().resolve() if args.bin_dir else default_bin_dir()

    chosen = sum(bool(flag) for flag in (args.do_global, args.do_local, args.status, args.uninstall))
    if chosen > 1:
        raise SystemExit("pick one of --global, --local, --status, --uninstall")
    if args.status or chosen == 0:
        print("\n".join(status(home, bin_dir=bin_dir)))
        if chosen == 0:
            print("\nnext: bin/install --local   # skill links in this checkout", file=sys.stderr)
            print("      bin/install --global  # shims on PATH + user skills", file=sys.stderr)
        return 0
    if args.uninstall:
        notes = uninstall_global(home, bin_dir=bin_dir)
        print("\n".join(notes) or "nothing to remove")
        return 0
    if args.do_global:
        notes = install_global(home, bin_dir=bin_dir, force=args.force)
    else:
        notes = install_local(home, force=args.force)
    print("\n".join(notes))
    return 0


if __name__ == "__main__":
    sys.exit(main())
