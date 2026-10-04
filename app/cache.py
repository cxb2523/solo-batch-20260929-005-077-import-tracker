"""
Thread-safe caches used by the scanning service.
"""

# Standard
from typing import Callable, Dict, Optional, Tuple, TypeVar
import os
import threading
import time

T = TypeVar("T")


class ParseCache:
    """Caches per-file parse results keyed by path.

    Entries are validated against the file's ``(mtime_ns, size)`` signature,
    so a modified file never hits a stale result. Per-path locks guarantee
    that concurrent requests for the same file compute the result exactly
    once (double-checked locking prevents duplicate computation).
    """

    def __init__(self) -> None:
        self._entries: Dict[str, Tuple[Tuple[int, int], object]] = {}
        self._locks: Dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def __len__(self) -> int:
        with self._guard:
            return len(self._entries)

    def clear(self) -> None:
        with self._guard:
            self._entries.clear()

    def get_or_compute(self, path: str, compute: Callable[[str], T]) -> T:
        """Return the cached result for `path` or compute it exactly once."""
        signature = self._signature(path)
        with self._guard:
            entry = self._entries.get(path)
            if entry is not None and entry[0] == signature:
                return entry[1]
            lock = self._locks.setdefault(path, threading.Lock())
        with lock:
            # Double-checked: another thread may have filled the entry while
            # this thread waited on the per-path lock.
            with self._guard:
                entry = self._entries.get(path)
                if entry is not None and entry[0] == signature:
                    return entry[1]
            result = compute(path)
            with self._guard:
                self._entries[path] = (signature, result)
            return result

    @staticmethod
    def _signature(path: str) -> Tuple[int, int]:
        stat = os.stat(path)
        return (stat.st_mtime_ns, stat.st_size)


class TTLCache:
    """A single-value in-process cache with a time-to-live."""

    def __init__(self, ttl_seconds: float, loader: Callable[[], T]) -> None:
        self.ttl_seconds = ttl_seconds
        self._loader = loader
        self._lock = threading.Lock()
        self._value: Optional[T] = None
        self._expires_at: float = 0.0

    def get(self, force: bool = False) -> T:
        """Return the cached value, reloading when expired or forced."""
        now = time.monotonic()
        with self._lock:
            if not force and self._value is not None and now < self._expires_at:
                return self._value
            value = self._loader()
            self._value = value
            self._expires_at = now + self.ttl_seconds
            return value
