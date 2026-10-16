"""Medication R4/export adapter. Source assertions are data, never lookup evidence."""
from contextlib import closing
from copy import deepcopy
from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
import hashlib
import os
import sqlite3
import tempfile
from pathlib import Path
from urllib.parse import quote

from .db_source import _identifier_parts, scan
from .export_review import InputContractError, _file_payloads, _resources, csv_rows, reject_json_constant
from .fhir_validation import fhir_json

REGIMEN_KINDS = {"MedicationRequest", "MedicationStatement"}
FIELDS = {"resource_json", "id", "resource_type", "count", "product_code", "product_system", "product_version",
          "product_name", "form_text", "ingredient_text", "medication_json", "status", "intent",
          "dosage_instructions_json", "dosage_json", "do_not_perform", "duration_value", "duration_code",
          "duration_system", "constant_rate", "source_note"}
FIELDS |= {f"strength_{side}_{field}" for side in ("numerator", "denominator") for field in ("value", "code", "system")}
DOSE_FIELDS = {"dosage_text", "route_code", "route_system", "route_text"}
DOSE_FIELDS |= {f"{prefix}_{field}" for prefix in ("dose", "rate") for field in ("value", "unit", "code", "system")}
TIMING_FIELDS = {"frequency", "period", "period_unit", "as_needed"}
FIELDS |= DOSE_FIELDS | TIMING_FIELDS
UCUM = "http://unitsofmeasure.org"


def validate_options(config, data):
    """Reject lab-only options rather than silently discarding their meaning."""
    inp = config.input
    if inp.get("elements") or inp.get("context_file") or inp.get("snapshot") or inp.get("manifest"):
        raise ValueError("Medication uses native FHIR or the medication column map; laboratory elements/context/snapshots/manifests are unsupported")
    if set(inp.get("context", {})) - {"constant_rate"}:
        raise ValueError("Medication input.context supports only constant_rate (explicit uninterrupted constant infusion assertion)")
    if "constant_rate" in inp.get("context", {}) and type(inp["context"]["constant_rate"]) is not bool:
        raise ValueError("input.context.constant_rate must be a Boolean")
    if data.get("quality") or data.get("output", {}).get("lookup_pack") or data.get("study"):
        raise ValueError("Medication review registry, lookup packs and cross-source study comparison are not implemented")
    spec = data.get("specification", {})
    if spec.get("PROFILE", "auto") != "auto" or spec.get("PROFILE_FILE"):
        raise ValueError("Medication uses specification.IG_CANONICAL/STRUCTURE_DEFINITION for full validation; omit the laboratory PROFILE/PROFILE_FILE policy")


def validate_fields(mapping):
    fields = set(mapping["fields"]) | set(mapping.get("defaults", {}))
    if fields == {"resource_json"}:
        return
    kind = mapping["resource_type"]
    required = set() if kind == "Medication" else {"status"}
    if kind == "MedicationRequest":
        required.add("intent")
    if not required <= fields or not ({"product_code", "product_system"} <= fields or "medication_json" in fields):
        raise ValueError("Medication columns need medication_json OR product_code/product_system; events also need status, requests intent")
    if "medication_json" in fields and any(f.startswith(("product_", "strength_")) or f in {"form_text", "ingredient_text"} for f in fields):
        raise ValueError("Choose medication_json OR flat product/strength columns; do not mix competing product definitions")
    if fields & (DOSE_FIELDS | TIMING_FIELDS) and fields & {"dosage_json", "dosage_instructions_json"}:
        raise ValueError("Choose dosage columns OR a complete dosage JSON column")
    if kind == "Medication" and fields & (DOSE_FIELDS | TIMING_FIELDS):
        raise ValueError("Select MedicationRequest, MedicationStatement or MedicationAdministration for dosage columns")
    if kind == "MedicationAdministration" and fields & TIMING_FIELDS:
        raise ValueError("Administration uses dose/rate/duration; prescription timing belongs to Request or Statement")
    administration_only = {"dosage_json", "duration_value", "duration_code", "duration_system", "constant_rate"}
    request_only = {"intent", "do_not_perform"}
    wrong = administration_only if kind in REGIMEN_KINDS else {"dosage_instructions_json"}
    if kind != "MedicationRequest":
        wrong |= request_only
    if kind == "Medication":
        wrong |= administration_only
    if fields & wrong:
        raise ValueError("Medication columns contain fields for the other medication resource type")


