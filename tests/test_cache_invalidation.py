"""Parse cache invalidation: a changed file must never hit a stale entry."""

# Standard
import os

# Local
from app.cache import ParseCache


def _write(path, text):
    path.write_text(text, encoding="utf-8")


def test_unchanged_file_hits_cache(tmp_path):
    target = tmp_path / "mod.py"
    _write(target, "import os\n")
    cache = ParseCache()
    calls = []

    def parse(path):
        calls.append(path)
        return ("parsed", os.path.getsize(path))

    first = cache.get_or_parse(target, parse)
    second = cache.get_or_parse(target, parse)
    assert first == second
    assert len(calls) == 1


def test_changed_content_invalidates(tmp_path):
    target = tmp_path / "mod.py"
    _write(target, "import os\n")
    cache = ParseCache()
    calls = []

    def parse(path):
        calls.append(path)
        return open(path, encoding="utf-8").read()

    assert cache.get_or_parse(target, parse) == "import os\n"

    # Change size and mtime: must re-parse.
    _write(target, "import os\nimport sys\n")
    assert cache.get_or_parse(target, parse) == "import os\nimport sys\n"
    assert len(calls) == 2


def test_same_size_new_mtime_invalidates(tmp_path):
    target = tmp_path / "mod.py"
    _write(target, "import aaa\n")
    cache = ParseCache()
    calls = []

    def parse(path):
        calls.append(path)
        return open(path, encoding="utf-8").read()

    cache.get_or_parse(target, parse)

    # Same size, different content, bumped mtime: must re-parse.
    _write(target, "import bbb\n")
    stat = os.stat(target)
    os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    assert cache.get_or_parse(target, parse) == "import bbb\n"
    assert len(calls) == 2


def test_scan_repository_uses_fresh_results_after_edit(sample_repo):
    # Local
    from app.cache import InstallProbeCache, ParseCache
    from app.service import scan_repository

    parse_cache = ParseCache()
    install_cache = InstallProbeCache(probe=lambda: {"source": "pip", "packages": []})

    first = scan_repository(
        sample_repo, parse_cache=parse_cache, install_cache=install_cache
    )
    assert first["counts"]["stdlib"] == 4  # json, os, re, sys

    target = sample_repo / "tool.py"
    target.write_text("import sys\nimport pkg\nimport collections\n", encoding="utf-8")

    second = scan_repository(
        sample_repo, parse_cache=parse_cache, install_cache=install_cache
    )
    assert second["counts"]["stdlib"] == 5  # + collections
