"""Process-local caches for the scanning service.

Two caches live here:

* :class:`InstallProbeCache` caches the installed-package probe with a TTL
  (default 30s) and degrades to a static table when pip is missing or the
  environment is offline. It never raises.
* :class:`ParseCache` caches per-file parse results keyed by path and
  invalidated by (mtime, size). Per-file locks guarantee that concurrent
  callers parse the same file exactly once.
"""

# Standard
import os
import shutil
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple

# Static fallback table used when pip is unavailable or the probe fails.
STATIC_FALLBACK_PACKAGES: Tuple[str, ...] = (
    "certifi",
    "charset-normalizer",
    "idna",
    "pip",
    "requests",
    "setuptools",
    "urllib3",
    "wheel",
)

DEFAULT_TTL_SECONDS = 30


def probe_installed_packages() -> Dict:
    """Probe the environment for installed third-party packages.

    Raises RuntimeError when pip is not available so callers can degrade.
    """
    if shutil.which("pip") is None:
        raise RuntimeError("pip not available on PATH")
    # Standard
    import importlib.metadata

    names = set()
    for dist in importlib.metadata.distributions():
        name = dist.metadata.get("Name")
        if name:
            names.add(name.lower())
    if not names:
        raise RuntimeError("package probe returned no distributions")
    return {"source": "pip", "packages": sorted(names)}


def static_fallback_snapshot() -> Dict:
    """Snapshot used when the live probe is unavailable."""
    return {"source": "static", "packages": sorted(STATIC_FALLBACK_PACKAGES)}


class InstallProbeCache:
    """TTL cache around the installed-package probe.

    ``get(force=True)`` bypasses the TTL so newly installed packages are
    visible on the very next call. Probe failures never propagate; the
    static fallback table is returned instead.
    """

    def __init__(
        self,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        probe: Optional[Callable[[], Dict]] = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.ttl_seconds = ttl_seconds
        self._probe = probe or probe_installed_packages
        self._clock = clock
        self._lock = threading.Lock()
        self._snapshot: Optional[Dict] = None
        self._fetched_at: float = 0.0

    def get(self, force: bool = False) -> Dict:
        with self._lock:
            now = self._clock()
            fresh = (
                self._snapshot is not None
                and (now - self._fetched_at) < self.ttl_seconds
            )
            if not force and fresh:
                return self._snapshot
            self._snapshot = self._probe_safely()
            self._fetched_at = now
            return self._snapshot

    def _probe_safely(self) -> Dict:
        try:
            snapshot = self._probe()
            if not isinstance(snapshot, dict) or "packages" not in snapshot:
                raise RuntimeError("malformed probe result")
            return snapshot
        except Exception:
            return static_fallback_snapshot()


class ParseCache:
    """Per-file parse cache invalidated by (mtime, size).

    A changed file never hits a stale entry: the cache key embeds the
    file's mtime and size at parse time. Per-file locks ensure concurrent
    requests for the same file parse it exactly once; entries are written
    atomically after a full parse, so a cancelled scan never leaves a
    half-written entry behind.
    """

    def __init__(self):
        self._entries: Dict[str, Tuple[Tuple[int, int], object]] = {}
        self._locks: Dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def _lock_for(self, path: str) -> threading.Lock:
        with self._guard:
            lock = self._locks.get(path)
            if lock is None:
                lock = threading.Lock()
                self._locks[path] = lock
            return lock

    @staticmethod
    def _fingerprint(path: str) -> Tuple[int, int]:
        stat = os.stat(path)
        return (stat.st_mtime_ns, stat.st_size)

    def get_or_parse(
        self,
        path,
        parse_fn: Callable[[str], object],
        cancel_event: Optional[threading.Event] = None,
    ):
        """Return the cached parse for ``path`` or compute it once.

        If ``cancel_event`` is set before the parse completes, the result
        is computed but not cached, keeping cancelled scans out of the
        cache.
        """
        path = str(path)
        lock = self._lock_for(path)
        with lock:
            fingerprint = self._fingerprint(path)
            entry = self._entries.get(path)
            if entry is not None and entry[0] == fingerprint:
                return entry[1]
            result = parse_fn(path)
            if cancel_event is None or not cancel_event.is_set():
                self._entries[path] = (fingerprint, result)
            return result

    def contains(self, path) -> bool:
        return str(path) in self._entries

    def __len__(self) -> int:
        return len(self._entries)
