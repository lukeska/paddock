from __future__ import annotations

import argparse
from pathlib import Path
import sys

from . import __version__
from .execution import plan_composer, plan_php
from .caddy import CaddyProjector
from .diagnostics import doctor, service_status
from .lifecycle import Lifecycle
from .integration import INSTALL_CHANGES, REMOVE_CHANGES, Integration
from .paths import Paths
from .artifacts import ArtifactManifest
from .php_runtime import RuntimeInstaller
from .projects import write_project_selection
from .runtimes import RuntimeRegistry
from .state import StateError, StateStore
from .sites import SiteManager
from .tls import SecurityManager


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="paddock")
    result.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subcommands = result.add_subparsers(dest="command", required=True)
    php = subcommands.add_parser("php")
    php.add_argument("arguments", nargs=argparse.REMAINDER)
    composer = subcommands.add_parser("composer")
    composer.add_argument("arguments", nargs=argparse.REMAINDER)
    link = subcommands.add_parser("link")
    link.add_argument("name", nargs="?")
    link.add_argument("--php")
    unlink = subcommands.add_parser("unlink")
    unlink.add_argument("name", nargs="?")
    secure = subcommands.add_parser("secure")
    secure.add_argument("name", nargs="?")
    unsecure = subcommands.add_parser("unsecure")
    unsecure.add_argument("name", nargs="?")
    subcommands.add_parser("doctor")
    subcommands.add_parser("status")
    for action in ("start", "stop", "restart"):
        subcommands.add_parser(action)
    logs = subcommands.add_parser("logs")
    logs.add_argument("--follow", action="store_true")
    setup = subcommands.add_parser("setup")
    setup.add_argument("--yes", action="store_true")
    uninstall = subcommands.add_parser("uninstall")
    uninstall.add_argument("--yes", action="store_true")
    return result


def run(argv: list[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    store = StateStore(Paths.from_environment())
    store.initialize()
    forwarded = getattr(arguments, "arguments", [])
    explicit_execution = forwarded[:1] == ["--"]
    if explicit_execution:
        forwarded = forwarded[1:]
    if arguments.command == "php":
        registry = RuntimeRegistry(store)
        if not explicit_execution and forwarded == ["list"]:
            for runtime in registry.list():
                print(f"{runtime.version}\t{runtime.path}")
            return 0
        if not explicit_execution and forwarded[:1] == ["use"]:
            if len(forwarded) != 2:
                raise ValueError("Usage: paddock php use VERSION")
            registry.resolve(forwarded[1])
            path = write_project_selection(Path.cwd(), forwarded[1])
            print(f"PHP {forwarded[1]} selected in {path.parent}")
            return 0
        if not explicit_execution and forwarded[:1] == ["install"]:
            if len(forwarded) != 2:
                raise ValueError("Usage: paddock php install VERSION")
            manifest_path = Path("/usr/share/paddock/artifacts.json")
            destination = RuntimeInstaller(store).install(
                forwarded[1], ArtifactManifest.load(manifest_path)
            )
            print(f"Installed PHP {forwarded[1]} at {destination}")
            return 0
        if not explicit_execution and forwarded[:1] == ["remove"]:
            if len(forwarded) != 2:
                raise ValueError("Usage: paddock php remove VERSION")
            RuntimeInstaller(store).remove(forwarded[1])
            print(f"Removed PHP {forwarded[1]}")
            return 0
        plan_php(Path.cwd(), forwarded, store).execute()
    if arguments.command == "composer":
        plan_composer(Path.cwd(), forwarded, store).execute()
    manager = SiteManager(store, CaddyProjector(store.paths))
    if arguments.command == "link":
        site = manager.link(Path.cwd(), arguments.name, arguments.php)
        print(f"Linked {site.root} as http://{site.name}.test using PHP {site.php}")
    if arguments.command == "unlink":
        site = manager.unlink(arguments.name, Path.cwd())
        print(f"Unlinked {site.name}.test")
    security = SecurityManager(store, CaddyProjector(store.paths))
    if arguments.command == "secure":
        site = security.secure(arguments.name, Path.cwd())
        print(f"Secured https://{site.name}.test")
    if arguments.command == "unsecure":
        site = security.unsecure(arguments.name, Path.cwd())
        print(f"Unsecured http://{site.name}.test")
    if arguments.command == "doctor":
        checks = doctor(store)
        for check in checks:
            print(f"{'PASS' if check.ok else 'FAIL'}\t{check.name}\t{check.detail}")
        return 0 if all(check.ok for check in checks) else 1
    if arguments.command == "status":
        checks = service_status()
        for check in checks:
            print(f"{'active' if check.ok else 'inactive'}\t{check.name}\t{check.detail}")
        return 0 if all(check.ok for check in checks) else 3
    if arguments.command in {"start", "stop", "restart"}:
        Lifecycle().control(arguments.command)
    if arguments.command == "logs":
        return Lifecycle().logs(arguments.follow)
    if arguments.command in {"setup", "uninstall"}:
        changes = INSTALL_CHANGES if arguments.command == "setup" else REMOVE_CHANGES
        print(f"Paddock will {arguments.command} the following system integration:")
        for change in changes:
            print(f"  - {change}")
        if not arguments.yes and input("Continue? [y/N] ").strip().lower() not in {"y", "yes"}:
            print("Cancelled")
            return 2
        integration = Integration(store)
        if arguments.command == "setup":
            integration.prepare()
            integration.install()
            print("Paddock system integration installed")
        else:
            integration.uninstall()
            print("Paddock system integration removed; user data was preserved")
    return 0


def main() -> int:
    try:
        return run()
    except (OSError, StateError, ValueError) as error:
        print(f"paddock: {error}", file=sys.stderr)
        return 78


if __name__ == "__main__":
    raise SystemExit(main())
