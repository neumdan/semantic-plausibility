"""Bounded study screening and loss-aware text/unit handling; no clinical inference."""
from html import unescape
from html.parser import HTMLParser
import re


TEXT_RENDERING = "HTML/XML text with explicit ^superscripts and collapsed whitespace (v1)"
_SUPER = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺", "0123456789-+")
_POWER = r"10\s*(?:[\^*]\s*[+-]?\d+|[⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺]+)"
_ATOM = r"[A-Za-zµμ%]+(?:\s*(?:\^?[−-]\d+|[⁻⁰¹²³⁴⁵⁶⁷⁸⁹]+))?"
_RECIPROCAL = r"[A-Za-zµμ]+\s*(?:\^?[−-]1|⁻¹)"
_UNIT = re.compile(r"(?<![\w./])(?:[×x·⋅]\s*)?(?:" + _POWER + r"\s*(?:/\s*" + _ATOM + r"|\s+" + _ATOM + r")|" + _ATOM + r"\s*/\s*" + _ATOM + r"|" + _ATOM + r"\s*[·⋅]\s*" + _ATOM + r"|[A-Za-zµμ]+\s+" + _RECIPROCAL + r"|[A-Za-zµμ]+[⁻][¹²³]|%)(?![\w/])")


class _StudyText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        elif not self.hidden and tag == "sup":
            self.parts.append("^")
        elif not self.hidden and tag in {"p", "br", "div"}:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
        elif not self.hidden and tag in {"p", "div"}:
            self.parts.append(" ")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def study_text(markup):
    # Europe PMC can encode the markup itself. Never execute retrieved markup.
    text = str(markup)
    for _ in range(2):
        if re.search(r"&(?:lt|#60);/?(?:sup|sub|p|i|b)\b", text):
            text = unescape(text)
    parser = _StudyText()
    parser.feed(text)
    return re.sub(r"\s+", " ", "".join(parser.parts)).strip()


def unit_key(unit):
    """Typography only: never fold case, change prefixes, scales or denominators."""
    text = unit.replace("µ", "u").replace("μ", "u").replace("−", "-")
    text = re.sub(r"[⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺]+", lambda m: "^" + m[0].translate(_SUPER), text)
    text = re.sub(r"(?<=[A-Za-z])-1\b", "^-1", text)
    # Keep the separator until the reciprocal denominator has been resolved.
    text = re.sub(r"(?<=[A-Za-z0-9])(?:\s*[·⋅]\s*|\s+)([A-Za-z]+)\s*\^-1$", r"/\1", text)
    text = re.sub(r"^([A-Za-z]+)\^-1$", r"1/\1", text)
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"^[×x·⋅](?=10[\^*])", "", text)
    text = re.sub(r"^10\*([+-]?\d+)", r"10^\1", text)
    return text


def unit_tokens(text):
    return list(dict.fromkeys(m[0].strip() for m in _UNIT.finditer(text)))


def source_unit(claim, excerpt):
    """Return the actual source span only for one unambiguous typography match."""
    candidates = [u for u in unit_tokens(excerpt) if unit_key(u) == unit_key(claim)]
    if claim in candidates:
        return claim
    return candidates[0] if len(set(candidates)) == 1 else None


def ucum_spelling(unit):
    """A spelling candidate, requiring an independent configured UCUM validation."""
    return re.sub(r"^10\^([+-]?\d+)", r"10*\1", unit_key(unit))


def analyte_aliases(term):
    """Use the component and English analyte labels; RELATEDNAMES also has non-analytes."""
    display_name = term.display.split("[", 1)[0].strip()
    # Entitic properties describe a cell quantity (e.g. MCHC), not the generic
    # component's blood concentration. Keep the terminology's specific labels.
    base = display_name if term.property.startswith("Ent") and display_name else term.component or display_name
    aliases = [base]
    for designation in term.designations:
        if not designation.get("language", "").startswith("en"):
            continue
        label = designation.get("value", "")
        if designation.get("use") == "ConsumerName":
            label = label.split(",", 1)[0]
            aliases += re.findall(r"\(([A-Za-z]{2,8})\)", label)
            aliases.append(re.sub(r"\s+count$", "", re.sub(r"\([^)]*\)", "", label)).strip())
    # British/American spelling, only when actually listed by this terminology.
    def spelling(value):
        return value.casefold().replace("haem", "hem").replace("leuc", "leuk").rstrip("s")
    aliases += [s for s in term.synonyms if spelling(s) == spelling(base)]
    return list(dict.fromkeys(a for a in aliases if a and len(a) <= 120))[:8]


def mentions_analyte(text, aliases):
    return any(re.search(r"(?<!\w)" + re.escape(a) + r"(?!\w)", text, re.I) for a in aliases if a)


def statistic_context(text, aliases):
    """'Mean' inside an analyte name (MCHC/MCV/MCH) is not a sample statistic."""
    for alias in sorted(filter(None, aliases), key=len, reverse=True):
        text = re.sub(r"(?<!\w)" + re.escape(alias) + r"(?!\w)", "[analyte]", text, flags=re.I)
    return text


def analyte_clauses(text, aliases):
    # Parentheses can contain an analyte and its unit: "(MCHC; g/dL)".
    # Keep sex-labelled strata with their analyte, while separating other tests.
    parts, start = [], 0
    for delimiter in re.finditer(r";|,(?=\s*[A-Za-z])", text):
        following = text[delimiter.end():].lstrip()
        unit = _UNIT.match(following)
        if unit and re.match(r"\s*[)\]]", following[unit.end():]):
            continue
        if re.match(r"(?:female|male|women|men|girls|boys|respectively)\b", following, re.I):
            continue
        parts.append(text[start:delimiter.start()])
        start = delimiter.end()
    parts.append(text[start:])
    return [clause for part in parts for clause in re.split(r"(?<=[.!?])\s+(?=[A-Z])", part)
            if mentions_analyte(clause, aliases)]


