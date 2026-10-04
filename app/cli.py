"""
Thin CLI shell.

Keeps the legacy `import_tracker` commands (thin wrapper around
`import_tracker.track_module`) and adds a `scan` command that delegates to
the reusable service layer. Legacy invocations such as
`python -m app.cli --name my_library` keep working unchanged.
"""

# Standard
import argparse
import json
import logging
import os
import sys


def _add_track_arguments(parser: argparse.ArgumentParser) -> None:
    """The legacy `python -m import_tracker` arguments, unchanged."""
    parser.add_argument("--name", "-n", required=True, help="Module name to track")
    parser.add_argument(
        "--package", "-p", help="Package for relative imports", default=None
    )
    parser.add_argument(
        "--indent", "-i", type=int, help="Indent for json printing", default=None
    )
    parser.add_argument(
        "--submodules",
        "-s",
        nargs="*",
        default=None,
        help="List of subodules to include (all if no value given)",
    )
    parser.add_argument(
        "--track_import_stack",
        "-t",
        action="store_true",
        default=False,
        help="Store the stack trace of imports belonging to the tracked module",
    )
    parser.add_argument(
        "--full_depth",
        "-f",
        action="store_true",
        default=False,
        help="Include transitive third-party deps brought in by direct third-party deps",
    )
    parser.add_argument(
        "--detect_transitive",
        "-d",
        action="store_true",
        default=False,
        help="Detect whether each dependency is 'direct' or 'transitive'",
    )
    parser.add_argument(
        "--show_optional",
        "-o",
        action="store_true",
        default=False,
        help="Show whether each dependency is optional or required",
    )
    parser.add_argument(
        "--log_level",
        "-l",
        default=os.environ.get("LOG_LEVEL", "warning"),
        help="Default log level",
    )


def _run_track(args: argparse.Namespace) -> None:
    """Delegate to the legacy library entrypoint."""
    # Local
    from import_tracker.import_tracker import track_module

    submodules = (
        False
        if args.submodules is None
        else (args.submodules if args.submodules else True)
    )
    log_level = getattr(logging, str(args.log_level).upper(), None)
    if log_level is None:
        log_level = int(args.log_level)
    logging.basicConfig(level=log_level)
    print(
        json.dumps(
            track_module(
                module_name=args.name,
                package_name=args.package,
                submodules=submodules,
                track_import_stack=args.track_import_stack,
                full_depth=args.full_depth,
                detect_transitive=args.detect_transitive,
                show_optional=args.show_optional,
            ),
            indent=args.indent,
        )
    )


def _run_scan(args: argparse.Namespace) -> None:
    """Delegate to the reusable service layer."""
    # Local
    from .service import scan_repository

    print(
        json.dumps(
            scan_repository(
                args.root,
                args.depth,
                timeout_ms=args.timeout_ms,
                force=args.force,
            ),
            indent=args.indent,
        )
    )


def main(argv=None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(prog="app.cli", description=__doc__)
    subparsers = parser.add_subparsers(dest="command")

    scan_parser = subparsers.add_parser("scan", help="Scan a repository directory")
    scan_parser.add_argument("--root", default=".", help="Repository root")
    scan_parser.add_argument("--depth", type=int, default=3, help="Scan depth")
    scan_parser.add_argument(
        "--timeout-ms", type=int, default=1500, dest="timeout_ms"
    )
    scan_parser.add_argument(
        "--force", action="store_true", help="Bypass the install-probe cache"
    )
    scan_parser.add_argument(
        "--indent", "-i", type=int, default=None, help="Indent for json printing"
    )

    track_parser = subparsers.add_parser(
        "track", help="Track a module (legacy command)"
    )
    _add_track_arguments(track_parser)

    # Legacy compatibility: bare `--name ...` behaves like the old
    # `python -m import_tracker --name ...` invocation.
    if argv and argv[0] not in ("scan", "track", "-h", "--help"):
        argv = ["track"] + argv

    args = parser.parse_args(argv)
    if args.command == "scan":
        _run_scan(args)
    elif args.command == "track":
        _run_track(args)
    else:
        parser.print_help()


if __name__ == "__main__":  # pragma: no cover
    main()
