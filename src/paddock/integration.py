from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
from typing import Callable

from .artifacts import ArtifactManifest, ManifestError, normalized_architecture
from .composer import install_composer
from .config_watcher import ConfigWatcher
from .php_runtime import RuntimeInstaller
from .parking import ParkingManager
from .services import ServiceManager
from .shell import install_shell_integration, remove_shell_integration
from .node_runtime import NodeInstaller, NodeManifest
from .state import StateStore
from .web import WebProjector


SYSTEM_HELPER = Path("/usr/lib/paddock/system-helper")


class IntegrationError(RuntimeError):
    pass


Runner = Callable[..., subprocess.CompletedProcess[str]]


INSTALL_CHANGES = (
    "install Paddock-only systemd target, DNS, web, and PHP-FPM units",
    "add a NetworkManager dummy connection routing only ~test to 127.0.0.1",
    "trust the Paddock public CA in system and current-user NSS stores",
    "allow the desktop user to manage only Paddock systemd units",
    "enable and start paddock.target at boot",
    "enable lingering for the desktop user, so supporting services start at "
    "boot and survive logout; this also keeps your other enabled user units "
    "running after logout",
    "install the latest published PHP runtime on first setup",
    "install a pinned, checksum-verified Composer release",
    "enable project-aware php and composer commands in new terminals",
    "install the latest supported Node.js LTS runtime on first setup",
    "automatically validate and apply saved nginx configuration fragments",
)

REMOVE_CHANGES = (
    "stop and disable paddock.target",
    "remove the Paddock NetworkManager connection and systemd units",
    "remove only the matching Paddock CA trust entries",
    "remove the Paddock-specific policy rule and DNS configuration",
    "disable lingering again, but only if Paddock was the one that enabled it",
    "preserve projects, configuration, runtimes, logs, cache, and private CA",
    "remove Paddock's shell PATH block without touching other shell customizations",
)


