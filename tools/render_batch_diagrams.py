"""Generate manual SVG/Mermaid and README diagrams from the same graphs."""
from pathlib import Path
from html import escape

ROOT = Path(__file__).resolve().parents[1]
D = ROOT / "docs"
colors = {"normal": ("#f3f7fa", "#c4d5df"), "teal": ("#eef7f4", "#a8cfc6")}


def write(name, title, height, nodes, edges, labels=(), choices=()):
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="1100" height="{height}" viewBox="0 0 1100 {height}" role="img" aria-labelledby="title desc">',
        f'<title id="title">{escape(title)}</title><desc id="desc">{escape("; ".join(n[5][0] for n in nodes))}</desc>',
        '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10z" fill="#537487"/></marker></defs>',
        '<style>text{font-family:Arial,sans-serif;fill:#142d42}.heading{font-size:19px;font-weight:700}.body{font-size:17px}.label{font-size:14px;fill:#526978}</style><rect width="1100" height="100%" fill="white"/>',
    ]
    mmd = ["%% Generated with " + name + ".svg by tools/render_batch_diagrams.py.", "flowchart TD"]
    for ident, x, y, w, h, lines, style in nodes:
        fill, stroke = colors[style]
        choice = ident in choices
        shape = (f'<polygon points="{x+w/2},{y} {x+w},{y+h/2} {x+w/2},{y+h} {x},{y+h/2}"'
                 if choice else f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="9"')
        parts.append(f'<g data-node="{ident}">{shape} fill="{fill}" stroke="{stroke}"/>')
        for i, line in enumerate(lines):
            yy = y + h / 2 - 12 + i * 27 if choice else y + (29 if i == 0 else 57 + (i - 1) * 25)
            xx = x + w / 2 if choice else x + 16
            anchor = ' text-anchor="middle"' if choice else ""
            cls = "heading" if i == 0 else "body"
            size = ' style="font-size:16px"' if w <= 340 and i > 0 else ""
            parts.append(f'<text x="{xx}" y="{yy}" class="{cls}"{anchor}{size}>{escape(line)}</text>')
        parts.append("</g>")
        opening, closing = ('{"', '"}') if choice else ('["', '"]')
        mmd.append("    " + ident + opening + "<br/>".join(lines) + closing)
    for a, b, path, label, dashed in edges:
        dash = ' stroke-dasharray="6 4"' if dashed else ""
        parts.append(f'<path data-edge="{a}-{b}" d="{path}" fill="none" stroke="#537487" stroke-width="1.8"{dash} marker-end="url(#arrow)"/>')
        arrow = "-.->" if dashed else "-->"
        mmd.append("    " + a + " " + arrow + (' |"' + label + '"| ' if label else " ") + b)
    for x, y, label in labels:
        parts.append(f'<text x="{x}" y="{y}" class="label">{escape(label)}</text>')
    for style, (fill, stroke) in colors.items():
        mmd.append(f"    classDef {style} fill:{fill},stroke:{stroke},color:#142d42")
        ids = [n[0] for n in nodes if n[-1] == style]
        if ids:
            mmd.append("    class " + ",".join(ids) + " " + style)
    parts.append("</svg>")
    (D / (name + ".svg")).write_text("\n".join(parts) + "\n")
    (D / (name + ".mmd")).write_text("\n".join(mmd) + "\n")
    readme = ROOT / "README.md"
    text = readme.read_text()
    start, end = f"<!-- {name}:start -->", f"<!-- {name}:end -->"
    before, rest = text.split(start, 1)
    _, after = rest.split(end, 1)
    readme.write_text(before + start + "\n```mermaid\n" + "\n".join(mmd) + "\n```\n" + end + after)


write('architecture', 'Laboratory architecture: local processing and explicit source requests', 1190, [
 ('INPUT',20,20,510,125,['Selected laboratory input','CSV / FHIR export / read-only SQLite or PostgreSQL','TORCH / CQL selects server exports upstream','Mapped columns or native FHIR fields'],'normal'),
 ('CONFIG',570,20,510,125,['One pipeline.toml + catalog.csv','Mapping, grouping, profile, sources and model','Downloaded profiles / terminology under local/','Credentials in .env or secret files'],'normal'),
 ('A1',20,215,650,140,['A1: Python runner + embedded DuckDB','Preflight → optional validation → normalize → group → check','Preserve code / system, name, specimen, values and units','Check reference context and cross-field consistency'],'teal'),
 ('TX',740,215,340,140,['Terminology evidence','Direct files OR selected APIs','Optional Blaze: imported content','UCUM: NLM API OR local service'],'normal'),
 ('VALIDATE',20,420,650,115,['Optional full FHIR validation','Complete file / FHIR-in-CSV → Matchbox / HL7','Pinned guide + local Blaze terminology; separate outcomes'],'normal'),
 ('LAYA',740,420,340,110,['Optional local Laya','Advisory domain / next step','Configured source is retained'],'normal'),
 ('R1',20,600,650,110,['A1: audited CSV + HTML','Field decisions + five laboratory assessment scopes','Evidence JSONL + summary + hash-chained audit'],'teal'),
 ('MODEL',740,615,340,110,['Ollama / own local API','Candidate selection + extraction','Bounded texts; retained weights'],'normal'),
 ('RESEARCH',20,790,650,140,['A2 / A3: Python research runner','Read grouped A1 report and its verified sidecars','Retrieve source text → local model → verify claims','A3 includes AI lookup plus study extraction'],'teal'),
 ('WEB',740,800,340,140,['Selected source APIs / pages','A2: terminology + LOINC pages','A3: Europe PMC / PubMed','Python retrieves source text'],'normal'),
 ('OUT',20,1020,1060,115,['CSV + HTML: clinical review','Field → finding / condition → recommendation + why → sources','Clinician approval → local registry; recheck targets and factors on later A1 runs'],'teal')], [
 ('INPUT','A1','M275 145 V215','',False),('CONFIG','A1','M825 145 V180 H520 V215','',False),
 ('A1','TX','M670 275 H740','lookup',False),('A1','VALIDATE','M340 355 V420','if selected and applicable',True),
 ('A1','LAYA','M670 330 H700 V475 H740','advisory',True),
 ('A1','R1','M20 300 H8 V655 H20','check results',False),('VALIDATE','R1','M340 535 V600','outcomes',False),
 ('LAYA','R1','M740 505 H710 V655 H670','hints',True),
 ('R1','RESEARCH','M340 710 V790','A2 / A3 selected',True),('R1','OUT','M20 685 H8 V1080 H20','A1 review',False),
 ('RESEARCH','MODEL','M670 825 H700 V670 H740','inference',True),
 ('RESEARCH','WEB','M670 880 H740','retrieval',False),('RESEARCH','OUT','M340 930 V1020','sourced proposals',False)],
 [(350,390,'Selected + complete FHIR'),(350,754,'Optional research from grouped A1'),
  (32,1170,'Source data and inference stay local; public services receive terminology / search context.')])

write('decision-flow', 'Laboratory decisions: validation, conditional findings and optional research', 1520, [
 ('INPUT',20,20,1060,105,['1. Configure and map laboratory input','Code + system + name + specimen/method + value/statistics + unit/system + reference context','Preflight checks mappings, types, paths, profile and source editions'],'normal'),
 ('VALIDATION',20,170,510,140,['Validate complete FHIR?','Enabled + file / FHIR-in-CSV'],'normal'),
 ('FHIR',570,180,510,120,['Validate with local Matchbox / HL7','Installed profile + binding terminology','Retain OperationOutcome; continue semantic checks','Failed validation affects the configured exit status'],'normal'),
 ('SKIP',20,360,510,110,['Disabled / not applicable','Scalar CSV/DB: not_applicable when selected','DB JSON: export for complete-document validation'],'normal'),
 ('GROUP',570,360,510,110,['Aggregate or retain items','distinct: group contexts; calculate / pool statistics','items: retain source IDs for A1 review'],'normal'),
 ('A1',20,550,1060,125,['2. A1: check the whole laboratory set','Declared systems → lookup; missing systems → verify exact candidates','Supported candidate → conditional match; source systems/specimen stay unchanged','Check name/context, numeric quantities, code–unit fit and reference consistency'],'teal'),
 ('REPORT',20,735,1060,110,['3. Persist the audited A1 report','CSV + HTML + field decisions + five scope results / completeness / reasons','Distinguish missing metadata, unverified findings and demonstrated errors'],'teal'),
 ('METHOD',20,900,1060,140,['4. Select the next command','A2/A3 require audited distinct semantic sets'],'normal'),
 ('A2',20,1115,510,125,['A2: research-evidence --scope lookup','Read grouped A1 and sidecars; retrieve candidates','Local model → check against actual terminology','Record sources, proposals and review conditions'],'normal'),
 ('A3',570,1115,510,125,['A3: research-evidence --scope studies','Read the same grouped A1; include AI lookup','Python retrieves studies; local model extracts','Verify quote / analyte / number / unit / interval type'],'normal'),
 ('REVIEW',20,1340,1060,145,['5. Clinician review and scoped reuse','Review field and whole-set findings, short reasons, source support and applicability','A numeric value is a format check; study evidence needs clinical interpretation','Approve / reject / defer in a review copy; recheck approved targets and factors on reuse'],'teal')], [
 ('INPUT','VALIDATION','M275 125 V170','',False),
 ('VALIDATION','FHIR','M530 240 H570','yes',False),('VALIDATION','SKIP','M275 310 V360','no',False),
 ('FHIR','GROUP','M825 300 V360','',False),('SKIP','GROUP','M530 415 H570','',False),
 ('GROUP','A1','M825 470 V510 H550 V550','',False),('A1','REPORT','M550 675 V735','',False),
 ('REPORT','METHOD','M550 845 V900','',False),
 ('METHOD','A2','M20 970 H8 V1080 H275 V1115','lookup: grouped A1',True),
 ('METHOD','A3','M1080 970 H1092 V1080 H825 V1115','studies: grouped A1',True),
 ('METHOD','REVIEW','M550 1040 V1340','A1 only / items',False),
 ('A2','REVIEW','M275 1240 V1340','',False),('A3','REVIEW','M825 1240 V1340','',False)],
 [(535,228,'yes'),(285,341,'no'),(32,1070,'A2: grouped A1'),(830,1070,'A3: grouped A1'),
  (560,1300,'A1 only / items'),(32,1507,'For A2/A3: output.mode="distinct", without value in aggregation.group_by; rerun A1 when needed.')],
 choices={'VALIDATION','METHOD'})
