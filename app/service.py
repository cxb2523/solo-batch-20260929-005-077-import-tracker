"""
Reusable scanning library layer.

`scan_repository(root, depth)` walks a repository, parses every python
file's imports, classifies them into three tiers (stdlib / third_party /
local) and builds the local module dependency chains. It returns a single
unified structure that is shared verbatim by the HTTP layer and the CLI.
"""

# Standard
from concurrent.futures import ThreadPoolExecutor, wait
from typing import Dict, List, Optional
import os

# Local
from .cache import ParseCache
from .parsing import parse_imports
from .policy import (
    TIER_LOCAL,
    TIER_STDLIB,
    TIER_THIRD_PARTY,
    InstallProbe,
    classify_import,
)

DEFAULT_DEPTH = 3
DEFAULT_MAX_WORKERS = 8
DEFAULT_TIMEOUT_MS = 1500


class ScanTimeoutError(Exception):
    """Raised when a repository scan exceeds its timeout."""


_shared_parse_cache = ParseCache()
_shared_probe = InstallProbe()


def scan_repository(
    root: str,
    depth: int = DEFAULT_DEPTH,
    *,
    max_workers: int = DEFAULT_MAX_WORKERS,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    force: bool = False,
    parse_cache: Optional[ParseCache] = None,
    probe: Optional[InstallProbe] = None,
) -> Dict:
    """Scan `root` down to `depth` directory levels and return the unified
    scan structure.

    Parsing runs on a bounded thread pool (`max_workers`) and aborts with
    `ScanTimeoutError` after `timeout_ms`; on cancellation nothing partial
    is committed (the parse cache only ever stores fully computed results).
    """
    root = os.path.abspath(str(root))
    if not os.path.isdir(root):
        raise FileNotFoundError(f"Not a directory: {root}")
    parse_cache = parse_cache if parse_cache is not None else _shared_parse_cache
    probe = probe if probe is not None else _shared_probe

    files = _collect_files(root, depth)
    installed = probe.packages(force=force)
    local_roots = _local_roots(root)

    parsed = _parse_all(files, parse_cache, max_workers, timeout_ms)

    module_names = {_module_name(root, path) for path in files}
    tier_names: Dict[str, set] = {
        TIER_STDLIB: set(),
        TIER_THIRD_PARTY: set(),
        TIER_LOCAL: set(),
    }
    modules = []
    chains = []
    for path in files:
        module_name = _module_name(root, path)
        package_ctx = (
            module_name
            if os.path.basename(path) == "__init__.py"
            else module_name.rpartition(".")[0]
        )
        imports = {TIER_STDLIB: set(), TIER_THIRD_PARTY: set(), TIER_LOCAL: set()}
        depends_on = set()
        for record in parsed[path]:
            tier = classify_import(
                record.name, record.relative, local_roots, installed
            )
            if tier == TIER_LOCAL:
                resolved = _resolve_local(record, package_ctx, module_names)
                display = resolved or record.name
                if resolved and resolved != module_name:
                    depends_on.add(resolved)
            else:
                display = record.name
            imports[tier].add(display)
            tier_names[tier].add(display)
        modules.append(
            {
                "name": module_name,
                "path": os.path.relpath(path, root),
                "imports": {tier: sorted(names) for tier, names in imports.items()},
            }
        )
        chains.append({"module": module_name, "depends_on": sorted(depends_on)})

    modules.sort(key=lambda mod: mod["name"])
    chains.sort(key=lambda chain: chain["module"])

    return {
        "root": root,
        "depth": depth,
        "file_count": len(files),
        "counts": {tier: len(names) for tier, names in tier_names.items()},
        "modules": modules,
        "chains": chains,
        "probe": {
            "degraded": probe.degraded,
            "installed_count": len(installed),
            "ttl_seconds": probe.ttl_seconds,
        },
    }


def _collect_files(root: str, depth: int) -> List[str]:
    """Collect python files under `root`, descending at most `depth` levels."""
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames if not d.startswith(".") and d != "__pycache__"
        )
        rel = os.path.relpath(dirpath, root)
        level = 0 if rel == "." else rel.count(os.sep) + 1
        if level >= depth:
            dirnames[:] = []
        if level > depth:
            continue
        for filename in sorted(filenames):
            if filename.endswith(".py"):
                files.append(os.path.join(dirpath, filename))
    return sorted(files)


def _module_name(root: str, path: str) -> str:
    """Convert a file path under `root` into a dotted module name."""
    rel = os.path.splitext(os.path.relpath(path, root))[0]
    parts = rel.split(os.sep)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    if not parts:
        return os.path.basename(os.path.normpath(root))
    return ".".join(parts)


def _local_roots(root: str) -> frozenset:
    """Top-level module/package names available directly under `root`."""
    names = set()
    for entry in os.listdir(root):
        full = os.path.join(root, entry)
        if entry.startswith("."):
            continue
        if os.path.isdir(full) and entry != "__pycache__":
            names.add(entry)
        elif entry.endswith(".py"):
            names.add(entry[: -len(".py")])
    return frozenset(names)


def _resolve_local(record, package_ctx: str, module_names: set) -> Optional[str]:
    """Resolve a local import record to a scanned module name, if possible."""
    candidates = []
    if record.relative:
        if package_ctx:
            candidates.append(f"{package_ctx}.{record.name}")
    candidates.append(record.name)
    for candidate in candidates:
        parts = candidate.split(".")
        for idx in range(len(parts), 0, -1):
            prefix = ".".join(parts[:idx])
            if prefix in module_names:
                return prefix
    return None


def _parse_one(parse_cache: ParseCache, path: str):
    """Parse a single file through the cache; unreadable files are empty."""
    try:
        return parse_cache.get_or_compute(path, parse_imports)
    except OSError:
        return []


def _parse_all(
    files: List[str],
    parse_cache: ParseCache,
    max_workers: int,
    timeout_ms: int,
) -> Dict[str, list]:
    """Parse all files on a bounded pool; cancel cleanly on timeout."""
    executor = ThreadPoolExecutor(max_workers=max_workers)
    futures = {
        executor.submit(_parse_one, parse_cache, path): path for path in files
    }
    try:
        done, not_done = wait(futures, timeout=timeout_ms / 1000.0)
        if not_done:
            for future in not_done:
                future.cancel()
            raise ScanTimeoutError(
                f"Scan timed out after {timeout_ms}ms "
                f"({len(done)}/{len(futures)} files parsed)"
            )
        return {futures[future]: future.result() for future in done}
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
