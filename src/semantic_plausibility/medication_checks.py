"""Deterministic medication terminology and dose consistency, not prescribing advice."""
from datetime import datetime, timezone
from decimal import Decimal
import os

from .medication_input import UCUM, REGIMEN_KINDS, decimal, check_structure
from .observation_services import NlmUcumClient
from .terminology_router import TerminologyRouter, load_sources

FHIR = "https://hl7.org/fhir/R4/"
SCOPE = "Terminology, numeric and dimensional consistency. No clinical dose safety, interactions, indication or renal/weight adjustment assessment."


def terminology_plan(config):
    from .fhir_validation import target_profile
    return {"profile": target_profile(config), "formal_fhir_validation": "local validator on full resources only",
            "elements": ["Medication.code", "Medication.form", "Medication.ingredient.itemCodeableConcept"] +
                        ([] if config.kind == "Medication" else [config.kind + ".medication[x]",
                         config.kind + (".dosageInstruction" if config.kind == "MedicationRequest" else ".dosage")]),
            "services": [{"alias": alias, "role": entry.get("role", "primary"),
                          "systems": [UCUM] if entry.get("type") == "ucum" else entry.get("systems", []),
                          "location": entry.get("path") or entry.get("endpoint") or "endpoint reference"}
                         for alias, entry in config.data.get("lookups", {}).items() if entry.get("enabled")],
            "scope": "Each supplied coding is routed by its actual system. Profile bindings/slices are evaluated by the full FHIR validator; ATC does not establish product strength or form."}


