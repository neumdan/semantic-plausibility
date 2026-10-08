# Adapted for the standalone semantic-plausibility project (2026-10-02).
"""Ordered, system-scoped terminology sources. Web/LLM evidence is never validation."""

from __future__ import annotations

import csv
import json
import os
import re
import sqlite3
from pathlib import Path
from threading import Lock
from typing import Any
from urllib.parse import quote, urlparse

from .model_config import allowed_endpoint
from .observation_services import _HttpSession, _PlainText, _explicit_token
from .terminology_validation import FhirTerminologyClient
from . import research_prompts as prompts


class CsvCodeSource:
    """Exact versioned local list. A partial list cannot disprove a code."""

    def __init__(self, path: Path, *, complete: bool = False):
        self.path, self.complete = path, complete
        self.codes: dict[tuple[str, str, str], dict[str, str]] = {}
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            if not {"system", "code"}.issubset(reader.fieldnames or []):
                raise ValueError("Terminology CSV needs system and code columns")
            for row in reader:
                system, code = (row.get("system") or "").strip(), (row.get("code") or "").strip()
                if system and code:
                    self.codes[system, code, (row.get("version") or "").strip()] = row

    def check(self, system: str, code: str, version: str = "") -> dict[str, Any]:
        row = self.codes.get((system, code, version))
        ambiguous_version = False
        if row is None and not version:
            matches = [value for (s, c, _), value in self.codes.items() if (s, c) == (system, code)]
            row = matches[0] if len(matches) == 1 else None
            ambiguous_version = len(matches) > 1
        state = (
            "valid"
            if row is not None
            else "invalid"
            if self.complete and not ambiguous_version and any(s == system and (not version or v == version) for s, _, v in self.codes)
            else "unknown"
        )
        return {
            "state": state,
            "source": str(self.path),
            "metadata": {"display": row.get("display", ""), "version": row.get("version", "")}
            if row
            else None,
        }

    def labels(self, system, code, version=""):
        metadata = self.check(system, code, version).get("metadata")
        return concept_labels(metadata, source=str(self.path), version=metadata.get("version", version)) if metadata else []


class SqliteCodeSource:
    """Read-only code table with (system, code, version, display)."""

    def __init__(self, path: Path, *, complete: bool = False):
        self.path, self.complete = path, complete
        self.db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
        self._lock = Lock()
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(codes)")}
        if not {"system", "code", "version", "display"}.issubset(columns):
            raise ValueError("SQLite terminology source needs codes(system,code,version,display)")

    def close(self) -> None:
        with self._lock:
            self.db.close()

    def check(self, system: str, code: str, version: str = "") -> dict[str, Any]:
        ambiguous_version = False
        with self._lock:
            if version:
                row = self.db.execute(
                    "SELECT display,version FROM codes WHERE system=? AND code=? AND version=? LIMIT 1",
                    (system, code, version),
                ).fetchone()
            else:
                rows = self.db.execute(
                    "SELECT display,version FROM codes WHERE system=? AND code=? LIMIT 2", (system, code)
                ).fetchall()
                row = rows[0] if len(rows) == 1 else None
                ambiguous_version = len(rows) > 1
            covered = self.db.execute("SELECT 1 FROM codes WHERE system=?" + (" AND version=?" if version else "") + " LIMIT 1",
                                      (system, version) if version else (system,)).fetchone()
        return {
            "state": "valid" if row else "invalid" if self.complete and covered and not ambiguous_version else "unknown",
            "source": str(self.path),
            "metadata": {"display": row[0], "version": row[1]} if row else None,
        }

    def labels(self, system, code, version=""):
        metadata = self.check(system, code, version).get("metadata")
        return concept_labels(metadata, source=str(self.path), version=metadata.get("version", version)) if metadata else []