def decode(value, kind, label):
    try:
        parsed = json.loads(value, parse_float=Decimal, parse_constant=reject_json_constant) if isinstance(value, str) else deepcopy(value)
        if not isinstance(parsed, kind):
            raise ValueError()
        # Check depth/finite numbers even for native JSONB values.
        fhir_json(parsed)
        return parsed
    except (ValueError, TypeError, RecursionError):
        raise InputContractError(f"Invalid {label} JSON; check the mapped input column") from None


def boolean(value, label):
    if value in (None, ""):
        return None
    if type(value) is bool:
        return value
    if isinstance(value, str) and value.lower() in {"true", "false"}:
        return value.lower() == "true"
    raise InputContractError(f"{label} must be true or false, not an inferred flag")


def decimal(value):
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        number = Decimal(str(value))
        return number if number.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def quantity(row, prefix):
    value = row.get(prefix + "value")
    return {k: v for k, v in {"value": decimal(value) if decimal(value) is not None else value,
                              "unit": row.get(prefix + "unit"), "code": row.get(prefix + "code"),
                              "system": row.get(prefix + "system")}.items() if v not in (None, "")}


def dosage_from_columns(row, kind):
    """One explicit instruction per row. Native FHIR/DB JSON preserves complex regimens."""
    result = {}
    if row.get("dosage_text"):
        result["text"] = row["dosage_text"]
    route = {k: row["route_" + k] for k in ("code", "system") if row.get("route_" + k)}
    if route or row.get("route_text"):
        result["route"] = {"coding": [route]} if route else {}
        if row.get("route_text"):
            result["route"]["text"] = row["route_text"]
    dose, rate = quantity(row, "dose_"), quantity(row, "rate_")
    if kind in REGIMEN_KINDS:
        repeat = {field: decimal(row[field]) if decimal(row[field]) is not None else row[field]
                  for field in ("frequency", "period") if row.get(field) not in (None, "")}
        if row.get("period_unit"):
            repeat["periodUnit"] = row["period_unit"]
        if repeat:
            result["timing"] = {"repeat": repeat}
        needed = boolean(row.get("as_needed"), "as_needed")
        if needed is not None:
            result["asNeededBoolean"] = needed
        quantities = {key: value for key, value in (("doseQuantity", dose), ("rateQuantity", rate)) if value}
        if quantities:
            result["doseAndRate"] = [quantities]
    else:
        result.update({key: value for key, value in (("dose", dose), ("rateQuantity", rate)) if value})
    return result


def from_row(row, config):
    kind = config.kind
    if row.get("resource_type") and row["resource_type"] != kind:
        raise InputContractError("CSV/database resource_type differs from general.RESOURCE_TYPE")
    if row.get("medication_json"):
        medication = decode(row["medication_json"], dict, "medication")
        if medication.get("resourceType") != "Medication":
            raise InputContractError("medication_json must contain a FHIR Medication")
    else:
        coding = {k: row.get("product_" + col, "") for k, col in (("system", "system"), ("code", "code"), ("version", "version"))}
        medication = {"resourceType": "Medication", "code": {"coding": [coding], "text": row.get("product_name", "")},
                      "form": {"text": row.get("form_text", "")}}
        strength = {side: quantity(row, f"strength_{side}_") for side in ("numerator", "denominator")}
        if any(strength.values()) or row.get("ingredient_text"):
            medication["ingredient"] = [{"itemCodeableConcept": {"text": row.get("ingredient_text", "")}, "strength": strength}]
    resource = {"resourceType": kind, "status": row.get("status", ""), "id": row.get("id", "")}
    if kind in REGIMEN_KINDS:
        field = "dosageInstruction" if kind == "MedicationRequest" else "dosage"
        scalar = dosage_from_columns(row, kind)
        resource[field] = (decode(row["dosage_instructions_json"], list, "dosage instructions")
                           if row.get("dosage_instructions_json") else [scalar] if scalar else [])
    if kind == "MedicationRequest":
        resource["intent"] = row.get("intent", "")
        flag = boolean(row.get("do_not_perform"), "do_not_perform")
        if flag is not None:
            resource["doNotPerform"] = flag
    elif kind == "MedicationAdministration":
        resource["dosage"] = (decode(row["dosage_json"], dict, "dosage") if row.get("dosage_json")
                               else dosage_from_columns(row, kind))
    elif kind == "Medication":
        if "status" not in row and "status" in medication:
            resource["status"] = medication["status"]
    payload = normalize(resource, medication, config)
    if kind == "MedicationAdministration":
        payload["duration"] = quantity(row, "duration_")
        flag = boolean(row.get("constant_rate"), "constant_rate")
        if flag is not None:
            payload["constant_rate"] = flag
    raw = row.get("count")
    count = decimal(raw)
    individual = config.input.get("granularity", "individual") == "individual"
    if raw not in (None, "") and (count is None or count < 1 or count != count.to_integral_value() or count > 2**63 - 1):
        raise InputContractError("Medication count must be a positive 64-bit integer")
    if individual and count not in (None, 1):
        raise InputContractError("Individual medication rows cannot carry a frequency greater than one")
    return payload, 1 if individual else int(count) if count is not None else None, str(row.get("id") or "")