class MedicationChecker:
    def __init__(self, config, prepared):
        self.router = TerminologyRouter(load_sources(prepared.audit.terminology_sources) if prepared.audit.terminology_sources else [], strategy=config.lookup_strategy)
        self.ucum = NlmUcumClient(os.environ["UCUM_NLM_BASE_URL"]) if config.network_mode == "lookups" and any(
            x.get("enabled") and x.get("type") == "ucum" for x in config.data.get("lookups", {}).values()) else None
        self.cache, self.factors = {}, {}
        self.config = config

    def close(self):
        self.router.close()

    def lookup(self, system, code, version=""):
        key = (system, code, version)
        if key not in self.cache:
            if system == UCUM and code and self.ucum:
                try:
                    state = "valid" if self.ucum.is_valid(code) else "invalid"
                    row = {"state": state, "source": self.ucum.source, "operation": "isValidUCUM"}
                except Exception as exc:
                    row = {"state": "unknown", "source": self.ucum.source, "error_type": type(exc).__name__}
            else:
                row = self.router.check(system, code, version)
                # Raw provider exceptions can contain credentials; expose only a count.
                row = {**row, "error_count": len(row.get("errors", []))}
                row.pop("errors", None)
            self.cache[key] = {**row, "system": system, "code": code, "version": version,
                               "checked_at": datetime.now(timezone.utc).isoformat()}
        return self.cache[key]

    def factor(self, source, target):
        if not source or not target:
            return None
        if source == target:
            return Decimal(1)
        # Annotations/count units are not interchangeable product presentations.
        if not source or not target or any(c in source + target for c in "{}[]") or not self.ucum:
            return None
        key = (source, target)
        if key not in self.factors:
            try:
                self.factors[key] = self.ucum.factor(source, target)
            except Exception:
                self.factors[key] = None
        return self.factors[key]

    def assess(self, data):
        check_structure({"dosageInstruction": data["dosages"]}, data["medication"])
        findings, lookups, proposals = [], [], []
        def add(state, path, message, source=FHIR + "dosage.html"):
            findings.append({"state": state, "path": path, "message": message, "source": source})

        def concept(value, path, required=False):
            if not isinstance(value, dict):
                add("error", path, "Expected a CodeableConcept object")
                return
            codings = value.get("coding", [])
            if not isinstance(codings, list):
                add("error", path, "coding must be an array")
                return
            if not codings and (required or value.get("text")):
                add("review", path, "Text-only or missing coding: choose a code using a configured product/route/form catalogue")
            for coding in codings:
                if not isinstance(coding, dict) or any(not isinstance(coding.get(k, ""), str) for k in ("system", "code", "version")):
                    add("error", path, "Coding system, code and version must be strings")
                    continue
                result = self.lookup(coding.get("system", ""), coding.get("code", ""), coding.get("version", ""))
                lookups.append({"path": path, "kind": "coding", **result})
                if result["state"] != "valid":
                    add("error" if result["state"] == "invalid" else "review", path,
                        "Code rejected by the selected service" if result["state"] == "invalid" else "Code membership not established by the configured services", result.get("source") or "Configured terminology coverage")
                display = (result.get("metadata") or {}).get("display")
                if result["state"] == "valid" and display:
                    proposals.append({"type": "canonical_name", "path": path, "value": display,
                                      "code": coding.get("code"), "system": coding.get("system"), "source": result.get("source"), "review_required": True})

        def qty(value, path):
            if not isinstance(value, dict):
                add("error", path, "Quantity must be an object")
                return False
            n = decimal(value.get("value"))
            valid = n is not None and n > 0 and isinstance(value.get("value"), (int, float, Decimal)) and not isinstance(value.get("value"), bool)
            if not valid:
                add("error", path, "A positive finite JSON number is required for dose, strength, rate and duration")
            if value.get("comparator"):
                add("review", path, "Censored quantity: exact dose arithmetic is not applicable")
                valid = False
            system, code = value.get("system", ""), value.get("code", "")
            if not isinstance(system, str) or not isinstance(code, str):
                add("error", path, "Unit code and system must be strings")
                return False
            result = self.lookup(system, code)
            lookups.append({"path": path, "kind": "quantity", **result})
            if result["state"] != "valid":
                add("error" if result["state"] == "invalid" else "review", path,
                    "Unit rejected by the configured service" if result["state"] == "invalid" else "Unit is missing or not verified; display text is not a unit code", result.get("source") or "Configured terminology coverage")
            if not system or not code:
                valid = False
            return valid

        def walk(value, path):
            if isinstance(value, list):
                for i, child in enumerate(value):
                    walk(child, f"{path}[{i}]")
            elif isinstance(value, dict):
                if "modifierExtension" in value or "extension" in value:
                    add("review", path, "Extensions are preserved; their effect requires the selected guide and expert review")
                if "value" in value and any(k in value for k in ("unit", "system", "code")):
                    qty(value, path)
                    return
                for key, child in value.items():
                    if key in {"numerator", "denominator", "dose", "doseQuantity", "rateQuantity", "maxDosePerAdministration", "maxDosePerLifetime"} or key in {"low", "high"}:
                        qty(child, path + "." + key)
                    else:
                        walk(child, path + "." + key)
                if "coding" in value or path.rsplit(".", 1)[-1] in {"form", "itemCodeableConcept", "route", "site", "method", "asNeededCodeableConcept"}:
                    concept(value, path)
                if "low" in value and "high" in value:
                    low, high = value["low"], value["high"]
                    if isinstance(low, dict) and isinstance(high, dict):
                        factor = self.quantity_factor(high, low)
                        a, b = decimal(low.get("value")), decimal(high.get("value"))
                        if a is not None and b is not None and factor is not None and a > b * factor:
                            add("error", path, "Range lower bound exceeds upper bound after unit conversion")
                        elif factor is None:
                            add("review", path, "Range bounds need compatible verified units")
                for choices in (("doseQuantity", "doseRange"), ("rateQuantity", "rateRatio", "rateRange"), ("asNeededBoolean", "asNeededCodeableConcept")):
                    if sum(k in value for k in choices) > 1:
                        add("error", path, "FHIR choice conflict: " + ", ".join(choices))

        for issue in data["input_issues"]:
            add("review", "input", issue, FHIR + "medication.html")
        status = data["status"]
        allowed = {
            "Medication": {"", "active", "inactive", "entered-in-error"},
            "MedicationRequest": {"active", "on-hold", "cancelled", "completed", "entered-in-error", "stopped", "draft", "unknown"},
            "MedicationAdministration": {"in-progress", "not-done", "on-hold", "completed", "entered-in-error", "stopped", "unknown"},
            "MedicationStatement": {"active", "completed", "entered-in-error", "intended", "stopped", "on-hold", "unknown", "not-taken"},
        }[data["resource_type"]]
        if not isinstance(status, str) or status not in allowed:
            add("error", "status", "Missing or invalid FHIR R4 status", FHIR + data["resource_type"].lower() + ".html")
        if data["resource_type"] == "MedicationRequest" and (not isinstance(data["intent"], str) or data["intent"] not in {"proposal", "plan", "order", "original-order", "reflex-order", "filler-order", "instance-order", "option"}):
            add("error", "intent", "Missing or invalid FHIR R4 request intent", FHIR + "medicationrequest.html")
        if type(data["do_not_perform"]) is not bool:
            add("error", "doNotPerform", "Expected a Boolean")
        inactive = not isinstance(status, str) or status in {"not-done", "not-taken", "inactive", "entered-in-error", "cancelled", "stopped", "unknown"} or data["do_not_perform"] is not False
        if inactive:
            add("review", "status", "Inactive, uncertain, not-taken or prohibited medication: no dose transformation is proposed")
        product = data["medication"]
        concept(product.get("code", {}), "Medication.code", True)
        # Inspect every ingredient and dosage; never collapse to the first instruction.
        walk({k: v for k, v in product.items() if k != "code"}, "Medication")
        if product.get("ingredient"):
            add("review", "Medication.ingredient", "Product strength/form and ingredient identity remain source assertions; check an authoritative product catalogue", FHIR + "medication.html")
        for index, ingredient in enumerate(product.get("ingredient", [])):
            if not isinstance(ingredient, dict):
                add("error", "Medication.ingredient", "Ingredient must be an object")
                continue
            if ingredient.get("itemReference"):
                add("review", f"Medication.ingredient[{index}]", "Referenced ingredient is not expanded; export its coded identity/strength for review")
            if ingredient.get("strength") and not all(ingredient["strength"].get(k, {}).get("value") is not None for k in ("numerator", "denominator")):
                add("review", f"Medication.ingredient[{index}].strength", "Both strength numerator and denominator are needed")
        if data["resource_type"] != "Medication" and (not data["dosages"] or not any(data["dosages"])):
            add("review", "dosage", "No structured dosage supplied; terminology can still be checked")
        for index, dosage in enumerate(data["dosages"]):
            path = f"dosage[{index}]"
            walk(dosage, path)
            if not dosage.get("route"):
                add("review", path + ".route", "Route not supplied; route/form compatibility is unassessed")
            dose_entries = dosage.get("doseAndRate", []) if data["resource_type"] in REGIMEN_KINDS else [{"doseQuantity": dosage.get("dose")}]
            if not isinstance(dose_entries, list):
                add("error", path, "doseAndRate must be an array")
                continue
            if not dose_entries or not any(isinstance(x, dict) and (x.get("doseQuantity") or x.get("doseRange")) for x in dose_entries):
                add("review", path, "No numeric dose: text, titration or absent dosage needs review")
            self.timing(dosage, path, add)
            for j, entry in enumerate(dose_entries):
                if not isinstance(entry, dict):
                    add("error", path, "Dose entry must be an object")
                    continue
                dose = entry.get("doseQuantity")
                if entry.get("doseRange") or entry.get("rateRange"):
                    add("review", path, "Dose/rate range retained; no single point dose is inferred")
                if isinstance(dose, dict):
                    form = str(product.get("form", {}).get("text", "")).casefold()
                    unit = str(dose.get("unit", dose.get("code", ""))).casefold()
                    if form in {"liquid", "solution", "lösung", "suspension"} and any(x in unit for x in ("tablet", "tablett", "capsul", "kapsel")):
                        add("review", path, "Source form describes a liquid but dose describes tablets/capsules; verify product, reconstitution and dose unit", "Source export: Medication.form and dose unit")
                    if not inactive and not self.has_modifiers(data):
                        proposals.extend(self.strength_proposals(product, dose, f"{path}.doseAndRate[{j}]" if data["resource_type"] in REGIMEN_KINDS else path + ".dose"))
            if data["resource_type"] == "MedicationAdministration" and dosage.get("rateQuantity"):
                if data["duration"]:
                    qty(data["duration"], "duration")
                self.infusion(data, dosage, path, add, proposals, inactive)
            if dosage.get("rateRatio"):
                add("review", path + ".rateRatio", "Rate ratio quantities are checked; delivery integration is not calculated for this representation")
        if data["resource_type"] != "Medication" and not any(x.get("type") == "strength_conversion" for x in proposals) and product.get("ingredient"):
            add("review", "Medication.ingredient.strength", "No unambiguous dose/strength conversion: check ingredient identity, exact quantities and compatible units", "Source export")
        if proposals and any(p["type"] == "strength_conversion" for p in proposals):
            add("review", "Medication.ingredient.strength", "Strength is source-declared; conditional transformations require product/ingredient verification", "Source export")
        if any(f["state"] == "error" for f in findings):
            proposals = [p for p in proposals if p["type"] != "strength_conversion"]
        state = "error" if any(f["state"] == "error" for f in findings) else "needs_review" if any(f["state"] == "review" for f in findings) else "passed"
        from .local_terminology import ddd_references
        return {"status": state, "findings": findings, "terminology": lookups, "recommendations": proposals, "scope": SCOPE,
                "ddd_references": [reference for lookup in lookups for reference in ddd_references(lookup)],
                "product_strength_authority": "source_assertion_only", "clinical_dose_safety": "not_assessed"}

    @staticmethod
    def has_modifiers(data):
        def find(value):
            return any(k in {"modifierExtension", "extension"} or find(v) for k, v in value.items()) if isinstance(value, dict) else any(find(v) for v in value) if isinstance(value, list) else False
        return find(data)

    def quantity_factor(self, source, target):
        if not isinstance(source, dict) or not isinstance(target, dict) or source.get("system") != UCUM or target.get("system") != UCUM:
            return None
        return self.factor(source.get("code", ""), target.get("code", ""))

    @staticmethod
    def exact(q):
        return isinstance(q, dict) and isinstance(q.get("value"), (int, float, Decimal)) and not isinstance(q.get("value"), bool) and decimal(q["value"]) is not None and decimal(q["value"]) > 0 and not q.get("comparator")

    def strength_proposals(self, medication, dose, path):
        rows = []
        ingredients = medication.get("ingredient", [])
        for index, ingredient in enumerate(ingredients):
            if not isinstance(ingredient, dict) or ingredient.get("isActive") is False:
                continue
            strength = ingredient.get("strength", {})
            num, den = strength.get("numerator", {}), strength.get("denominator", {})
            if not all(self.exact(q) and q.get("system") == UCUM and self.lookup(UCUM, q.get("code", ""))["state"] == "valid" for q in (dose, num, den)):
                continue
            forward = self.quantity_factor(dose, den)
            reverse = self.quantity_factor(dose, num) if len(ingredients) == 1 else None
            if forward is not None:
                factor = forward * decimal(num["value"]) / decimal(den["value"])
                target = num
            elif reverse is not None:
                factor = reverse * decimal(den["value"]) / decimal(num["value"])
                target = den
            else:
                continue
            rows.append({"type": "strength_conversion", "path": path, "ingredient_index": index,
                         "input_value": str(dose["value"]), "input_unit": dose["code"], "target_unit": target["code"],
                         "target_system": UCUM, "factor": str(factor), "offset": "0",
                         "proposed_value": str(decimal(dose["value"]) * factor),
                         "explanation": "value × factor; factor uses source-declared ingredient strength and verified UCUM conversions",
                         "strength_source": f"Source export: Medication.ingredient[{index}].strength",
                         "source": self.ucum.source if self.ucum else "Configured UCUM catalogue; identical unit arithmetic",
                         "conditional": True, "review_required": True})
        return rows

    def timing(self, dosage, path, add):
        if "asNeededBoolean" in dosage and type(dosage["asNeededBoolean"]) is not bool:
            add("error", path + ".asNeededBoolean", "Expected a Boolean PRN flag")
        repeat = dosage.get("timing", {}).get("repeat", {})
        if not isinstance(repeat, dict):
            add("error", path + ".timing.repeat", "Expected an object")
            return
        for field in ("frequency", "frequencyMax", "period", "periodMax", "duration", "durationMax", "count", "countMax"):
            if field in repeat:
                n = decimal(repeat[field])
                if not isinstance(repeat[field], (int, float, Decimal)) or isinstance(repeat[field], bool) or n is None or n <= 0 or field.startswith(("frequency", "count")) and n != n.to_integral_value():
                    add("error", path + ".timing.repeat." + field, "Timing must contain positive numeric values (integer counts/frequencies)")
        for base in ("period", "duration"):
            if base in repeat and repeat.get(base + "Unit") not in {"s", "min", "h", "d", "wk", "mo", "a"}:
                add("error", path + ".timing.repeat", "Timing requires a FHIR UnitsOfTime code: s, min, h, d, wk, mo, a", FHIR + "valueset-units-of-time.html")
        for base in ("frequency", "period", "duration", "count"):
            low, high = decimal(repeat.get(base)), decimal(repeat.get(base + "Max"))
            if high is not None and (low is None or low > high):
                add("error", path + ".timing.repeat", "Timing maximum requires a base value no greater than the maximum")
        if dosage.get("asNeededBoolean") or dosage.get("asNeededCodeableConcept") or dosage.get("maxDosePerPeriod"):
            add("review", path, "PRN/max-dose constraint retained; no daily dose is inferred from this instruction")

    def infusion(self, data, dosage, path, add, proposals, inactive):
        dose, rate, duration = dosage.get("dose", {}), dosage.get("rateQuantity", {}), data["duration"]
        if inactive or self.has_modifiers(data) or not all(self.exact(q) and q.get("system") == UCUM and self.lookup(UCUM, q.get("code", ""))["state"] == "valid" for q in (dose, rate, duration)):
            add("review", path, "Infusion comparison requires exact dose, rate, elapsed duration and verified UCUM units")
            return
        factor = self.factor(f"({rate['code']}).({duration['code']})", dose["code"])
        if factor is None:
            add("review", path, "Rate × duration could not be converted to the dose unit; check dimensions")
            return
        expected = decimal(rate["value"]) * decimal(duration["value"]) * factor
        actual = decimal(dose["value"])
        # Numeric tolerance only, not a clinical dose tolerance.
        matches = abs(expected - actual) <= max(abs(actual), abs(expected)) * Decimal("0.000001")
        asserted = data["constant_rate"] is True
        add("consistent" if asserted and matches else "error" if asserted else "review", path,
            f"Rate × duration = {expected} {dose['code']}; recorded dose = {actual}. " +
            ("Source explicitly asserts constant uninterrupted delivery." if asserted else "Conditional only: constant uninterrupted delivery is not established."),
            "Source export + " + (self.ucum.source if self.ucum else "UCUM identity arithmetic"))
        proposals.append({"type": "infusion_comparison", "expected_value": str(expected), "recorded_value": str(actual),
                          "unit": dose["code"], "matches": matches, "constant_rate_asserted": asserted,
                          "source": self.ucum.source if self.ucum else "UCUM identity arithmetic", "review_required": True,
                          "numeric_relative_tolerance": "0.000001", "conversion_factor": str(factor),
                          "explanation": "Comparison, not a proposed dose replacement"})
