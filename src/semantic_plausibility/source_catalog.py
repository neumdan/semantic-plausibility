"""Resolve operator-selected source metadata; the catalogue contains no concepts."""

import csv
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

FIELDS = {"id", "type", "systems", "endpoint", "container_endpoint", "path", "download_url",
          "username_env", "password_env", "token_env", "description"}
ADAPTERS = {"loinc", "ucum", "fhir", "fhir_artifacts", "loinc_csv", "csv", "sqlite", "ols", "who_icd", "website"}


def read_catalog(path):
    """Read a bounded, explicit CSV registry, preserving canonical systems as URIs."""
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024 * 1024:
        raise ValueError("Source catalogue must be a regular CSV file up to 1 MiB")
    result = {}
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if set(reader.fieldnames or []) != FIELDS or len(reader.fieldnames) != len(FIELDS):
            raise ValueError("Use the columns from catalog.csv for your source catalogue")
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise ValueError("Malformed source catalogue row")
            row = {key: value.strip() for key, value in row.items()}
            alias = row["id"]
            if not re.fullmatch(r"[a-z][a-z0-9_-]*", alias) or alias in result or row["type"] not in ADAPTERS:
                raise ValueError("Catalogue IDs must be unique; use a supported adapter type")
            for key in ("endpoint", "container_endpoint", "download_url"):
                if row[key]:
                    url = urlsplit(row[key])
                    if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or url.query or url.fragment:
                        raise ValueError("Catalogue URLs need HTTP(S) without embedded credentials, queries or fragments")
            for key in ("username_env", "password_env", "token_env"):
                if row[key] and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", row[key]):
                    raise ValueError("Catalogue credentials must be environment variable names")
            result[alias] = row
    return result


def resolve_catalog(data, root):
    """TOML overrides catalogue defaults. Disabled entries never trigger lookups."""
    settings = data.get("catalog", {})
    if not isinstance(settings, dict) or set(settings) - {"path"}:
        raise ValueError("catalog accepts only path, e.g. catalog.csv")
    selected = data.get("lookups", {})
    if not isinstance(selected, dict) or any(not isinstance(row, dict) for row in selected.values()):
        raise ValueError("lookups must contain named tables")
    references = any(row.get("source") for row in selected.values())
    catalog_path = Path(settings.get("path", "catalog.csv"))
    if not catalog_path.is_absolute():
        catalog_path = root / catalog_path
    if references and not settings and not catalog_path.exists():
        catalog_path = Path(__file__).with_name("catalog.csv")
    catalog = read_catalog(catalog_path) if references or settings else {}
    if catalog:
        data["catalog"] = {"path": str(catalog_path.resolve())}
    for alias, entry in list(selected.items()):
        source = entry.get("source")
        defaults = {}
        if source:
            if source not in catalog:
                raise ValueError(f"Unknown source {source!r} for lookups.{alias}; choose an ID from catalog.csv")
            defaults = {key: value for key, value in catalog[source].items()
                        if key not in {"id", "description", "download_url"} and value}
            if "systems" in defaults:
                defaults["systems"] = defaults["systems"].split(";")
        merged = defaults | {key: value for key, value in entry.items() if key != "source"}
        # A file secret explicitly replaces an inherited environment reference.
        for key in ("username", "password", "token"):
            if merged.get(key + "_file") and key + "_env" not in entry:
                merged.pop(key + "_env", None)
        container = merged.pop("container_endpoint", None)
        if os.getenv("SQC_IN_CONTAINER") == "1" and container:
            merged["endpoint"] = container
        selected[alias] = merged
    return catalog_path if catalog else None
