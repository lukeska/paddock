"""Per-site nginx fragments, and whether one is trusted enough to include.

Two sources answer different needs. A user fragment under Paddock's own config
directory is authored by the person running Paddock, works for a parked folder
with no project file, and never touches the repository. A project fragment is
committed beside the code, so a team shares it — which is exactly why it cannot
simply be honoured.

nginx runs as the desktop user, not root, so a project fragment is not a
privilege escalation. It is still a new exposure: `alias /home/you/.ssh;` in a
cloned repository would publish those files on a `.test` host the user then
browses in an authenticated session. Nothing else Paddock reads from a
repository can do that — `paddock.yml` chooses between Paddock's own options,
while this is arbitrary server configuration.

So a project fragment is inert until explicitly trusted, and the trust is
recorded as the fragment's SHA-256. Editing it, or a pull that changes it,
revokes that trust automatically because the digest no longer matches. Refusing
never blocks anything: the site is served without the fragment.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path

from .drivers import DriverError, normalize_document_root
from .paths import Paths


# Herd names a per-site file after the site, and users coming from it look for
# that shape.
USER_SUFFIX = ".custom.conf"

# Status of a project fragment, in the order a user meets them.
NONE = "none"          # the project declares nothing
MISSING = "missing"    # declared, but the file is not there
PENDING = "pending"    # declared, never reviewed
CHANGED = "changed"    # trusted once, and the contents changed since
TRUSTED = "trusted"    # trusted, and unchanged


@dataclass(frozen=True)
class SiteConfiguration:
    """Everything a caller needs to render, explain, or act on one site."""

    user: Path
    user_present: bool
    project: Path | None
    project_relative: str | None
    status: str

    @property
    def includable(self) -> tuple[Path, ...]:
        """Fragments safe to include, in precedence order.

        The project fragment first and the user fragment last, because the
        person at the keyboard outranks the repository.
        """
        paths: list[Path] = []
        if self.status == TRUSTED and self.project is not None:
            paths.append(self.project)
        if self.user_present:
            paths.append(self.user)
        return tuple(paths)

    @property
    def needs_review(self) -> bool:
        return self.status in {PENDING, CHANGED}


def user_path(paths: Paths, name: str) -> Path:
    return paths.config / "nginx" / f"{name}{USER_SUFFIX}"


def normalize_project_path(value: str) -> str:
    """Confine a declared fragment to its own project.

    Reuses the document-root rule, for the same reason: the value arrives with
    a clone, so it must not be able to name a file elsewhere on the machine.
    """
    return normalize_document_root(value)


def digest(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def describe(paths: Paths, name: str, record: dict) -> SiteConfiguration:
    """Resolve both fragments for one site record."""
    user = user_path(paths, name)
    declaration = record.get("nginx")
    if not declaration:
        return SiteConfiguration(user, user.is_file(), None, None, NONE)

    relative = declaration["path"]
    project = Path(record["root"]) / relative
    trusted = declaration.get("sha256")
    current = digest(project)
    if current is None:
        status = MISSING
    elif trusted is None:
        status = PENDING
    elif trusted != current:
        status = CHANGED
    else:
        status = TRUSTED
    return SiteConfiguration(user, user.is_file(), project, relative, status)


def declare(record: dict, relative: str | None) -> dict:
    """Record what a project file declares, preserving trust where it can.

    A declaration that names the same file keeps its trust, so re-running
    `paddock init` does not ask again. Pointing at a different file drops it,
    because trust was granted to contents, not to a key in a YAML document.
    """
    if relative is None:
        return {key: value for key, value in record.items() if key != "nginx"}
    relative = normalize_project_path(relative)
    previous = record.get("nginx") or {}
    declaration: dict = {"path": relative}
    if previous.get("path") == relative and previous.get("sha256"):
        declaration["sha256"] = previous["sha256"]
    return {**record, "nginx": declaration}


def template(name: str) -> str:
    """The starting point `paddock config edit` writes for a new fragment."""
    return (
        f"# nginx directives for {name}.test, owned by you.\n"
        "#\n"
        "# Included at the end of this site's server block, after Paddock's own\n"
        "# directives, so a plain directive here overrides one above it — nginx\n"
        "# takes the last. A `location` is different: nginx picks the longest\n"
        "# matching prefix, and only then tries regular expressions in the\n"
        "# order they appear. To beat one of Paddock's generated regex\n"
        "# locations, such as the PHP handler, use `^~` or an exact `=` match,\n"
        "# which both outrank a regex whatever the order.\n"
        "#\n"
        "# Paddock validates this file with `nginx -t` before anything is\n"
        "# promoted, so a mistake here is reported and the site keeps serving\n"
        "# what it served before.\n"
        "\n"
        "# client_max_body_size 512m;\n"
        "# location = /health { return 200 'ok'; }\n"
    )
