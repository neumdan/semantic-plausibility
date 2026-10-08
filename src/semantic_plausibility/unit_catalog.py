"""Versioned unit catalogues and bounded spelling candidates; no inferred factors."""

from __future__ import annotations

import csv
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import re
import tempfile
from typing import Any
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET
import zipfile

from . import research_prompts as prompts

UCUM = "http://unitsofmeasure.org"
ESSENCE_URL = "https://raw.githubusercontent.com/ucum-org/ucum/main/ucum-essence.xml"
COMMON_URL = "https://raw.githubusercontent.com/ucum-org/ucum/main/common-units/TableOfExampleUcumCodesForElectronicMessaging.xlsx"
LICENSE_URL = "https://ucum.org/license"
LIMIT = 8 * 1024 * 1024


def _download(url: str) -> bytes:
    if not url.startswith("https://"):
        raise ValueError("Catalogue downloads require HTTPS")
    with urlopen(Request(url, headers={"User-Agent": "semantic-plausibility/unit-catalog"}), timeout=25) as r:
        body = r.read(LIMIT + 1)
    if len(body) > LIMIT:
        raise ValueError("Unit catalogue exceeds the download budget")
    return body


def _example_codes(body: bytes) -> list[str]:
    """Read the code column of the official example workbook using only stdlib."""
    ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        if sum(i.file_size for i in z.infolist()) > 32 * 1024 * 1024:
            raise ValueError("Unit example workbook is too large")
        strings = []
        if "xl/sharedStrings.xml" in z.namelist():
            strings = ["".join(e.itertext()) for e in ET.fromstring(z.read("xl/sharedStrings.xml"))]
        sheet = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
        column, result = None, []
        for row in sheet.findall(".//s:row", ns):
            cells = {}
            for cell in row.findall("s:c", ns):
                ref = re.sub(r"\d+", "", cell.get("r", ""))
                value = cell.findtext("s:v", "", ns)
                if cell.get("t") == "s":
                    value = strings[int(value)]
                elif cell.get("t") == "inlineStr":
                    value = "".join(cell.find("s:is", ns).itertext())
                cells[ref] = value.strip()
            if column is None:
                column = next((k for k, v in cells.items() if "ucum" in v.lower() and "code" in v.lower()), None)
            elif cells.get(column):
                result.append(cells[column])
    if column is None:
        raise ValueError("Official unit example workbook has no UCUM code column")
    return sorted(set(result))


def ucum_document(essence: bytes, common: bytes | None = None, *, source: str = ESSENCE_URL) -> dict:
    if b"<!DOCTYPE" in essence.upper() or b"<!ENTITY" in essence.upper():
        raise ValueError("Unit catalogue XML must not contain entity declarations")
    root = ET.fromstring(essence)
    if root.tag != "{http://unitsofmeasure.org/ucum-essence}root" or not root.get("version"):
        raise ValueError("Expected versioned UCUM essence XML")
    ns = {"u": "http://unitsofmeasure.org/ucum-essence"}
    units, prefixes = [], []
    for element in root:
        kind = element.tag.rsplit("}", 1)[-1]
        if kind not in {"unit", "base-unit", "prefix"}:
            continue
        row = {"code": element.get("Code"), "display": element.findtext("u:name", "", ns),
               "kind": kind, "metric": kind == "base-unit" or element.get("isMetric") == "yes"}
        if not row["code"]:
            raise ValueError("UCUM definition is missing its code")
        (prefixes if kind == "prefix" else units).append(row)
    if not units or not prefixes:
        raise ValueError("UCUM definitions and prefixes are required")
    return {"system": UCUM, "version": root.get("version"), "source": source,
            "coverage": "all_definitions_and_prefixes_in_source", "expressions_complete": False,
            "units": units, "prefixes": prefixes,
            "common_units": _example_codes(common) if common else [], "license": LICENSE_URL,
            "source_sha256": hashlib.sha256(essence).hexdigest(),
            "common_source": COMMON_URL if common else None,
            "common_sha256": hashlib.sha256(common).hexdigest() if common else None}


def codesystem_document(resource: dict, *, source: str) -> dict:
    if resource.get("resourceType") != "CodeSystem" or resource.get("content") != "complete":
        raise ValueError("A finite unit CodeSystem must declare content=complete")
    if not resource.get("url") or not resource.get("version"):
        raise ValueError("Unit CodeSystem needs url and version")
    if resource["url"] == UCUM:
        raise ValueError("Use UCUM essence definitions; UCUM expressions cannot be a complete finite CodeSystem")
    rows = []

    def walk(concepts):
        for concept in concepts:
            if not isinstance(concept.get("code"), str) or not concept["code"]:
                raise ValueError("CodeSystem concept has no code")
            rows.append({"code": concept["code"], "display": concept.get("display", ""), "kind": "concept"})
            walk(concept.get("concept", []))

    walk(resource.get("concept", []))
    if not rows or len({r["code"] for r in rows}) != len(rows):
        raise ValueError("Unit CodeSystem is empty or contains duplicate codes")
    return {"system": resource["url"], "version": resource["version"], "source": source,
            "coverage": "complete_finite_codesystem", "expressions_complete": True,
            "units": rows, "prefixes": [], "common_units": []}


