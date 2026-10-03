"""Reusable scanning library.

``scan_repository(root, depth)`` is the single entry point shared by the
HTTP API, the panel, and the CLI. It returns one unified structure; the
HTTP layer serializes it verbatim so library and wire contracts never
drift apart.
"""

# Standard
import concurrent.futures
import threading
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

# Local
from .cache import InstallProbeCache, ParseCache
from .parser import ParsedFile, parse_file
from .policy import (
    DEFAULT_DEPTH,
    TIERS,
    ScanPolicy,
    classify_module,
    stdlib_names,
)

SKIP_DIRS = {".git", ".hg", ".svn", "__pycache__", ".venv", "venv", "node_modules"}

_DEFAULT_PARSE_CACHE = ParseCache()
_DEFAULT_INSTALL_CACHE = InstallProbeCache()
_STDLIB = stdlib_names()


def default_parse_cache() -> ParseCache:
    return _DEFAULT_PARSE_CACHE


def default_install_cache() -> InstallProbeCache:
    return _DEFAULT_INSTALL_CACHE


def _iter_python_files(root: Path) -> List[Path]:
    files = []
    for path in sorted(root.rglob("*.py")):
        if any(part in SKIP_DIRS or part.startswith(".") for part in path.parts):
            continue
        files.append(path)
    return files


def _module_name(root: Path, path: Path) -> str:
    parts = list(path.relative_to(root).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) if parts else path.stem


def _resolve_local(dotted: str, modules: Set[str]) -> Optional[str]:
    """Longest-prefix match of a dotted name against known modules."""
    parts = dotted.split(".")
    for end in range(len(parts), 0, -1):
        candidate = ".".join(parts[:end])
        if candidate in modules:
            return candidate
    return None


def _resolve_imports(
    module: str,
    parsed: ParsedFile,
    modules: Set[str],
    local_roots: Set[str],
    package_parts: List[str],
) -> Tuple[Dict[str, List[str]], List[str]]:
    """Split a module's imports into tiers and resolve local edges."""
    tiered: Dict[str, Set[str]] = {tier: set() for tier in TIERS}
    local_deps: Set[str] = set()

    def note(dotted: str):
        top = dotted.split(".")[0]
        tier = classify_module(top, local_roots, _STDLIB)
        tiered[tier].add(top)
        if tier == "local":
            target = _resolve_local(dotted, modules)
            if target and target != module:
                local_deps.add(target)

    for dotted in parsed.absolute_imports:
        note(dotted)

    for level, rel_module, names in parsed.relative_imports:
        anchor = package_parts[: len(package_parts) - (level - 1)] if level > 1 else list(package_parts)
        base = ".".join(anchor + ([rel_module] if rel_module else []))
        candidates = [base] if base else []
        for name in names:
            candidates.append(".".join([base, name]) if base else name)
        for candidate in candidates:
            if not candidate:
                continue
            tiered["local"].add(candidate.split(".")[0])
            target = _resolve_local(candidate, modules)
            if target and target != module:
                local_deps.add(target)

    return (
        {tier: sorted(names) for tier, names in tiered.items()},
        sorted(local_deps),
    )


def _build_chains(
    dependencies: Dict[str, List[str]],
    depth: int,
) -> Dict:
    """Expand local module dependency chains up to ``depth`` levels."""
    def expand(name: str, level: int, trail: Set[str]) -> Dict:
        node: Dict = {}
        if level >= depth:
            return node
        for dep in dependencies.get(name, []):
            if dep in trail:
                node[dep] = {}
                continue
            node[dep] = expand(dep, level + 1, trail | {dep})
        return node

    if depth <= 0:
        return {}
    return {name: expand(name, 1, {name}) for name in sorted(dependencies)}


def scan_repository(
    root,
    depth: int = DEFAULT_DEPTH,
    *,
    force: bool = False,
    policy: Optional[ScanPolicy] = None,
    parse_cache: Optional[ParseCache] = None,
    install_cache: Optional[InstallProbeCache] = None,
) -> Dict:
    """Scan ``root`` and return the unified repository structure.

    Parsing runs on a bounded thread pool (``policy.max_workers``) and is
    aborted after ``policy.timeout_ms``; cancelled files are excluded from
    the result and never reach the parse cache.
    """
    policy = policy or ScanPolicy()
    parse_cache = parse_cache if parse_cache is not None else _DEFAULT_PARSE_CACHE
    install_cache = install_cache if install_cache is not None else _DEFAULT_INSTALL_CACHE

    root_path = Path(root).resolve()
    files = _iter_python_files(root_path)
    names = {path: _module_name(root_path, path) for path in files}
    modules = set(names.values())
    local_roots = {name.split(".")[0] for name in modules}

    cancel_event = threading.Event()
    parsed: Dict[Path, ParsedFile] = {}
    timed_out = False

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=policy.max_workers)
    try:
        futures = {
            executor.submit(
                parse_cache.get_or_parse, path, parse_file, cancel_event
            ): path
            for path in files
        }
        done, not_done = concurrent.futures.wait(
            futures, timeout=policy.timeout_ms / 1000.0
        )
        if not_done:
            timed_out = True
            cancel_event.set()
            for future in not_done:
                future.cancel()
        for future in done:
            parsed[futures[future]] = future.result()
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    module_entries = []
    dependencies: Dict[str, List[str]] = {}
    counts: Dict[str, Set[str]] = {tier: set() for tier in TIERS}
    for path in sorted(parsed, key=lambda p: names[p]):
        name = names[path]
        package_parts = (
            name.split(".") if path.stem == "__init__" else name.split(".")[:-1]
        )
        tiered, local_deps = _resolve_imports(
            name, parsed[path], modules, local_roots, package_parts
        )
        for tier, imported in tiered.items():
            counts[tier].update(imported)
        dependencies[name] = local_deps
        module_entries.append(
            {
                "name": name,
                "path": str(path.relative_to(root_path)),
                "imports": tiered,
            }
        )

    installed = install_cache.get(force=force)

    return {
        "root": str(root_path),
        "depth": depth,
        "timed_out": timed_out,
        "files_total": len(files),
        "files_scanned": len(parsed),
        "counts": {tier: len(counts[tier]) for tier in TIERS},
        "modules": module_entries,
        "dependencies": {name: dependencies[name] for name in sorted(dependencies)},
        "chains": _build_chains(dependencies, depth),
        "installed": installed,
    }
