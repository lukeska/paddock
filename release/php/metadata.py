#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import re


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha1(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def command(binary: Path, code: str) -> str:
    return subprocess.run(
        [binary, "-n", "-r", code], check=True, text=True, capture_output=True
    ).stdout.strip()


def php_info(binary: Path) -> str:
    return subprocess.run(
        [binary, "-n", "-i"], check=True, text=True, capture_output=True
    ).stdout


def info_value(info: str, label: str) -> str:
    match = re.search(rf"^{re.escape(label)}\s*=>\s*(.+)$", info, re.MULTILINE)
    if not match:
        raise SystemExit(f"PHP info is missing {label}")
    return match.group(1).strip()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--php", required=True)
    parser.add_argument("--architecture", required=True)
    parser.add_argument("--builder-sha256", required=True)
    parser.add_argument("--source-date-epoch", type=int, required=True)
    args = parser.parse_args()
    artifact_digest = sha256(args.artifact)
    created = datetime.fromtimestamp(args.source_date_epoch, timezone.utc).isoformat()
    files = []
    verification_hashes = []
    for path in sorted(candidate for candidate in args.runtime.rglob("*") if candidate.is_file()):
        relative = path.relative_to(args.runtime).as_posix()
        files.append(
            {
                "SPDXID": "SPDXRef-File-" + hashlib.sha256(relative.encode()).hexdigest()[:16],
                "fileName": f"./{relative}",
                "checksums": [{"algorithm": "SHA256", "checksumValue": sha256(path)}],
            }
        )
        verification_hashes.append(sha1(path))
    namespace = f"https://github.com/lukeska/paddock/spdx/php-{args.php}/{artifact_digest}"
    spdx = {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": f"paddock-php-{args.php}-{args.architecture}",
        "documentNamespace": namespace,
        "creationInfo": {"created": created, "creators": ["Tool: paddock-metadata-v1"]},
        "documentDescribes": ["SPDXRef-Package"],
        "packages": [
            {
                "name": f"paddock-php-{args.php}",
                "SPDXID": "SPDXRef-Package",
                "versionInfo": args.php,
                "downloadLocation": "NOASSERTION",
                "filesAnalyzed": True,
                "licenseConcluded": "NOASSERTION",
                "licenseDeclared": "NOASSERTION",
                "copyrightText": "NOASSERTION",
                "packageVerificationCode": {
                    "packageVerificationCodeValue": hashlib.sha1(
                        "".join(sorted(verification_hashes)).encode()
                    ).hexdigest()
                },
            }
        ],
        "files": files,
        "relationships": [
            {"spdxElementId": "SPDXRef-Package", "relationshipType": "CONTAINS", "relatedSpdxElement": file["SPDXID"]}
            for file in files
        ],
        "annotations": [
            {
                "annotationDate": created,
                "annotationType": "OTHER",
                "annotator": "Tool: paddock-metadata-v1",
                "comment": "File-level inventory of the shipped runtime payload; component build manifests and license texts are included in the artifact.",
            }
        ],
    }
    php = args.runtime / "bin/php"
    info = php_info(php)
    compatibility = {
        "schema_version": 1,
        "php": args.php,
        "architecture": args.architecture,
        "thread_safety": command(php, "echo PHP_ZTS ? 'zts' : 'nts';"),
        "debug": command(php, "echo PHP_DEBUG ? 'debug' : 'non-debug';"),
        "php_api": info_value(info, "PHP API"),
        "zend_extension_api": info_value(info, "Zend Extension Build"),
        "artifact_sha256": artifact_digest,
        "builder_sha256": args.builder_sha256,
        "glibc_baseline": "2.17",
    }
    subject = {"name": args.artifact.name, "digest": {"sha256": artifact_digest}}
    provenance = {
        "_type": "https://in-toto.io/Statement/v1",
        "subject": [subject],
        "predicateType": "https://slsa.dev/provenance/v1",
        "predicate": {
            "buildDefinition": {
                "buildType": "https://github.com/lukeska/paddock/blob/main/release/php/README.md",
                "externalParameters": {
                    "php": args.php,
                    "architecture": args.architecture,
                    "source_date_epoch": args.source_date_epoch,
                },
                "resolvedDependencies": [
                    {"uri": "https://github.com/crazywhalecc/static-php-cli/releases/tag/2.8.5", "digest": {"sha256": args.builder_sha256}}
                ],
            },
            "runDetails": {
                "builder": {"id": "https://github.com/lukeska/paddock/tree/main/release/php"},
                "metadata": {"invocationId": artifact_digest[:20]},
            },
        },
    }
    stem = args.artifact.with_suffix("").with_suffix("")
    stem.with_suffix(".spdx.json").write_text(json.dumps(spdx, indent=2) + "\n")
    stem.with_suffix(".compatibility.json").write_text(json.dumps(compatibility, indent=2) + "\n")
    stem.with_suffix(".provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    args.artifact.with_suffix(args.artifact.suffix + ".sha256").write_text(
        f"{artifact_digest}  {args.artifact.name}\n"
    )


if __name__ == "__main__":
    main()
