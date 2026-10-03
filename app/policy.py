"""Scan policy: concurrency limits, timeouts, and import classification."""

# Standard
import sys
from dataclasses import dataclass
from typing import FrozenSet, Iterable

# Tier labels used across the service, API, and panel.
TIER_STDLIB = "stdlib"
TIER_THIRD_PARTY = "third_party"
TIER_LOCAL = "local"
TIERS = (TIER_STDLIB, TIER_THIRD_PARTY, TIER_LOCAL)

DEFAULT_MAX_WORKERS = 8
DEFAULT_TIMEOUT_MS = 1500
DEFAULT_DEPTH = 2


@dataclass(frozen=True)
class ScanPolicy:
    """Tunables for a single repository scan."""

    max_workers: int = DEFAULT_MAX_WORKERS
    timeout_ms: int = DEFAULT_TIMEOUT_MS
    ttl_seconds: float = 30.0


def stdlib_names() -> FrozenSet[str]:
    """Top-level module names that ship with the interpreter."""
    names = set(getattr(sys, "stdlib_module_names", ()))
    names.update(sys.builtin_module_names)
    return frozenset(names)


def classify_module(
    top_level: str,
    local_roots: Iterable[str],
    stdlib: FrozenSet[str],
) -> str:
    """Classify a top-level import name into one of the three tiers."""
    if top_level in local_roots:
        return TIER_LOCAL
    if top_level in stdlib:
        return TIER_STDLIB
    return TIER_THIRD_PARTY
