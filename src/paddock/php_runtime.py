from __future__ import annotations

import grp
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import tarfile
import tempfile
from typing import Callable
from urllib.request import urlopen

from .artifacts import Artifact, ArtifactManifest
from .atomic import atomic_write, exclusive_lock
from .runtimes import Runtime, RuntimeRegistry, file_sha256, normalize_minor
from .state import StateStore


BASELINE_EXTENSIONS = (
    "curl", "dom", "fileinfo", "filter", "intl", "mbstring", "openssl",
    "pdo", "session", "tokenizer", "xml", "zip",
)

BASELINE_FUNCTIONS = ("mb_split",)


class RuntimeInstallError(RuntimeError):
    pass


Runner = Callable[..., subprocess.CompletedProcess[str]]


class RuntimeInstaller:
    def __init__(self, store: StateStore, runner: Runner = subprocess.run):
        self.store = store
        self.paths = store.paths
        self.runner = runner
        self.registry = RuntimeRegistry(store)
        self.lock = self.paths.state / "runtime-install.lock"

    def install(self, minor: str, manifest: ArtifactManifest, *, replace_custom: bool = False,
                expected_release: str | None = None) -> Path:
        artifact = manifest.select(minor)
        if expected_release is not None and artifact.php != expected_release:
            raise RuntimeInstallError(
                f"PHP {minor} catalog changed from confirmed {expected_release} to {artifact.php}; confirm again"
            )
        with exclusive_lock(self.lock):
            current = next((item for item in self.registry.list() if item.version == artifact.minor), None)
            releases = self.paths.data / "runtimes" / "releases"
            active = self.paths.data / "runtimes" / "active"
            if current is not None:
                if not self._managed(current.path, current.release, current.sha256, releases):
                    if not replace_custom:
                        raise RuntimeInstallError(
                            f"PHP {artifact.minor} is custom or has an unknown patch; "
                            "use --replace-custom to replace it explicitly"
                        )
                elif ((current.release, current.sha256) == (artifact.php, artifact.sha256)
                      and (active / artifact.minor).is_symlink()
                      and (active / artifact.minor).resolve() == current.path.parent.parent):
                    return current.path.parent.parent
                elif self._patch_key(artifact.php) < self._patch_key(current.release):
                    raise RuntimeInstallError(
                        f"PHP {artifact.php} is older than installed {current.release}; use rollback instead"
                    )
                elif current.release == artifact.php and current.sha256 != artifact.sha256:
                    raise RuntimeInstallError(
                        f"PHP {artifact.php} catalog hash differs from the installed release"
                    )
            archive = self._download(artifact)
            releases.mkdir(parents=True, exist_ok=True, mode=0o700)
            active.mkdir(parents=True, exist_ok=True, mode=0o700)
            destination = releases / f"php-{artifact.php}-{artifact.sha256[:12]}"
            if not destination.exists():
                staging = Path(tempfile.mkdtemp(prefix=".install-", dir=releases))
                try:
                    self._extract(archive, staging)
                    runtime = self._runtime_root(staging)
                    self._ensure_configuration(runtime)
                    self._validate(runtime, artifact)
                    os.replace(runtime, destination)
                finally:
                    shutil.rmtree(staging, ignore_errors=True)
            else:
                self._validate(destination, artifact)
            self._switch(artifact.minor, destination, artifact.php, artifact.sha256, current)
            return destination

    def rollback(self, minor: str, *, expected_release: str | None = None) -> Path:
        version = normalize_minor(minor)
        with exclusive_lock(self.lock):
            current = self.registry.resolve(version)
            if not current.previous_release or not current.previous_sha256:
                raise RuntimeInstallError(f"PHP {version} has no retained previous release")
            if expected_release is not None and current.previous_release != expected_release:
                raise RuntimeInstallError(
                    f"PHP {version} rollback target changed from confirmed {expected_release}; confirm again"
                )
            releases = self.paths.data / "runtimes" / "releases"
            if not self._managed(current.path, current.release, current.sha256, releases):
                raise RuntimeInstallError(f"PHP {version} active runtime is not managed by Paddock")
            destination = releases / f"php-{current.previous_release}-{current.previous_sha256[:12]}"
            if not destination.is_dir():
                raise RuntimeInstallError(f"PHP {current.previous_release} release is missing")
            artifact = Artifact(current.previous_release, version, "", "", current.previous_sha256)
            self._validate(destination, artifact)
            self._switch(version, destination, artifact.php, artifact.sha256, current)
            return destination

    @staticmethod
    def _patch_key(value: str) -> tuple[int, ...]:
        return tuple(int(part) for part in value.split("."))

    @staticmethod
    def _managed(path: Path, release: str | None, digest: str, releases: Path) -> bool:
        return bool(release and path == releases / f"php-{release}-{digest[:12]}" / "bin/php"
                    and path.is_file())

    def _switch(self, minor: str, destination: Path, release: str, digest: str,
                current: Runtime | None) -> None:
        active = self.paths.data / "runtimes/active"
        config = self.paths.state / "fpm" / f"php-{minor}.conf"
        old_config = config.read_bytes() if config.exists() else None
        old_link = active / minor
        if old_link.exists() and not old_link.is_symlink():
            raise RuntimeInstallError(f"PHP {minor} activation path is not a symlink: {old_link}")
        old_target = old_link.readlink() if old_link.is_symlink() else None
        old_registry = self.store.read("runtimes")
        changed = False
        try:
            # Validate the candidate configuration before changing the active link.
            self._write_fpm_config(minor, destination)
            changed = True
            self._activate(minor, destination, active)
            self._control_service("restart", minor)
            self._control_service("is-active", minor)
            previous_release = None
            previous_sha256 = None
            if current and self._managed(current.path, current.release, current.sha256,
                                         self.paths.data / "runtimes/releases"):
                if (current.release, current.sha256) == (release, digest):
                    previous_release, previous_sha256 = current.previous_release, current.previous_sha256
                else:
                    previous_release, previous_sha256 = current.release, current.sha256
            self.registry.register(
                minor, destination / "bin/php", digest, release,
                previous_release, previous_sha256,
            )
        except (Exception, KeyboardInterrupt) as error:
            recovery = []
            try:
                if old_target is not None:
                    self._activate(minor, old_target, active)
                elif changed:
                    old_link.unlink(missing_ok=True)
                if old_config is not None:
                    atomic_write(config, old_config)
                else:
                    config.unlink(missing_ok=True)
                self.store.write("runtimes", old_registry)
            except Exception as rollback_error:
                recovery.append(f"state restore failed: {rollback_error}")
            if changed:
                try:
                    self._control_service("restart" if old_target else "stop", minor)
                except Exception as rollback_error:
                    recovery.append(f"service restore failed: {rollback_error}")
            detail = f"PHP {minor} activation failed: {error}"
            if recovery:
                detail += "; rollback incomplete: " + "; ".join(recovery)
            raise RuntimeInstallError(detail) from error

    def reproject(self) -> list[str]:
        """Rewrite the FPM configuration of every registered runtime.

        The configuration is derived from the runtime record and the socket
        layout, so a generation written before the layout changed would make
        FPM bind a path its unit no longer grants. Returns the versions
        reprojected.
        """
        active = self.paths.data / "runtimes" / "active"
        reprojected = []
        with exclusive_lock(self.lock):
            for runtime in self.registry.list():
                root = active / runtime.version
                if not (root / "bin" / "php-fpm").is_file():
                    continue
                self._write_fpm_config(runtime.version, root)
                reprojected.append(runtime.version)
        return reprojected

    def remove(self, minor: str) -> None:
        version = normalize_minor(minor)
        sites = self.store.read("sites")["sites"]
        users = sorted(name for name, site in sites.items() if site["php"] == version)
        if users:
            raise RuntimeInstallError(
                f"PHP {version} is used by: {', '.join(name + '.test' for name in users)}"
            )
        with exclusive_lock(self.lock):
            self._control_service("stop", version)
            link = self.paths.data / "runtimes" / "active" / version
            release = link.resolve(strict=False) if link.is_symlink() else None
            link.unlink(missing_ok=True)
            self.registry.remove(version)
            (self.paths.state / "fpm" / f"php-{version}.conf").unlink(missing_ok=True)
            if release is not None and release.is_dir():
                shutil.rmtree(release)

    def _download(self, artifact: Artifact) -> Path:
        downloads = self.paths.cache / "artifacts"
        downloads.mkdir(parents=True, exist_ok=True, mode=0o700)
        archive = downloads / f"{artifact.sha256}.tar.gz"
        if archive.exists() and file_sha256(archive) == artifact.sha256:
            return archive
        archive.unlink(missing_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix=".download-", dir=downloads)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as output, urlopen(artifact.url) as response:
                shutil.copyfileobj(response, output)
                output.flush()
                os.fsync(output.fileno())
            actual = file_sha256(temporary)
            if actual != artifact.sha256:
                raise RuntimeInstallError(
                    f"artifact checksum mismatch: expected {artifact.sha256}, got {actual}"
                )
            os.replace(temporary, archive)
            return archive
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _extract(archive: Path, staging: Path) -> None:
        try:
            with tarfile.open(archive, "r:gz") as source:
                for member in source.getmembers():
                    if member.issym() or member.islnk() or member.isdev():
                        raise RuntimeInstallError(f"unsafe archive member: {member.name}")
                source.extractall(staging, filter="data")
        except (tarfile.TarError, OSError) as error:
            raise RuntimeInstallError(f"cannot extract runtime artifact: {error}") from error

    @staticmethod
    def _runtime_root(staging: Path) -> Path:
        if (staging / "bin" / "php").is_file():
            return staging
        children = [child for child in staging.iterdir() if child.is_dir()]
        if len(children) == 1 and (children[0] / "bin" / "php").is_file():
            return children[0]
        raise RuntimeInstallError("artifact must contain one runtime root with bin/php")

    @staticmethod
    def _ensure_configuration(runtime: Path) -> None:
        config = runtime / "etc"
        (config / "conf.d").mkdir(parents=True, exist_ok=True, mode=0o700)
        php_ini = config / "php.ini"
        if not php_ini.exists():
            atomic_write(
                php_ini,
                b"date.timezone = UTC\ndisplay_errors = On\nerror_reporting = E_ALL\nmemory_limit = 256M\n",
            )

    def _validate(self, runtime: Path, artifact: Artifact) -> None:
        php = runtime / "bin" / "php"
        fpm = runtime / "bin" / "php-fpm"
        if not php.is_file() or not fpm.is_file():
            raise RuntimeInstallError("runtime requires bin/php and bin/php-fpm")
        php.chmod(0o755)
        fpm.chmod(0o755)
        environment = self._environment(runtime)
        extension_dir = f"extension_dir={runtime / 'modules'}"
        cli_version = self._run(
            [str(php), "-d", extension_dir, "-r", "echo PHP_VERSION;"], environment
        ).stdout.strip()
        fpm_output = self._run([str(fpm), "-d", extension_dir, "-v"], environment).stdout.splitlines()
        fpm_version = fpm_output[0].split()[1] if fpm_output and len(fpm_output[0].split()) > 1 else ""
        if cli_version != artifact.php or fpm_version != artifact.php:
            raise RuntimeInstallError(
                f"runtime version mismatch: manifest={artifact.php} cli={cli_version} fpm={fpm_version}"
            )
        missing = []
        for extension in BASELINE_EXTENSIONS:
            result = self.runner(
                [str(php), "-d", extension_dir, "-r", f"exit(extension_loaded('{extension}') ? 0 : 1);"],
                env=environment, text=True, capture_output=True, check=False,
            )
            if result.returncode != 0:
                missing.append(extension)
        if missing:
            raise RuntimeInstallError(
                f"PHP {artifact.php} is missing required extensions: {', '.join(missing)}"
            )
        missing_functions = []
        for function in BASELINE_FUNCTIONS:
            result = self.runner(
                [
                    str(php), "-d", extension_dir, "-r",
                    f"exit(function_exists('{function}') ? 0 : 1);",
                ],
                env=environment, text=True, capture_output=True, check=False,
            )
            if result.returncode != 0:
                missing_functions.append(function)
        if missing_functions:
            raise RuntimeInstallError(
                f"PHP {artifact.php} is missing required functions: "
                f"{', '.join(missing_functions)}"
            )

    def _write_fpm_config(self, minor: str, runtime: Path) -> None:
        # `run` is created by the unit's RuntimeDirectory=, not here: it lives
        # under root-owned /run and must be absent until the service starts.
        run = self.paths.runtime / "php" / minor
        log = self.paths.state / "logs" / "php" / minor
        config = self.paths.state / "fpm" / f"php-{minor}.conf"
        log.mkdir(parents=True, exist_ok=True, mode=0o700)
        user = pwd.getpwuid(os.getuid()).pw_name
        group = grp.getgrgid(os.getgid()).gr_name
        value = (
            "[global]\n"
            f"pid = {run / 'php-fpm.pid'}\n"
            f"error_log = {log / 'php-fpm.log'}\n"
            "daemonize = no\n\n"
            "[paddock]\n"
            f"user = {user}\n"
            f"group = {group}\n"
            f"listen = {run / 'fpm.sock'}\n"
            "listen.mode = 0600\n"
            "pm = dynamic\n"
            "pm.max_children = 5\n"
            "pm.start_servers = 1\n"
            "pm.min_spare_servers = 1\n"
            "pm.max_spare_servers = 3\n"
            "catch_workers_output = yes\n"
            "clear_env = yes\n"
        )
        atomic_write(config, value.encode())
        environment = self._environment(runtime)
        self._run(
            [
                str(runtime / "bin" / "php-fpm"),
                "-d", f"extension_dir={runtime / 'modules'}",
                "-t", "-y", str(config),
            ],
            environment,
        )

    @staticmethod
    def _activate(minor: str, destination: Path, active: Path) -> None:
        temporary = active / f".{minor}.next"
        temporary.unlink(missing_ok=True)
        temporary.symlink_to(destination)
        os.replace(temporary, active / minor)
        directory = os.open(active, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    @staticmethod
    def _environment(runtime: Path) -> dict[str, str]:
        environment = os.environ.copy()
        environment["PHPRC"] = str(runtime / "etc" / "php.ini")
        environment["PHP_INI_SCAN_DIR"] = str(runtime / "etc" / "conf.d")
        return environment

    def _run(self, command: list[str], environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
        result = self.runner(
            command, env=environment, text=True, capture_output=True, check=False
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
            raise RuntimeInstallError(f"runtime validation failed: {detail}")
        return result

    def _control_service(self, action: str, minor: str) -> None:
        result = self.runner(
            ["systemctl", action, f"paddock-php@{minor}.service"],
            text=True, capture_output=True, check=False,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
            raise RuntimeInstallError(
                f"cannot {action} PHP {minor} service: {detail}"
            )
