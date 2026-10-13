"""Compare supplied terminology labels with sourced aliases; no automatic mapping."""

from __future__ import annotations

import re
import unicodedata


def normalized_name(value):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value).strip()).casefold()


def check_name(name, system, code, version="", *, term=None, router=None, search=False):
    if not name:
        return {"state": "not_provided", "aliases": [], "candidates": [], "errors": []}
    aliases, errors = [], []
    if (
        term
        and system == "http://loinc.org"
        and term.code == code
        and (not version or version == term.version)
    ):
        version = term.version
        aliases = [
            {"value": value, "language": "", "source": term.source, "version": term.version}
            for value in (term.display, *term.synonyms)
            if value
        ]
        aliases += [dict(row, source=term.source, version=term.version) for row in term.designations]
        if term.component:
            aliases.append({"value": term.component, "language": "", "use": "component_only",
                            "source": term.axis_sources.get("COMPONENT", {}).get("source", term.source), "version": term.version})
    if router:
        additional, errors = router.labels(system, code, version)
        aliases += additional
    matches = [row for row in aliases if normalized_name(str(row["value"])) == normalized_name(name)]
    confirmed = [
        row for row in matches if not row.get("supplement") or row.get("base_version_declared") or not version
    ]
    state = ("component_label" if confirmed and all(row.get("use") == "component_only" for row in confirmed)
             else "exact_alias" if confirmed else "alias_version_unverified" if matches else "unverified")
    candidates = []
    if router and search and not confirmed:
        candidates, failures = router.search_names(system, name)
        errors += failures
    return {
        "state": state,
        "input_label": name,
        "system": system,
        "code": code,
        "version": version,
        "matched_aliases": matches,
        "aliases": aliases[:100],
        "candidates": candidates,
        "errors": errors,
        "scope": "Label evidence only; analyte/specimen/property/time/scale/method need separate review",
    }


def measurement_names(concept, quantity, *, term=None, router=None):
    checks = []
    codings = concept.get("coding", [])
    for coding in codings:
        if coding.get("display"):
            checks.append(
                check_name(
                    str(coding["display"]),
                    str(coding.get("system", "")),
                    str(coding.get("code", "")),
                    str(coding.get("version", "")),
                    term=term,
                    router=router,
                    search=True,
                )
            )
    if concept.get("text"):
        candidates = [c for c in codings if c.get("system") == "http://loinc.org"] or codings
        if len(candidates) == 1:
            coding = candidates[0]
            checks.append(
                check_name(
                    str(concept["text"]),
                    str(coding.get("system", "")),
                    str(coding.get("code", "")),
                    str(coding.get("version", "")),
                    term=term,
                    router=router,
                    search=True,
                )
            )
        else:
            checks.append(
                {
                    "state": "unverified",
                    "input_label": str(concept["text"]),
                    "aliases": [],
                    "candidates": [],
                    "reason": "No unambiguous target coding for the supplied concept name",
                }
            )
    label, code = str(quantity.get("unit") or ""), str(quantity.get("code") or "")
    if label and code and label == code:
        unit_label = {
            "state": "same_as_code",
            "input_label": label,
            "code": code,
            "scope": "Code syntax is assessed separately",
        }
    else:
        unit_label = check_name(label, str(quantity.get("system", "")), code, router=router, search=True)
    return checks, unit_label
