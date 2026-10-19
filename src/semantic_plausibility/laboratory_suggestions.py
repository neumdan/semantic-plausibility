"""Retrieve candidates first; a local model may select only verified codes/names.

No measured values, reference bounds or identifiers enter model/name searches.
Unit factors and comparisons remain owned by the deterministic quality kernel.
"""

from __future__ import annotations

from . import research_prompts as prompts
from .name_checks import normalized_name
from .observation_quality import LOINC_URI, UCUM_URI


def existing_code_conflicts(existing_axes, candidate_axes):
    """A unit mismatch cannot justify changing an existing code's known meaning.

    Disagreements remain review candidates, including when the source code itself
    might be wrong. A clinician must resolve that conflict before model selection.
    """
    existing_axes, candidate_axes = existing_axes or {}, candidate_axes or {}
    return ["existing_code_" + axis for axis in ("component", "property", "time", "system", "scale", "method")
            if existing_axes.get(axis) and candidate_axes.get(axis)
            and existing_axes[axis].casefold() != candidate_axes[axis].casefold()]


class LaboratorySuggestions:
    def __init__(self, llm):
        self.llm = llm
        self.cache = {}

    def assess(self, concept, quantity, term, pipeline, *, specimen="", method="", name_checks=(), context_conflict=False, source_axes=None, research=True, allow_loinc=True, allow_ucum=True):
        source_axes = source_axes or {}
        codings = tuple(sorted((str(c.get("code") or ""), str(c.get("system") or ""), str(c.get("version") or ""))
                               for c in concept.get("coding", []) if c.get("code")))
        names = list(dict.fromkeys(str(name).strip() for name in
            [concept.get("text"), *(c.get("display") for c in concept.get("coding", []))]
            if name and len(str(name)) <= 300))[:3]
        source_name_present = bool(names)
        if not names and term and term.display:
            names = [term.display]
        unit = str(quantity.get("code") or quantity.get("unit") or "")
        key = (tuple(names), unit, quantity.get("unit"), quantity.get("system"), term.code if term else "",
               term.version if term else "", specimen, method, context_conflict, tuple(sorted(source_axes.items())), source_name_present, codings, research, allow_loinc, allow_ucum)
        if key not in self.cache:
            self.cache[key] = self._assess(names, unit, quantity, term, pipeline,
                specimen, method, name_checks, context_conflict, source_axes, source_name_present, codings, research, allow_loinc, allow_ucum)
        return self.cache[key]

    def _assess(self, names, unit, quantity, term, pipeline, specimen, method, name_checks, context_conflict, source_axes, source_name_present, codings, research, allow_loinc, allow_ucum):
        unit_system = quantity.get("system")
        result = {"state": "no_name", "queries": [], "coding_candidates": [], "name_candidates": [],
                  "selected_code": None, "selected_name": None, "model_used": False, "errors": [],
                  "review_required": True, "automatic_changes": False,
                  "model": getattr(self.llm, "model", None),
                  "scope": "Terminology proposals; unit compatibility is necessary, not proof of analyte identity"}
        candidates = []
        if term:
            result["name_candidates"] = self._names(term, pipeline.terminology)
        needs_search = bool(names) and (unit_system != UCUM_URI or context_conflict or not term or (source_name_present and not name_checks) or
            any(check["state"] not in {"exact_alias", "component_label", "not_provided"} for check in name_checks))
        if not needs_search and term and unit_system == UCUM_URI:
            result["state"] = "sourced_name_available" if term else "no_name"
            return result
        # Missing URIs are hypotheses, never rewritten source declarations. Verify
        # literal codes against the configured LOINC service, with the supplied edition.
        if term and allow_loinc:
            candidates.append((term, {"source": term.source, "method": "existing_exact_code"}))
        if allow_loinc:
            for code, system, version in codings[:12]:
                if system:
                    continue
                try:
                    verified = pipeline.loinc.lookup(code, version=version) if version else pipeline.loinc.lookup(code)
                    if verified and all((verified.code, verified.version) != (candidate.code, candidate.version)
                                        for candidate, _ in candidates):
                        candidates.append((verified, {"source": verified.source, "method": "missing_system_exact_code",
                            "source_system": "", "assumed_candidate_system": LOINC_URI}))
                except Exception as exc:
                    result["errors"].append("Exact candidate lookup unavailable: " + type(exc).__name__)
        source_codes = {code for code, system, _ in codings if system in {"", LOINC_URI}}
        def name_matches(candidate):
            labels = {normalized_name(label["name"]) for label in self._names(candidate, pipeline.terminology)}
            return source_name_present and bool(names) and all(normalized_name(name) in labels for name in names)
        exact_matches = {candidate.code for candidate, _ in candidates
                         if candidate.code in source_codes and name_matches(candidate)}
        search = research and allow_loinc and len(exact_matches) != 1 and bool(names)
        result["queries"] = [{"query": name, "origin": "input_name"} for name in names] if search else []
        if search and self.llm:
            try:
                answer = self.llm.json(prompts.NAME_QUERIES, {"analyte_name": names[0]})
                result["model_used"] = True
                known = {normalized_name(name) for name in names}
                for query in answer["queries"]:
                    if normalized_name(query) not in known:
                        result["queries"].append({"query": query, "origin": "unverified_model_query"})
                        known.add(normalized_name(query))
            except Exception as exc:
                result["errors"].append("Name-query model unavailable: " + type(exc).__name__)
        found = [candidate for check in name_checks for candidate in check.get("candidates", [])] if search else []
        if search and hasattr(pipeline.loinc, "search_names"):
            for query in result["queries"][:3]:
                try:
                    found = pipeline.loinc.search_names(query["query"]) + found
                except (OSError, ValueError, RuntimeError) as exc:
                    result["errors"].append("Primary name search unavailable: " + type(exc).__name__)
        if search and pipeline.terminology:
            for query in result["queries"][:5]:
                rows, errors = pipeline.terminology.search_names(LOINC_URI, query["query"])
                found += rows
                result["errors"] += errors
        seen = {(candidate.code, candidate.version) for candidate, _ in candidates}
        verified_seen = set(seen)
        for row in found[:40]:
            code, version = row.get("code"), row.get("version", "")
            if row.get("system") != LOINC_URI or not code or (code, version) in seen:
                continue
            seen.add((code, version))
            if len(candidates) >= 12:
                break
            try:
                verified = pipeline.loinc.lookup(code, version=version) if version else pipeline.loinc.lookup(code)
                if verified and (verified.code, verified.version) not in verified_seen:
                    candidates.append((verified, row))
                    verified_seen.add((verified.code, verified.version))
            except Exception as exc:
                result["errors"].append("Candidate exact lookup unavailable: " + type(exc).__name__)
        interpretations = []
        unit_inputs = [(unit, "declared_ucum") if unit_system == UCUM_URI else
                       (str(quantity.get("code") or ""), "missing_system_exact_unit") if not unit_system else ("", ""),
                       (str(quantity.get("unit") or ""), "source_display_only")]
        for candidate_unit, basis in unit_inputs if allow_ucum else []:
            # A verified literal unit code takes precedence over its display label.
            if interpretations:
                break
            if not candidate_unit or any(row["unit"] == candidate_unit for row in interpretations):
                continue
            try:
                if pipeline.ucum.is_valid(candidate_unit):
                    interpretations.append({"unit": candidate_unit, "basis": basis,
                        "source_mapping_verified": basis == "declared_ucum", "system": UCUM_URI,
                        "state": "valid", "source": getattr(pipeline.ucum, "source", None)})
            except Exception as exc:
                result["errors"].append("Unit interpretation unavailable: " + type(exc).__name__)
        if research and allow_ucum and not interpretations and pipeline.terminology and quantity.get("unit"):
            rows, failures = pipeline.terminology.search_names(UCUM_URI, quantity["unit"])
            result["errors"] += failures
            for row in rows[:5]:
                if row.get("system") != UCUM_URI or not row.get("code"):
                    continue
                try:
                    if pipeline.ucum.is_valid(row["code"]):
                        interpretations.append({"unit": row["code"], "basis": "sourced_unit_name_candidate",
                            "source": getattr(pipeline.ucum, "source", None), "name_source": row.get("source"),
                            "system": UCUM_URI, "state": "valid", "source_mapping_verified": False})
                except Exception:
                    pass
        result["unit_interpretations"] = interpretations
        def priority(pair):
            candidate = pair[0]
            mismatch = bool(specimen and candidate.system and specimen.casefold() != candidate.system.casefold())
            component_match = normalized_name(candidate.component) in {normalized_name(row["query"]) for row in result["queries"]}
            return (candidate.code != (term.code if term else ""), mismatch, not component_match,
                    bool(candidate.method and not method), candidate.status != "ACTIVE")
        candidates.sort(key=priority)
        for candidate, retrieval in candidates[:5]:
            try:
                options = [(interpretation, pipeline._mapping(interpretation["unit"], True, candidate, "", specimen, method))
                           for interpretation in interpretations]
                supported = [(interpretation, mapping) for interpretation, mapping in options
                    if mapping["class"] in {"correct", "normalization_verified", "conversion_verified"}]
                unique = {interpretation["unit"] for interpretation, _ in supported}
                interpretation, mapping = supported[0] if len(unique) == 1 else ({}, {"class": "unresolved"})
                mapping = {**mapping, "interpreted_source_unit": interpretation.get("unit"),
                    "source_unit_mapping_verified": interpretation.get("source_mapping_verified", False),
                    "factor_from_original": mapping.get("factor") if interpretation.get("source_mapping_verified") else None}
            except Exception as exc:
                mapping = {"class": "unresolved", "target": None, "factor": None}
                result["errors"].append("Candidate unit check unavailable: " + type(exc).__name__)
            axes = {"component": candidate.component, "property": candidate.property,
                    "time": candidate.time_aspect, "system": candidate.system, "scale": candidate.scale,
                    "method": candidate.method, "unresolved_part_codes": candidate.part_codes}
            axes["axis_sources"] = candidate.axis_sources
            conflicts = []
            if term and candidate.code != term.code:
                existing_axes = {"component": term.component, "property": term.property,
                    "time": term.time_aspect, "system": term.system, "scale": term.scale, "method": term.method}
                conflicts += existing_code_conflicts(existing_axes, axes)
            conflicts += [name for name, value in source_axes.items() if value and axes.get(name)
                          and value.casefold() != axes[name].casefold()]
            for supplied, target, label in [(specimen, candidate.system, "specimen"), (method, candidate.method, "method")]:
                if supplied and target and supplied.casefold() != target.casefold():
                    conflicts.append(label)
            if candidate.scale and candidate.scale.casefold() not in {"qn", "quantitative"}:
                conflicts.append("scale")
            if candidate.status and candidate.status.upper() != "ACTIVE":
                conflicts.append("code_status_requires_review")
            missing_axes = [name for name in ("component", "property", "system", "scale") if not axes[name]]
            allowed = mapping["class"] in {"correct", "normalization_verified", "conversion_verified"} and not conflicts and not missing_axes
            labels = self._names(candidate, pipeline.terminology)
            result["coding_candidates"].append({
                "code": candidate.code, "system": LOINC_URI, "version": candidate.version,
                "display": candidate.display, "names": [row["name"] for row in labels][:12],
                "code_status": candidate.status or "not_returned",
                "name_evidence": labels, "axes": axes, "unit_fit": mapping,
                "context_conflicts": conflicts, "missing_context": [key for key, value in
                    (("specimen", specimen), ("method", method), ("time", "")) if not value],
                "unresolved_axes": missing_axes,
                "selectable": allowed, "target_verified": True, "source": candidate.source,
                "retrieval": retrieval, "review_required": True,
                "source_name_matches": name_matches(candidate),
            })
        result["state"] = "verified_candidates" if result["coding_candidates"] else "no_verified_candidate"
        for row in result["coding_candidates"]:
            if sum(other["code"] == row["code"] for other in result["coding_candidates"]) > 1:
                row["selectable"] = False
                row["context_conflicts"].append("multiple_candidate_editions")
        exact = [row for row in result["coding_candidates"] if row["selectable"]
                 and row["code"] in source_codes and row["source_name_matches"]]
        if len(exact) == 1 and len(source_codes) == 1:
            result.update(selected_code=exact[0]["code"], selected_name=exact[0]["display"],
                selection_method="exact_code_name_unit", state="verified_metadata_proposal",
                reason="Literal code and supplied name match the retrieved definition; unit is compatible. Confirm missing system and specimen metadata before applying.")
        if research and self.llm and any(row["selectable"] for row in result["coding_candidates"]):
            # Send a small evidence projection, never patient data or the complete lookup response.
            shortlist = model_shortlist(result["coding_candidates"])
            try:
                choice = self.llm.json(prompts.LOINC_REVIEW, {"input_names": names, "source_unit": unit,
                    "specimen": specimen, "method": method, "source_axes": source_axes, "candidates": shortlist})
                if result.get("selection_method") == "exact_code_name_unit":
                    # The model's opinion is supplementary; retain the exact-match
                    # evidence even if it abstains. Neither is clinician approval.
                    result.update(model_review=choice, model_used=True)
                else:
                    result.update(choice, model_used=True, selection_method="model",
                        state="model_review_proposal" if choice["selected_code"] else "model_abstained")
            except Exception as exc:
                result["errors"].append("LOINC review model unavailable: " + type(exc).__name__)
        return result

    @staticmethod
    def _names(term, router):
        labels = [{"name": name, "language": "", "source": term.source, "version": term.version}
                  for name in (term.display,) if name]
        labels += [{"name": row["value"], "language": row.get("language", ""),
                    "source": term.source, "version": term.version} for row in term.designations
                   if row.get("value") and (row.get("use") in {"LONG_COMMON_NAME", "FullySpecifiedName"}
                   or ":" in row["value"] or "[" in row["value"])]
        if router:
            rows, _ = router.labels(LOINC_URI, term.code, term.version)
            labels += [{"name": row["value"], "language": row.get("language", ""),
                        "source": row.get("source"), "version": row.get("version"),
                        "supplement": row.get("supplement"),
                        **{key: row.get(key) for key in ("source_url", "source_title", "source_sha256", "artifact_version", "supplement_version") if row.get(key)}} for row in rows
                       if not row.get("supplement") or row.get("base_version_declared")]
        seen, result = set(), []
        for label in labels:
            if label["name"] not in seen and len(label["name"]) <= 300:
                result.append(label)
                seen.add(label["name"])
        return result[:30]


def model_shortlist(candidates):
    """Retain all candidates in reports; send only selectable definitions to the model."""
    return [{**{key: row[key] for key in ("code", "display", "names", "context_conflicts", "missing_context", "selectable")},
             "axes": {key: value for key, value in row["axes"].items() if key != "axis_sources"},
             "unit_fit": {key: value for key, value in row["unit_fit"].items()
                          if key in {"class", "target", "factor", "interpreted_source_unit", "source_unit_mapping_verified", "factor_from_original"}}}
            for row in candidates if row["selectable"]]
