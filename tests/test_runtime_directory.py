"""Fresh-boot regression coverage for the PHP-FPM runtime directory.

Paddock PHP units failed at boot with `226/NAMESPACE` because the generated
unit named `/run/user/<uid>/paddock` in `ReadWritePaths=`. `user-runtime-dir@`
creates `/run/user/<uid>` but never the Paddock child, and systemd builds the
mount namespace before `ExecStart`, so the launcher never ran. These tests pin
the systemd-owned replacement: the runtime directory must be created by
`RuntimeDirectory=` and Paddock itself must never depend on it already
existing.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import socket
import subprocess
import tempfile
from types import ModuleType
import unittest

from paddock.caddy import CaddyProjector
from paddock.paths import SYSTEM_RUNTIME_ROOT, Paths
from paddock.php_runtime import RuntimeInstaller
from paddock.runtimes import RuntimeRegistry
from paddock.state import StateStore


def load_helper():
    # Compile from source text rather than SourceFileLoader: that loader
    # caches bytecode under system/__pycache__, and a stale entry would make
    # these tests read a previous revision of the helper. The version/digest
    # guard exists to catch template edits, so it must never see cached source.
    path = Path(__file__).parents[1] / "system/system-helper"
    module = ModuleType("paddock_system_helper")
    module.__file__ = str(path)
    code = compile(path.read_text(encoding="utf-8"), str(path), "exec")
    exec(code, module.__dict__)
    return module


def directives(unit: str, name: str) -> list[str]:
    prefix = f"{name}="
    return [line[len(prefix):] for line in unit.splitlines() if line.startswith(prefix)]


def sections(unit: str) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    current = ""
    for line in unit.splitlines():
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1]
            found.setdefault(current, [])
        elif line:
            found[current].append(line)
    return found


class PhpUnitRuntimeDirectoryTests(unittest.TestCase):
    def setUp(self) -> None:
        helper = load_helper()
        self.unit = helper.php_unit(
            "demo", 1000, Path("/home/demo"),
            Path("/home/demo/.local/share/paddock"),
            Path("/home/demo/.local/state/paddock"),
        )

    def test_systemd_creates_the_per_version_runtime_directory(self) -> None:
        self.assertIn("RuntimeDirectory=paddock/php/%i", self.unit)
        self.assertIn("RuntimeDirectoryMode=0700", self.unit)

    def test_unit_does_not_depend_on_a_login_session_runtime_directory(self) -> None:
        self.assertNotIn("/run/user/", self.unit)
        self.assertNotIn("user-runtime-dir@", self.unit)

    def test_no_writable_path_is_declared_below_run(self) -> None:
        # A `ReadWritePaths=` entry that does not exist yet aborts namespace
        # setup with 226/NAMESPACE. Only systemd may hand out /run access.
        for value in directives(self.unit, "ReadWritePaths"):
            for entry in value.split():
                self.assertFalse(
                    entry.startswith("/run"),
                    f"{entry} must be provided by RuntimeDirectory=, not ReadWritePaths=",
                )

    def test_unit_and_cli_agree_on_the_socket_directory(self) -> None:
        # Drift here silently routes Caddy at a socket FPM never binds.
        declared = directives(self.unit, "RuntimeDirectory")
        self.assertEqual(1, len(declared))
        systemd_owned = Path("/run") / declared[0].replace("%i", "8.4")
        self.assertEqual(SYSTEM_RUNTIME_ROOT / "php" / "8.4", systemd_owned)


class PhpUnitReadinessTests(unittest.TestCase):
    """ADR 0005 requires a socket-readiness edge and bounded restarts."""

    def setUp(self) -> None:
        helper = load_helper()
        self.unit = helper.php_unit(
            "demo", 1000, Path("/home/demo"),
            Path("/home/demo/.local/share/paddock"),
            Path("/home/demo/.local/state/paddock"),
        )

    def test_startup_gates_on_the_socket_it_actually_binds(self) -> None:
        # php-fpm is Type=simple, so without this gate Caddy orders against a
        # socket that does not exist yet and boots serve 502s.
        gates = directives(self.unit, "ExecStartPost")
        self.assertEqual(1, len(gates))
        command, socket = gates[0].split()
        self.assertEqual("/usr/lib/paddock/wait-for-socket", command)
        listen = f"{SYSTEM_RUNTIME_ROOT / 'php' / '%i' / 'fpm.sock'}"
        self.assertEqual(listen, socket)

    def test_the_gate_is_a_packaged_root_owned_helper(self) -> None:
        # ADR 0006: a privileged unit never executes a user-writable command.
        helper = Path(__file__).parents[1] / "system/wait-for-socket"
        self.assertTrue(helper.is_file())
        self.assertTrue(helper.stat().st_mode & 0o111)

    def test_caddy_is_ordered_after_the_gate_without_requiring_it(self) -> None:
        # Ordering only: a later FPM failure must 502 one version, not stop Caddy.
        self.assertIn("paddock-caddy.service", directives(self.unit, "Before"))
        for requirement in ("Requires", "BindsTo", "Requisite"):
            for value in directives(self.unit, requirement):
                self.assertNotIn("paddock-caddy", value)

    def test_restart_policy_is_bounded_and_in_the_correct_sections(self) -> None:
        # systemd reads StartLimit* from [Unit]; in [Service] they are ignored.
        parsed = sections(self.unit)
        self.assertIn("StartLimitIntervalSec=10s", parsed["Unit"])
        self.assertIn("StartLimitBurst=3", parsed["Unit"])
        self.assertIn("RestartSec=500ms", parsed["Service"])
        self.assertIn("Restart=on-failure", parsed["Service"])


class AbsentRuntimeRootTests(unittest.TestCase):
    """Everything Paddock generates must work on a boot where /run is bare."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        base = Path(self.temporary.name)
        self.runtime_root = base / "run" / "paddock"
        self.paths = Paths.from_environment(
            {
                "HOME": str(base / "home"),
                "XDG_CONFIG_HOME": str(base / "config"),
                "XDG_DATA_HOME": str(base / "data"),
                "XDG_STATE_HOME": str(base / "state"),
                "XDG_CACHE_HOME": str(base / "cache"),
            },
            runtime_root=self.runtime_root,
        )
        self.store = StateStore(self.paths)
        self.store.initialize()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_initialize_does_not_create_the_systemd_owned_root(self) -> None:
        self.assertFalse(self.runtime_root.exists())

    def test_caddy_projection_targets_the_systemd_owned_socket(self) -> None:
        rendered = CaddyProjector(self.paths).render(
            {"demo": {"root": str(Path(self.temporary.name) / "demo"), "php": "8.4", "secured": False}}
        )
        self.assertIn(f"unix/{self.runtime_root / 'php' / '8.4' / 'fpm.sock'}", rendered)
        self.assertFalse(self.runtime_root.exists())

    def test_fpm_configuration_is_written_without_the_socket_directory(self) -> None:
        installer = RuntimeInstaller(
            self.store,
            runner=lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, "", ""),
        )
        runtime = Path(self.temporary.name) / "runtime"
        (runtime / "bin").mkdir(parents=True)
        installer._write_fpm_config("8.4", runtime)

        written = (self.paths.state / "fpm" / "php-8.4.conf").read_text(encoding="utf-8")
        self.assertIn(f"listen = {self.runtime_root / 'php' / '8.4' / 'fpm.sock'}", written)
        self.assertIn(f"pid = {self.runtime_root / 'php' / '8.4' / 'php-fpm.pid'}", written)
        self.assertFalse(self.runtime_root.exists())