def sync_units(destination: Path, *, source_file: Path | None = None, source_url: str = "") -> dict:
    """Download all UCUM definitions/examples, or import a complete finite CodeSystem."""
    if source_file and source_url:
        raise ValueError("Choose a local unit CodeSystem or an HTTPS source, not both")
    if source_url:
        body = _download(source_url)
        document = codesystem_document(json.loads(body), source=source_url)
        document["source_sha256"] = hashlib.sha256(body).hexdigest()
    elif source_file:
        body = source_file.read_bytes()
        if source_file.suffix.lower() == ".xml":
            document = ucum_document(body, source=str(source_file.name))
        else:
            document = codesystem_document(json.loads(body), source=str(source_file.name))
            document["source_sha256"] = hashlib.sha256(body).hexdigest()
    else:
        document = ucum_document(_download(ESSENCE_URL), _download(COMMON_URL))
    data = {"format_version": 1, "downloaded_at": datetime.now(timezone.utc).isoformat(), "systems": [document]}
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=destination.parent, encoding="utf-8", delete=False) as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        temporary = Path(handle.name)
    temporary.replace(destination)
    # A readable inventory accompanies the machine-readable definitions.
    with destination.with_suffix(".csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["system", "version", "code", "display", "kind"])
        writer.writeheader()
        for row in document["units"] + document["prefixes"]:
            writer.writerow({"system": document["system"], "version": document["version"],
                             **{k: row.get(k, "") for k in ("code", "display", "kind")}})
    return {"system": document["system"], "version": document["version"],
            "definitions": len(document["units"]), "prefixes": len(document["prefixes"]),
            "common_expressions": len(document["common_units"]), "expressions_complete": document["expressions_complete"],
            "catalogue": str(destination), "source_sha256": document["source_sha256"]}


def _distance(a: str, b: str) -> int:
    previous = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        current = [i]
        for j, y in enumerate(b, 1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (x != y)))
        previous = current
    return previous[-1]


