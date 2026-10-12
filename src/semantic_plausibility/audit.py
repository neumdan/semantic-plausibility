"""Laboratory metadata extraction and shared semantic checks.

The export workflow groups in DuckDB before these checks. Patient resources are
reduced to semantic metadata; measured values never become model prompts.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from decimal import Decimal
import json
from pathlib import Path
import re
from typing import Any, Iterator

from .batch_quality import CsvMapping, observation_from_row, _weight
from .fhir_quality_pipeline import FhirQualityPipeline, _approvals, number
from .recommendations import transformation, value_checks, display_checks
from .observation_quality import LoincTerm
from .observation_services import services_from_environment
from .profile_config import InputProfile, load_profile
from .unit_catalog import UnitSpelling, spelling_columns, FiniteUnitCodeSource
from .snapshot_services import SnapshotLoinc, SnapshotUcum
from .terminology_router import TerminologyRouter, load_sources, router_from_environment


STATUS_LABELS = {
    "error": "Fehler",
    "suggestion": "Vorschlag",
    "unclear": "Unklar",
    "passed": "Prüfungen bestanden",
    "not_applicable": "Nicht im Prüfumfang",
}
STATE_LABELS = {
    "valid": "gültig",
    "invalid": "ungültig",
    "unknown": "unklar",
    "match": "passend",
    "mismatch": "abweichend",
    "not_provided": "nicht angegeben",
    "not_applicable": "nicht erforderlich",
    "bounds_valid": "Grenzen konsistent",
    "not_numeric": "nicht numerisch",
    "unit_unresolved": "Einheit ungeklärt",
    "unit_invalid": "Einheiten-Code ungültig",
    "invalid_bounds": "Grenzen fehlerhaft",
    "policy_invalid": "Profilregel verletzt",
    "comparator_unresolved": "Vergleichszeichen prüfen",
    "service_unavailable": "Dienst nicht erreichbar",
}
CSV_FIELDS = [
    "lauf_id",
    "tupel_id",
    "status",
    "status_text",
    "begruendung",
    "ressourcentyp",
    "messpfad",
    "ressource_id",
    "quellbezug",
    "codesystem",
    "code",
    "code_version",
    "eingangseinheit",
    "einheiten_code",
    "einheiten_system",
    "material",
    "methode",
    "haeufigkeit",
    "haeufigkeit_grundlage",
    "gelieferte_pruefzeilen",
    "werte_min",
    "werte_max",
    "numerische_werte",
    "fehlende_werte",
    "ungueltige_werte",
    "werte_pruefumfang",
    "werttyp_pruefung",
    "code_pruefung",
    "einheit_pruefung",
    "code_einheit_pruefung",
    "material_pruefung",
    "methode_pruefung",
    "referenz_pruefung",
    "referenz_unten",
    "referenz_oben",
    "referenz_einheit",
    "referenz_fachlich",
    "vorschlag",
    "vorschlag_einheit",
    "vorschlag_faktor",
    "vorschlag_grundlage",
    "vorschlag_transformation",
    "transformation_pruefung",
    "werte_referenz_pruefung",
    "werte_evidenz_pruefung",
    "referenz_evidenz_pruefung",
    "schreibweise_pruefung",
    "schreibweise_kandidaten",
    "schreibweise_grundlage",
    "evidenz_quellen",
    "experten_status",
    "experten_vollstaendig",
    "experten_richtig",
    "experten_notiz",
]
SCOPE = "laboratory_code_unit_context_reference_bounds_and_values; clinical intervals require actual evidence and expert review"


def encoded(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def canonical(value: Any, *, preserve_lists: bool = False) -> Any:
    """Order-independent metadata; normalize numeric FHIR boundary values."""
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            parsed = number(item) if key == "value" else None
            if parsed is not None:
                item = format(parsed, "f")
                item = item.rstrip("0").rstrip(".") if "." in item else item
                item = "0" if parsed == 0 else item
            result[key] = canonical(item, preserve_lists=preserve_lists)
        return result
    if isinstance(value, list):
        items = [canonical(item, preserve_lists=preserve_lists) for item in value]
        return items if preserve_lists else sorted(items, key=encoded)
    return value


@dataclass
class SourceRecord:
    resource: dict[str, Any]
    context: dict[str, str]
    source_ref: str
    occurrences: int = 1
    value_min: Any = None
    value_max: Any = None
    aggregate_values: bool = False
    input_granularity: str = "auto"
    occurrences_known: bool = True


def file_records(
    path: Path, columns: Path | None = None, *, delimiter: str = ",", context: dict[str, str] | None = None
) -> Iterator[SourceRecord]:
    """Read a mapped UTF-8 CSV; paths need not have a particular suffix."""
    if columns is None:
        raise ValueError("CSV needs --columns with an explicit mapping")
    mapping = CsvMapping.read(columns)
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=delimiter)
        missing = set(mapping.fields.values()) - set(reader.fieldnames or [])
        if missing:
            raise ValueError("CSV columns missing: " + ", ".join(sorted(missing)))
        for line, row in enumerate(reader, 2):
            resource, row_context = observation_from_row(
                row, mapping, with_values=True, allow_incomplete=True
            )
            identifier = mapping.value(row, "id")
            if identifier:
                resource["id"] = identifier
            weight = _weight(row, mapping)
            if weight < 1:
                raise ValueError(f"CSV row {line}: count must be positive")
            aggregate = bool(mapping.fields.get("value_min") or mapping.fields.get("value_max"))
            yield SourceRecord(
                resource,
                {**row_context, **(context or {})},
                f"row:{line}",
                weight,
                mapping.value(row, "value_min") or None,
                mapping.value(row, "value_max") or None,
                aggregate,
                mapping.granularity,
                mapping.granularity == "individual" or bool(mapping.value(row, "count")),
            )
    return


def ndjson_files(path: Path) -> list[Path]:
    """An explicit export directory, not an arbitrary recursive data discovery."""
    files = sorted([*path.glob("*.ndjson"), *path.glob("*.jsonl")])
    if not files:
        raise ValueError("Export directory contains no NDJSON/JSONL files")
    if any(not source.is_file() or source.is_symlink() for source in files):
        raise ValueError("Export directory needs regular NDJSON/JSONL files")
    return files


def payload_records(
    payload: Any, *, context: dict[str, str] | None = None, source_ref: str = ""
) -> Iterator[SourceRecord]:
    if isinstance(payload, list):
        for i, resource in enumerate(payload):
            yield from payload_records(resource, context=context, source_ref=f"item:{i + 1}")
        return
    if not isinstance(payload, dict):
        raise ValueError("Expected FHIR resource, Bundle, array or NDJSON")
    if payload.get("resourceType") == "Bundle":
        for i, entry in enumerate(payload.get("entry") or []):
            resource = entry.get("resource") or {}
            if resource.get("resourceType") == "Observation":
                yield from payload_records(resource, context=context, source_ref=f"entry:{i + 1}")
        return
    kind = payload.get("resourceType")
    if kind != "Observation":
        raise ValueError(f"Resource type outside the audit scope: {kind}")
    local_context = dict(context or {})
    # Native specimen/method codings are retained below, but a LOINC axis match
    # requires a resolved local mapping; SNOMED codes are not LOINC axis tokens.
    yield SourceRecord(payload, local_context, source_ref or f"{kind}/{payload.get('id', '')}")


def semantic_items(record: SourceRecord, *, preserve_order: bool = False) -> Iterator[tuple[dict, Any, str]]:
    resource = record.resource
    kind = resource["resourceType"]
    if kind != "Observation":
        raise ValueError("Only laboratory Observations are supported")
    items = [("Observation", resource)] + [
        (f"Observation.component[{i}]", item) for i, item in enumerate(resource.get("component") or [])
    ]
    for path, item in items:
        if (
            path == "Observation"
            and "valueQuantity" not in item
            and not item.get("referenceRange")
            and resource.get("component")
        ):
            continue
        quantity = item.get("valueQuantity") or {}
        if not isinstance(quantity, dict):
            raise ValueError(f"{path}.valueQuantity must be an object")
        payload = {
            "resource_type": kind,
            "path": re.sub(r"\[\d+\]", "[*]", path),
            "code": item.get("code") or {},
            "quantity": {key: value for key, value in quantity.items() if key != "value"},
            "reference_ranges": item.get("referenceRange") or [],
            "context": record.context,
            "specimen_reference_unresolved": bool(
                (resource.get("specimen") or {}).get("reference") and not record.context.get("loinc_system")
            ),
            "method": resource.get("method") or {},
            "status": resource.get("status") or "",
            "input_issues": resource.get("_plausibility_input_issues") or [],
            "modifier_extensions": [
                *(resource.get("modifierExtension") or []),
                *(item.get("modifierExtension") or []),
            ]
            if path != "Observation"
            else resource.get("modifierExtension") or [],
            "numeric_applicable": "valueQuantity" in item
            or any(
                isinstance(rng, dict) and bool(rng.get("low") or rng.get("high"))
                for rng in item.get("referenceRange") or []
            ),
            "source_value_types": sorted(
                key
                for key in item
                if key
                in {
                    "valueQuantity",
                    "valueCodeableConcept",
                    "valueString",
                    "valueBoolean",
                    "valueInteger",
                    "valueRange",
                    "valueRatio",
                    "valueSampledData",
                    "valueTime",
                    "valueDateTime",
                    "valuePeriod",
                }
            ),
            "has_data_absent_reason": bool(item.get("dataAbsentReason")),
        }
        yield canonical(payload, preserve_lists=preserve_order), quantity.get("value"), path
    return


class OfflineUcum(SnapshotUcum):
    def __init__(self, data: dict, source: str):
        super().__init__(data["ucum"], source)
        self.factors = {
            (row["source"], row["target"]): Decimal(row["factor"]) for row in data.get("conversions") or []
        }

    def factor(self, source: str, target: str) -> Decimal | None:
        if source == target:
            return Decimal(1) if self.is_valid(source) else None
        return self.factors.get((source, target))


def offline_services(path: Path) -> tuple[Any, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("evidence_kind") != "captured_lookup_responses" or not data.get("captured_at"):
        raise ValueError("Use a captured real lookup pack; manually declared or demonstration verdicts are unsupported")
    provenance = data.get("provenance", {})
    if any(not isinstance(provenance.get(key), str) or not provenance[key].startswith("https://")
           for key in ("ucum_source", "loinc_source")):
        raise ValueError("Captured lookup pack needs its real UCUM and LOINC source endpoints")
    if not isinstance(data.get("ucum"), dict) or not isinstance(data.get("loinc"), list):
        raise ValueError("Evidence snapshot needs ucum verdicts and loinc terms")
    if any(
        not isinstance(unit, str) or not unit or verdict is not None and type(verdict) is not bool
        for unit, verdict in data["ucum"].items()
    ):
        raise ValueError("Snapshot UCUM verdicts must be true, false or null")
    if any(
        not isinstance(row, dict) or not isinstance(row.get("code"), str) or not row["code"]
        for row in data["loinc"]
    ):
        raise ValueError("Snapshot LOINC terms need nonempty code strings")
    for conversion in data.get("conversions", []):
        factor = number(conversion.get("factor")) if isinstance(conversion, dict) else None
        if (
            factor is None
            or factor <= 0
            or not all(
                isinstance(conversion.get(key), str) and conversion[key] for key in ("source", "target")
            )
        ):
            raise ValueError("Snapshot conversion factors must be positive and finite")
    source = f"{data.get('evidence_kind', 'saved_snapshot')}: {path.name}"
    terms = {
        (row["code"], row.get("version", "")): LoincTerm(
            **{**row, "source": source, "example_ucum_units": tuple(row.get("example_ucum_units") or [])}
        )
        for row in data["loinc"]
    }
    return OfflineUcum(data, source), SnapshotLoinc(terms)


class Checker:
    """Injectable terminology clients; provider/model choice is deployment-owned."""

    def __init__(
        self,
        *,
        snapshot: Path | None = None,
        profile: str = "auto",
        profile_config: Path | None = None,
        terminology_sources: Path | None = None,
        unit_catalog: Path | None = None,
        research: bool = False,
        model_alias: str = "local",
        research_scope: str = "studies",
        models_file: Path | None = None,
        approvals: Path | None = None,
        ucum: Any = None,
        loinc: Any = None,
        evidence: Any = None,
        terminology: Any = None,
    ):
        if snapshot and research:
            raise ValueError("--snapshot is offline; use a separate live --research audit")
        if research_scope not in {"lookup", "studies"}:
            raise ValueError("research_scope must be lookup or studies")
        self.research = research
        if ucum is None or loinc is None:
            if snapshot:
                ucum, loinc = offline_services(snapshot)
            else:
                ucum, loinc, evidence = services_from_environment(
                    use_llm=research,
                    research_scope=research_scope,
                    model_alias=model_alias,
                    models_file=str(models_file) if models_file else None,
                )
        self.ucum, self.loinc, self.evidence = ucum, loinc, evidence
        if terminology is not None:
            self.terminology = terminology
        elif snapshot:
            entries = load_sources(terminology_sources) if terminology_sources else []
            if any(entry.get("kind") not in {"csv", "sqlite"} for entry in entries):
                raise ValueError("--snapshot accepts only local CSV/SQLite terminology sources")
            self.terminology = TerminologyRouter(entries) if entries else None
        else:
            self.terminology = router_from_environment(
                llm=getattr(evidence, "llm", None), path=terminology_sources
            )
        self.profile_name = profile
        self.spelling = (
            UnitSpelling(
                unit_catalog,
                self.ucum,
                getattr(evidence, "llm", None) if research else None,
                self.terminology,
            )
            if unit_catalog
            else None
        )
        if self.spelling:
            self.terminology = self.terminology or TerminologyRouter([])
            for system, document in self.spelling.systems.items():
                if (
                    system != "http://unitsofmeasure.org"
                    and document["coverage"] == "complete_finite_codesystem"
                ):
                    self.terminology.sources.insert(0, ({system}, FiniteUnitCodeSource(document), False))
        self.custom = (
            load_profile(json.loads(profile_config.read_text(encoding="utf-8"))["name"], profile_config)
            if profile_config
            else None
        )
        self.approvals = _approvals(approvals)
        self.evidence_mode = "offline_snapshot" if snapshot else "configured_services"
        from .laboratory_suggestions import LaboratorySuggestions

        self.suggestions = LaboratorySuggestions(getattr(self.evidence, "llm", None)) if research else None

    def profile_for(self, kind: str) -> InputProfile:
        if kind != "Observation":
            raise ValueError("Only laboratory Observations are supported")
        return self.custom or load_profile(
            "fhir-r4-lab" if self.profile_name == "auto" else self.profile_name
        )

    def assess(self, payload: dict, stats: dict) -> tuple[dict, dict]:
        return self._laboratory(payload, stats, self.profile_for(payload["resource_type"]))

    def _laboratory(self, payload: dict, stats: dict, profile: InputProfile) -> tuple[dict, dict]:
        q = payload["quantity"]
        measurement = {
            "code": payload["code"],
            "valueQuantity": q,
            "referenceRange": payload["reference_ranges"],
        }
        obs = (
            {"resourceType": "Observation", **measurement}
            if payload["path"] == "Observation"
            else {"resourceType": "Observation", "component": [measurement]}
        )
        pipeline = FhirQualityPipeline(
            self.ucum,
            self.loinc,
            self.evidence,
            self.approvals,
            profile=profile,
            terminology=self.terminology,
            spelling=self.spelling,
            suggestions=self.suggestions,
        )
        report = pipeline.check(
            obs,
            research=self.research,
            **{
                key: value
                for key, value in payload["context"].items()
                if key in {"source_test", "loinc_system", "method", "source_axes"}
            },
        )
        item = report["measurements"][0]
        from .recommendations import source_reference_checks
        item["source_reference_checks"] = source_reference_checks(payload, stats, item["unit_check"])
        ranges = item["reference_checks"]
        findings: list[tuple[str, str]] = []
        if "mismatch" in item.get("additional_axis_fits", {}).values():
            findings.append(("unclear", "A supplied component/property/time/scale descriptor conflicts with the resolved LOINC axis."))
        if any(check["state"] in {"unverified", "alias_version_unverified"} for check in item["name_checks"]):
            findings.append(("unclear", "Analytbezeichnung ist für diesen Code und diese Version nicht eindeutig belegt; fachlich prüfen."))
        if item["unit_label_check"]["state"] in {"unverified", "alias_version_unverified"}:
            findings.append(("unclear", "Einheitenbezeichnung ist für den angegebenen Einheiten-Code nicht belegt; fachlich prüfen."))
        value_types = payload.get("source_value_types", [])
        if len(value_types) > 1:
            findings.append(
                ("error", "Mehrere value[x]-Typen in einem Messwert; Interpretation widersprüchlich.")
            )
        if value_types and payload.get("has_data_absent_reason"):
            findings.append(
                ("error", "value[x] und dataAbsentReason gemeinsam vorhanden; Datenquelle klären.")
            )
        codes = item["code_checks"]
        code_state = _combined([c["terminology"]["state"] for c in codes])
        if not codes:
            findings.append(("unclear", "Code oder Codesystem fehlt."))
        elif not item["code_policy_compatible"] and all(c["state"] == "compatible" for c in codes):
            findings.append(("error", "Ein vom Profil verlangtes Codesystem fehlt in der Code-Zuordnung."))
        for check in codes:
            if check["state"] == "invalid":
                findings.append(
                    ("error", "Code verletzt eine explizite Profilregel: " + ", ".join(check["issues"]))
                )
            elif check["state"] != "compatible":
                findings.append(("unclear", "Code-System-Zuordnung ist unvollständig."))
            if check["terminology"]["state"] == "invalid":
                findings.append(("error", f"Code {check['code']} ist im angegebenen System ungültig."))
            elif check["terminology"]["state"] != "valid":
                findings.append(("unclear", f"Code {check['code']} konnte nicht verifiziert werden."))
            binding = check.get("value_set_membership") or {}
            if check.get("concept_binding_satisfied"):
                continue
            if binding.get("state") == "not_member":
                findings.append(
                    (
                        "error" if check["binding_strength"] == "required" else "unclear",
                        "Code außerhalb der ValueSet-Bindung; bei extensible ist eine Fachprüfung erforderlich.",
                    )
                )
            elif binding.get("state") == "unknown":
                findings.append(("unclear", "ValueSet-Mitgliedschaft konnte nicht verifiziert werden."))
        rule = item["quantity_policy"]
        if payload["numeric_applicable"] and rule["state"] == "invalid":
            findings.append(
                ("error", "Einheit verletzt eine explizite Elementregel: " + ", ".join(rule["issues"]))
            )
        unit_state = item["unit_check"]["state"]
        if not payload["numeric_applicable"]:
            unit_state = "not_applicable"
        if unit_state == "invalid":
            findings.append(("error", "Einheiten-Code ist im angegebenen System ungültig."))
        elif unit_state not in {"valid", "not_applicable"}:
            findings.append(("unclear", "Einheiten-Code/System fehlt oder ist nicht verifizierbar."))
        if (
            q.get("system") == "http://unitsofmeasure.org"
            and q.get("unit")
            and q.get("code")
            and q["unit"] != q["code"]
        ):
            try:
                if self.ucum.is_valid(q["unit"]):
                    display_factor = self.ucum.factor(q["unit"], q["code"])
                    if display_factor is None or display_factor != 1:
                        findings.append(
                            (
                                "unclear",
                                "Text-Einheit und Einheiten-Code bezeichnen unterschiedliche oder ungeklärte Skalen.",
                            )
                        )
            except Exception:
                findings.append(
                    ("unclear", "Text-Einheit und Einheiten-Code konnten nicht abgeglichen werden.")
                )
        mapping = item["mapping"]
        raw_candidate = None
        if not q.get("code") and q.get("unit"):
            try:
                raw_candidate = self.ucum.is_valid(q["unit"])
                term = self.loinc.lookup(item["loinc_code"]) if item["loinc_code"] else None
                if raw_candidate and term:
                    mapping = pipeline._mapping(
                        q["unit"],
                        True,
                        term,
                        **{
                            "source_test": payload["context"].get("source_test", ""),
                            "loinc_system": payload["context"].get("loinc_system", ""),
                            "method": payload["context"].get("method", ""),
                        },
                    )
                    findings.append(
                        (
                            "suggestion",
                            "Text-Einheit ist UCUM-Kandidat; ein deklariertes Einheitensystem bleibt erforderlich.",
                        )
                    )
            except Exception:
                pass
        mapping_class = mapping["class"]
        proposal = target = factor = basis = ""
        if mapping_class in {"normalization_verified", "conversion_verified"}:
            target, factor, basis = mapping["target"], mapping["factor"], "verified_factor"
            approval_text = (
                "Lokal fachlich freigegeben." if mapping["approved"] else "Lokale Freigabe erforderlich."
            )
            proposal = f"Einheit → {target}; Wert × {factor}. Referenzgrenzen je eigener Einheit umrechnen. {approval_text}"
            if not mapping["approved"]:
                findings.append(
                    ("suggestion", "Umrechnung mathematisch belegt; lokale fachliche Freigabe fehlt.")
                )
        elif mapping_class in {"needs_refinement", "unresolved"}:
            selected = (item.get("unit_spelling") or {}).get("selected_unit")
            if selected:
                target, basis = selected, "catalogue_llm_spelling_candidate"
                proposal = f"Kataloggestützter Schreibfehlerkandidat: {target}. Interpretation fachlich bestätigen; Faktor aus der Originaleinheit ungeklärt."
            elif item["loinc_examples"]:
                target, basis = item["loinc_examples"][0], "first_example_candidate"
                proposal = f"LOINC-Beispiele prüfen: {', '.join(item['loinc_examples'])}. Erstes Beispiel {target} ist nur ein Kandidat; kein belegter Faktor."
            elif item["unit_candidates"]:
                units = list(dict.fromkeys(c["unit"] for c in item["unit_candidates"]))
                target, basis = units[0], "website_example_candidate"
                proposal = f"Belegte Web-/Katalogkandidaten prüfen: {', '.join(units)}; keine automatisch freigegebene Zuordnung."
            findings.append(("unclear", "Code-Einheiten-Passung ist nicht eindeutig belegt."))
        if raw_candidate and mapping_class == "correct" and not proposal:
            target, basis = q["unit"], "raw_unit_ucum_candidate"
            proposal = f"Text-Einheit {target} als Einheiten-Code mit System UCUM fachlich bestätigen."
        if item["multiple_loinc_codes"]:
            findings.append(("unclear", "Mehrere LOINC-Codes in einem Messwert; keine eindeutige Auswahl."))
        if item["multiple_loinc_versions"]:
            findings.append(("unclear", "Mehrere LOINC-Versionen; die semantische Zuordnung bleibt offen."))
        for fit, noun in (("system_fit", "Material"), ("method_fit", "Methode"), ("scale_fit", "Skala")):
            if fit == "scale_fit" and not payload["numeric_applicable"]:
                continue
            if item[fit] == "mismatch":
                findings.append(("error", f"{noun} passt nicht zur LOINC-Achse."))
            elif item[fit] == "not_provided":
                findings.append(("unclear", f"{noun} ist nicht ausreichend aufgelöst."))
        for check in ranges:
            if check["state"] in {"invalid_bounds", "policy_invalid", "unit_invalid"}:
                findings.append(
                    ("error", "Referenzgrenzen oder ihre Einheiten-Codes/Elementregeln sind fehlerhaft.")
                )
            elif check["state"] != "bounds_valid":
                findings.append(("unclear", "Referenzgrenzen oder ihre Einheiten benötigen Klärung."))
        if stats["invalid"]:
            findings.append(
                (
                    "error",
                    "Ein Wert oder gelieferte Min/Max-Grenzen sind nicht endlich numerisch bzw. ungeordnet.",
                )
            )
        if q.get("comparator"):
            findings.append(
                ("unclear", "Vergleichszeichen vorhanden; Minimum/Maximum sind keine exakten Messwerte.")
            )
        scale = str((item.get("loinc_axes") or {}).get("scale") or "").casefold()
        if (
            value_types
            and scale in {"qn", "quantitative"}
            and not any(
                t in {"valueQuantity", "valueInteger", "valueSampledData", "valueRange", "valueRatio"}
                for t in value_types
            )
        ):
            findings.append(
                ("error", "Quantitative LOINC-Skala und nicht numerischer Werttyp passen nicht zusammen.")
            )
        if not payload["numeric_applicable"]:
            findings.append(
                (
                    "not_applicable",
                    "Nicht numerische Observation: semantische Messwertprüfung nicht anwendbar.",
                )
            )
        if item["study_candidates"]:
            findings.append(
                ("suggestion", "Zusätzliche Literatur-/Katalogevidenz liegt zur fachlichen Prüfung vor.")
            )
        if self.research and item["errors"]:
            findings.append(("unclear", "Evidenzrecherche unvollständig; Details stehen im Evidenzbericht."))
        values = value_checks(item, ranges, stats, q, pipeline)
        if payload.get("modifier_extensions"):
            findings.append(("unclear", "Modifier extensions require an explicit interpretation policy."))
        if payload.get("input_issues"):
            findings.append(
                ("error", "Input encoding or structure violates the selected laboratory contract.")
            )
        plan = transformation(mapping, q, payload["reference_ranges"], target, pipeline)
        row = self._base_row(payload, stats, findings)
        row["werttyp_pruefung"] = (
            "widersprüchliche Werttypen/Fehlgrund"
            if len(value_types) > 1 or value_types and payload.get("has_data_absent_reason")
            else "Quantity-Metadaten"
            if value_types == ["valueQuantity"]
            else "; ".join(value_types) or "kein value[x] geliefert"
        )
        row.update(display_checks(values))
        row.update(spelling_columns(item.get("unit_spelling")))
        for candidate in item.get("reference_unit_spelling", []):
            if candidate.get("selected_unit"):
                row["schreibweise_kandidaten"] += (
                    f"; {candidate['path']}: {candidate['selected_unit']} (Faktor ungeklärt)"
                )
                proposal += f" Referenz-Einheit {candidate['path']}: Kandidat {candidate['selected_unit']} fachlich bestätigen."
        row["vorschlag_transformation"] = "; ".join(
            f"{op['path']}: {op['formula'] or 'Faktor ungeklärt'} → {op['target_unit'] or 'Zieleinheit offen'}"
            for op in plan["operations"]
        )
        row["transformation_pruefung"] = (
            "Faktoren belegt; fachliche Passung/Freigabe prüfen"
            if plan["state"] == "verified_linear"
            else "nicht vollständig berechenbar; Kontext/Einheit/Faktor klären"
        )
        if not payload["numeric_applicable"] and row["status"] != "error":
            row.update({"status": "not_applicable", "status_text": STATUS_LABELS["not_applicable"]})
        row.update(
            {
                "code_pruefung": STATE_LABELS[code_state],
                "einheit_pruefung": STATE_LABELS[unit_state],
                "code_einheit_pruefung": _mapping_label(mapping_class),
                "material_pruefung": STATE_LABELS[item["system_fit"]],
                "methode_pruefung": STATE_LABELS[item["method_fit"]],
                "referenz_pruefung": "; ".join(STATE_LABELS.get(r["state"], r["state"]) for r in ranges)
                or "nicht vorhanden",
                "referenz_fachlich": "offen" if ranges else "nicht vorhanden",
                "vorschlag": proposal,
                "vorschlag_einheit": target,
                "vorschlag_faktor": factor,
                "vorschlag_grundlage": basis,
            }
        )
        sources = [item.get("ucum_source"), item.get("loinc_source")]
        sources += [c["terminology"].get("source") for c in codes]
        sources += [s.get("source") for s in item["study_candidates"]]
        sources += [label.get("source") for check in (*item["name_checks"], item["unit_label_check"])
                    for label in (*check.get("matched_aliases", []), *check.get("candidates", []))]
        row["evidenz_quellen"] = "; ".join(dict.fromkeys(str(s) for s in sources if s))
        report.pop("decision", None)
        for measurement_report in report["measurements"]:
            measurement_report.pop("decision", None)
        evidence = {
            "report": report,
            "reference_metadata": ranges,
            "raw_unit_ucum_candidate": raw_candidate,
            "mapping_proposal": mapping,
            "check": {
                "status": row["status"],
                "code_unit_fit": mapping_class,
                "values": values,
                "unit_spelling": item.get("unit_spelling"),
                "reference_unit_spelling": item.get("reference_unit_spelling", []),
            },
            "recommendation": {
                "text": proposal,
                "unit_candidate": target or None,
                "basis": basis,
                "reference_unit_candidates": [
                    {"path": r["path"], "unit": r["selected_unit"], "factor": None}
                    for r in item.get("reference_unit_spelling", [])
                    if r["selected_unit"]
                ],
                "transformation": plan,
                "review_required": row["status"] != "passed",
            },
            "findings": findings,
        }
        return row, evidence

    def _base_row(self, payload: dict, stats: dict, findings: list[tuple[str, str]]) -> dict:
        statuses = {status for status, _ in findings}
        status = next(
            (s for s in ("error", "unclear", "suggestion", "not_applicable") if s in statuses), "passed"
        )
        codings = (payload.get("code") or {}).get("coding") or payload.get("codings") or []
        q, context = payload["quantity"], payload["context"]
        ranges = payload["reference_ranges"]
        row = {field: "" for field in CSV_FIELDS}
        row.update(
            {
                "status": status,
                "status_text": STATUS_LABELS[status],
                "begruendung": " ".join(dict.fromkeys(reason for _, reason in findings))
                or "Verfügbare deterministische Prüfungen bestanden; klinische Evidenzbewertung bleibt separat.",
                "ressourcentyp": payload["resource_type"],
                "messpfad": payload["path"],
                "codesystem": "; ".join(str(c.get("system") or "") for c in codings),
                "code": "; ".join(str(c.get("code") or "") for c in codings),
                "code_version": "; ".join(str(c.get("version") or "") for c in codings),
                "eingangseinheit": q.get("unit", ""),
                "einheiten_code": q.get("code", ""),
                "einheiten_system": q.get("system", ""),
                "material": context.get("loinc_system", ""),
                "methode": context.get("method", ""),
                "haeufigkeit": stats["known_occurrences"] if not stats["unknown_frequency_inputs"] else "",
                "haeufigkeit_grundlage": "vollständig bekannt"
                if not stats["unknown_frequency_inputs"]
                else "teilweise bekannt"
                if stats["known_occurrences"]
                else "unbekannt: distinct ohne count",
                "gelieferte_pruefzeilen": stats["aggregate_inputs"] + stats["individual_inputs"],
                "werte_min": stats["minimum"],
                "werte_max": stats["maximum"],
                "numerische_werte": stats["numeric"],
                "fehlende_werte": stats["missing"],
                "ungueltige_werte": stats["invalid"],
                "werte_pruefumfang": (
                    "gelieferte Einzelwerte und Min/Max-Grenzen"
                    if stats.get("aggregate_inputs") and stats.get("individual_inputs")
                    else "gelieferte Min/Max-Grenzen"
                    if stats.get("aggregate_inputs")
                    else "gelieferte Einzelwerte"
                ),
                "referenz_unten": "; ".join(str((r.get("low") or {}).get("value", "")) for r in ranges),
                "referenz_oben": "; ".join(str((r.get("high") or {}).get("value", "")) for r in ranges),
                "referenz_einheit": "; ".join(
                    str(
                        (r.get("low") or r.get("high") or {}).get("code")
                        or (r.get("low") or r.get("high") or {}).get("unit")
                        or "Beobachtungseinheit geerbt"
                    )
                    for r in ranges
                ),
                "experten_status": "offen",
            }
        )
        return row


def _combined(states: list[str]) -> str:
    return "invalid" if "invalid" in states else "unknown" if not states or "unknown" in states else "valid"


def _mapping_label(name: str) -> str:
    return {
        "correct": "LOINC-Beispiel passt",
        "normalization_verified": "Schreibweise: Faktor belegt",
        "conversion_verified": "Umrechnung: Faktor belegt",
        "needs_refinement": "fachlich präzisieren",
        "unresolved": "ungeklärt",
    }[name]
