"""
AST-based import parsing for individual Python source files.
"""

# Standard
from typing import List, NamedTuple
import ast


class ImportRecord(NamedTuple):
    """A single import found in a source file.

    `name` is the dotted module name as written; `relative` marks imports
    that use explicit relative syntax (``from . import x``), which are always
    local to the scanned repository.
    """

    name: str
    relative: bool


def parse_imports(path: str) -> List[ImportRecord]:
    """Parse the given python file and return the imports it makes.

    Returns a sorted, de-duplicated list. Files that cannot be parsed yield
    an empty list rather than raising, so a single bad file cannot break a
    repository scan.
    """
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        source = handle.read()
    try:
        tree = ast.parse(source, filename=path)
    except (SyntaxError, ValueError):
        return []
    records = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                records.add(ImportRecord(alias.name, False))
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                if node.module:
                    records.add(ImportRecord(node.module, True))
                for alias in node.names:
                    if alias.name != "*":
                        records.add(ImportRecord(alias.name, True))
            elif node.module:
                records.add(ImportRecord(node.module, False))
    return sorted(records)