def normalize(resource, medication, config):
    check_structure(resource, medication or {})
    medication = deepcopy(medication or {})
    # Keep all product semantics, never copy product identifiers/batches/manufacturer identities.
    medication = {k: v for k, v in medication.items() if k in {"code", "form", "ingredient", "amount", "extension", "modifierExtension"}}
    instructions = (resource.get("dosageInstruction", []) if config.kind == "MedicationRequest" else
                    resource.get("dosage", []) if config.kind == "MedicationStatement" else
                    [resource.get("dosage", {})] if config.kind == "MedicationAdministration" else [])
    if not isinstance(instructions, list) or any(not isinstance(x, dict) for x in instructions):
        raise InputContractError("Medication dosage instructions must be objects in an array")
    result = {"resource_type": config.kind, "medication": medication, "dosages": deepcopy(instructions),
              "status": resource.get("status", ""), "intent": resource.get("intent", ""),
              "do_not_perform": resource.get("doNotPerform", False), "duration": {},
              "constant_rate": config.input.get("context", {}).get("constant_rate", False),
              "context": {k: resource[k] for k in ("category", "reasonCode", "statusReason", "modifierExtension", "extension", "courseOfTherapyType") if k in resource},
              "input_issues": []}
    if not medication:
        result["input_issues"].append("Medication reference was not resolved from the supplied export")
        result["unresolved_reference_sha256"] = hashlib.sha256(str(resource.get("medicationReference", {})).encode()).hexdigest()
    if "medicationReference" in resource and "medicationCodeableConcept" in resource:
        result["input_issues"].append("Medication reference and code are both present (FHIR choice conflict)")
    if config.kind in {"MedicationAdministration", "MedicationStatement"}:
        period = resource.get("effectivePeriod", {})
        if config.kind == "MedicationAdministration" and period.get("start") and period.get("end"):
            try:
                # Do not infer missing timezone/date precision.
                start, end = (datetime.fromisoformat(period[k].replace("Z", "+00:00")) for k in ("start", "end"))
                if start.tzinfo is None or end.tzinfo is None:
                    raise ValueError()
                elapsed = end - start
                seconds = Decimal(elapsed.days * 86400 + elapsed.seconds) + Decimal(elapsed.microseconds) / 1000000
                result["duration"] = {"value": seconds, "code": "s", "system": UCUM}
            except (ValueError, TypeError, AttributeError):
                result["input_issues"].append("Effective period needs complete timezone-qualified start and end")
        if resource.get("effectiveDateTime") and period:
            result["input_issues"].append("Both effectiveDateTime and effectivePeriod are present")
    return result


def native_rows(payloads, config, folder):
    """Two bounded passes resolve only exported Medication references; no network fetch."""
    with closing(sqlite3.connect(folder / "medication-references.sqlite")) as db:
        db.execute("CREATE TABLE refs (key TEXT PRIMARY KEY, data TEXT)")
        db.execute("CREATE TABLE ids (key TEXT PRIMARY KEY)")
        for location, payload in payloads():
            for _, resource, url in _resources(payload, location):
                if resource.get("resourceType") != "Medication":
                    continue
                text = fhir_json(resource)
                keys = [url] + (["Medication/" + resource["id"]] if resource.get("id") else [])
                for key in filter(None, keys):
                    old = db.execute("SELECT data FROM refs WHERE key=?", (key,)).fetchone()
                    if old and old[0] != text:
                        raise InputContractError("Export contains conflicting Medication versions")
                    db.execute("INSERT OR IGNORE INTO refs VALUES (?,?)", (key, text))
        for location, payload in payloads():
            for ref, resource, _ in _resources(payload, location):
                if resource.get("resourceType") != config.kind:
                    yield None
                    continue
                identifier = resource.get("id", "")
                if identifier:
                    try:
                        db.execute("INSERT INTO ids VALUES (?)", (identifier,))
                    except sqlite3.IntegrityError:
                        raise InputContractError("Export repeats a medication resource ID; select one version upstream") from None
                medication = resource if config.kind == "Medication" else {"code": resource["medicationCodeableConcept"]} if resource.get("medicationCodeableConcept") else None
                reference = resource.get("medicationReference", {}).get("reference", "")
                if reference.startswith("#"):
                    matches = [r for r in resource.get("contained", []) if r.get("id") == reference[1:] and r.get("resourceType") == "Medication"]
                    if len(matches) > 1:
                        raise InputContractError("Duplicate contained Medication ID")
                    medication = matches[0] if matches else None
                elif reference:
                    found = db.execute("SELECT data FROM refs WHERE key=?", (reference,)).fetchone()
                    medication = decode(found[0], dict, "Medication") if found else None
                yield normalize(resource, medication, config), 1, identifier, ref


