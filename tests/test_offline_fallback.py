"""Install-probe degradation: no pip / offline must fall back, never raise."""

# Third Party
import pytest

# Local
from app.cache import (
    STATIC_FALLBACK_PACKAGES,
    InstallProbeCache,
    probe_installed_packages,
)


def test_probe_failure_falls_back_to_static():
    def broken_probe():
        raise RuntimeError("offline")

    cache = InstallProbeCache(probe=broken_probe)
    snapshot = cache.get()
    assert snapshot["source"] == "static"
    assert snapshot["packages"] == sorted(STATIC_FALLBACK_PACKAGES)


def test_missing_pip_falls_back(monkeypatch):
    # Local
    import app.cache as cache_module

    monkeypatch.setattr(cache_module.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError):
        probe_installed_packages()
    snapshot = InstallProbeCache().get()
    assert snapshot["source"] == "static"


def test_malformed_probe_result_falls_back():
    cache = InstallProbeCache(probe=lambda: {"oops": True})
    assert cache.get()["source"] == "static"


def test_ttl_caching_and_force_refresh():
    now = [1000.0]
    calls = []

    def probe():
        calls.append(len(calls))
        return {"source": "pip", "packages": [f"pkg-{len(calls)}"]}

    cache = InstallProbeCache(ttl_seconds=30, probe=probe, clock=lambda: now[0])

    first = cache.get()
    assert first["packages"] == ["pkg-1"]

    # Within TTL: cached, no new probe.
    now[0] += 10
    assert cache.get()["packages"] == ["pkg-1"]
    assert len(calls) == 1

    # force=True bypasses the TTL: newly installed package visible now.
    assert cache.get(force=True)["packages"] == ["pkg-2"]
    assert len(calls) == 2

    # After TTL expiry the next round picks up new packages automatically.
    now[0] += 31
    assert cache.get()["packages"] == ["pkg-3"]
    assert len(calls) == 3


def test_scan_repository_degrades_without_raising(sample_repo):
    # Local
    from app.cache import ParseCache
    from app.service import scan_repository

    def broken_probe():
        raise ConnectionError("no network")

    result = scan_repository(
        sample_repo,
        parse_cache=ParseCache(),
        install_cache=InstallProbeCache(probe=broken_probe),
    )
    assert result["installed"]["source"] == "static"
    assert result["installed"]["packages"] == sorted(STATIC_FALLBACK_PACKAGES)
