from __future__ import annotations

import argparse
import json
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
from .report import build as build_report
from .runtimes import RuntimeRegistry
from .services import ServiceManager
from .state import StateStore
from .sites import SiteManager
from .tls import SecurityManager


SUMMARY = "Serve Laravel projects on .test domains with managed PHP runtimes."

# `"stop".capitalize() + "ed"` spells "Stoped", so the past tense is spelled out.
ACTION_DONE = {"start": "Started", "stop": "Stopped", "restart": "Restarted"}

# Grouped command list rendered by `paddock help` and `paddock --help`.
# argparse would otherwise emit one flat alphabetical block, and it cannot
# describe `php list`/`php use` at all, because `php` forwards everything
# after itself through nargs=REMAINDER. A test pins this table against the
# registered subcommands so the two cannot drift apart.
OVERVIEW: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    ("Projects", (
        ("link [NAME]", "Serve the current directory at NAME.test"),
        ("unlink [NAME]", "Stop serving a linked project"),
        ("secure [NAME]", "Serve a site over locally trusted HTTPS"),
        ("unsecure [NAME]", "Serve a site over plain HTTP again"),
        ("sites", "List linked sites, their PHP version and URL"),
    )),
    ("PHP", (
        ("php list", "List the installed PHP runtimes"),
        ("php install VERSION", "Install a Paddock-built PHP runtime"),
        ("php remove VERSION", "Remove an installed PHP runtime"),
        ("php use VERSION", "Select the PHP version for this project"),
        ("php -- ARGS", "Run PHP with the version selected here"),
        ("composer -- ARGS", "Run Composer with the version selected here"),
    )),
    ("Supporting services", (
        ("services", "List configured services and their state"),
        ("service add NAME", "Configure a service, e.g. redis"),
        ("service start NAME", "Start a configured service"),
        ("service stop NAME", "Stop a configured service"),
        ("service logs NAME", "Show one service's journal"),
        ("service remove NAME", "Forget a service; data is kept unless asked"),
    )),
    ("Services", (
        ("status", "Report whether the Paddock services are running"),
        ("start", "Start the Paddock services"),
        ("stop", "Stop the Paddock services"),
        ("restart", "Restart the Paddock services"),
        ("logs", "Show the Paddock service journal"),
        ("doctor", "Check the environment and report what to fix"),
        ("report", "Print one JSON snapshot for scripts and the Omarchy plugin"),
    )),
    ("Installation", (
        ("setup", "Install .test DNS, the local CA, and the systemd units"),
        ("uninstall", "Remove the system integration, keeping your data"),
    )),
    ("Help", (
        ("help [COMMAND]", "Show this list, or explain one command"),
    )),
)


def overview() -> str:
    """Render the grouped command list."""
    width = max(len(invocation) for _, entries in OVERVIEW for invocation, _ in entries)
    lines = [SUMMARY, ""]
    for group, entries in OVERVIEW:
        lines.append(f"{group}:")
        for invocation, description in entries:
            lines.append(f"  paddock {invocation.ljust(width)}  {description}")
        lines.append("")
    lines.append('Run "paddock help COMMAND" for one command in detail.')
    return "\n".join(lines)


PHP_DETAIL = """Subcommands:
  paddock php list             List the installed runtimes and their paths
  paddock php install VERSION  Install the Paddock-built runtime for VERSION
  paddock php remove VERSION   Remove the installed runtime for VERSION
  paddock php use VERSION      Record VERSION in ./.paddock.json
  paddock php -- ARGS          Run ARGS with the PHP selected for this directory

The selected PHP is the nearest .paddock.json found walking up from the
current directory, otherwise the linked site that contains it. `--` is
required when forwarding arguments, so `paddock php -- -v` reports the
version of the PHP this directory resolves to."""


