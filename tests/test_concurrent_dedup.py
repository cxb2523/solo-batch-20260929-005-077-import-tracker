"""Concurrent requests for the same file must parse it exactly once."""

# Standard
import threading
import time

# Local
from app.cache import ParseCache


def test_concurrent_parse_dedup(tmp_path):
    target = tmp_path / "mod.py"
    target.write_text("import os\n", encoding="utf-8")
    cache = ParseCache()
    calls = []
    calls_lock = threading.Lock()
    started = threading.Event()

    def slow_parse(path):
        with calls_lock:
            calls.append(path)
        started.set()
        time.sleep(0.2)  # hold the per-file lock so others queue up
        return "result"

    threads = [
        threading.Thread(target=cache.get_or_parse, args=(target, slow_parse))
        for _ in range(8)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(calls) == 1
    assert len(cache) == 1


def test_concurrent_scan_dedup(sample_repo, monkeypatch):
    # Local
    import app.service as service
    from app.cache import InstallProbeCache, ParseCache

    real_parse_file = service.parse_file
    calls = []
    calls_lock = threading.Lock()

    def counting_parse(path):
        with calls_lock:
            calls.append(str(path))
        time.sleep(0.05)
        return real_parse_file(path)

    monkeypatch.setattr(service, "parse_file", counting_parse)

    parse_cache = ParseCache()
    install_cache = InstallProbeCache(probe=lambda: {"source": "pip", "packages": []})

    results = []

    def run_scan():
        results.append(
            service.scan_repository(
                sample_repo,
                parse_cache=parse_cache,
                install_cache=install_cache,
            )
        )

    threads = [threading.Thread(target=run_scan) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    # Each file parsed exactly once across all concurrent scans.
    assert len(calls) == len(set(calls))
    assert all(result["files_scanned"] == results[0]["files_scanned"] for result in results)
