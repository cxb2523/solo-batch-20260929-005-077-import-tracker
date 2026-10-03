# Standard
import textwrap

# Third Party
import pytest


@pytest.fixture
def sample_repo(tmp_path):
    """A small repo with stdlib, third-party-free, and local imports."""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("from . import core\n", encoding="utf-8")
    (pkg / "core.py").write_text(
        textwrap.dedent(
            """
            import json
            import os
            import pkg.util
            """
        ).lstrip(),
        encoding="utf-8",
    )
    (pkg / "util.py").write_text(
        textwrap.dedent(
            """
            import re
            from . import core
            """
        ).lstrip(),
        encoding="utf-8",
    )
    (tmp_path / "tool.py").write_text(
        "import sys\nimport pkg\n", encoding="utf-8"
    )
    return tmp_path
