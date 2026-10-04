"""
Classification policy for imported modules.

Determines whether an imported name is part of the standard library, a
third-party (installed) package, or a local module of the scanned
repository. Installed-package detection is cached in-process with a TTL and
degrades to a static table when the environment cannot be probed (no pip /
offline) without raising.
"""

# Standard
from typing import Callable, FrozenSet
import importlib.metadata
import sys

# Local
from .cache import TTLCache

TIER_STDLIB = "stdlib"
TIER_THIRD_PARTY = "third_party"
TIER_LOCAL = "local"

#: Fallback table of well-known third-party top-level module names, used
#: when the environment cannot be probed (no pip / offline).
STATIC_KNOWN_PACKAGES: FrozenSet[str] = frozenset(
    {
        "alog",
        "boto3",
        "celery",
        "click",
        "django",
        "fastapi",
        "flask",
        "httpx",
        "jinja2",
        "matplotlib",
        "numpy",
        "pandas",
        "pip",
        "protobuf",
        "pydantic",
        "pytest",
        "redis",
        "requests",
        "scipy",
        "setuptools",
        "six",
        "sklearn",
        "sqlalchemy",
        "typing_extensions",
        "uvicorn",
        "wheel",
        "yaml",
    }
)


def probe_installed_packages() -> FrozenSet[str]:
    """Probe the current environment for installed top-level packages."""
    return frozenset(importlib.metadata.packages_distributions().keys())


class InstallProbe:
    """In-process cache of the installed-package probe result.

    Results are cached for ``ttl_seconds``; ``force=True`` bypasses the cache
    so newly installed packages are visible on the next round. If probing
    fails (no pip, offline, broken metadata) the probe degrades to a static
    table and never raises.
    """

    def __init__(
        self,
        ttl_seconds: float = 30.0,
        probe: Callable[[], FrozenSet[str]] = probe_installed_packages,
    ) -> None:
        self._cache = TTLCache(ttl_seconds, self._load)
        self._probe = probe
        self.degraded = False

    @property
    def ttl_seconds(self) -> float:
        return self._cache.ttl_seconds

    def packages(self, force: bool = False) -> FrozenSet[str]:
        """Return the known installed top-level packages."""
        return self._cache.get(force=force)

    def _load(self) -> FrozenSet[str]:
        try:
            packages = frozenset(self._probe())
        except Exception:  # never raise: degrade to the static table
            self.degraded = True
            return STATIC_KNOWN_PACKAGES
        self.degraded = False
        return packages


def classify_import(
    name: str,
    relative: bool,
    local_roots: FrozenSet[str],
    installed: FrozenSet[str],
) -> str:
    """Classify an imported module name into one of the three tiers."""
    if relative:
        return TIER_LOCAL
    top = name.partition(".")[0]
    if top in sys.stdlib_module_names:
        return TIER_STDLIB
    if top in local_roots:
        return TIER_LOCAL
    # Anything that is neither stdlib nor local is third-party; membership
    # in the probed set is the common case, unknown names fall back to
    # third-party as well since they cannot be anything else.
    return TIER_THIRD_PARTY
