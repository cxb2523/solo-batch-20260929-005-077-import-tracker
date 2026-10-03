"""Thin CLI shell over the service layer.

The historical commands are kept, but they do no work themselves: every
command delegates to :func:`app.service.scan_repository`.
"""

# Standard
import argparse
import json
import sys

# Local
from .policy import DEFAULT_DEPTH
from .service import scan_repository


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="import-tracker")
    sub = parser.add_subparsers(dest="command")

    scan = sub.add_parser("scan", help="Scan a repository and print JSON")
    scan.add_argument("--root", default=".", help="Repository root to scan")
    scan.add_argument("--depth", type=int, default=DEFAULT_DEPTH,
                      help="Dependency chain expansion depth")
    scan.add_argument("--force", action="store_true",
                      help="Bypass the installed-package probe cache")
    scan.add_argument("--indent", type=int, default=2,
                      help="JSON indent (0 for compact)")
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command in (None, "scan"):
        result = scan_repository(
            getattr(args, "root", "."),
            getattr(args, "depth", DEFAULT_DEPTH),
            force=getattr(args, "force", False),
        )
        indent = getattr(args, "indent", 2) or None
        json.dump(result, sys.stdout, indent=indent)
        sys.stdout.write("\n")
        return 0
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