class FhirCodeSource:
    def __init__(self, base_url: str, token: str = "", client_cert: str = "", client_key: str = ""):
        self.client = FhirTerminologyClient(
            base_url, bearer_token=token, client_cert=client_cert, client_key=client_key
        )

    def check(self, system: str, code: str, version: str = "") -> dict[str, Any]:
        valid = self.client.validate_code(system, code, version)
        metadata = None
        if valid:
            try:
                metadata = self.client.lookup_code(system, code, version)
            except Exception:
                pass  # optional display metadata cannot reverse code validation
        return {
            "state": "valid" if valid is True else "invalid" if valid is False else "unknown",
            "source": self.client.base_url,
            "metadata": metadata,
            "validation": self.client.validation_details.get((system, code, version)),
        }


    def check_binding(
        self, value_set: str, system: str, code: str, version: str = "", value_set_version: str = ""
    ) -> dict[str, Any]:
        valid = self.client.validate_value_set(value_set, system, code, version, value_set_version)
        return {
            "state": "member" if valid is True else "not_member" if valid is False else "unknown",
            "source": self.client.base_url,
            "validation": self.client.value_set_details.get(
                (value_set, value_set_version, system, code, version)
            ),
        }

    def labels(self, system, code, version=""):
        metadata = self.check(system, code, version).get("metadata")
        return concept_labels(metadata, source=self.client.base_url, version=metadata.get("version", version)) if metadata else []


