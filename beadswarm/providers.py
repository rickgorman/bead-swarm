"""LLM CLI auto-detect + PLANNING_MODELS / BUILDING_MODELS ladders.

Spec format: `harness/model/effort` (model and effort optional).
Examples: `grok`, `claude/fable-5`, `codex/gpt-5.6-sol/xhigh`.
"""
from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
HARNESS_ALIASES = {
    "claude": "claude",
    "claude-code": "claude",
    "anthropic": "claude",
    "codex": "codex",
    "openai": "codex",
    "openai_codex": "codex",
    "grok": "grok",
    "xai": "grok",
    "cursor": "cursor",
    "cursor-agent": "cursor",
    "composer": "cursor",
}

HARNESS_BIN_ENV = {
    "claude": ("BEAD_SWARM_CLAUDE_BIN",),
    "codex": ("BEAD_SWARM_CODEX_BIN",),
    "grok": ("BEAD_SWARM_GROK_BIN",),
    "cursor": ("BEAD_SWARM_CURSOR_BIN",),
}

HARNESS_DEFAULT_BIN = {
    "claude": "claude",
    "codex": "codex",
    "grok": "grok",
    "cursor": "cursor-agent",
}

HARNESS_PROGRAM = {
    "claude": "claude-code",
    "codex": "codex-cli",
    "grok": "grok",
    "cursor": "cursor-agent",
}

DEFAULT_PLANNING = (
    "claude/fable",
    "codex/gpt-5.6-sol",
    "claude/opus",
    "codex/gpt-5.6-terra",
    "grok",
    "cursor/composer-2.5",
)

DEFAULT_BUILDING = (
    "cursor/composer-2.5",
    "grok",
    "claude/opus",
    "codex/gpt-5.6-terra",
)


@dataclass(frozen=True)
class ModelSpec:
    harness: str
    model: str
    effort: str
    raw: str

    @property
    def label(self) -> str:
        parts = [self.harness]
        if self.model:
            parts.append(self.model)
        if self.effort:
            parts.append(self.effort)
        return "/".join(parts)


@dataclass(frozen=True)
class Seat:
    id: str
    harness: str
    program: str
    ping: tuple[str, ...]
    spawn_prefix: tuple[str, ...]
    model_flag: str | None = None
    effort: str | None = None
    spec: str = ""


def parse_spec(raw: str) -> ModelSpec:
    token = (raw or "").strip().strip("\"'")
    if not token:
        raise ValueError("empty model spec")
    parts = [p.strip() for p in token.split("/") if p.strip()]
    if not parts:
        raise ValueError(f"invalid model spec: {raw!r}")
    harness = HARNESS_ALIASES.get(parts[0].lower())
    if harness is None:
        raise ValueError(f"unknown harness {parts[0]!r} in spec {raw!r} (want claude|codex|grok|cursor)")
    model = parts[1] if len(parts) > 1 else ""
    effort = parts[2] if len(parts) > 2 else ""
    if len(parts) > 3:
        effort = "/".join(parts[2:])
    return ModelSpec(harness=harness, model=model, effort=effort, raw=token)


def parse_model_list(raw: str | None) -> list[str]:
    if raw is None:
        return []
    text = raw.strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"model list is not JSON: {exc}") from exc
        if not isinstance(payload, list):
            raise ValueError("model list JSON must be an array of strings")
        return [str(item).strip() for item in payload if str(item).strip()]
    return [item.strip().strip("\"'") for item in text.replace("\n", ",").split(",") if item.strip()]


def env_model_list(*names: str, default: tuple[str, ...]) -> list[ModelSpec]:
    from beadswarm.settings import raw

    value = raw(*names)
    tokens = parse_model_list(value) if value is not None else list(default)
    if not tokens:
        tokens = list(default)
    return [parse_spec(token) for token in tokens]


def planning_specs() -> list[ModelSpec]:
    return env_model_list("BEAD_SWARM_PLANNING_MODELS", "PLANNING_MODELS", default=DEFAULT_PLANNING)


def building_specs() -> list[ModelSpec]:
    return env_model_list("BEAD_SWARM_BUILDING_MODELS", "BUILDING_MODELS", default=DEFAULT_BUILDING)


def harness_bin(harness: str, env: dict[str, str] | None = None) -> str:
    src = env if env is not None else os.environ
    key = HARNESS_ALIASES.get(harness, harness)
    for name in HARNESS_BIN_ENV.get(key, ()):
        override = src.get(name)
        if override:
            return override
    return HARNESS_DEFAULT_BIN.get(key, key)


