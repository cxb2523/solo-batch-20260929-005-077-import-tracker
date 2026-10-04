"""
Tests for the app scanning service layers: cache invalidation, concurrent
de-duplication, timeout cancellation, offline degradation, the HTTP/library
contract, and the thin CLI shell.
"""

# Standard
import json
import os
import threading
import time

# Third Party
import pytest

# Local
from app import cli, policy, service
from app.cache import ParseCache
from app.parsing import parse_imports


class StubProbe:
    """Deterministic probe stub for service-level tests."""

    degraded = False
    ttl_seconds = 30.0

    def __init__(self, packages=frozenset({"requests"})):
        self._packages = packages

    def packages(self, force=False):
        return self._packages


def _write(path, content):
    path.write_text(content, encoding="utf-8")


## Cache invalidation ##########################################################


class TestParseCacheInvalidation:
    """A modified file must never hit a stale cached result."""

    def test_unmodified_file_hits_cache(self, tmp_path):
        target = tmp_path / "mod.py"
        _write(target, "import os\n")
        cache = ParseCache()
        calls = []

        def counting_parse(path):
            calls.append(path)
            return parse_imports(path)

        first = cache.get_or_compute(str(target), counting_parse)
        second = cache.get_or_compute(str(target), counting_parse)
        assert first == second
        assert calls == [str(target)]

    def test_modified_file_invalidates(self, tmp_path):
        target = tmp_path / "mod.py"
        _write(target, "import os\n")
        cache = ParseCache()
        first = cache.get_or_compute(str(target), parse_imports)
        assert ("os", False) in first

        _write(target, "import os\nimport json\n")
        second = cache.get_or_compute(str(target), parse_imports)
        assert ("json", False) in second

    def test_same_size_edit_still_invalidates_via_mtime(self, tmp_path):
        target = tmp_path / "mod.py"
        _write(target, "import os\n")
        cache = ParseCache()
        assert cache.get_or_compute(str(target), parse_imports) == [("os", False)]

        # Same size, different content, explicitly newer mtime
        _write(target, "import re\n")
        now = time.time_ns()
        os.utime(target, ns=(now, now))
        assert cache.get_or_compute(str(target), parse_imports) == [("re", False)]


## Concurrent de-duplication ###################################################


