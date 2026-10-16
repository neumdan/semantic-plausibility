"""Acquire a pinned profile and explicit bindings into operator-owned runtime files."""

from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import tarfile
import tomllib
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .profile_import import parse_structure_definition
from .progress import LOGGER


class HttpsRedirect(HTTPRedirectHandler):
    """Public package registries redirect to HTTPS artifact storage; no auth is sent."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urlsplit(newurl)
        if parsed.scheme != "https" or parsed.username or parsed.password:
            raise ValueError("Profile download redirect must remain HTTPS without credentials")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(url, limit):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Profile downloads need explicit HTTPS URLs without credentials, queries or fragments")
    LOGGER.info("Download profile reference: %s", url)
    with build_opener(HttpsRedirect()).open(Request(url, headers={"Accept": "application/json, application/octet-stream"}), timeout=45) as response:
        raw = response.read(limit + 1)
        if response.status != 200 or len(raw) > limit:
            raise ValueError("Profile download failed or exceeded its size budget")
        return raw


def package_profile(archive, canonical, version):
    """Inspect regular JSON members without extracting archive paths to disk."""
    found = []
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as package:
        members = package.getmembers()
        if len(members) > 10000 or sum(m.size for m in members) > 256 * 1024**2:
            raise ValueError("FHIR package exceeds the extraction budget")
        for member in members:
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or not (member.isdir() or member.isfile()):
                raise ValueError("FHIR package contains unsafe archive members")
            if member.isfile() and member.name.endswith(".json"):
                if member.size > 32 * 1024**2:
                    raise ValueError("FHIR package member exceeds the size budget")
                raw = package.extractfile(member).read()
                resource = json.loads(raw)
                if isinstance(resource, dict) and resource.get("resourceType") == "StructureDefinition" and resource.get("url") == canonical and resource.get("version") == version:
                    found.append(raw)
    if len(found) != 1:
        raise ValueError("Package must contain exactly the selected profile canonical and version")
    return found[0]


def initialise_profile(config_path):
    """Explicit, idempotent setup. Normal checks only read installed references."""
    config_path = Path(config_path).resolve()
    data = tomllib.loads(config_path.read_text())
    spec = data.get("specification", {})
    package, edition = spec.get("IG_PACKAGE", ""), spec.get("IG_VERSION", "")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9.-]*", package) or not re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?", edition):
        raise ValueError("profile-init needs pinned IG_PACKAGE and IG_VERSION in pipeline.toml")
    if not spec.get("STRUCTURE_DEFINITION") or not spec.get("IG_CANONICAL"):
        raise ValueError("Set STRUCTURE_DEFINITION (local destination) and IG_CANONICAL in pipeline.toml")
    paths = [spec["STRUCTURE_DEFINITION"], *spec.get("VALUE_SETS", [])]
    urls = [spec.get("PACKAGE_URL", f"https://packages2.fhir.org/packages/{package}/{edition}"), *spec.get("BINDING_URLS", [])]
    versions = spec.get("BINDING_VERSIONS", [])
    if not all(isinstance(value, str) and value for value in paths + urls):
        raise ValueError("Profile paths and download URLs must be nonempty strings")
    if len(paths) != len(urls) or len(versions) != len(paths) - 1 or any(not isinstance(v, str) or not v for v in versions):
        raise ValueError("Each VALUE_SETS path needs a BINDING_URLS URL and BINDING_VERSIONS edition")
    targets = [Path(value) if Path(value).is_absolute() else config_path.parent / value for value in paths]
    if len(set(targets)) != len(targets) or any(p == config_path or p.suffix != ".json" for p in targets):
        raise ValueError("Select distinct local JSON destinations for the profile and bindings")
    if any(any(parent.is_symlink() for parent in [p, *p.parents]) for p in targets):
        raise ValueError("Profile destinations must not pass through symlinks")
    receipt = targets[0].with_suffix(".installation.json")
    identity = {"package": package, "edition": edition, "canonical": spec["IG_CANONICAL"],
                "profile_version": spec.get("PROFILE_VERSION", edition), "urls": urls, "binding_versions": versions}
    if receipt.is_symlink():
        raise ValueError("Profile installation receipt must be a regular file")
    if receipt.exists():
        installed = json.loads(receipt.read_text())
        if not isinstance(installed, dict) or not isinstance(installed.get("files"), list) or len(installed["files"]) != len(targets) or any(not isinstance(row, dict) or not re.fullmatch(r"[0-9a-f]{64}", str(row.get("sha256", ""))) for row in installed["files"]):
            raise ValueError("Profile installation receipt is malformed")
        if installed.get("selection") != identity:
            raise ValueError("Profile selection changed; choose a new versioned STRUCTURE_DEFINITION path")
        for path, entry in zip(targets, installed["files"], strict=True):
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
                raise ValueError("Installed profile reference changed; restore the original or select a fresh destination")
        return {**installed, "state": "already_installed",
                "files": [{**entry, "path": str(path)} for path, entry in zip(targets, installed["files"], strict=True)]}
    archive = download(urls[0], 64 * 1024**2)
    contents = [package_profile(archive, spec["IG_CANONICAL"], identity["profile_version"])]
    contents += [download(url, 16 * 1024**2) for url in urls[1:]]
    parse_structure_definition(contents[0])
    profile_resource = json.loads(contents[0])
    bindings = {element.get("binding", {}).get("valueSet", "").split("|")[0]
                for element in profile_resource.get("snapshot", {}).get("element", [])}
    for raw, edition in zip(contents[1:], versions, strict=True):
        resource = json.loads(raw)
        if resource.get("resourceType") != "ValueSet" or resource.get("version") != edition or resource.get("url") not in bindings:
            raise ValueError("Downloaded ValueSet must match a selected profile binding and its configured edition")
    # Verify all destinations before writing any one of them.
    for path, raw in zip(targets, contents, strict=True):
        if path.exists() and path.read_bytes() != raw:
            raise ValueError("Profile destination already contains different content; choose a fresh versioned path")
    for path, raw in zip(targets, contents, strict=True):
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            with path.open("xb") as stream:
                stream.write(raw)
            path.chmod(0o600)
    result = {"state": "installed", "selection": identity,
              "installed_at": datetime.now(timezone.utc).isoformat(),
              "package_sha256": hashlib.sha256(archive).hexdigest(),
              "files": [{"path": str(path), "source": url, "sha256": hashlib.sha256(raw).hexdigest()}
                        for path, url, raw in zip(targets, urls, contents, strict=True)]}
    receipt.write_text(json.dumps(result, indent=2) + "\n")
    receipt.chmod(0o600)
    LOGGER.info("Profile and %s selected bindings installed; full IG dependencies are prepared by validator-init", len(versions))
    return result