def detect_clis(env: dict[str, str] | None = None) -> dict[str, str | None]:
    """Every start: which provider CLIs exist. Values are resolved paths or None."""
    src = env if env is not None else os.environ
    out: dict[str, str | None] = {}
    for harness in ("claude", "codex", "grok", "cursor"):
        configured = harness_bin(harness, src)
        if os.path.isfile(configured) and os.access(configured, os.X_OK):
            out[harness] = configured
            continue
        found = shutil.which(configured)
        out[harness] = found
    return out


def format_clis(clis: dict[str, str | None]) -> str:
    parts = []
    for harness in ("claude", "codex", "grok", "cursor"):
        path = clis.get(harness)
        parts.append(f"{harness}={path or 'missing'}")
    return "clis: " + " ".join(parts)


def short_id(spec: ModelSpec) -> str:
    model = (spec.model or "").lower()
    if spec.harness == "claude":
        if model.startswith("fable"):
            return "fable"
        if model.startswith("opus"):
            return "opus5"
        if model:
            return model
        return "claude"
    if spec.harness == "codex":
        if "sol" in model:
            return "sol"
        if "terra" in model:
            return "terra"
        if "luna" in model:
            return "luna"
        return model or "codex"
    if spec.harness == "grok":
        return "grok"
    if spec.harness == "cursor":
        return "composer"
    return spec.label


def _effort_args(harness: str, effort: str, model: str) -> tuple[list[str], str]:
    """Return (extra argv, maybe-rewritten model)."""
    if not effort:
        return [], model
    if harness == "claude":
        return ["--effort", effort], model
    if harness == "grok":
        return ["--effort", effort], model
    if harness == "codex":
        return ["-c", f"model_reasoning_effort={effort}"], model
    if harness == "cursor":
        if model and "[" not in model:
            return [], f"{model}[effort={effort}]"
        return [], model
    return [], model


def seat_argv(spec: ModelSpec, *, ping: bool) -> tuple[str, ...]:
    from beadswarm.settings import ping_prompt

    binary = harness_bin(spec.harness)
    model = spec.model
    extra, model = _effort_args(spec.harness, spec.effort, model)
    prompt = ping_prompt()
    if spec.harness == "claude":
        argv = [binary, "-p"]
        if model:
            argv.extend(["--model", model])
        argv.extend(extra)
        if ping:
            argv.append(prompt)
        else:
            argv.extend(["--permission-mode", "acceptEdits", "--output-format", "text"])
        return tuple(argv)
    if spec.harness == "codex":
        argv = [binary, "exec"]
        if model:
            argv.extend(["-m", model])
        argv.extend(extra)
        if ping:
            argv.extend(["-s", "read-only", "--skip-git-repo-check", prompt])
        else:
            argv.extend(["-s", "workspace-write", "--skip-git-repo-check"])
        return tuple(argv)
    if spec.harness == "grok":
        argv = [binary]
        if model:
            argv.extend(["-m", model])
        argv.extend(extra)
        if ping:
            argv.extend(["--single", prompt])
        else:
            argv.extend(["--output-format", "json", "--permission-mode", "bypassPermissions", "--always-approve"])
        return tuple(argv)
    if spec.harness == "cursor":
        argv = [binary, "--print", "--output-format", "text", "-f"]
        if model:
            argv.extend(["--model", model])
        if ping:
            argv.append(prompt)
        return tuple(argv)
    raise ValueError(f"unknown harness {spec.harness}")


def seat_from_spec(spec: ModelSpec, *, seat_id: str | None = None) -> Seat:
    ident = seat_id or short_id(spec)
    return Seat(
        id=ident,
        harness=spec.harness,
        program=HARNESS_PROGRAM[spec.harness],
        ping=seat_argv(spec, ping=True),
        spawn_prefix=seat_argv(spec, ping=False),
        model_flag=spec.model or None,
        effort=spec.effort or None,
        spec=spec.label,
    )


def planning_seats() -> list[Seat]:
    seats: list[Seat] = []
    used: set[str] = set()
    for spec in planning_specs():
        ident = short_id(spec)
        if ident in used:
            ident = spec.label
        used.add(ident)
        seats.append(seat_from_spec(spec, seat_id=ident))
    return seats


def resolve_pin(pin: str, seats: list[Seat]) -> Seat:
    needle = pin.strip()
    for seat in seats:
        if needle in {seat.id, seat.spec, seat.harness}:
            return seat
    spec = parse_spec(needle)
    return seat_from_spec(spec)


def available_building(clis: dict[str, str | None]) -> list[ModelSpec]:
    return [spec for spec in building_specs() if clis.get(spec.harness)]


def format_building(specs: list[ModelSpec]) -> str:
    if not specs:
        return "(none — no BUILDING_MODELS CLI is installed)"
    return "\n".join(f"- {spec.label}" for spec in specs)
