"""What kind of project a directory holds, and how to serve it.

Paddock served Laravel only, so `public/` and Laravel's front-controller
fallback were hardcoded in two places. Anything else — WordPress, Symfony, a
built static site — could not be linked at all, because linking refused a
project without a `public/` directory.

A driver is a data record, not a class. Everything that differs between
frameworks is a value: where the document root is, which index files count,
the front-controller expression, and any extra location blocks. Adding a
framework is adding a row, which is the point of moving to nginx — these rows
are the well-known nginx rules for each framework rather than a translation of
them.

`document_root` is a tuple of candidates because the generic drivers cannot
know the layout in advance: a plain PHP project might keep its entry point in
`public/` or at the top level. The first candidate that exists wins, and the
last is the fallback, so resolution always produces an answer for callers that
cannot fail — parking reconciliation, which runs on a filesystem event and must
not raise because a folder is mid-clone.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class DriverError(ValueError):
    pass


@dataclass(frozen=True)
class Driver:
    name: str
    # Relative to the project root, in preference order. "." is the root.
    document_root: tuple[str, ...]
    index: tuple[str, ...]
    # nginx `try_files` arguments, without the trailing semicolon.
    try_files: str
    # Whether a PHP location is generated at all. A static site must not hand
    # a `.php` file to FPM just because one was dropped in the document root.
    php: bool = True
    # Verbatim location blocks, emitted before the shared ones. Regex
    # locations are matched in order of appearance, so anything that must beat
    # the generic `\.php$` block has to be here.
    locations: tuple[str, ...] = ()


LARAVEL = Driver(
    name="laravel",
    document_root=("public",),
    index=("index.php", "index.html"),
    try_files="$uri $uri/ /index.php?$query_string",
)

STATAMIC = Driver(
    name="statamic",
    document_root=("public",),
    index=("index.php", "index.html"),
    try_files="$uri $uri/ /index.php?$query_string",
)

SYMFONY = Driver(
    name="symfony",
    document_root=("public",),
    index=("index.php",),
    try_files="$uri /index.php$is_args$args",
)

WORDPRESS = Driver(
    name="wordpress",
    # WordPress serves from the directory holding wp-config.php.
    document_root=(".",),
    index=("index.php", "index.html"),
    try_files="$uri $uri/ /index.php?$args",
    locations=(
        # The one WordPress rule worth having by default: an upload directory
        # is writable by the application, so executing PHP from it turns any
        # upload bug into code execution. This must precede the shared PHP
        # location, because nginx takes the first matching regex.
        "location ~* ^/wp-content/uploads/.*\\.php$ {\n\t\tdeny all;\n\t}",
    ),
)

PHP = Driver(
    name="php",
    document_root=("public", "."),
    index=("index.php", "index.html"),
    try_files="$uri $uri/ /index.php?$query_string",
)

STATIC = Driver(
    name="static",
    document_root=("public", "dist", "build", "."),
    index=("index.html", "index.htm"),
    try_files="$uri $uri/ =404",
    php=False,
    locations=(
        # This driver is also the fallback for a directory nothing else
        # matched, so it may well contain PHP. With no PHP location a request
        # for one would fall to the static file server and hand back the
        # source. A regex location outranks the `/` prefix, so this wins.
        "location ~ \\.php$ {\n\t\tdeny all;\n\t}",
    ),
)

DRIVERS = {
    driver.name: driver
    for driver in (LARAVEL, STATAMIC, SYMFONY, WORDPRESS, PHP, STATIC)
}

DEFAULT = LARAVEL.name


def resolve(name: str) -> Driver:
    try:
        return DRIVERS[name]
    except KeyError:
        raise DriverError(
            f"unknown project type '{name}'; supported: {', '.join(sorted(DRIVERS))}"
        ) from None


def detect(root: Path) -> Driver:
    """Identify a project from files a repository commits.

    Order matters and the first match wins. Every marker is something present
    in a fresh clone, before any dependency is installed, so linking a
    just-cloned repository identifies it correctly.
    """
    if (root / "please").is_file() and (root / "artisan").is_file():
        # Statamic is Laravel underneath and ships both, so it has to be
        # tested before Laravel or it would never be reported.
        return STATAMIC
    if (root / "artisan").is_file():
        return LARAVEL
    if (root / "wp-config.php").is_file() or (root / "wp-settings.php").is_file():
        return WORDPRESS
    if (root / "bin/console").is_file() and (root / "public/index.php").is_file():
        return SYMFONY
    if (root / "public/index.php").is_file() or (root / "index.php").is_file():
        return PHP
    return STATIC


def document_root(root: Path, driver: Driver, override: str | None = None) -> str:
    """Choose the document root, as a path relative to the project root.

    Relative rather than absolute so the value cannot drift from the project
    root recorded beside it, and so a project that moves needs only its root
    corrected. `.` means the project root itself.

    An override comes from `paddock.yml`, which is committed code from a clone,
    so it is confined to the project: absolute paths and `..` are refused.
    """
    if override is not None:
        return normalize_document_root(override)
    for candidate in driver.document_root:
        if (root / candidate).is_dir():
            return candidate
    return driver.document_root[-1]


def normalize_document_root(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DriverError("document root must be a non-empty relative path")
    candidate = value.strip()
    path = Path(candidate)
    if path.is_absolute():
        raise DriverError(f"document root must be relative to the project: {candidate}")
    parts = [part for part in path.parts if part not in (".",)]
    if any(part == ".." for part in parts):
        raise DriverError(f"document root must stay inside the project: {candidate}")
    return "/".join(parts) if parts else "."


def document_path(root: Path, value: str) -> Path:
    """Resolve a recorded document root against its project root."""
    return root if value == "." else root / value
