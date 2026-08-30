"""Env-backed knobs. Namespaced `BEAD_SWARM_*` wins over the short alias when both are set."""
from __future__ import annotations

import os


def raw(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value is not None and str(value).strip() != "":
            return value
    return None


def get_str(*names: str, default: str = "") -> str:
    value = raw(*names)
    return default if value is None else value


def get_int(*names: str, default: int = 0) -> int:
    value = raw(*names)
    if value is None:
        return default
    return int(value)


def get_float(*names: str, default: float = 0.0) -> float:
    value = raw(*names)
    if value is None:
        return default
    return float(value)


def wave_size(default: int = 10) -> int:
    return get_int("BEAD_SWARM_WAVE_SIZE", default=default)


def max_waves(default: int = 4) -> int:
    return get_int("BEAD_SWARM_MAX_WAVES", default=default)


def stagger_seconds(default: int = 30) -> int:
    return get_int("BEAD_SWARM_STAGGER", default=default)


def probe_timeout(default: int = 45) -> int:
    return get_int("BEAD_SWARM_PROBE_TIMEOUT", default=default)


def seat_cache_ttl(default: int = 3600) -> int:
    return get_int("BEAD_SWARM_SEAT_CACHE_TTL", default=default)


def launcher_lock_ttl(default: int = 6 * 3600) -> int:
    return get_int("BEAD_SWARM_LAUNCHER_LOCK_TTL", default=default)


def ready_limit(default: int = 200) -> int:
    return get_int("BEAD_SWARM_READY_LIMIT", default=default)


def wait_poll(default: float = 1.0) -> float:
    return get_float("BEAD_SWARM_WAIT_POLL", default=default)


def scavenge_max_age(default: float = 2.0) -> float:
    return get_float("BEAD_SWARM_SCAVENGE_MAX_AGE", default=default)


def ping_prompt(default: str = "Reply with the single word pong.") -> str:
    return get_str("BEAD_SWARM_PING_PROMPT", default=default)


def asdf_ruby(default: str = "4.0.1") -> str:
    return get_str("ASDF_RUBY_VERSION", default=default)