def build() -> tuple[argparse.ArgumentParser, dict[str, argparse.ArgumentParser]]:
    """Build the parser and a name-to-subparser map for `paddock help`."""
    result = argparse.ArgumentParser(
        prog="paddock",
        # Suppressing the choices block below also drops COMMAND from the
        # generated usage line, so state it here.
        usage="paddock COMMAND [ARGUMENTS]",
        description=overview(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    result.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    # The command list lives in the description above, grouped by task, so the
    # flat choices block is suppressed. `required=False` lets a bare `paddock`
    # print that list instead of only a usage line.
    subcommands = result.add_subparsers(
        dest="command", required=False, metavar="COMMAND", help=argparse.SUPPRESS
    )
    commands: dict[str, argparse.ArgumentParser] = {}

    def command(name: str, description: str, **kwargs) -> argparse.ArgumentParser:
        created = subcommands.add_parser(
            name,
            description=description,
            formatter_class=argparse.RawDescriptionHelpFormatter,
            **kwargs,
        )
        commands[name] = created
        return created

    php = command(
        "php",
        "Run PHP, or manage the installed PHP runtimes.",
        epilog=PHP_DETAIL,
    )
    # The epilog documents the forwarded arguments; the bare positional adds
    # only an undescribed "arguments" line to the listing.
    php.add_argument("arguments", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)
    composer = command(
        "composer",
        "Run Composer with the PHP selected for this directory.",
        epilog="`--` is required: paddock composer -- install",
    )
    composer.add_argument("arguments", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)
    link = command(
        "link",
        "Serve the current directory at NAME.test over HTTP.",
    )
    link.add_argument("name", nargs="?", help="site name; defaults to the directory name")
    link.add_argument("--php", help="PHP minor version to serve this project with")
    unlink = command("unlink", "Stop serving a linked project.")
    unlink.add_argument("name", nargs="?", help="site name; defaults to the site rooted here")
    secure = command(
        "secure",
        "Issue a locally trusted certificate and serve the site over HTTPS.",
    )
    secure.add_argument("name", nargs="?", help="site name; defaults to the site rooted here")
    unsecure = command("unsecure", "Serve the site over plain HTTP again.")
    unsecure.add_argument("name", nargs="?", help="site name; defaults to the site rooted here")
    command(
        "doctor",
        "Check runtimes, state, sites, and generated configuration.",
        epilog="Exits 1 if any check fails.",
    )
    command("sites", "List linked sites: name, PHP minor, scheme, and root.")
    command(
        "report",
        "Print one JSON snapshot of units, PHP, services, and sites.",
        epilog="The stable machine interface, carrying its own schema_version. "
               "Used by the Omarchy plugin; safe to parse in scripts.",
    )
    command(
        "status",
        "Report whether the Paddock services are running.",
        epilog="Exits 3 if any service is inactive.",
    )
    for action in ("start", "stop", "restart"):
        command(action, f"{action.capitalize()} the Paddock services (paddock.target).")
    command("services", "List configured supporting services and their state.")
    service = command(
        "service",
        "Configure and control a supporting service.",
        epilog="Supported services: mysql, postgres, redis. Data outlives "
               "`remove` unless --delete-data is given. Pin a different "
               "version with --image, e.g. --image docker.io/library/postgres:16.",
    )
    service.add_argument(
        "action", choices=("add", "start", "stop", "restart", "logs", "remove")
    )
    service.add_argument("name", help="service name, e.g. redis")
    service.add_argument("--image", help="override the container image")
    service.add_argument("--port", type=int, help="override the published loopback port")
    service.add_argument("--follow", action="store_true", help="logs: keep printing new entries")
    service.add_argument(
        "--delete-data", action="store_true", help="remove: also delete the data volume"
    )
    logs = command("logs", "Show the journal for the Caddy, PHP-FPM, and DNS services.")
    logs.add_argument("--follow", action="store_true", help="keep printing new entries")
    setup = command(
        "setup",
        "Install system integration: .test DNS, the local CA, and the systemd units.",
        epilog="The changes are printed for confirmation first. Requires sudo.",
    )
    setup.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    uninstall = command(
        "uninstall",
        "Remove the system integration installed by setup.",
        epilog="Projects, runtimes, state, logs, and the private CA are preserved.",
    )
    uninstall.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    help_command = command("help", "List everything Paddock can do, or explain one command.")
    help_command.add_argument("topic", nargs="?", help="command to explain")
    return result, commands


def parser() -> argparse.ArgumentParser:
    return build()[0]


def run(argv: list[str] | None = None) -> int:
    root, commands = build()
    arguments = root.parse_args(argv)
    # Help must work before any state exists, so it answers ahead of the store.
    if arguments.command is None:
        root.print_help()
        return 2
    if arguments.command == "help":
        if arguments.topic is None:
            root.print_help()
            return 0
        if arguments.topic not in commands:
            raise ValueError(f'unknown command: {arguments.topic}; run "paddock help"')
        commands[arguments.topic].print_help()
        return 0
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
    services = ServiceManager(store)
    if arguments.command == "services":
        for service in services.list():
            print(f"{service.name}\t{services.state_of(service)}\t{service.address}\t{service.image}")
        return 0
    if arguments.command == "service":
        if arguments.action == "add":
            service = services.configure(arguments.name, arguments.image, arguments.port)
            print(f"Configured {service.name} on {service.address} using {service.image}")
            settings = services.connection_lines(service.name)
            if settings:
                # A database nobody can connect to is not much use, and the
                # .env is the user's file to edit.
                print("\nAdd to your .env:")
                for line in settings:
                    print(f"  {line}")
            print(f'\nStart it with "paddock service start {service.name}"')
            return 0
        if arguments.action == "logs":
            return services.logs(arguments.name, arguments.follow)
        if arguments.action == "remove":
            service = services.remove(arguments.name, delete_data=arguments.delete_data)
            kept = "and its data volume was deleted" if arguments.delete_data else (
                f"data volume {service.volume} was kept"
            )
            print(f"Removed {service.name}; {kept}")
            return 0
        # Re-project before starting so an edited image or port takes effect
        # and a missing file cannot leave the unit inert via ConditionPathExists.
        if arguments.action in {"start", "restart"}:
            services.project(services.require(arguments.name))
        services.control(arguments.action, arguments.name)
        service = services.require(arguments.name)
        print(f"{ACTION_DONE[arguments.action]} {service.name} on {service.address}")
        return 0
    if arguments.command == "sites":
        for site in manager.list():
            scheme = "https" if site.secured else "http"
            print(f"{site.name}\t{site.php}\t{scheme}\t{site.root}")
        return 0
    if arguments.command == "report":
        print(json.dumps(build_report(store), indent=2, sort_keys=True))
        return 0
    if arguments.command == "doctor":
        checks = doctor(store)
        for check in checks:
            print(f"{'PASS' if check.ok else 'FAIL'}\t{check.name}\t{check.detail}")
        return 0 if all(check.ok for check in checks) else 1
    if arguments.command == "status":
        checks = service_status(store)
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
    # LifecycleError, CaddyError, TlsError, IntegrationError and
    # RuntimeInstallError all subclass RuntimeError and used to escape as a
    # traceback, which is hostile to anyone parsing this CLI and tells a user
    # nothing. Every deliberate failure is one line and exit 78.
    except (OSError, RuntimeError, ValueError) as error:
        print(f"paddock: {error}", file=sys.stderr)
        return 78


if __name__ == "__main__":
    raise SystemExit(main())
