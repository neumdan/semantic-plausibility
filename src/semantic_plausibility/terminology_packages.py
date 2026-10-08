"""Explicit, pinned BfArM package installation. Content and licences stay local."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import tarfile
import tempfile
from urllib.parse import urlsplit
from urllib.request import Request, build_opener

from .deployment import Deployment, table
from .observation_services import _HttpSession, _NoModelRedirects

REGISTRY = "https://terminologien.bfarm.de"
PACKAGE_NAME = re.compile(r"[a-z][a-z0-9-]*(?:\.[a-z][a-z0-9-]*)+")
PACKAGE_VERSION = re.compile(r"\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?")
ARCHIVE_LIMIT, CONTENT_LIMIT, FILE_LIMIT = 64 * 1024**2, 256 * 1024**2, 10000


def https_url(url):
    if not isinstance(url, str):
        raise ValueError("Package/API URL must be a string")
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Package/API URLs require HTTPS without credentials, query or fragment")
    return url.rstrip("/")


def validate_packages(packages):
    if not isinstance(packages, dict):
        raise ValueError("packages must be named TOML tables")
    for row in packages.values():
        table(
            row,
            "package",
            {
                "enabled",
                "registry",
                "name",
                "version",
                "path",
                "accept_download_conditions",
                "token_env",
                "token_file",
            },
        )
        for flag in ("enabled", "accept_download_conditions"):
            if type(row.get(flag, False)) is not bool:
                raise ValueError(f"package.{flag} must be true or false")
        if not PACKAGE_NAME.fullmatch(str(row.get("name", ""))) or not PACKAGE_VERSION.fullmatch(
            str(row.get("version", ""))
        ):
            raise ValueError("Pin each package name and exact semantic version")
        if not isinstance(row.get("path"), str) or not row["path"]:
            raise ValueError("Each package needs a local installation path")
        https_url(row.get("registry", REGISTRY))
    Deployment.validate_references(packages)


def package_metadata(name, version, registry=REGISTRY):
    if (
        not isinstance(name, str)
        or not isinstance(version, str)
        or not PACKAGE_NAME.fullmatch(name)
        or not PACKAGE_VERSION.fullmatch(version)
    ):
        raise ValueError("Invalid package name/version")
    registry = https_url(registry)
    response = _HttpSession().get(f"{registry}/packages/{name}")
    response.raise_for_status()
    document = response.json()
    row = document.get("versions", {}).get(version, {})
    if row.get("name") != name or row.get("version") != version or row.get("fhirVersion") != "R4":
        raise ValueError("Requested R4 package edition not found in registry metadata")
    url = https_url(row.get("dist", {}).get("tarball", ""))
    if urlsplit(url).netloc != urlsplit(registry).netloc:
        raise ValueError("Package archive must be on the configured registry origin")
    if not re.fullmatch(r"[a-fA-F0-9]{40}", row.get("dist", {}).get("shasum", "")):
        raise ValueError("Registry metadata has no usable archive SHA-1")
    return row


def install_package(
    name, version, destination, *, registry=REGISTRY, accept_download_conditions=False, token=""
):
    """Download real content, check publisher checksum, safely unpack and record hashes.

    Dependencies are recorded, never silently installed. Supplements remain
    label evidence; installation does not imply complete CodeSystem coverage.
    """
    registry = https_url(registry)
    metadata = package_metadata(name, version, registry)
    if metadata.get("protected") and not accept_download_conditions:
        raise ValueError(
            "Read "
            + metadata.get("download-conditions", registry)
            + " and explicitly set accept_download_conditions=true before downloading"
        )
    destination = Path(destination)
    receipt_file = destination / "installation.json"
    if destination.exists():
        if destination.is_symlink() or not receipt_file.is_file():
            raise ValueError("Package destination already exists without a verified installation receipt")
        receipt = json.loads(receipt_file.read_text())
        if (
            receipt.get("name"),
            receipt.get("version"),
            receipt.get("registry"),
            receipt.get("publisher_sha1"),
        ) != (name, version, registry, metadata["dist"]["shasum"]):
            raise ValueError("Existing package differs; choose a new versioned installation path")
        for relative, digest in receipt["files"].items():
            if PurePosixPath(relative).is_absolute() or ".." in PurePosixPath(relative).parts:
                raise ValueError("Unsafe installed package receipt path")
            path = destination / relative
            if (
                not path.resolve().is_relative_to(destination.resolve())
                or path.is_symlink()
                or not path.is_file()
                or hashlib.sha256(path.read_bytes()).hexdigest() != digest
            ):
                raise ValueError("Installed package content changed; choose a fresh installation path")
        return {**receipt, "path": str(destination), "downloaded": False}
    if metadata.get("protected") and not token:
        # BfArM's token denotes acceptance of the named package's download conditions.
        response = _HttpSession().post(registry + "/api/generate-token", json={"packages": [name]})
        response.raise_for_status()
        token = response.json().get("token", "")
        if not isinstance(token, str) or not token:
            raise ValueError("Registry did not issue a package download token")
    headers = {"Accept": "application/tar+gzip"}
    if token:
        headers["Authorization"] = "Bearer " + token
    try:
        with build_opener(_NoModelRedirects()).open(
            Request(metadata["dist"]["tarball"], headers=headers), timeout=60
        ) as response:
            archive = response.read(ARCHIVE_LIMIT + 1)
    except OSError:
        raise ValueError("Package download failed; check registry access and download conditions") from None
    if (
        len(archive) > ARCHIVE_LIMIT
        or hashlib.sha1(archive).hexdigest() != metadata["dist"]["shasum"].lower()
    ):
        raise ValueError("Package archive exceeds budget or differs from publisher checksum")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".terminology-", dir=destination.parent) as folder:
        staging = Path(folder) / "install"
        staging.mkdir()
        files, size = {}, 0
        try:
            with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
                for index, member in enumerate(tar, 1):
                    parts = PurePosixPath(member.name).parts
                    if (
                        index > FILE_LIMIT
                        or not parts
                        or parts[0] != "package"
                        or any(part in {"..", "."} for part in parts)
                        or "\\" in member.name
                        or PurePosixPath(member.name).as_posix() != member.name.rstrip("/")
                    ):
                        raise ValueError("Unsafe package archive path or file count")
                    if member.isdir():
                        continue
                    if not member.isfile() or len(parts) < 2 or not 0 <= member.size <= 64 * 1024**2:
                        raise ValueError("Package archive contains links/special files or an oversized file")
                    size += member.size
                    if size > CONTENT_LIMIT or member.name in files:
                        raise ValueError("Package archive exceeds content budget or duplicates paths")
                    with tar.extractfile(member) as handle:
                        body = handle.read(member.size + 1)
                    if len(body) != member.size:
                        raise ValueError("Truncated package content")
                    target = staging / member.name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(body)
                    files[member.name] = hashlib.sha256(body).hexdigest()
        except tarfile.TarError:
            raise ValueError("Invalid package archive") from None
        manifest = json.loads((staging / "package/package.json").read_text())
        if (
            manifest.get("name") != name
            or manifest.get("version") != version
            or "4.0.1" not in manifest.get("fhirVersions", [])
        ):
            raise ValueError("Archive manifest does not match the pinned FHIR R4 edition")
        (staging / "package.tgz").write_bytes(archive)  # Retain original licence/readme files too.
        files["package.tgz"] = hashlib.sha256(archive).hexdigest()
        receipt = {
            "name": name,
            "version": version,
            "registry": registry,
            "archive_url": metadata["dist"]["tarball"],
            "publisher_sha1": metadata["dist"]["shasum"],
            "archive_sha256": files["package.tgz"],
            "download_conditions": metadata.get("download-conditions", ""),
            "conditions_accepted": accept_download_conditions,
            "installed_at": datetime.now(timezone.utc).isoformat(),
            "dependencies": manifest.get("dependencies", {}),
            "files": files,
        }
        (staging / "installation.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
        if destination.exists():
            raise ValueError("Package destination appeared during installation")
        os.rename(staging, destination)
    return {**receipt, "path": str(destination), "downloaded": True}


def initialise_packages(path, alias=None):
    from .batch import BatchConfiguration

    config = BatchConfiguration(path)
    if config.network_mode != "lookups":
        raise ValueError("Package downloads require network.mode=lookups; installed packages work offline")
    packages = config.data.get("packages", {})
    selected = {
        key: row for key, row in packages.items() if row.get("enabled") and (alias is None or key == alias)
    }
    if not selected:
        raise ValueError("Enable at least one configured package; --package selects its alias")
    results = {}
    for key, row in selected.items():
        results[key] = install_package(
            row["name"],
            row["version"],
            config.path_value(row["path"]),
            registry=row.get("registry", REGISTRY),
            accept_download_conditions=row.get("accept_download_conditions", False),
            token=config.deployment.secret(row, "token"),
        )
    return results