class ReprojectionTests(unittest.TestCase):
    """An FPM config written before the socket layout changed must not survive."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        base = Path(self.temporary.name)
        self.runtime_root = base / "run" / "paddock"
        self.paths = Paths.from_environment(
            {"HOME": str(base / "home"), "XDG_STATE_HOME": str(base / "state"),
             "XDG_DATA_HOME": str(base / "data"), "XDG_CONFIG_HOME": str(base / "config"),
             "XDG_CACHE_HOME": str(base / "cache")},
            runtime_root=self.runtime_root,
        )
        self.store = StateStore(self.paths)
        self.store.initialize()
        self.active = self.paths.data / "runtimes" / "active" / "8.4"
        (self.active / "bin").mkdir(parents=True)
        php = self.active / "bin" / "php"
        php.write_text("#!/bin/sh\n", encoding="utf-8")
        php.chmod(0o755)
        (self.active / "bin" / "php-fpm").write_text("#!/bin/sh\n", encoding="utf-8")
        RuntimeRegistry(self.store).register("8.4", php, "0" * 64)
        self.config = self.paths.state / "fpm" / "php-8.4.conf"
        self.config.parent.mkdir(parents=True, exist_ok=True)
        self.config.write_text(
            "[paddock]\nlisten = /run/user/1000/paddock/php/8.4/fpm.sock\n", encoding="utf-8"
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_reproject_rewrites_a_session_scoped_socket(self) -> None:
        installer = RuntimeInstaller(
            self.store,
            runner=lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, "", ""),
        )
        self.assertEqual(["8.4"], installer.reproject())
        written = self.config.read_text(encoding="utf-8")
        self.assertNotIn("/run/user/", written)
        self.assertIn(f"listen = {self.runtime_root / 'php' / '8.4' / 'fpm.sock'}", written)
        self.assertFalse(self.runtime_root.exists())

    def test_reproject_skips_a_registered_runtime_without_fpm(self) -> None:
        (self.active / "bin" / "php-fpm").unlink()
        installer = RuntimeInstaller(
            self.store,
            runner=lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, "", ""),
        )
        self.assertEqual([], installer.reproject())


class PhpUnitWritableProjectTests(unittest.TestCase):
    """A project served by Paddock must be writable by php-fpm.

    `ProtectHome=read-only` served static PHP fine but made every Laravel
    project a 500: the framework writes compiled views, real-time facades, and
    logs inside its own tree, and `tempnam()` on a read-only directory falls
    back to the private /tmp with a notice that Laravel turns into an
    ErrorException. Granting one linked root at a time would need root on every
    `paddock link`, which the CLI must not require.
    """

    def setUp(self) -> None:
        helper = load_helper()
        self.unit = helper.php_unit(
            "demo", 1000, Path("/home/demo"),
            Path("/home/demo/.local/share/paddock"),
            Path("/home/demo/.local/state/paddock"),
        )

    def test_the_home_directory_is_writable(self) -> None:
        writable = " ".join(directives(self.unit, "ReadWritePaths")).split()
        self.assertIn("/home/demo", writable)

    def test_home_is_not_shadowed_read_only(self) -> None:
        # ReadWritePaths= cannot win back a home that ProtectHome= has hidden.
        self.assertEqual(["no"], directives(self.unit, "ProtectHome"))

    def test_state_stays_writable_when_xdg_moves_it_outside_home(self) -> None:
        writable = " ".join(directives(self.unit, "ReadWritePaths")).split()
        self.assertIn("/home/demo/.local/state/paddock", writable)

    def test_system_directories_stay_read_only(self) -> None:
        # Relaxing the home directory must not relax /usr and /etc.
        self.assertEqual(["strict"], directives(self.unit, "ProtectSystem"))

    def test_user_secrets_and_the_ca_key_are_hidden(self) -> None:
        hidden = " ".join(directives(self.unit, "InaccessiblePaths")).split()
        for entry in (
            "-/home/demo/.ssh",
            "-/home/demo/.gnupg",
            "-/home/demo/.local/share/paddock/pki",
        ):
            self.assertIn(entry, hidden)

    def test_hidden_paths_tolerate_absence(self) -> None:
        # Without the `-` prefix a missing ~/.gnupg aborts namespace setup.
        for entry in " ".join(directives(self.unit, "InaccessiblePaths")).split():
            self.assertTrue(entry.startswith("-"), entry)

    def test_runtimes_remain_reachable(self) -> None:
        # The denial covers pki only; php-fpm loads its interpreter from a
        # sibling directory under the same data root.
        hidden = " ".join(directives(self.unit, "InaccessiblePaths")).split()
        self.assertNotIn("-/home/demo/.local/share/paddock", hidden)


class UnitVersionStampTests(unittest.TestCase):
    """The stamp is how an upgrade notices the installed unit is stale."""

    # Regenerate both together when php_unit() changes:
    #   ./system/system-helper unit-version
    #   python -c "import hashlib,pathlib;..."  (see the failure message)
    EXPECTED_VERSION = 2
    EXPECTED_DIGEST = "793a332e8970850fa40916501c997387e9a0776bcdb210419d80a96828daef4f"

    def setUp(self) -> None:
        self.helper = load_helper()
        self.unit = self.helper.php_unit(
            "demo", 1000, Path("/home/demo"),
            Path("/home/demo/.local/share/paddock"),
            Path("/home/demo/.local/state/paddock"),
        )

    def test_the_unit_carries_the_stamp_in_the_unit_section(self) -> None:
        # `X-` keys are ignored by systemd, so this is inert at runtime.
        parsed = sections(self.unit)
        self.assertIn(f"X-Paddock-Unit-Version={self.helper.UNIT_VERSION}", parsed["Unit"])

    def test_template_and_version_move_in_lockstep(self) -> None:
        digest = hashlib.sha256(self.unit.encode()).hexdigest()
        self.assertEqual(
            (self.EXPECTED_VERSION, self.EXPECTED_DIGEST),
            (self.helper.UNIT_VERSION, digest),
            "php_unit() changed: bump UNIT_VERSION in system/system-helper and "
            f"update EXPECTED_VERSION/EXPECTED_DIGEST here to {digest!r}. "
            "Without a bump, upgrades leave a stale unit installed and only "
            "fail at the next boot.",
        )

    def test_version_is_queryable_without_root(self) -> None:
        # post_upgrade runs this to learn the shipped version.
        result = subprocess.run(
            [str(Path(__file__).parents[1] / "system/system-helper"), "unit-version"],
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(0, result.returncode)
        self.assertEqual(str(self.helper.UNIT_VERSION), result.stdout.strip())


class WaitForSocketTests(unittest.TestCase):
    """The gate must pass only on a real bound socket, and stay bounded."""

    GATE = Path(__file__).parents[1] / "system/wait-for-socket"

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "fpm.sock"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def gate(self, attempts: str = "3") -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(self.GATE), str(self.path), attempts],
            text=True, capture_output=True, check=False,
        )

    def test_bound_socket_passes(self) -> None:
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(listener.close)
        listener.bind(str(self.path))
        self.assertEqual(0, self.gate().returncode)

    def test_absent_socket_fails_within_the_bound(self) -> None:
        result = self.gate()
        self.assertNotEqual(0, result.returncode)
        self.assertIn("Timed out", result.stderr)

    def test_a_plain_file_is_not_accepted_as_a_socket(self) -> None:
        # -S, not -e: a leftover regular file must not satisfy readiness.
        self.path.write_text("", encoding="utf-8")
        self.assertNotEqual(0, self.gate().returncode)

    def test_missing_argument_is_rejected(self) -> None:
        result = subprocess.run(
            [str(self.GATE)], text=True, capture_output=True, check=False
        )
        self.assertNotEqual(0, result.returncode)


if __name__ == "__main__":
    unittest.main()