class TestConcurrentDedup:
    """Concurrent requests for the same file parse it exactly once."""

    def test_same_file_parsed_once_under_concurrency(self, tmp_path):
        target = tmp_path / "mod.py"
        _write(target, "import os\nimport sys\n")
        cache = ParseCache()
        calls = []
        calls_lock = threading.Lock()
        start = threading.Barrier(16)

        def slow_parse(path):
            with calls_lock:
                calls.append(path)
            time.sleep(0.05)
            return parse_imports(path)

        results = []

        def worker():
            start.wait()
            results.append(cache.get_or_compute(str(target), slow_parse))

        threads = [threading.Thread(target=worker) for _ in range(16)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert calls == [str(target)]
        assert all(result == results[0] for result in results)


## Timeout cancellation ########################################################


class TestScanTimeout:
    """A timed-out scan cancels remaining work and leaves no half results."""

    def test_timeout_cancels_and_cache_stays_clean(self, tmp_path, monkeypatch):
        for idx in range(20):
            _write(tmp_path / f"mod{idx}.py", "import os\n")
        cache = ParseCache()

        def slow_parse(path):
            time.sleep(0.5)
            return parse_imports(path)

        monkeypatch.setattr(service, "parse_imports", slow_parse)
        with pytest.raises(service.ScanTimeoutError):
            service.scan_repository(
                str(tmp_path),
                2,
                timeout_ms=100,
                parse_cache=cache,
                probe=StubProbe(),
            )
        # No half-committed entries: the cache only stores complete parses
        assert len(cache) == 0

    def test_scan_succeeds_after_timeout(self, tmp_path, monkeypatch):
        for idx in range(4):
            _write(tmp_path / f"mod{idx}.py", "import os\n")
        cache = ParseCache()

        def slow_parse(path):
            time.sleep(0.3)
            return parse_imports(path)

        monkeypatch.setattr(service, "parse_imports", slow_parse)
        with pytest.raises(service.ScanTimeoutError):
            service.scan_repository(
                str(tmp_path),
                2,
                timeout_ms=50,
                parse_cache=cache,
                probe=StubProbe(),
            )
        # A retry with a generous timeout works; entries committed by the
        # cancelled scan are complete results for the same parser.
        result = service.scan_repository(
            str(tmp_path), 2, timeout_ms=10000, parse_cache=cache, probe=StubProbe()
        )
        assert result["file_count"] == 4
        assert result["counts"]["stdlib"] == 1


## Offline degradation #########################################################


class TestOfflineDegrade:
    """No pip / offline must degrade to the static table without raising."""

    def test_probe_failure_degrades_to_static_table(self):
        def boom():
            raise RuntimeError("offline")

        probe = policy.InstallProbe(probe=boom)
        packages = probe.packages()
        assert packages == policy.STATIC_KNOWN_PACKAGES
        assert probe.degraded is True

    def test_scan_survives_offline_probe(self, tmp_path):
        _write(tmp_path / "mod.py", "import os\nimport yaml\n")

        def boom():
            raise RuntimeError("offline")

        probe = policy.InstallProbe(probe=boom)
        result = service.scan_repository(str(tmp_path), 1, probe=probe)
        assert result["probe"]["degraded"] is True
        assert result["counts"]["stdlib"] == 1
        assert result["counts"]["third_party"] == 1

    def test_new_package_visible_next_round(self):
        current = {"packages": frozenset({"old_pkg"})}
        probe = policy.InstallProbe(
            ttl_seconds=30.0, probe=lambda: current["packages"]
        )
        assert "new_pkg" not in probe.packages()

        # A package gets "installed": within the TTL the cached value is used
        current["packages"] = frozenset({"old_pkg", "new_pkg"})
        assert "new_pkg" not in probe.packages()
        # ...but force=true makes it visible immediately
        assert "new_pkg" in probe.packages(force=True)

    def test_ttl_expiry_refreshes(self):
        current = {"packages": frozenset({"old_pkg"})}
        probe = policy.InstallProbe(
            ttl_seconds=0.05, probe=lambda: current["packages"]
        )
        assert probe.packages() == frozenset({"old_pkg"})
        current["packages"] = frozenset({"old_pkg", "new_pkg"})
        time.sleep(0.06)
        assert "new_pkg" in probe.packages()


## Unified structure / chains ##################################################


class TestScanStructure:
    def test_three_tier_counts_and_chains(self, tmp_path):
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        _write(pkg / "__init__.py", "from . import core\n")
        _write(pkg / "core.py", "import os\nimport requests\nfrom . import util\n")
        _write(pkg / "util.py", "import json\n")
        result = service.scan_repository(str(tmp_path), 2, probe=StubProbe())

        assert result["counts"] == {"stdlib": 2, "third_party": 1, "local": 2}
        chains = {chain["module"]: chain["depends_on"] for chain in result["chains"]}
        assert chains["pkg"] == ["pkg.core"]
        assert chains["pkg.core"] == ["pkg.util"]
        assert chains["pkg.util"] == []
        module_names = [mod["name"] for mod in result["modules"]]
        assert module_names == sorted(module_names)

    def test_depth_limits_walk(self, tmp_path):
        deep = tmp_path / "a" / "b" / "c"
        deep.mkdir(parents=True)
        _write(tmp_path / "top.py", "import os\n")
        _write(deep / "deep.py", "import sys\n")
        shallow = service.scan_repository(str(tmp_path), 1, probe=StubProbe())
        full = service.scan_repository(str(tmp_path), 3, probe=StubProbe())
        assert shallow["file_count"] == 1
        assert full["file_count"] == 2


## HTTP contract ###############################################################


class TestApiContract:
    """The HTTP layer must return exactly what the library returns."""

    def test_api_matches_library_result(self, tmp_path):
        # Third Party
        from fastapi.testclient import TestClient

        # Local
        from app.api import app

        pkg = tmp_path / "pkg"
        pkg.mkdir()
        _write(pkg / "__init__.py", "from . import core\n")
        _write(pkg / "core.py", "import os\nimport requests\n")

        client = TestClient(app)
        response = client.get(
            "/api/scan", params={"root": str(tmp_path), "depth": 2}
        )
        assert response.status_code == 200
        expected = service.scan_repository(str(tmp_path), 2)
        body = response.json()
        assert body == json.loads(json.dumps(expected))
        assert list(body.keys()) == list(expected.keys())

    def test_api_timeout_returns_504(self, tmp_path, monkeypatch):
        # Third Party
        from fastapi.testclient import TestClient

        # Local
        from app.api import app

        _write(tmp_path / "mod.py", "import os\n")
        monkeypatch.setattr(
            service, "parse_imports", lambda path: (time.sleep(0.5), [])[1]
        )
        client = TestClient(app)
        response = client.get(
            "/api/scan",
            params={"root": str(tmp_path), "depth": 1, "timeout_ms": 50},
        )
        assert response.status_code == 504

    def test_panel_renders(self):
        # Third Party
        from fastapi.testclient import TestClient

        # Local
        from app.api import app

        client = TestClient(app)
        response = client.get("/panel", params={"root": ".", "depth": 1})
        assert response.status_code == 200
        assert "Import Tracker Panel" in response.text


## CLI thin shell ################################################################


class TestCli:
    def test_scan_command(self, tmp_path, capsys):
        _write(tmp_path / "mod.py", "import os\n")
        cli.main(["scan", "--root", str(tmp_path), "--depth", "1"])
        data = json.loads(capsys.readouterr().out)
        assert data["file_count"] == 1
        assert data["counts"]["stdlib"] == 1

    def test_legacy_track_command(self, monkeypatch, capsys):
        # Local
        import import_tracker.import_tracker as legacy

        captured = {}

        def fake_track_module(**kwargs):
            captured.update(kwargs)
            return {"mylib": ["requests"]}

        monkeypatch.setattr(legacy, "track_module", fake_track_module)
        cli.main(["--name", "mylib", "--detect_transitive"])
        assert json.loads(capsys.readouterr().out) == {"mylib": ["requests"]}
        assert captured["module_name"] == "mylib"
        assert captured["detect_transitive"] is True
