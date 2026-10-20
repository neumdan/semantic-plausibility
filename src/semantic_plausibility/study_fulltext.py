"""Bounded JATS reading. Table evidence retains cells and headers, never guessed layout."""

import re
import xml.etree.ElementTree as ET

from .study_sources import study_text, mentions_analyte, unit_tokens, quantity_exclusion

RENDERING = "JATS paragraphs or labelled table cells with original headers/notes (v1)"
NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
NONHUMAN = re.compile(
    r"\b(?:veterinary|animals?|mice|rats|murine|canine|feline|dogs?|cats?|cattle|bovine|"
    r"ovine|porcine|equine|wolves|mink|pelicans?|birds?|fish|plants?|fertili[sz]er)\b", re.I)


def text(node):
    return study_text(ET.tostring(node, encoding="unicode")) if node is not None else ""


def _grid(rows):
    """Expand explicit spans; fail closed for overlaps, oversized or irregular tables."""
    cells = {}
    for r, row in enumerate(rows):
        if r >= 400:
            raise ValueError("table row budget")
        c = 0
        for cell in row:
            if cell.tag not in {"td", "th"}:
                continue
            while (r, c) in cells:
                c += 1
            rs, cs = int(cell.get("rowspan", "1")), int(cell.get("colspan", "1"))
            if not 1 <= rs <= 100 or not 1 <= cs <= 40 or c + cs > 40 or r + rs > len(rows):
                raise ValueError("unsupported table span")
            for rr in range(r, r + rs):
                for cc in range(c, c + cs):
                    if (rr, cc) in cells:
                        raise ValueError("overlapping table spans")
                    cells[rr, cc] = text(cell)
            c += cs
    width = max((c + 1 for _, c in cells), default=0)
    if any((r, c) not in cells for r in range(len(rows)) for c in range(width)):
        raise ValueError("irregular table")
    return [[cells[r, c] for c in range(width)] for r in range(len(rows))]


def _roles(header, value, notes):
    """Only explicit cell roles; exclude CI, counts, risk and method differences."""
    if re.search(r"\bCI\b|confidence|tolerance|difference|bias|percent|coefficient|within|\bCV\b", header, re.I):
        return [], None
    interval = re.fullmatch(rf"\s*({NUMBER})\s*[-–−—]\s*({NUMBER})\s*", value)
    reference = bool(re.search(r"reference\s+(?:interval|range)|\bRI\b", header, re.I))
    if reference and interval:
        # RI must be explicitly expanded in the article/table, not inferred.
        if re.search(r"reference\s+(?:interval|range)", header + " " + notes, re.I):
            return [], {"evidence_type": "reference_interval", "low": interval[1], "high": interval[2]}
    roles = {"mean": r"\bmean\b|average", "sd": r"\bSD\b|standard deviation",
             "median": r"\bmedian\b", "min": r"\bmin(?:imum)?\b", "max": r"\bmax(?:imum)?\b"}
    found = [k for k, pattern in roles.items() if re.search(pattern, header, re.I)]
    if len(found) == 1 and re.fullmatch(NUMBER, value):
        return [(found[0], value)], None
    pair = re.fullmatch(rf"\s*({NUMBER})\s*(?:±\s*({NUMBER})|\(\s*({NUMBER})\s*\))\s*", value)
    if set(found) == {"mean", "sd"} and pair:
        return [("mean", pair[1]), ("sd", pair[2] or pair[3])], None
    median = re.fullmatch(rf"\s*({NUMBER})\s*\(\s*{NUMBER}\s*(?:[-–−—]\s*{NUMBER})?\s*\)\s*", value)
    if found == ["median"] and re.search(r"\bIQR\b|interquartile", header, re.I) and median:
        return [("median", median[1])], None
    return [], None