class UnitSpelling:
    def __init__(self, path: Path, ucum: Any, llm: Any = None, terminology: Any = None):
        data = json.loads(path.read_text())
        if data.get("format_version") != 1 or not isinstance(data.get("systems"), list):
            raise ValueError("Unsupported unit catalogue")
        self.systems = {}
        for entry in data["systems"]:
            if not isinstance(entry, dict):
                raise ValueError("Unit catalogue entries must be objects")
            system = entry.get("system")
            if not isinstance(system, str) or not system or system in self.systems or not entry.get("version") or not entry.get("units"):
                raise ValueError("Unit catalogues need unique systems, versions and nonempty definitions")
            if entry.get("coverage") not in {"all_definitions_and_prefixes_in_source", "complete_finite_codesystem"}:
                raise ValueError("Unit catalogue coverage is not specified")
            if not entry.get("source") or not isinstance(entry.get("expressions_complete"), bool):
                raise ValueError("Unit catalogue source and expression coverage are required")
            for name in ("units", "prefixes"):
                if not isinstance(entry.get(name), list) or any(
                    not isinstance(row, dict) or not isinstance(row.get("code"), str) or not row["code"]
                    for row in entry[name]
                ):
                    raise ValueError("Unit catalogue definitions must have nonempty string codes")
            if not isinstance(entry.get("common_units", []), list) or any(
                not isinstance(code, str) for code in entry.get("common_units", [])
            ):
                raise ValueError("Common unit expressions must be strings")
            if system == UCUM and entry["expressions_complete"]:
                raise ValueError("A finite UCUM expression list must not claim completeness")
            self.systems[system] = entry
        self.ucum, self.llm, self.terminology = ucum, llm, terminology
        self.cache: dict[tuple, dict] = {}

    def check(self, system: str, source: str, examples: list[str] | None = None, *, context_conflict: bool = False) -> dict:
        key = (system, source, tuple(examples or []), context_conflict)
        if key not in self.cache:
            self.cache[key] = self._check(system, source, examples or [], context_conflict)
        return self.cache[key]

    def _check(self, system: str, source: str, examples: list[str], context_conflict: bool) -> dict:
        entry = self.systems.get(system)
        result = {"state": "not_configured", "source_unit": source, "system": system,
                  "selected_unit": None, "candidates": [], "factor": None,
                  "requires_expert_review": True, "errors": [], "model_used": False,
                  "context_conflict": context_conflict, "source_code_valid": None}
        if not entry:
            return result
        result.update({"catalogue_source": entry["source"], "catalogue_version": entry["version"],
                       "catalogue_coverage": entry["coverage"], "expressions_complete": entry["expressions_complete"]})
        if not isinstance(source, str) or not source or len(source) > 80:
            result["state"] = "not_comparable"
            return result
        if system == UCUM and re.fullmatch(r"10(?:\^|\*{1,2})[+\-]?\d+", source):
            result["state"] = "ambiguous_not_spelling"
            return result
        literal = {row["code"] for row in entry["units"]}
        try:
            valid = self.ucum.is_valid(source) if system == UCUM else source in literal
        except Exception:
            valid = None
        result["source_code_valid"] = valid
        if valid and not context_conflict:
            result.update(state="valid_spelling", requires_expert_review=False)
            return result
        # A bare multiplier does not identify a count denominator.
        if re.fullmatch(r"[0-9\s.^*+\-]+", source):
            result["state"] = "ambiguous_not_spelling"
            return result
        pool = set(literal)
        if system == UCUM:
            pool.update(entry.get("common_units", []))
            metric = {row["code"] for row in entry["units"] if row.get("metric")}
            pool.update(prefix["code"] + code for prefix in entry["prefixes"] for code in metric)
            pool.update(examples)
        limit = 1 if len(source) <= 3 else 2
        ranked = sorted(((_distance(source, unit), unit not in examples, unit)
                         for unit in pool if abs(len(unit) - len(source)) <= limit), key=lambda x: x)
        for distance, _, unit in ranked:
            if distance > limit or len(result["candidates"]) >= 8:
                break
            if unit == source:
                continue
            try:
                candidate_valid = self.ucum.is_valid(unit) if system == UCUM else unit in literal
            except Exception:
                candidate_valid = None
            if candidate_valid is True:
                result["candidates"].append({"unit": unit, "edit_distance": distance,
                                             "validation": "valid", "source": entry["source"]})
        result["state"] = "catalogue_candidates" if result["candidates"] else "no_close_candidate"
        if result["candidates"] and self.llm:
            try:
                answer = self.llm.json(prompts.UNIT_SPELLING, {
                    "source_unit": source, "system": system, "catalogue_version": entry["version"],
                    "candidates": result["candidates"], "documented_code_units": examples,
                    "source_code_valid": valid, "context_conflict": context_conflict,
                })
                selected = answer["selected_unit"]
                if selected is not None and selected not in {r["unit"] for r in result["candidates"]}:
                    raise ValueError("Model selected a unit outside the validated shortlist")
                result.update(state="llm_spelling_candidate" if selected else "llm_abstained",
                              selected_unit=selected, reason=answer["reason"], model_used=True)
            except Exception as exc:
                result["errors"].append(f"Spelling selection unavailable: {type(exc).__name__}")
        return result


def spelling_columns(result: dict | None) -> dict:
    if result is None:
        return {"schreibweise_pruefung": "nicht aktiviert", "schreibweise_kandidaten": "", "schreibweise_grundlage": ""}
    labels = {"not_configured": "kein Katalog für dieses System", "not_comparable": "nicht vergleichbar",
              "valid_spelling": "Einheiten-Code gültig", "ambiguous_not_spelling": "Bedeutung ungeklärt; kein Schreibfehler",
              "catalogue_candidates": "Katalogkandidaten; Auswahl offen", "no_close_candidate": "kein naher Katalogkandidat",
              "llm_spelling_candidate": "LLM-Kandidat aus geprüftem Katalog", "llm_abstained": "LLM enthält sich"}
    return {"schreibweise_pruefung": labels[result["state"]],
            "schreibweise_kandidaten": "; ".join(row["unit"] for row in result["candidates"]),
            "schreibweise_grundlage": "; ".join(str(result.get(k, "")) for k in ("catalogue_source", "catalogue_version"))}


class FiniteUnitCodeSource:
    """An imported content=complete CodeSystem can also supply exact code checks."""
    def __init__(self, document: dict):
        self.document = document
        self.codes = {row["code"]: row for row in document["units"]}

    def check(self, system: str, code: str, version: str = "") -> dict:
        matching = not version or version == self.document["version"]
        state = "valid" if matching and code in self.codes else "invalid" if matching else "unknown"
        return {"state": state, "source": self.document["source"],
                "metadata": {"version": self.document["version"], **self.codes.get(code, {})},
                "errors": [] if matching else ["Catalogue version does not match requested version"]}