ANALYTICAL_LIMITS = {
    "limit_of_detection": r"\b(?:LoD|limit of detection|detection limit)\b",
    "limit_of_quantification": r"\b(?:LoQ|limit of quanti(?:fication|tation)|quantification limit)\b",
    "limit_of_blank": r"\b(?:LoB|limit of blank)\b",
}


def analytical_limit_matches(clause, kind, value, unit):
    """Bind explicit LoD/LoQ/LoB labels to their printed numbers, never to patients."""
    labels = sorted((m.start(), m.end(), role) for role, pattern in ANALYTICAL_LIMITS.items()
                    for m in re.finditer(pattern, clause, re.I))
    quantities = list(re.finditer(r"(?<![\w.])([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*"
                                 + re.escape(unit) + r"(?!\w)", clause))
    # Explicit ordered lists, e.g. "LoD and LoQ were 0.05 x10^9/L and 0.1 x10^9/L".
    if labels and quantities and labels[-1][1] < quantities[0].start():
        return len(labels) == len(quantities) and any(
            label[2] == kind and quantity[1] == value for label, quantity in zip(labels, quantities))
    # Individually labelled values, e.g. "LoD = 0.05 ...; LoQ = 0.1 ...".
    for index, (_, end, role) in enumerate(labels):
        stop = labels[index + 1][0] if index + 1 < len(labels) else len(clause)
        values = [q for q in quantities if end <= q.start() < stop]
        if role == kind and len(values) == 1 and values[0][1] == value:
            return True
    return False


def ambiguous_analyte_clause(text, aliases):
    """Do not infer 'respectively' mappings between multiple named analytes."""
    for alias in filter(None, aliases):
        name = r"(?<!\w)" + re.escape(alias) + r"(?!\w)"
        patterns = [name + r"(?:\s+(?:counts?|concentrations?|levels?))?\s+and\s+([A-Za-z][\w-]*)",
                    r"([A-Za-z][\w-]*)\s+and\s+(?:total\s+)?" + name]
        for pattern in patterns:
            for match in re.finditer(pattern, text, re.I):
                if not mentions_analyte(match[1], aliases):
                    return True
    return False


def quantity_exclusion(text, unit, property_code="", *, bounds=False, analytical=False):
    """Conservative exclusions, not a clinical equivalence or applicability test."""
    if unit_key(unit) == "%" and property_code in {"MCnc", "SCnc", "NCnc", "EntMCnc"}:
        return "percentage_is_not_the_requested_concentration"
    if re.search(r"\bbias\b|limits? of agreement|Bland.?Altman|\bdifference\b", text, re.I):
        return "method_difference_is_not_an_analyte_result"
    if re.search(r"\binterferen\w*\b|\bspik(?:e|ed|ing)\b", text, re.I):
        return "interference_experiment_is_not_an_analyte_result"
    if not analytical and any(re.search(pattern, text, re.I) for pattern in ANALYTICAL_LIMITS.values()):
        return "analytical_limit_requires_its_own_category"
    if not bounds and re.search(r"\bthresholds?\b|\bcut[ -]?(?:off|point)s?\b|\bsensitivity\b|\bspecificity\b|\bdefined\b|\beligib\w*|\brisk\b|\bincidence\b", text, re.I):
        return "decision_threshold_is_not_a_measured_result"
    return ""


def screen_record(record, aliases, property_code=""):
    abstract = record.get("abstract", "")
    if not abstract:
        return 0, "no_abstract"
    clauses = analyte_clauses(abstract, aliases)
    quantitative = [c for c in clauses if re.search(r"\d", c) and any(
        not quantity_exclusion(c, u, property_code, analytical=True,
            bounds=bool(re.search(r"reference\s+(?:interval|range)|\bRIs?\b", c, re.I)))
        for u in unit_tokens(c))]
    if not quantitative:
        return 0, "no_eligible_analyte_quantity_in_abstract"
    title = record.get("title", "")
    reference = bool(re.search(r"reference\s+(?:interval|range)", title, re.I))
    return 10 + 5 * reference + 2 * mentions_analyte(title, aliases), "analyte_quantity_in_abstract"


def provider_query(query, provider):
    """Search the part we actually extract, not unrelated mentions in full text."""
    if provider == "europe_pmc":
        return re.sub(r'"([^"\n]+)"', r'TITLE_ABS:"\1"', query)
    return re.sub(r'"([^"\n]+)"', r'"\1"[Title/Abstract]', query)


def study_queries(term):
    aliases = analyte_aliases(term)
    topic = " OR ".join('"' + re.sub(r'[^\w -]', ' ', a).strip() + '"' for a in aliases if len(a) > 3)
    return {
        "reference_interval": f'({topic}) AND ("reference interval" OR "reference intervals" OR "reference range" OR "reference ranges")',
        "summary_statistics": f'({topic}) AND ("mean" OR "median" OR "standard deviation" OR "minimum" OR "maximum")',
        "analytical_interval": f'({topic}) AND ("analytical measuring interval" OR "reportable range")',
        "documented_extreme": f'({topic}) AND ("extreme value" OR "case report")',
        "reported_measurement": f'({topic}) AND (measurement OR concentration OR units)',
    }