class Integration:
    def __init__(
        self,
        store: StateStore,
        runner: Runner = subprocess.run,
        artifact_paths: tuple[Path, ...] | None = None,
        composer_paths: tuple[Path, ...] | None = None,
        node_paths: tuple[Path, ...] | None = None,
    ):
        self.store = store
        self.runner = runner
        self.artifact_paths = artifact_paths or (
            Path("/usr/share/paddock/artifacts.json"),
            Path(__file__).resolve().parents[2] / "resources" / "artifacts.json",
        )
        self.composer_paths = composer_paths or (
            Path("/usr/share/paddock/composer.json"),
            Path(__file__).resolve().parents[2] / "resources" / "composer.json",
        )
        self.node_paths = node_paths or (
            Path("/usr/share/paddock/node-artifacts.json"),
            Path(__file__).resolve().parents[2] / "resources/node-artifacts.json",
        )

    # Written by releases that served sites with Caddy. Removed after the
    # nginx tree is promoted, never before: a failed projection must leave a
    # machine able to roll back to the previous package.
    LEGACY_STATE = ("caddy", "caddy-data", "caddy-config")

    def prepare(self) -> None:
        self.store.initialize()
        # Always reproject. The nginx tree is derived from durable site records
        # and the socket layout, so keeping a stale generation would point
        # nginx at sockets the current units never bind. Validation still runs
        # first, so an invalid render never replaces the promoted generation.
        projector = WebProjector(self.store.paths, self.runner)
        parking = ParkingManager(self.store, self.runner)
        if self.store.paths.home is not None:
            parking.ensure_default(self.store.paths.home)
            parking.sync_watchers()
            parking.reconcile(projector, reload=False)
        candidate = projector.render(self.store.read("sites")["sites"])
        projector.validate(candidate)
        projector.write(candidate)
        for legacy in self.LEGACY_STATE:
            shutil.rmtree(self.store.paths.state / legacy, ignore_errors=True)
        RuntimeInstaller(self.store, self.runner).reproject()
        ServiceManager(self.store, self.runner).reproject()
        ConfigWatcher(self.store, self.runner).install()
        self._ensure_ca()

    def install(self) -> None:
        self._helper("install")

    def install_initial_php(self) -> str | None:
        """Install the newest compatible PHP exactly once for this user."""
        settings = self.store.read("settings")
        if settings["initial_php_setup_complete"]:
            return None
        manifest = None
        for path in self.artifact_paths:
            if not path.is_file():
                continue
            try:
                manifest = ArtifactManifest.load(path)
            except ManifestError:
                continue
            break
        if manifest is None:
            raise IntegrationError("no valid PHP runtime catalog is available")
        architecture = normalized_architecture()
        compatible = tuple(
            artifact for artifact in manifest.artifacts
            if artifact.architecture == architecture
        )
        if not compatible:
            raise IntegrationError(
                f"no published PHP runtime is available for {architecture}"
            )
        latest = max(
            compatible,
            key=lambda artifact: tuple(int(part) for part in artifact.php.split(".")),
        )
        RuntimeInstaller(self.store, self.runner).install(latest.minor, manifest)
        self.store.update("settings", lambda value: {
            **value,
            "default_php": value["default_php"] or latest.minor,
            "initial_php_setup_complete": True,
        })
        return latest.minor

    def install_composer(self) -> str | None:
        catalog = next((path for path in self.composer_paths if path.is_file()), None)
        if catalog is None:
            raise IntegrationError("no Composer artifact catalog is available")
        return install_composer(self.store.paths.data, catalog)

    def install_initial_node(self) -> str | None:
        settings = self.store.read("settings")
        if settings["initial_node_setup_complete"]: return None
        catalog = next((path for path in self.node_paths if path.is_file()), None)
        if catalog is None: raise IntegrationError("no Node runtime catalog is available")
        manifest = NodeManifest.load(catalog)
        latest = max(manifest.artifacts, key=lambda item: tuple(map(int, item.node.split("."))))
        NodeInstaller(self.store).install(latest.major, manifest)
        self.store.update("settings", lambda value: {**value, "default_node": value.get("default_node") or latest.major, "initial_node_setup_complete": True})
        return latest.major

    def install_shell_integration(self) -> bool:
        if self.store.paths.home is None:
            raise IntegrationError("HOME is required for shell integration")
        return install_shell_integration(self.store.paths.home)

    def uninstall(self) -> None:
        ConfigWatcher(self.store, self.runner).remove()
        if self.store.paths.home is not None:
            remove_shell_integration(self.store.paths.home)
        self._helper("uninstall")

    def _ensure_ca(self) -> None:
        caroot = self.store.paths.data / "pki"
        root = caroot / "rootCA.pem"
        if root.exists():
            return
        caroot.mkdir(parents=True, exist_ok=True, mode=0o700)
        bootstrap = caroot / ".bootstrap.pem"
        bootstrap_key = caroot / ".bootstrap-key.pem"
        try:
            result = self.runner(
                [
                    "mkcert", "-cert-file", str(bootstrap), "-key-file",
                    str(bootstrap_key), "paddock-bootstrap.invalid",
                ],
                env={**os.environ, "CAROOT": str(caroot)},
                text=True,
                capture_output=True,
                check=False,
            )
            if result.returncode != 0 or not root.is_file():
                detail = result.stderr.strip() or result.stdout.strip() or "root CA not created"
                raise IntegrationError(f"cannot create Paddock CA: {detail}")
        finally:
            bootstrap.unlink(missing_ok=True)
            bootstrap_key.unlink(missing_ok=True)

    def _helper(self, action: str) -> None:
        if action not in {"install", "uninstall"}:
            raise IntegrationError(f"unsupported integration action: {action}")
        result = self.runner(
            [
                "sudo", str(SYSTEM_HELPER), action, "--user", os.environ["USER"],
                "--data-dir", str(self.store.paths.data),
                "--state-dir", str(self.store.paths.state),
            ],
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise IntegrationError(f"system integration {action} failed")