def _table_passages(wrap, aliases, index):
    table = wrap.find(".//table")
    if table is None:
        return []
    head = table.findall("./thead/tr")
    body = table.findall("./tbody/tr")
    if not head or not body:
        raise ValueError("table requires explicit column headers and body")
    rows = _grid(head + body)
    headers = [" / ".join(dict.fromkeys(row[c] for row in rows[:len(head)] if row[c]))
               for c in range(len(rows[0]))]
    caption = text(wrap.find("caption"))
    notes = text(wrap.find("table-wrap-foot")) + " " + " ".join(
        row[0] for row in rows[len(head):] if len(set(row)) == 1)
    abbreviations = {}
    for alias in aliases:
        for match in re.finditer(r"\b([A-Za-z]{1,8})\s*[-–:]\s*" + re.escape(alias) + r"\b", notes, re.I):
            abbreviations[match[1]] = match[0]
    # Entire table is an interference/bias experiment: do not borrow its units.
    if quantity_exclusion(caption, "", bounds=True):
        return []
    out = []
    for r, row in enumerate(rows[len(head):], 1):
        # Analyte identity belongs to the row label, never a caption/abbreviation list.
        alias_definition = next((definition for abbreviation, definition in abbreviations.items()
                                 if mentions_analyte(row[0], [abbreviation])), "")
        if len(set(row)) <= 1 or not (mentions_analyte(row[0], aliases) or alias_definition):
            continue
        unit_columns = [c for c, h in enumerate(headers) if re.fullmatch(r"units?", h, re.I)]
        units = list(dict.fromkeys(unit_tokens(row[0] + " " + " ".join(row[c] for c in unit_columns))))
        if len(units) != 1:
            continue
        unit = units[0]
        context = [f"{h}: {v}" for h, v in zip(headers, row) if re.search(
            r"\b(?:sex|gender|age|population|partition|group|cohort|specimen|method)\b", h, re.I)]
        n = next((row[c] for c, h in enumerate(headers) if re.fullmatch(r"n|number", h, re.I)), "")
        for c, (header, value) in enumerate(zip(headers, row)):
            measurements, bounds = _roles(header, value, caption + " " + notes)
            if not measurements and not bounds:
                continue
            # A labelled rendering, not a claim to be a contiguous prose quotation.
            parts = [caption, row[0], alias_definition, *[row[i] for i in unit_columns],
                     *context, header + ": " + value]
            excerpt = " | ".join(p for p in parts if p)
            out.append({"excerpt": excerpt, "kind": "full_text_table",
                "location": f"table[{wrap.get('id') or index}]/body/row[{r}]/column[{c + 1}]",
                "table_cell": {"header": header, "row_label": row[0], "cell": value,
                    "alias_definition": alias_definition,
                    "caption": caption, "notes": notes[:6000], "context": context,
                    "sample_size": n, "unit": unit, "measurements": measurements, "bounds": bounds}})
    return out


def fulltext_passages(xml, aliases):
    """Read permitted XML locally; no entity declarations or external resource loading."""
    if len(xml.encode()) > 4 * 1024 * 1024 or re.search(r"<!ENTITY\b|<!DOCTYPE[^>]*\[", xml, re.I):
        raise ValueError("XML exceeds budget or contains an entity/internal DTD")
    root = ET.fromstring(xml)
    for node in root.iter():
        node.tag = node.tag.split("}")[-1]
    passages, skipped = [], []
    for index, wrap in enumerate(root.findall(".//body//table-wrap"), 1):
        try:
            passages.extend(_table_passages(wrap, aliases, index))
        except (ValueError, IndexError):
            skipped.append(wrap.get("id") or str(index))
    parents = {child: node for node in root.iter() for child in node}
    context = []
    for index, node in enumerate(root.findall(".//body//p"), 1):
        parent = node
        ancestors = []
        while parent in parents:
            parent = parents[parent]
            ancestors.append(parent)
        if any(a.tag in {"table-wrap", "ref-list", "fn-group"} for a in ancestors):
            continue
        section = next((text(a.find("title")) for a in ancestors if a.tag == "sec"), "body")
        for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z])", text(node)):
            if not sentence or len(sentence) > 2500:
                continue
            part = {"excerpt": sentence, "kind": "full_text_paragraph",
                    "location": f"{section}/paragraph[{node.get('id') or index}]"}
            if mentions_analyte(sentence, aliases) and re.search(r"\d", sentence) and unit_tokens(sentence):
                passages.append(part)
            elif len(sentence) <= 512 and re.search(
                r"participants|patients|volunteers|serum|plasma|whole blood|assay|analy[sz]er", sentence, re.I):
                context.append(part)
    # Table cells come first. Bound prompt size and record omissions explicitly.
    kept, size = [], 0
    for part in passages + context[:6]:
        if len(kept) >= 24 or size + len(part["excerpt"]) > 12000:
            continue
        kept.append(part)
        size += len(part["excerpt"])
    return {"passages": kept, "skipped_tables": skipped,
            "omitted_passages": len(passages) + min(len(context), 6) - len(kept),
            "license": text(root.find(".//permissions/license"))}
