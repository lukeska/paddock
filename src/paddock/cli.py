from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from . import __version__
from .execution import plan_composer, plan_php
from .caddy import CaddyProjector
from .diagnostics import doctor, service_status
from .lifecycle import Lifecycle
from .integration import INSTALL_CHANGES, REMOVE_CHANGES, Integration
from .paths import Paths
from .parking import ParkingManager
from .artifacts import ArtifactManifest
from .php_runtime import RuntimeInstaller
from .projectfile import PROJECT_FILE, ProjectFileError, Reconciler, find, load
from .projects import write_node_selection, write_project_selection
from .node_runtime import NodeInstaller, NodeManifest, NodeRegistry
from .report import build as build_report
from .reverb import ReverbManager
from .queue_worker import QueueWorkerManager
from .runtimes import RuntimeRegistry
from .service_instances import ServiceInstanceManager
from .state import StateStore
from .sites import SiteManager
from .tls import SecurityManager
from .uninstall import PurgePlan


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
        ("park [PATH]", "Serve every immediate child of a directory"),
        ("paths", "List parked directories"),
        ("forget [PATH]", "Stop parking a directory"),
        ("init", "Apply this project's paddock.yml"),
    )),
    ("PHP", (
        ("php list", "List the installed PHP runtimes"),
        ("php install VERSION", "Install a Paddock-built PHP runtime"),
        ("php remove VERSION", "Remove an installed PHP runtime"),
        ("php use VERSION", "Select the PHP version for this project"),
        ("php -- ARGS", "Run PHP with the version selected here"),
        ("composer -- ARGS", "Run Composer with the version selected here"),
        ("node list", "List installed Node.js runtimes"),
        ("node install VERSION", "Install a checksum-pinned Node.js runtime"),
        ("node use VERSION", "Select Node.js for this project"),
        ("node -- ARGS", "Run Node.js with the version selected here"),
    )),
    ("Supporting services", (
        ("services", "List configured services and their state"),
        ("service add TYPE", "Add a service instance, e.g. redis"),
        ("service start ID", "Start a service instance"),
        ("service stop ID", "Stop a service instance"),
        ("service logs ID", "Show one instance's journal"),
        ("service remove ID", "Remove an instance and its data"),
    )),
    ("Site workers", (
        ("worker start reverb [SITE]", "Start Reverb for a linked Laravel site"),
        ("worker stop reverb [SITE]", "Stop the site's Reverb worker"),
        ("worker restart reverb [SITE]", "Restart the site's Reverb worker"),
        ("worker logs reverb [SITE]", "Show the site's Reverb journal"),
        ("worker start queue [SITE]", "Start a Laravel queue worker for a site"),
        ("worker stop queue [SITE]", "Stop the site's queue worker"),
        ("worker restart queue [SITE]", "Restart the site's queue worker"),
        ("worker logs queue [SITE]", "Show the site's queue worker journal"),
    )),
    ("Services", (
        ("tui", "Open the terminal dashboard for services and sites"),
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
    node = command("node", "Manage or run project-selected Node.js.")
    node.add_argument("arguments", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)
    link = command(
        "link",
        "Serve the current directory at NAME.test over HTTP.",
    )
    link.add_argument("name", nargs="?", help="site name; defaults to the directory name")
    link.add_argument("--php", help="PHP minor version to serve this project with")
    link.add_argument("--node", help="Node.js major version for this project")
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
    initialize = command(
        "init",
        f"Bring this machine in line with the project's {PROJECT_FILE}.",
        epilog="Idempotent: a second run reports everything unchanged. Never "
               "reconfigures a supporting service another project may share; "
               "a difference is reported instead. Exits 1 if anything was "
               "blocked.",
    )
    initialize.add_argument(
        "--dry-run", action="store_true", help="report what would change, and do nothing"
    )
    command("sites", "List linked sites: name, PHP minor, scheme, and root.")
    park = command(
        "park", "Park a directory so each immediate subdirectory becomes a .test site."
    )
    park.add_argument("path", nargs="?", help="directory; defaults to the current directory")
    park.add_argument(
        "--refresh", action="store_true", help="rescan parked folders without adding a path"
    )
    command("paths", "List every parked directory.")
    forget = command("forget", "Remove a directory from the parked-path list.")
    forget.add_argument("path", nargs="?", help="directory; defaults to the current directory")
    command(
        "report",
        "Print one JSON snapshot of units, PHP, services, and sites.",
        epilog="The stable machine interface, carrying its own schema_version. "
               "Used by the Omarchy plugin; safe to parse in scripts.",
    )
    command(
        "tui",
        "Open Paddock's Omarchy-native terminal dashboard.",
        epilog="Use tab to switch between Dashboard and Sites; the footer shows "
               "the keyboard controls for the active section.",
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
        epilog="Supported services: mysql, postgres, redis. Removing an instance "
               "also permanently removes its data. Pin a different "
               "version with --image, e.g. --image docker.io/library/postgres:16.",
    )
    service.add_argument(
        "action", choices=("add", "start", "stop", "restart", "logs", "remove")
    )
    service.add_argument("target", help="service type for add; instance ID otherwise")
    service.add_argument("--name", dest="label", help="add: display name for the instance")
    service.add_argument("--image", help="override the container image")
    service.add_argument("--port", type=int, help="override the published loopback port")
    worker = command(
        "worker",
        "Control a worker belonging to a linked site.",
        epilog="Reverb is available when laravel/reverb is installed in the project.",
    )
    worker.add_argument("action", choices=("start", "stop", "restart", "logs"))
    worker.add_argument("type", choices=("reverb", "queue"))
    worker.add_argument("site", nargs="?", help="site name; defaults to the site rooted here")
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
        epilog="By default all user data is preserved. --purge deletes Paddock's "
               "configuration, runtimes, Composer, logs, cache and private CA, "
               "but never project sources. Service volumes require the additional "
               "--delete-service-data flag.",
    )
    uninstall.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    uninstall.add_argument(
        "--purge", action="store_true", help="also delete all Paddock-owned user files"
    )
    uninstall.add_argument(
        "--delete-service-data", action="store_true",
        help="with --purge, permanently delete recorded database and cache volumes",
    )
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
    if arguments.command == "tui":
        executable = Path(os.environ.get("PADDOCK_TUI_BIN", "/usr/bin/paddock-tui"))
        if not executable.is_file():
            raise RuntimeError(
                f"terminal UI is not installed at {executable}; rebuild and reinstall Paddock"
            )
        os.execv(str(executable), [str(executable)])
        return 0
    store = StateStore(Paths.from_environment())
    store.initialize()
    parking = ParkingManager(store)
    if arguments.command in {"park", "paths", "forget", "sites", "report", "init"}:
        home = Path(os.environ["HOME"]).expanduser()
        parking.ensure_default(home)
        if not (arguments.command == "park" and arguments.refresh):
            parking.sync_watchers()
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
    if arguments.command == "node":
        registry = NodeRegistry(store)
        if not explicit_execution and forwarded == ["list"]:
            for runtime in registry.list(): print(f"{runtime.version}\t{runtime.path}")
            return 0
        if not explicit_execution and forwarded[:1] == ["use"]:
            if len(forwarded) != 2: raise ValueError("Usage: paddock node use VERSION")
            registry.resolve(forwarded[1]); path = write_node_selection(Path.cwd(), forwarded[1])
            print(f"Node {forwarded[1]} selected in {path.parent}"); return 0
        if not explicit_execution and forwarded[:1] == ["install"]:
            if len(forwarded) != 2: raise ValueError("Usage: paddock node install VERSION")
            catalog = Path("/usr/share/paddock/node-artifacts.json")
            if not catalog.is_file(): catalog = Path(__file__).resolve().parents[2] / "resources/node-artifacts.json"
            destination = NodeInstaller(store).install(forwarded[1], NodeManifest.load(catalog))
            print(f"Installed Node {forwarded[1]} at {destination}"); return 0
        if not explicit_execution and forwarded[:1] == ["remove"]:
            if len(forwarded) != 2: raise ValueError("Usage: paddock node remove VERSION")
            NodeInstaller(store).remove(forwarded[1]); print(f"Removed Node {forwarded[1]}"); return 0
        from .execution import plan_node
        plan_node(Path.cwd(), "node", forwarded, store).execute()
    if arguments.command == "composer":
        plan_composer(Path.cwd(), forwarded, store).execute()
    manager = SiteManager(store, CaddyProjector(store.paths))
    if arguments.command == "worker":
        records = store.read("sites")["sites"]
        site_name = (
            arguments.site.removesuffix(".test").lower()
            if arguments.site
            else manager._name_for_directory(records, Path.cwd())
        )
        worker_manager = (
            ReverbManager(store)
            if arguments.type == "reverb"
            else QueueWorkerManager(store)
        )
        if arguments.action == "logs":
            for line in worker_manager.logs(site_name):
                print(line)
        else:
            worker = worker_manager.control(arguments.action, site_name)
            address = f" on 127.0.0.1:{worker.port}" if arguments.type == "reverb" else ""
            print(
                f"{ACTION_DONE[arguments.action]} {arguments.type.capitalize()} "
                f"for {site_name}.test{address}"
            )
        return 0
    if arguments.command == "link":
        site = manager.link(Path.cwd(), arguments.name, arguments.php, arguments.node)
        node_detail = f" and Node {site.node}" if site.node else ""
        print(f"Linked {site.root} as http://{site.name}.test using PHP {site.php}{node_detail}")
    if arguments.command == "unlink":
        site_name = (
            arguments.name.removesuffix(".test").lower()
            if arguments.name
            else manager._name_for_directory(store.read("sites")["sites"], Path.cwd())
        )
        ReverbManager(store).remove(site_name)
        QueueWorkerManager(store).remove(site_name)
        site = manager.unlink(arguments.name, Path.cwd())
        print(f"Unlinked {site.name}.test")
    security = SecurityManager(store, CaddyProjector(store.paths))
    if arguments.command == "secure":
        site = security.secure(arguments.name, Path.cwd())
        ReverbManager(store).sync_environment(site.name)
        print(f"Secured https://{site.name}.test")
    if arguments.command == "unsecure":
        site = security.unsecure(arguments.name, Path.cwd())
        ReverbManager(store).sync_environment(site.name)
        print(f"Unsecured http://{site.name}.test")
    instances = ServiceInstanceManager(store)
    if arguments.command == "services":
        configured = instances.list()
        states = instances.states_of(configured)
        for service in configured:
            print(
                f"{service.id}\t{service.type}\t{service.label}\t"
                f"{states.get(service.id, 'unknown')}\t{service.address}\t{service.image}"
            )
        return 0
    if arguments.command == "service":
        if arguments.action == "add":
            kind = arguments.target
            label = arguments.label or {
                "redis": "Redis", "mysql": "MySQL", "postgres": "PostgreSQL",
            }.get(kind, kind)
            service = instances.create(
                kind, label, arguments.port, image=arguments.image
            )
            instances.control("start", service.id)
            print(
                f"Added and started {service.label} ({service.id}) on "
                f"{service.address} using {service.image}"
            )
            settings = instances.connection_lines(service.id)
            if settings:
                # A database nobody can connect to is not much use, and the
                # .env is the user's file to edit.
                print("\nAdd to your .env:")
                for line in settings:
                    print(f"  {line}")
            return 0
        if arguments.action == "logs":
            for line in instances.logs(arguments.target):
                print(line)
            return 0
        if arguments.action == "remove":
            service = instances.remove(arguments.target)
            print(f"Removed {service.label} ({service.id}) and deleted {service.volume}")
            return 0
        # Re-project before starting so an edited image or port takes effect
        # and a missing file cannot leave the unit inert via ConditionPathExists.
        instances.control(arguments.action, arguments.target)
        service = instances.require(arguments.target)
        print(f"{ACTION_DONE[arguments.action]} {service.label} on {service.address}")
        return 0
    if arguments.command == "init":
        root = Path.cwd()
        path = find(root)
        if path is None:
            raise ProjectFileError(
                f"no {PROJECT_FILE} in {root}; create one describing the site, "
                "PHP version, and services this project needs"
            )
        steps = Reconciler(store, manager, security, instances).apply(
            root, load(path), dry_run=arguments.dry_run
        )
        for step in steps:
            print(f"{step.marker} {step.detail}")
        blocked = [step for step in steps if step.outcome == "blocked"]
        if arguments.dry_run:
            print("\n(dry run: nothing was changed)")
        elif not blocked and not any(step.outcome == "changed" for step in steps):
            print("\nAlready up to date.")
        return 1 if blocked else 0
    if arguments.command == "sites":
        parking.reconcile(CaddyProjector(store.paths))
        for site in manager.list():
            scheme = "https" if site.secured else "http"
            print(f"{site.name}\t{site.php}\t{scheme}\t{site.root}")
        return 0
    if arguments.command == "park":
        if arguments.refresh:
            result = parking.reconcile(CaddyProjector(store.paths))
            return 1 if result.conflicts else 0
        path = parking.add(Path(arguments.path) if arguments.path else Path.cwd())
        parking.sync_watchers()
        result = parking.reconcile(CaddyProjector(store.paths))
        print(f"Parked {path}")
        for conflict in result.conflicts:
            print(f"! {conflict.name or conflict.roots[0]}: {conflict.reason}")
        return 1 if result.conflicts else 0
    if arguments.command == "paths":
        for path in parking.list():
            print(path)
        return 0
    if arguments.command == "forget":
        path = parking.remove(Path(arguments.path) if arguments.path else Path.cwd())
        parking.sync_watchers()
        parking.reconcile(CaddyProjector(store.paths))
        print(f"Forgot {path}")
        return 0
    if arguments.command == "report":
        parking.reconcile(CaddyProjector(store.paths))
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
        if (
            arguments.command == "uninstall"
            and arguments.delete_service_data
            and not arguments.purge
        ):
            raise ValueError("--delete-service-data requires --purge")
        changes = INSTALL_CHANGES if arguments.command == "setup" else REMOVE_CHANGES
        print(f"Paddock will {arguments.command} the following system integration:")
        for change in changes:
            print(f"  - {change}")
        purge = None
        if arguments.command == "uninstall" and arguments.purge:
            purge = PurgePlan.discover(store.paths)
            print("Paddock will also purge:")
            for change in purge.preview(arguments.delete_service_data):
                print(f"  - {change}")
        if not arguments.yes and input("Continue? [y/N] ").strip().lower() not in {"y", "yes"}:
            print("Cancelled")
            return 2
        integration = Integration(store)
        if arguments.command == "setup":
            integration.prepare()
            integration.install()
            installed_php = integration.install_initial_php()
            installed_composer = integration.install_composer()
            installed_node = integration.install_initial_node()
            shell_changed = integration.install_shell_integration()
            print("Paddock system integration installed")
            if installed_php is not None:
                print(f"Installed PHP {installed_php} as the default runtime")
            if installed_composer is not None:
                print(f"Installed Composer {installed_composer}")
            if installed_node is not None:
                print(f"Installed Node {installed_node} as the default runtime")
            if shell_changed:
                print(
                    "Enabled project-aware php, composer, node, npm, and npx commands; "
                    "open a new terminal"
                )
        else:
            integration.uninstall()
            if purge is None:
                print("Paddock system integration removed; user data was preserved")
            else:
                purge.execute(arguments.delete_service_data)
                suffix = " including service data" if arguments.delete_service_data else ""
                print(f"Paddock system integration and user data removed{suffix}; projects were preserved")
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