class FhirArtifactSource:
    """Installed R4 resources, including package/ layouts and label supplements."""
    def __init__(self, path: Path):
        self.path = path
        self.systems, self.value_sets, self.supplements = {}, {}, []
        self._name_index = None
        receipt = path / "installation.json" if path.is_dir() else None
        self.provenance = {}
        if receipt and receipt.is_file():
            if receipt.is_symlink():
                raise ValueError("Package receipt must be a regular file")
            installation = json.loads(receipt.read_text())
            self.provenance = {key: installation.get(key) for key in
                               ("name", "version", "registry", "archive_url", "publisher_sha1", "archive_sha256")}
        files = artifact_files(path)
        for source in files:
            if source.is_symlink():
                raise ValueError("Terminology artifacts must be regular files")
            data = json.loads(source.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("Terminology artifact must contain a JSON object")
            kind = data.get("resourceType")
            if kind not in {"CodeSystem", "ValueSet"}:
                continue
            if not data.get("url"):
                raise ValueError("Terminology artifacts need their canonical URL")
            key = (data["url"], data.get("version", ""))
            if kind == "CodeSystem":
                def concepts(items):
                    for item in items:
                        if item.get("code"):
                            yield item["code"], item
                        yield from concepts(item.get("concept", []))
                rows = dict(concepts(data.get("concept", [])))
                if data.get("content") == "supplement" or data.get("supplements"):
                    base, _, version = str(data.get("supplements", "")).partition("|")
                    if not base:
                        raise ValueError("CodeSystem supplement needs its base canonical system")
                    self.supplements.append((base, version, key, rows, str(source)))
                else:
                    if key in self.systems:
                        raise ValueError("Duplicate CodeSystem edition in terminology artifacts")
                    self.systems[key] = (rows, data.get("content") == "complete", data.get("version", ""), str(source))
            elif "expansion" in data:
                expansion = data["expansion"]
                def entries(items, inherited_system=""):
                    for item in items:
                        system = item.get("system", inherited_system)
                        if item.get("code") and system:
                            yield (system, item["code"], item.get("version", ""))
                        yield from entries(item.get("contains", []), system)
                values = set(entries(expansion.get("contains", [])))
                params = {item.get("name"): item for item in expansion.get("parameter", [])}
                complete = (expansion.get("offset", 0) == 0 and expansion.get("total") == len(values)
                            and "filter" not in params)
                self.value_sets[key] = (values, complete)

    def _select(self, entries, canonical, version):
        if version:
            return entries.get((canonical, version))
        found = [value for (url, _), value in entries.items() if url == canonical]
        return found[0] if len(found) == 1 else None

    def check(self, system, code, version=""):
        selected = self._select(self.systems, system, version)
        row = selected[0].get(code) if selected else None
        return {"state": "valid" if row else "invalid" if selected and selected[1] else "unknown",
                "source": selected[3] if selected else str(self.path),
                "metadata": {**row, "version": selected[2], "package": self.provenance} if row else None}

    def labels(self, system, code, version=""):
        """Translations enrich a base code; they never independently validate it."""
        results = []
        selected = self._select(self.systems, system, version)
        if selected and code in selected[0]:
            results += concept_labels(selected[0][code], source=selected[3], version=selected[2])
        for base, base_version, (canonical, edition), rows, source in self.supplements:
            if base != system or code not in rows or version and base_version and version != base_version:
                continue
            results += [dict(label, supplement=canonical, supplement_version=edition,
                             base_version_declared=bool(base_version))
                        for label in concept_labels(rows[code], source=source, version=base_version)]
        return [dict(label, package=self.provenance) for label in results]

    def search_names(self, name):
        """Reverse exact display/translation search; ambiguous matches remain candidates."""
        from .name_checks import normalized_name

        if not isinstance(name, str) or not name.strip() or len(name) > 300:
            raise ValueError("Name search requires 1..300 characters of terminology text")
        if self._name_index is None:
            index = {}
            collections = [(system, version, rows, source, {})
                           for (system, version), (rows, _, _, source) in self.systems.items()]
            collections += [(base, version, rows, source, {"supplement": canonical, "supplement_version": edition})
                            for base, version, (canonical, edition), rows, source in self.supplements]
            for system, version, rows, source, qualifier in collections:
                for code, concept in rows.items():
                    for label in concept_labels(concept, source=source, version=version):
                        candidate = {"state": "candidate", "system": system, "code": code, "version": version,
                                     "label": label["value"], "language": label["language"], "source": source,
                                     "method": "artifact_exact_name", "requires_code_and_context_validation": True,
                                     "package": self.provenance, **qualifier}
                        index.setdefault(normalized_name(label["value"]), []).append(candidate)
            self._name_index = index
        return self._name_index.get(normalized_name(name), [])[:10]

    def check_binding(self, value_set, system, code, version="", value_set_version=""):
        selected = self._select(self.value_sets, value_set, value_set_version)
        matches = [entry for entry in selected[0] if entry[:2] == (system, code)] if selected else []
        member = any(not version or entry[2] == version for entry in matches)
        # Expansion omitting a requested CodeSystem version cannot establish that edition.
        version_unknown = bool(version and any(not entry[2] for entry in matches))
        return {"state": "member" if member else "unknown" if version_unknown or not selected or not selected[1]
                else "not_member", "source": str(self.path)}


def artifact_files(path):
    """Use the package's content directory, or an explicitly supplied artifact directory."""
    path = Path(path)
    if path.is_symlink() or not path.exists():
        raise ValueError("Terminology artifacts are missing; run package-init for configured packages")
    if not path.is_dir():
        return [path]
    content = path / "package" if (path / "package").is_dir() else path
    files = sorted(content.rglob("*.json"))
    if not files or len(files) > 10000:
        raise ValueError("Terminology artifact directory is empty or exceeds file budget")
    if any(parent.is_symlink() for file in files for parent in (file, *file.parents) if parent.is_relative_to(path)):
        raise ValueError("Terminology artifacts must not traverse symbolic links")
    return files


def concept_labels(concept, *, source, version=""):
    labels = []
    if concept.get("display"):
        labels.append({"value": concept["display"], "language": "", "source": source, "version": version})
    for row in concept.get("designation", []):
        if row.get("value"):
            labels.append({"value": row["value"], "language": row.get("language", ""), "source": source, "version": version})
    return labels

class WebsiteCandidateSource:
    """Extract only quoted source text; the result is a review candidate."""

    def __init__(self, template: str, keywords: list[str], llm: Any = None, session: Any = None):
        if template.count("{code}") != 1:
            raise ValueError("Website URL must contain one {code} placeholder")
        probe = template.replace("{code}", "TEST")
        if urlparse(probe).scheme != "https" or not allowed_endpoint(probe):
            raise ValueError("Website source requires an approved HTTPS URL")
        self.template, self.keywords = template, keywords
        self.llm, self.session = llm, session or _HttpSession()
        self._cache: dict[tuple[str, str], list[dict[str, Any]]] = {}

    def candidates(self, system: str, code: str) -> list[dict[str, Any]]:
        key = (system, code)
        if key in self._cache:
            return list(self._cache[key])
        url = self.template.replace("{code}", quote(code, safe=""))
        response = self.session.get(url, timeout=25)
        response.raise_for_status()
        parser = _PlainText()
        parser.feed(response.text[:500_000])
        page = parser.text()
        if not _explicit_token(code, page):
            self._cache[key] = []
            return []
        chunks = []
        for keyword in self.keywords:
            match = re.search(re.escape(keyword), page, flags=re.I)
            if match:
                chunks.append(page[max(0, match.start() - 200) : match.start() + 1000].strip())
        chunks = chunks[:5]
        result = [
            {"state": "candidate", "source": url, "excerpt": chunk[:1200], "method": "website_keyword"}
            for chunk in chunks
        ]
        if self.llm and chunks:
            answer = self.llm.json(
                prompts.TERMINOLOGY_CLAIMS,
                {"system": system, "code": code, "source": url, "excerpts": chunks},
            )
            for row in answer.get("claims", [])[:10]:
                claim, excerpt = str(row.get("claim") or ""), str(row.get("excerpt") or "")
                if claim and excerpt and any(excerpt in chunk for chunk in chunks):
                    result.append(
                        {
                            "state": "candidate",
                            "source": url,
                            "excerpt": excerpt[:1200],
                            "claim": claim[:300],
                            "method": "llm_extraction",
                        }
                    )
        self._cache[key] = result
        return list(result)


class TerminologyRouter:
    def __init__(self, sources: list[dict[str, Any]], *, llm: Any = None):
        self.sources: list[tuple[set[str], Any, bool]] = []
        for entry in sources:
            systems = entry.get("systems")
            if not isinstance(systems, list) or not systems or not all(isinstance(s, str) for s in systems):
                raise ValueError("Each terminology source needs explicit systems")
            kind = entry.get("kind")
            source = make_source(entry, llm=llm)
            self.sources.append((set(systems), source, kind == "website"))

    def close(self) -> None:
        for _, source, _ in self.sources:
            if hasattr(source, "close"):
                source.close()

    @classmethod
    def from_file(cls, path: Path, *, llm: Any = None) -> "TerminologyRouter":
        return cls(load_sources(path), llm=llm)

    def check(self, system: str, code: str, version: str = "", *, research: bool = False) -> dict[str, Any]:
        if not system or not code:
            return {"state": "unknown", "source": None, "metadata": None, "candidates": [], "errors": []}
        candidates: list[dict[str, Any]] = []
        errors: list[str] = []
        negative = None
        unknown = None
        for systems, source, website in self.sources:
            if system not in systems:
                continue
            try:
                if website:
                    if research:
                        candidates.extend(source.candidates(system, code))
                    continue
                result = source.check(system, code, version)
            except Exception as exc:
                errors.append(f"{type(source).__name__}: {exc}")
                continue
            if result["state"] == "valid":
                return {**result, "candidates": candidates, "errors": errors}
            if result["state"] == "invalid" and negative is None:
                negative = result
            if result["state"] == "unknown" and unknown is None:
                unknown = result
        return {
            **(negative or unknown or {"state": "unknown", "source": None, "metadata": None}),
            "candidates": candidates,
            "errors": errors,
        }

    def check_binding(
        self, value_set: str, system: str, code: str, version: str = "", value_set_version: str = ""
    ) -> dict[str, Any]:
        errors = []
        negative = unknown = None
        for systems, source, website in self.sources:
            if website or system not in systems or not hasattr(source, "check_binding"):
                continue
            try:
                result = source.check_binding(value_set, system, code, version, value_set_version)
                if result["state"] == "member":
                    return {**result, "errors": errors}
                if result["state"] == "not_member" and negative is None:
                    negative = result
                if result["state"] == "unknown" and unknown is None:
                    unknown = result
            except Exception as exc:
                errors.append(str(exc))
        return {**(negative or unknown or {"state": "unknown", "source": None}), "errors": errors}

    def labels(self, system, code, version=""):
        labels, errors = [], []
        for systems, source, _ in self.sources:
            if system in systems and hasattr(source, "labels"):
                try:
                    labels.extend(source.labels(system, code, version))
                except Exception as exc:
                    errors.append(type(exc).__name__)
        return labels, errors

    def search_names(self, system, name):
        candidates, errors = [], []
        for systems, source, _ in self.sources:
            if system in systems and hasattr(source, "search_names"):
                try:
                    candidates.extend(source.search_names(name))
                except Exception as exc:
                    errors.append(type(exc).__name__)
        return candidates, errors


def make_source(entry, *, llm=None):
    kind = entry.get("kind")
    if kind == "fhir":
        return FhirCodeSource(entry["base_url"], os.getenv(entry.get("token_env", ""), ""),
                              entry.get("client_cert", ""), entry.get("client_key", ""))
    if kind == "csv":
        return CsvCodeSource(Path(entry["path"]), complete=bool(entry.get("complete", False)))
    if kind == "sqlite":
        return SqliteCodeSource(Path(entry["path"]), complete=bool(entry.get("complete", False)))
    if kind == "fhir_artifacts":
        return FhirArtifactSource(Path(entry["path"]))
    if kind == "website":
        return WebsiteCandidateSource(entry["url_template"], entry.get("keywords", []), llm=llm)
    from .terminology_apis import OlsNameSource, WhoIcdSource
    if kind == "ols":
        return OlsNameSource(entry["base_url"], ontology=entry["ontology"], limit=entry.get("limit", 5))
    if kind == "who_icd":
        return WhoIcdSource(entry["base_url"], release=entry["release"], language=entry.get("language", "en"),
            client_id=os.getenv(entry.get("client_id_env", ""), ""), client_secret=os.getenv(entry.get("client_secret_env", ""), ""),
            token_endpoint=entry["token_endpoint"])
    raise ValueError(f"Unknown terminology source: {kind}")


def load_sources(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("sources")
    if not isinstance(data, list) or any(not isinstance(row, dict) for row in data):
        raise ValueError("Terminology source config needs a JSON source array")
    data = [dict(row) for row in data]
    for row in data:
        if row.get("kind") in {"csv", "sqlite", "fhir_artifacts"} and row.get("path"):
            value = Path(row["path"])
            if not value.is_absolute():
                row["path"] = str(path.resolve().parent / value)
    return data


def router_from_environment(*, llm: Any = None, path: Path | None = None) -> TerminologyRouter | None:
    source_file = path or (
        Path(os.environ["SQC_TERMINOLOGY_SOURCES"]) if os.getenv("SQC_TERMINOLOGY_SOURCES") else None
    )
    entries = load_sources(source_file) if source_file else []
    base = os.getenv("TX_FHIR_BASE_URL", "")
    if base:
        # A dedicated source file can narrow or extend the systems. The
        # environment-only default routes the bundled registered systems.
        with (Path(__file__).parent / "terminologies/generated/systems.csv").open(encoding="utf-8") as handle:
            systems = [row["system"] for row in csv.DictReader(handle)]
        systems += [system for row in entries for system in row.get("systems") or []]
        entries.append(
            {
                "kind": "fhir",
                "base_url": base,
                "systems": list(dict.fromkeys(systems)),
                "token_env": "TX_FHIR_BEARER_TOKEN",
            }
        )
    return TerminologyRouter(entries, llm=llm) if entries else None
