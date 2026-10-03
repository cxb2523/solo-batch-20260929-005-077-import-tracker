"""Static parsing of python source files into import references.

This module is pure parsing: no caching, no policy. Results are plain
immutable data so they can be safely shared across threads and cached.
"""

# Standard
import ast
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple


@dataclass(frozen=True)
class ParsedFile:
    """Immutable parse result for a single python file.

    absolute_imports: dotted module names as written (``import a.b`` -> "a.b",
        ``from a.b import c`` -> "a.b").
    relative_imports: (level, module, names) tuples from ``from .mod import x``
        style statements; ``module`` may be empty for ``from . import x``.
    """

    absolute_imports: Tuple[str, ...] = ()
    relative_imports: Tuple[Tuple[int, str, Tuple[str, ...]], ...] = ()


def parse_source(source: str) -> ParsedFile:
    """Parse python source text into a ParsedFile."""
    tree = ast.parse(source)
    absolute: List[str] = []
    relative: List[Tuple[int, str, Tuple[str, ...]]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                absolute.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                names = tuple(alias.name for alias in node.names)
                relative.append((node.level, node.module or "", names))
            elif node.module:
                absolute.append(node.module)
    return ParsedFile(
        absolute_imports=tuple(sorted(set(absolute))),
        relative_imports=tuple(sorted(set(relative))),
    )


def parse_file(path) -> ParsedFile:
    """Parse a python file on disk into a ParsedFile."""
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    return parse_source(text)