def records(config, prepared, folder):
    if config.full_fhir_input:
        yield from native_rows(lambda: _file_payloads(prepared.source["path"], **config.fhir_reader_options), config, folder)
        return
    if config.input_type == "csv":
        rows = ((index, {role: row[column] for role, column in config.mapping["fields"].items()}) for index, row in
                csv_rows(prepared.source["path"], delimiter=config.input.get("delimiter", ","), required=config.mapping["fields"].values()))
    elif config.input_type == "postgres":
        rows = enumerate(scan(config.mapping, os.environ["SQC_CFG_DB_DSN"]), 1)
    else:
        relation = ".".join('"' + part + '"' for part in _identifier_parts(config.mapping["relation"], relation=True))
        columns = ",".join(f'"{column}" AS "{role}"' for role, column in config.mapping["fields"].items())
        def sqlite_rows():
            with closing(sqlite3.connect("file:" + quote(str(prepared.source["path"]), safe="/") + "?mode=ro", uri=True)) as db:
                from .db_source import sqlite_columns_present
                sqlite_columns_present(db, config.mapping)
                db.execute("PRAGMA query_only=ON")
                db.row_factory = sqlite3.Row
                yield from enumerate((dict(row) for row in db.execute(f"SELECT {columns} FROM {relation}")), 1)
        rows = sqlite_rows()
    for index, row in rows:
        row = {**config.mapping.get("defaults", {}), **row}
        if "resource_json" in row:
            # Each database JSON cell must be self-contained, including linked Medication.
            payload = decode(row["resource_json"], (dict, list), "resource")
            with tempfile.TemporaryDirectory(dir=folder) as name:
                yield from native_rows(lambda: iter([(f"db-row:{index}", payload)]), config, Path(name))
        else:
            value, count, identifier = from_row(row, config)
            yield value, count, identifier, f"{config.input_type}-row:{index}"


def check_structure(resource, medication):
    """Fail malformed containers early with field names, never data values."""
    def walk(value, path):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {"form", "strength", "numerator", "denominator", "timing", "repeat", "dose", "doseQuantity", "doseRange", "rateQuantity", "rateRatio", "rateRange", "route", "site", "method", "low", "high", "effectivePeriod", "medicationReference", "itemCodeableConcept"} and not isinstance(child, dict):
                    raise InputContractError("Malformed medication object at " + path + "." + key)
                if key in {"ingredient", "dosageInstruction", "doseAndRate", "coding", "contained", "extension", "modifierExtension"} and (not isinstance(child, list) or any(not isinstance(x, dict) for x in child)):
                    raise InputContractError("Malformed medication array at " + path + "." + key)
                walk(child, path + "." + key)
        elif isinstance(value, list):
            for child in value:
                walk(child, path + "[]")
    for key in ("code", "form"):
        if key in medication and not isinstance(medication[key], dict):
            raise InputContractError("Medication." + key + " must be an object")
    for key in ("medicationReference", "medicationCodeableConcept", "effectivePeriod"):
        if key in resource and not isinstance(resource[key], dict):
            raise InputContractError(key + " must be an object")
    if "dosage" in resource:
        value = resource["dosage"]
        valid = (isinstance(value, list) and all(isinstance(row, dict) for row in value)
                 if resource.get("resourceType") == "MedicationStatement" else isinstance(value, dict))
        if not valid:
            raise InputContractError("dosage must be an array for MedicationStatement and an object for MedicationAdministration")
    walk(resource, "resource")
    walk(medication, "Medication")
