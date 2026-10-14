"""Official local LOINC tables and one shared LOINC/API routing contract."""

from dataclasses import asdict, replace
import csv
import hashlib
from pathlib import Path

from .observation_quality import LoincTerm, LOINC_URI


class LoincCsvSource:
    """Read the publisher's full Loinc.csv; release is not VersionLastChanged."""

    def __init__(self, path, *, version, complete=False):
        self.path, self.version, self.complete = Path(path), version, complete
        if not isinstance(version, str) or not version.strip():
            raise ValueError("loinc_csv requires the downloaded release version, e.g. 2.83")
        if self.path.is_symlink() or not self.path.is_file() or self.path.stat().st_size > 512 * 1024**2:
            raise ValueError("LOINC CSV must be a regular local file smaller than 512 MiB")
        with self.path.open("rb") as stream:
            self.sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
        self.terms = {}
        self._names = None
        old_limit = csv.field_size_limit(2 * 1024**2)
        try:
            with self.path.open(newline="", encoding="utf-8-sig") as stream:
                reader = csv.DictReader(stream)
                required = {
                    "LOINC_NUM",
                    "LONG_COMMON_NAME",
                    "COMPONENT",
                    "PROPERTY",
                    "SYSTEM",
                    "SCALE_TYP",
                    "TIME_ASPCT",
                    "EXAMPLE_UCUM_UNITS",
                }
                if not required <= set(reader.fieldnames or []):
                    raise ValueError(
                        "Use the full official Loinc.csv, not a translation or changed-term subset"
                    )
                for row in reader:
                    code = (row.get("LOINC_NUM") or "").strip()
                    if not code or code in self.terms or len(self.terms) >= 250000 or None in row:
                        raise ValueError(
                            "LOINC CSV contains a missing/duplicate code, malformed row or too many terms"
                        )

                    def one(key):
                        return (row.get(key) or "").strip()

                    def many(key):
                        return tuple(dict.fromkeys(x.strip() for x in one(key).split(";") if x.strip()))

                    self.terms[code] = LoincTerm(
                        code=code,
                        display=one("LONG_COMMON_NAME"),
                        component=one("COMPONENT"),
                        property=one("PROPERTY"),
                        system=one("SYSTEM"),
                        time_aspect=one("TIME_ASPCT"),
                        scale=one("SCALE_TYP"),
                        method=one("METHOD_TYP"),
                        example_ucum_units=many("EXAMPLE_UCUM_UNITS"),
                        example_units=many("EXAMPLE_UNITS"),
                        synonyms=tuple(dict.fromkeys((*many("RELATEDNAMES2"), *many("SHORTNAME")))),
                        status=one("STATUS"),
                        version=version,
                        source=str(self.path),
                    )
        finally:
            csv.field_size_limit(old_limit)
        if not self.terms:
            raise ValueError("LOINC CSV contains no terms")

    def check(self, system, code, version=""):
        covered = system == LOINC_URI and (not version or version == self.version)
        term = self.terms.get(code) if covered else None
        return {
            "state": "valid" if term else "invalid" if covered and self.complete else "unknown",
            "source": str(self.path),
            "source_sha256": self.sha256,
            "metadata": {"display": term.display, "version": term.version, "loinc_term": asdict(term)}
            if term
            else None,
        }

    def labels(self, system, code, version=""):
        term = (
            self.terms.get(code) if system == LOINC_URI and (not version or version == self.version) else None
        )
        return (
            [
                {"value": label, "language": "en", "source": str(self.path), "version": self.version}
                for label in dict.fromkeys((term.display, *term.synonyms))
                if label
            ]
            if term
            else []
        )

    def search_names(self, name):
        from .name_checks import normalized_name

        if not isinstance(name, str) or not name.strip() or len(name) > 300:
            raise ValueError("Name search requires 1..300 characters of terminology text")
        query = normalized_name(name)
        if self._names is None:
            self._names = {}
            for term in self.terms.values():
                for label in (term.display, *term.synonyms):
                    codes = self._names.setdefault(normalized_name(label), [])
                    if len(codes) < 10 and term.code not in codes:
                        codes.append(term.code)
        return [
            {
                "state": "candidate",
                "system": LOINC_URI,
                "code": term.code,
                "version": self.version,
                "label": term.display,
                "source": str(self.path),
                "source_sha256": self.sha256,
                "method": "official_loinc_exact_name",
                "requires_code_and_context_validation": True,
            }
            for code in self._names.get(query, [])
            for term in [self.terms[code]]
        ]


