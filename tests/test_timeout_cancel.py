"""Slow scans must abort at the deadline and leave no half-written cache."""

# Standard
import time

# Local
from app.cache import InstallProbeCache, ParseCache
from app.policy import ScanPolicy


def _install_cache():
    return InstallProbeCache(probe=lambda: {"source": "pip", "packages": []})


def test_timeout_aborts_and_cache_stays_clean(tmp_path, monkeypatch):
    # Local
    import app.service as service

    (tmp_path / "fast.py").write_text("import os\n", encoding="utf-8")
    (tmp_path / "slow.py").write_text("import sys\n", encoding="utf-8")

    real_parse_file = service.parse_file

    def slow_parse_file(path):
        if str(path).endswith("slow.py"):
            time.sleep(0.6)
        return real_parse_file(path)

    monkeypatch.setattr(service, "parse_file", slow_parse_file)

    parse_cache = ParseCache()
    started = time.monotonic()
    result = service.scan_repository(
        tmp_path,
        policy=ScanPolicy(max_workers=8, timeout_ms=200),
        parse_cache=parse_cache,
        install_cache=_install_cache(),
    )
    elapsed = time.monotonic() - started

    assert result["timed_out"] is True
    assert result["files_total"] == 2
    assert result["files_scanned"] == 1
    assert elapsed < 0.6
    assert [m["name"] for m in result["modules"]] == ["fast"]

    # The cancelled file must not leave a half-written cache entry, even
    # after its worker finishes in the background.
    slow_path = tmp_path / "slow.py"
    time.sleep(0.7)
    assert not parse_cache.contains(slow_path)
    assert parse_cache.contains(tmp_path / "fast.py")


def test_fast_scan_does_not_timeout(tmp_path):
    # Local
    import app.service as service

    (tmp_path / "a.py").write_text("import os\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("import sys\n", encoding="utf-8")

    result = service.scan_repository(
        tmp_path,
        policy=ScanPolicy(max_workers=8, timeout_ms=1500),
        parse_cache=ParseCache(),
        install_cache=_install_cache(),
    )
    assert result["timed_out"] is False
    assert result["files_scanned"] == result["files_total"] == 2
