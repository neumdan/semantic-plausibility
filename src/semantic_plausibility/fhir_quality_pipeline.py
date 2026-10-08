# Adapted for the standalone semantic-plausibility project (2026-10-02).
"""Small, read-only quality pipeline for numeric FHIR laboratory Observations.

The fast path uses UCUM and LOINC. Research is an explicit second pass that
adds candidates, never a clinical decision. No source resource is modified.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
from typing import Any

from .observation_quality import ApprovedMapping, LoincTerm
from .name_checks import measurement_names
from .profile_config import (
    InputProfile,
    load_profile,
    profile_assessment,
    assess_coding,
    assess_quantity,
    element_rule,
)


LOINC = "http://loinc.org"
UCUM = "http://unitsofmeasure.org"
AMBIGUOUS = re.compile(r"(\.\.\.|…|\b10\s*\^\s*[+-]?\d+\s*$|\b10e[+-]?\d+\b)", re.I)


def number(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        text = str(value)
        if len(text) > 4096:
            return None
        result = Decimal(text)
        if not result.is_finite():
            return None
        # An encoding budget prevents tiny exponent strings allocating gigabytes
        # during fixed-point rendering. This is not a clinical plausibility limit.
        parts = result.as_tuple()
        return result if len(parts.digits) <= 1024 and abs(parts.exponent) <= 1024 and result.adjusted() < 1024 else None
    except InvalidOperation, ValueError:
        return None


def serial(value: Decimal | None) -> str | None:
    return format(value, "f") if value is not None else None


def examples(term: Any) -> list[str]:
    return list(
        dict.fromkeys(
            unit.strip() for field in term.example_ucum_units for unit in field.split(";") if unit.strip()
        )
    )


class FhirQualityPipeline:
    """Inject terminology services; call ``check`` at any FHIR integration point."""

    def __init__(
        self,
        ucum: Any,
        loinc: Any,
        research: Any = None,
        approvals: list[ApprovedMapping] | None = None,
        profile: InputProfile | None = None,
        terminology: Any = None,
        spelling: Any = None,
    ):
        self.ucum, self.loinc, self.research = ucum, loinc, research
        self.approvals = approvals or []
        self.profile = profile or load_profile("fhir-r4-lab")
        self.terminology = terminology
        self.spelling = spelling

    def check(
        self,
        observation: dict[str, Any],
        *,
        phase: str = "at_fhir",
        source_test: str = "",
        loinc_system: str = "",
        method: str = "",
        research: bool = False,
    ) -> dict[str, Any]:
        if phase not in {"pre_fhir", "at_fhir", "post_fhir"}:
            raise ValueError("phase must be pre_fhir, at_fhir or post_fhir")
        if observation.get("resourceType") != "Observation":
            raise ValueError("Expected a FHIR Observation")
        profile_result = profile_assessment(observation, self.profile)
        items = [("Observation", observation)]
        items += [
            (f"Observation.component[{i}]", component)
            for i, component in enumerate(observation.get("component") or [])
        ]
        checks = [
            self._check_item(item, path, source_test, loinc_system, method, research)
            for path, item in items
            if "valueQuantity" in item or item.get("referenceRange")
        ]
        return {
            "kind": "fhir-lab-quality/v2",
            "phase": phase,
            "observation_id": observation.get("id", ""),
            "source_unchanged": True,
            "profile": profile_result,
            "policy": {
                "guide": self.profile.canonical or "FHIR R4",
                "guide_version": self.profile.guide_version or "4.0.1",
                "fallback": "FHIR R4 for elements without an IG rule",
            },
            "decision": (
                "not_applicable"
                if not checks
                else "needs_review"
                if any(item["decision"] == "needs_review" for item in checks)
                else "screen_supported"
            ),
            "measurements": checks,
        }

    def _check_item(
        self,
        item: dict[str, Any],
        path: str,
        source_test: str,
        loinc_system: str,
        method: str,
        do_research: bool,
    ) -> dict[str, Any]:
        quantity = item.get("valueQuantity") or {}
        if not isinstance(quantity, dict):
            raise ValueError(f"{path}.valueQuantity must be an object")
        unit = str(quantity.get("code") or quantity.get("unit") or "").strip()
        unit_coded = bool(quantity.get("code")) and quantity.get("system") == UCUM
        quantity_path = (
            "Observation.valueQuantity" if path == "Observation" else "Observation.component.valueQuantity"
        )
        quantity_policy = assess_quantity(self.profile, quantity_path, quantity)
        value = number(quantity.get("value"))
        value_state = (
            "missing" if quantity.get("value") is None else "valid" if value is not None else "invalid"
        )
        codings = (item.get("code") or {}).get("coding", [])
        source_codings = sorted(
            {
                (str(c.get("system") or ""), str(c.get("code") or ""), str(c.get("version") or ""))
                for c in codings
                if c.get("system") or c.get("code")
            }
        )
        code_systems = sorted({str(c.get("system") or "") for c in codings})
        code_path = "Observation.code" if path == "Observation" else "Observation.component.code"
        code_rule, _ = element_rule(self.profile, code_path)
        loinc_codes = list(
            dict.fromkeys(str(c["code"]) for c in codings if c.get("system") == LOINC and c.get("code"))
        )
        loinc_code = loinc_codes[0] if len(loinc_codes) == 1 else ""
        loinc_versions = {
            str(c.get("version") or "")
            for c in codings
            if c.get("system") == LOINC and c.get("code") == loinc_code
        }
        errors: list[str] = []
        ucum_valid = None
        if quantity.get("system") == UCUM and quantity.get("code"):
            try:
                ucum_valid = self.ucum.is_valid(unit)
            except Exception as exc:
                errors.append(f"UCUM unavailable: {exc}")
        try:
            selected_version = next(iter(loinc_versions)) if len(loinc_versions) == 1 else ""
            term = (
                (
                    self.loinc.lookup(loinc_code, version=selected_version)
                    if selected_version
                    else self.loinc.lookup(loinc_code)
                )
                if loinc_code and len(loinc_versions) == 1
                else None
            )
        except Exception as exc:
            term = None
            errors.append(f"LOINC unavailable: {exc}")

        name_checks, unit_label_check = measurement_names(item.get("code") or {}, quantity, term=term, router=self.terminology)
        code_checks = []
        for coding in codings:
            if not isinstance(coding, dict):
                continue
            system, code = str(coding.get("system") or ""), str(coding.get("code") or "")
            policy = assess_coding(self.profile, code_path, system, code)
            check = {
                **policy,
                "version": str(coding.get("version") or ""),
                "terminology": {
                    "state": "unknown",
                    "source": None,
                    "metadata": None,
                    "candidates": [],
                    "errors": [],
                },
            }
            if system and code and policy["state"] == "compatible":
                if (
                    system == LOINC
                    and term
                    and code == loinc_code
                    and (not coding.get("version") or coding.get("version") == term.version)
                ):
                    check["terminology"] = {
                        "state": "valid",
                        "source": term.source,
                        "metadata": {"display": term.display},
                        "candidates": [],
                        "errors": [],
                    }
                elif self.terminology:
                    check["terminology"] = self.terminology.check(
                        system, code, str(coding.get("version") or ""), research=False
                    )
                if policy["binding"] and policy["binding_strength"] in {"required", "extensible"}:
                    check["value_set_membership"] = (
                        self.terminology.check_binding(
                            policy["binding"],
                            system,
                            code,
                            str(coding.get("version") or ""),
                            policy["binding_version"] or "",
                        )
                        if self.terminology
                        else {"state": "unknown", "source": None}
                    )
            code_checks.append(check)
        if code_rule.required_coding_system and code_rule.required_coding_system not in code_systems:
            errors.append(f"IG requires a {code_rule.required_coding_system} coding")
        code_policy_ok = (
            bool(code_checks)
            and all(check["state"] == "compatible" for check in code_checks)
            and (not code_rule.required_coding_system or code_rule.required_coding_system in code_systems)
        )
        code_validated = bool(code_checks) and all(
            check["terminology"]["state"] == "valid" for check in code_checks
        )
        concept_member = any(check.get("value_set_membership", {}).get("state") == "member"
            and check.get("binding") == code_rule.binding and check.get("binding_version", "") == code_rule.binding_version
            for check in code_checks)
        if concept_member:
            for check in code_checks:
                if check.get("binding") == code_rule.binding and check.get("system") not in code_rule.system_bindings:
                    check["concept_binding_satisfied"] = True
        binding_ok = all(
            check["binding_strength"] not in {"required", "extensible"}
            or check.get("value_set_membership", {}).get("state") == "member"
            or check.get("concept_binding_satisfied", False)
            for check in code_checks
        )
        if quantity.get("system") == UCUM:
            unit_check = {
                "state": "valid" if ucum_valid is True else "invalid" if ucum_valid is False else "unknown",
                "source": getattr(self.ucum, "source", None),
                "candidates": [],
                "errors": [],
            }
        elif quantity.get("system") and quantity.get("code") and self.terminology:
            unit_check = self.terminology.check(
                str(quantity["system"]), str(quantity["code"]), research=False
            )
        else:
            unit_check = {"state": "unknown", "source": None, "candidates": [], "errors": []}
        range_policies = []
        for rng in item.get("referenceRange") or []:
            for side in ("low", "high"):
                if isinstance(rng.get(side), dict):
                    range_policies.append(
                        assess_quantity(self.profile, f"{path}.referenceRange.{side}", rng[side])
                    )

        mapping = self._mapping(unit, ucum_valid, term, source_test, loinc_system, method)
        interval = self._interval(item, unit, ucum_valid)
        system_fit = (
            "not_provided"
            if not loinc_system or not term or not term.system
            else "match"
            if loinc_system.casefold() == term.system.casefold()
            else "mismatch"
        )
        method_fit = (
            "not_applicable"
            if not term or not term.method
            else "not_provided"
            if not method
            else "match"
            if method.casefold() == term.method.casefold()
            else "mismatch"
        )
        scale_fit = (
            "not_provided"
            if not term or not term.scale
            else "match"
            if term.scale.casefold() in {"qn", "quantitative"}
            else "mismatch"
        )

        reference_checks = self.reference_range_metadata(item, unit, ucum_valid, path=path)
        spelling = self.spelling.check(quantity.get("system", ""), unit, examples(term) if term else [],
                                       context_conflict=bool(term and examples(term) and mapping["class"] == "needs_refinement")) if self.spelling else None
        unit_candidates: list[dict[str, Any]] = []
        if spelling:
            errors += spelling["errors"]
            if spelling["selected_unit"]:
                unit_candidates.append({"unit": spelling["selected_unit"], "kind": "catalogue_llm_spelling_candidate",
                                        "source": spelling["catalogue_source"], "factor_from_source": None,
                                        "status": "candidate_only"})
        reference_spelling = []
        if self.spelling:
            for index, rng in enumerate(item.get("referenceRange") or []):
                for side in ("low", "high"):
                    bound = rng.get(side) or {}
                    bound_unit = str(bound.get("code") or bound.get("unit") or "")
                    if not bound_unit or not bound.get("system"):
                        continue
                    conflict = False
                    if bound.get("system") == UCUM and term and examples(term):
                        try:
                            conflict = self._mapping(bound_unit, self.ucum.is_valid(bound_unit), term,
                                                     source_test, loinc_system, method)["class"] == "needs_refinement"
                        except Exception:
                            pass
                    result = self.spelling.check(bound["system"], bound_unit, examples(term) if term else [],
                                                 context_conflict=conflict)
                    errors += result["errors"]
                    reference_spelling.append({"path": f"referenceRange[{index}].{side}", **result})
        studies: list[dict[str, Any]] = []
        if term and self.research and hasattr(self.research, "catalog_evidence"):
            try:
                catalog_units, catalog_ranges = self.research.catalog_evidence(loinc_code)
                unit_candidates += [
                    {
                        "unit": evidence.unit,
                        "kind": evidence.kind,
                        "source": evidence.source,
                        "factor_from_source": None,
                        "status": "candidate_only",
                    }
                    for evidence in catalog_units
                ]
                studies += [vars(candidate) for candidate in catalog_ranges]
            except Exception as exc:
                errors.append(f"ObservationDefinition catalog unavailable: {exc}")
        research_term = term or (
            LoincTerm(loinc_code, "", source="unverified input code") if loinc_code else None
        )
        if do_research and self.research and research_term:
            if self.terminology:
                for check in code_checks:
                    if check["terminology"]["state"] != "valid":
                        try:
                            check["source_candidates"] = self.terminology.check(
                                check["system"], check["code"], check["version"], research=True).get("candidates", [])
                        except Exception as exc:
                            errors.append(f"Code website lookup unavailable: {type(exc).__name__}")
            if mapping["class"] in {"needs_refinement", "unresolved"}:
                found = []
                try:
                    found = self.research.website_units(loinc_code, research_term)
                except Exception as exc:
                    errors.append(f"LOINC website unavailable: {exc}")
                try:
                    found += self.research.suggest_units((spelling or {}).get("selected_unit") or unit, research_term, found)
                except Exception as exc:
                    errors.append(f"Evidence extraction model unavailable: {exc}")
                try:
                    for candidate in found:
                        try:
                            verified = self._factor(unit, candidate.unit) if ucum_valid else None
                        except Exception:
                            verified = None
                        unit_candidates.append(
                            {
                                "unit": candidate.unit,
                                "kind": candidate.kind,
                                "source": candidate.source,
                                "factor_from_source": serial(verified),
                                "status": "candidate_only",
                            }
                        )
                except Exception as exc:
                    errors.append(f"Candidate verification unavailable: {exc}")
            if hasattr(self.research, "website_ranges"):
                try:
                    studies += [
                        vars(candidate)
                        for candidate in self.research.website_ranges(loinc_code, research_term)
                    ]
                except Exception as exc:
                    errors.append(f"LOINC website ranges unavailable: {exc}")
            try:
                if term:
                    studies += [vars(candidate) for candidate in self.research.search_studies(term)]
            except Exception as exc:
                errors.append(f"PubMed unavailable: {exc}")
        comparisons = self._study_comparisons(studies, unit, value, interval) if ucum_valid else []

        has_context = (
            system_fit == "match" and method_fit in {"match", "not_applicable"} and scale_fit == "match"
        )
        range_ok = interval["state"] in {"inside", "below", "above"}
        mapping_ok = mapping["class"] == "correct" or mapping["approved"]
        decision = (
            "screen_supported"
            if all(
                (
                    quantity_policy["state"] == "compatible",
                    unit_check["state"] == "valid",
                    code_policy_ok,
                    code_validated,
                    binding_ok,
                    all(p["state"] == "compatible" for p in range_policies),
                    mapping_ok,
                    range_ok,
                    has_context,
                    value_state == "valid",
                )
            )
            else "needs_review"
        )
        axes = (
            {
                "component": term.component,
                "property": term.property,
                "time": term.time_aspect,
                "system": term.system,
                "scale": term.scale,
                "method": term.method,
                "unresolved_part_codes": term.part_codes,
            }
            if term
            else None
        )
        return {
            "path": path,
            "name_checks": name_checks,
            "unit_label_check": unit_label_check,
            "loinc_code": loinc_code,
            "loinc_axes": axes,
            "loinc_source": term.source if term else None,
            "loinc_version": term.version if term else None,
            "source_codings": [
                {"system": system, "code": code, "version": version}
                for system, code, version in source_codings
            ],
            "code_systems": code_systems,
            "code_policy_compatible": code_policy_ok,
            "code_checks": code_checks,
            "quantity_policy": quantity_policy,
            "reference_range_policies": range_policies,
            "unit_check": unit_check,
            "multiple_loinc_codes": len(loinc_codes) > 1,
            "multiple_loinc_versions": len(loinc_versions) > 1,
            "source_value": serial(value),
            "value_state": value_state,
            "source_unit": unit,
            "display_unit": quantity.get("unit"),
            "declared_unit_system": quantity.get("system"),
            "ucum_valid": ucum_valid,
            "ucum_source": getattr(self.ucum, "source", None),
            "unit_coded_as_ucum": unit_coded,
            "system_fit": system_fit,
            "method_fit": method_fit,
            "scale_fit": scale_fit,
            "loinc_examples": examples(term) if term else [],
            "mapping": mapping,
            "local_interval": interval,
            "source_reference_ranges": [
                {
                    "low": rng.get("low"),
                    "high": rng.get("high"),
                    "type": rng.get("type"),
                    "appliesTo": rng.get("appliesTo"),
                    "age": rng.get("age"),
                    "text": rng.get("text"),
                }
                for rng in (item.get("referenceRange") or [])
                if isinstance(rng, dict)
            ],
            "decision": decision,
            "unit_candidates": unit_candidates,
            "unit_spelling": spelling,
            "reference_unit_spelling": reference_spelling,
            "reference_checks": reference_checks,
            "execution_order": ["exact_lookup", "name_aliases_and_candidates", "catalogue_spelling", "website_lookup", "study_search"],
            "study_candidates": studies,
            "study_comparisons": comparisons,
            "errors": errors,
            "scope": "Available checks only; LOINC examples and studies are not clinical approval",
        }

    def _factor(self, source: str, target: str) -> Decimal | None:
        if not self.ucum.is_valid(target):
            return None
        factor = self.ucum.factor(source, target)
        return factor if factor is not None and factor.is_finite() and factor > 0 else None

    def _mapping(
        self, unit: str, valid: bool | None, term: Any, source_test: str, loinc_system: str, method: str
    ) -> dict[str, Any]:
        result: dict[str, Any] = {"class": "unresolved", "target": None, "factor": None, "approved": False}
        if not unit or not term or valid is None:
            return result
        if not valid or AMBIGUOUS.search(unit):
            result["class"] = "needs_refinement"
            return result
        targets = examples(term)
        if unit in targets:
            result.update({"class": "correct", "target": unit, "factor": "1"})
            return result
        conversions = []
        unavailable = False
        for target in targets:
            try:
                factor = self._factor(unit, target)
                if factor is not None:
                    conversions.append((target, factor))
            except Exception:
                unavailable = True
        if unavailable:
            result["reason"] = "UCUM conversion service unavailable for at least one example"
            return result
        if len(conversions) != 1:
            result["class"] = "needs_refinement" if targets else "unresolved"
            result["candidates"] = [{"target": t, "factor": serial(f)} for t, f in conversions]
            return result
        target, factor = conversions[0]
        spelling_only = factor == 1 and unit.casefold() == target.casefold()
        result.update(
            {
                "class": "normalization_verified" if spelling_only else "conversion_verified",
                "target": target,
                "factor": serial(factor),
            }
        )
        result["approved"] = any(
            a.matches(
                source_test=source_test,
                loinc_code=term.code,
                source_unit=unit,
                target_unit=target,
                factor=factor,
                specimen=loinc_system,
                method=method,
            )
            for a in self.approvals
        )
        return result

    def _interval(self, item: dict[str, Any], unit: str, valid: bool | None) -> dict[str, Any]:
        intervals = item.get("referenceRange") or []
        if not intervals:
            return {"state": "not_provided"}
        if len(intervals) != 1 or intervals[0].get("age") or intervals[0].get("appliesTo"):
            return {"state": "context_needed"}
        if valid is not True or not unit:
            return {"state": "unit_unresolved"}
        interval = intervals[0]
        bounds: dict[str, Decimal | None] = {}
        for side in ("low", "high"):
            bound = interval.get(side)
            if not bound:
                bounds[side] = None
                continue
            if not isinstance(bound, dict):
                return {"state": "invalid_bounds"}
            value = number(bound.get("value"))
            bound_unit = str(bound.get("code") or bound.get("unit") or unit)
            if value is None:
                return {"state": "invalid_bounds"}
            if (bound.get("code") or bound.get("unit")) and (
                not bound.get("code") or bound.get("system") != UCUM
            ):
                return {"state": "unit_unresolved"}
            try:
                factor = Decimal(1) if bound_unit == unit else self._factor(bound_unit, unit)
            except Exception:
                return {"state": "service_unavailable"}
            if factor is None:
                return {"state": "unit_unresolved"}
            bounds[side] = value * factor
        low, high = bounds["low"], bounds["high"]
        if low is None and high is None:
            return {"state": "not_numeric"}
        if low is not None and high is not None and low > high:
            return {"state": "invalid_bounds"}
        value = number((item.get("valueQuantity") or {}).get("value"))
        comparator = (item.get("valueQuantity") or {}).get("comparator")
        state = (
            "comparator_unresolved"
            if comparator
            else "bounds_valid"
            if value is None
            else "below"
            if low is not None and value < low
            else "above"
            if high is not None and value > high
            else "inside"
        )
        return {"state": state, "low": serial(low), "high": serial(high), "unit": unit}

    def reference_range_metadata(
        self, item: dict[str, Any], unit: str, valid: bool | None, *, path: str = "Observation"
    ) -> list[dict[str, Any]]:
        """Check each interval's bounds and units without choosing a patient's interval."""
        checks = []
        for index, interval in enumerate(item.get("referenceRange") or []):
            numeric = {key: value for key, value in interval.items() if key not in {"age", "appliesTo"}}
            result = self._interval({"referenceRange": [numeric]}, unit, valid)
            policies = [
                assess_quantity(self.profile, path + ".referenceRange." + side, interval[side])
                for side in ("low", "high")
                if isinstance(interval.get(side), dict)
            ]
            unit_checks = {}
            for side in ("low", "high"):
                bound = interval.get(side)
                if not isinstance(bound, dict):
                    continue
                code, system = bound.get("code"), bound.get("system")
                check = {"state": "not_provided", "source": None}
                if code or bound.get("unit"):
                    check = {"state": "unknown", "source": None}
                    try:
                        if code and system == UCUM:
                            verdict = self.ucum.is_valid(str(code))
                            check = {"state": "valid" if verdict is True else "invalid" if verdict is False else "unknown",
                                     "source": getattr(self.ucum, "source", None)}
                        elif code and system and self.terminology:
                            check = self.terminology.check(system, str(code), research=False)
                    except Exception as exc:
                        check["error"] = type(exc).__name__
                unit_checks[side] = check
            if any(check["state"] == "invalid" for check in unit_checks.values()):
                result = {**result, "state": "unit_invalid"}
            if any(
                (interval.get(side) or {}).get("comparator")
                for side in ("low", "high")
                if isinstance(interval.get(side), dict)
            ):
                result = {"state": "comparator_unresolved"}
            if any(policy["state"] != "compatible" for policy in policies):
                result = {**result, "state": "policy_invalid"}
            checks.append(
                {
                    "index": index,
                    **result,
                    "policies": policies,
                    "unit_checks": unit_checks,
                    "bound_unit_basis": {
                        side: "declared"
                        if interval[side].get("code") or interval[side].get("unit")
                        else "observation_unit_inherited"
                        for side in ("low", "high")
                        if isinstance(interval.get(side), dict)
                    },
                    "population_qualified": bool(interval.get("age") or interval.get("appliesTo")),
                    "clinical_evidence_status": "not_approved",
                }
            )
        return checks

    def _study_comparisons(
        self, studies: list[dict[str, Any]], unit: str, value: Decimal | None, interval: dict[str, Any]
    ) -> list[dict[str, Any]]:
        results = []
        for study in studies:
            source_unit = study.get("unit")
            low, high = number(study.get("low")), number(study.get("high"))
            if not source_unit or (low is None and high is None):
                continue
            try:
                factor = Decimal(1) if source_unit == unit else self._factor(source_unit, unit)
            except Exception:
                factor = None
            if factor is None:
                continue
            low = low * factor if low is not None else None
            high = high * factor if high is not None else None
            is_reference = study.get("evidence_type") == "reference_interval"
            relation = (
                None
                if value is None or not is_reference
                else "below"
                if low is not None and value < low
                else "above"
                if high is not None and value > high
                else "inside"
            )
            local_low, local_high = number(interval.get("low")), number(interval.get("high"))
            results.append(
                {
                    "evidence_type": study["evidence_type"],
                    "source": study["source"],
                    "candidate_low": serial(low),
                    "candidate_high": serial(high),
                    "unit": unit,
                    "value_relation": relation,
                    "local_low_minus_candidate": serial(local_low - low)
                    if is_reference and local_low is not None and low is not None
                    else None,
                    "local_high_minus_candidate": serial(local_high - high)
                    if is_reference and local_high is not None and high is not None
                    else None,
                    "status": (
                        "candidate_only_context_and_method_unverified"
                        if is_reference
                        else "candidate_only_not_a_reference_interval"
                    ),
                }
            )
        return results


def _approvals(path: Path | None) -> list[ApprovedMapping]:
    if path is None:
        return []
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError("Approval registry must be a JSON array")
    required = set(ApprovedMapping.__dataclass_fields__)
    if any(not isinstance(row, dict) or set(row) != required for row in rows):
        raise ValueError("Each approval needs exactly the ApprovedMapping fields")
    approvals = []
    for row in rows:
        factor = number(row["factor"])
        if (
            factor is None
            or factor <= 0
            or any(
                not str(row[key]).strip()
                for key in (
                    "source_test",
                    "loinc_code",
                    "source_unit",
                    "target_unit",
                    "specimen",
                    "approved_by",
                    "approved_at",
                    "evidence",
                )
            )
        ):
            raise ValueError("Approval has invalid factor or missing identity/evidence")
        approvals.append(ApprovedMapping(**{**row, "factor": factor}))
    return approvals