class LoincApiSource:
    """Expose the existing guarded client through the same traceable router."""

    def __init__(self, client, alias):
        self.client, self.alias, self.role, self.remote = client, alias, "primary", True
        self.source_type = "loinc"
        self.source = client.source

    def check(self, system, code, version=""):
        before = len(self.client.errors)
        term = self.client.lookup(code, version=version)
        result = {
            "state": "valid" if term else "unknown",
            "source": term.source if term else self.client.source,
            "metadata": {"display": term.display, "version": term.version, "loinc_term": asdict(term)}
            if term
            else None,
        }
        if len(self.client.errors) > before:
            result.update(error_type=self.client.errors[-1]["error_type"], reason="lookup_unavailable")
        elif not term and self.client.failures >= 3:
            result["reason"] = "source_circuit_open"
        return result


def term_from_metadata(code, metadata, source):
    if metadata.get("loinc_term"):
        return LoincTerm(**metadata["loinc_term"])
    # A FHIR CodeSystem concept may carry the same official property names.
    props = {}
    raw = metadata.get("property", [])
    properties = list(raw) if isinstance(raw, list) else []
    raw = metadata.get("properties", [])
    if isinstance(raw, list):
        properties.extend(
            {"code": item.get("code"), "valueString": item.get("value")}
            for item in raw
            if isinstance(item, dict)
        )
    for entry in properties:
        if not isinstance(entry, dict):
            continue
        coding = entry.get("valueCoding") or {}
        value = (
            entry.get("valueString")
            or entry.get("valueCode")
            or (coding.get("display") if isinstance(coding, dict) else None)
        )
        if value and isinstance(value, str) and not value.startswith("LP"):
            props.setdefault(entry.get("code"), []).append(value)

    def one(key):
        return next(iter(props.get(key, [])), "")

    if not metadata.get("display"):
        return None
    return LoincTerm(
        code=code,
        display=metadata["display"],
        source=source,
        version=metadata.get("version", ""),
        component=one("COMPONENT"),
        property=one("PROPERTY"),
        system=one("SYSTEM"),
        time_aspect=one("TIME_ASPCT"),
        scale=one("SCALE_TYP"),
        method=one("METHOD_TYP"),
        example_ucum_units=tuple(
            x.strip() for v in props.get("EXAMPLE_UCUM_UNITS", []) for x in v.split(";") if x.strip()
        ),
        status=one("STATUS"),
        designations=tuple(metadata.get("designation", [])),
    )


def ddd_references(lookup):
    """Preserve publisher DDD text; it is a utilization metric, not a patient dose."""
    if lookup.get("state") != "valid" or lookup.get("system") not in {
        "http://fhir.de/CodeSystem/bfarm/atc",
        "http://www.whocc.no/atc",
    }:
        return []
    references = []
    for result in lookup.get("attempts", []) or [lookup]:
        if result.get("state") != "valid":
            continue
        metadata = result.get("metadata") or {}
        properties = metadata.get("property", [])
        values = (
            [
                entry.get("valueString")
                for entry in properties
                if isinstance(entry, dict) and entry.get("code") == "ddd"
            ]
            if isinstance(properties, list)
            else []
        )
        if metadata.get("ddd"):
            values.append(metadata["ddd"])
        values.extend(
            item.get("value")
            for item in metadata.get("properties", [])
            if isinstance(item, dict) and item.get("code") == "ddd"
        )
        for value in dict.fromkeys(v for v in values if isinstance(v, str) and v.strip()):
            references.append(
                {
                    "text": value,
                    "system": lookup["system"],
                    "code": lookup["code"],
                    "version": metadata.get("version", lookup.get("version", "")),
                    "source": result.get("source"),
                    "artifact": metadata.get("artifact", {}),
                    "source_sha256": result.get("source_sha256", ""),
                    "scope": "Published DDD context; not a recommended or verified patient dose",
                }
            )
    return references


class RoutedLoinc:
    def __init__(self, router):
        self.router = router
        locations = [
            str(
                getattr(
                    source,
                    "source",
                    getattr(
                        source,
                        "path",
                        getattr(getattr(source, "client", None), "base_url", "configured_source"),
                    ),
                )
            )
            for systems, source, website in router.sources
            if LOINC_URI in systems and not website and getattr(source, "source_type", "") != "ols"
        ]
        self.source = "; ".join(dict.fromkeys(locations)) or "no_enabled_loinc_source"

    def lookup(self, code, version=""):
        result = self.router.check(LOINC_URI, code, version)
        if result["state"] != "valid":
            return None
        term = term_from_metadata(code, result.get("metadata") or {}, result.get("source") or "")
        return replace(term, lookup_attempts=tuple(result.get("attempts", []))) if term else None
