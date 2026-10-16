from pathlib import Path
from html import escape

D = Path(__file__).resolve().parents[1] / "docs"
colors = {"normal": ("#f3f7fa", "#c4d5df"), "teal": ("#eef7f4", "#a8cfc6"), "error": ("#fcf1f3", "#ecc7cf")}


def write(name, title, height, nodes, edges, labels=()):
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="1100" height="{height}" viewBox="0 0 1100 {height}" role="img" aria-labelledby="title desc">',
        f'<title id="title">{escape(title)}</title><desc id="desc">{escape("; ".join(n[5][0] for n in nodes))}</desc>',
        '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10z" fill="#537487"/></marker></defs>',
        '<style>text{font-family:Arial,sans-serif;fill:#142d42}.heading{font-size:19px;font-weight:700}.body{font-size:17px}.label{font-size:14px;fill:#526978}</style><rect width="1100" height="100%" fill="white"/>',
    ]
    mmd = ["%% Same node/edge contract as " + name + ".svg.", "flowchart TD"]
    for ident, x, y, w, h, lines, style in nodes:
        fill, stroke = colors[style]
        parts.append(
            f'<g data-node="{ident}"><rect x="{x}" y="{y}" width="{w}" height="{h}" rx="9" fill="{fill}" stroke="{stroke}"/>'
        )
        for i, line in enumerate(lines):
            yy = y + 29 if i == 0 else y + 57 + (i - 1) * 25
            cls = "heading" if i == 0 else "body"
            size = ' style="font-size:16px"' if w <= 340 and i > 0 else ""
            parts.append(f'<text x="{x + 16}" y="{yy}" class="{cls}"{size}>{escape(line)}</text>')
        parts.append("</g>")
        mmd.append("    " + ident + '["' + "<br/>".join(lines) + '"]')
    for a, b, path, label, dashed in edges:
        dash = ' stroke-dasharray="6 4"' if dashed else ""
        parts.append(
            f'<path data-edge="{a}-{b}" d="{path}" fill="none" stroke="#537487" stroke-width="1.8"{dash} marker-end="url(#arrow)"/>'
        )
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


write('architecture', 'Containers and data flow', 925, [
 ('INPUT',20,20,510,135,['Input: selected laboratory data','CSV columns or read-only SQLite / PostgreSQL view','FHIR JSON / Bundle / NDJSON / DB resource column','TORCH / CQL creates the selected FHIR export'],'normal'),
 ('CONFIG',570,20,510,135,['pipeline.toml + catalog.csv + separate secrets','Input/columns, item / distinct output','profile-init downloads pinned profile / bindings to local/','Terminology endpoints/files; local model + research'],'normal'),
 ('RUN',20,220,650,195,['Python runner: one-shot container or uv command','Read and check input contract; optional full FHIR validation','Preserve code + name + value + unit + reference context','Aggregate complete sets and statistics with embedded DuckDB','A1 checks; separate A2 / A3 research from audited A1 output','CSV + HTML + evidence + hash-chained audit'],'teal'),
 ('TX',740,220,340,157,['Terminology evidence','Direct local files OR API','Optional Blaze: explicit imports','UCUM: NLM API OR local service','Sources / editions / hashes'],'normal'),
 ('AI',740,445,340,157,['Optional Ollama service','Local inference; shared weights','A2: real lookup candidates','A3: Europe PMC / PubMed','Quotes + numbers + unit guards'],'teal'),
 ('VALIDATE',20,490,650,137,['Optional local validation services','Matchbox: HL7 validator + acquired FHIR / MII packages','Blaze: terminology, persistent database, internal network','Complete documents checked before semantic grouping'],'normal'),
 ('OUT',20,735,1060,135,['Output and clinical review','One item/group per CSV row; one value per cell; numbered columns for repeated elements','HTML: source value → method/finding → proposal → source/proposal status and citations','Original structures in evidence JSONL; clinician decisions in a versioned local registry'],'teal')], [
 ('INPUT','RUN','M275 155 V220','',False),('CONFIG','RUN','M825 155 V185 H520 V220','',False),
 ('RUN','TX','M670 275 H740','lookups',False),('RUN','AI','M670 340 H705 V520 H740','A2 / A3',True),
 ('RUN','VALIDATE','M250 415 V490','selected validation',True),
 ('RUN','OUT','M20 335 H5 V690 H300 V735','reports',False),
 ('VALIDATE','OUT','M350 627 V735','outcomes',False)],
 [(32,710,'Read-only inputs/configuration · writable output · retained model and terminology volumes')])

write('decision-flow', 'Processing decisions and review', 1160, [
 ('CONTRACT',20,20,1060,125,['1. Select laboratory source and mapped fields','Laboratory: code/system + name + specimen/method + value/statistics + unit/system + references','One pipeline.toml selects sources from catalog.csv; downloads stay under local/','Preflight checks roles, types, paths, count semantics, source/profile editions and configured services'],'normal'),
 ('FHIR',20,195,1060,100,['2. Full FHIR validation selected?','Complete resource export → local validator + pinned profile → separate OperationOutcome','Then use the same semantic adapter for scalar or native FHIR data'],'normal'),
 ('GROUP',20,345,1060,100,['3. Aggregate the complete semantic set','distinct: individual CSV / FHIR / DB values → n, mean, sample SD / SE, min / max, median / IQR','Pool supplied disjoint summaries; keep reference context. Optional items mode serves A1 inspection.'],'normal'),
 ('A1',20,495,1060,125,['4. A1: check source meaning, then cross-field plausibility','Exact code/system and unit evidence → name/axes/specimen fit → code–unit compatibility','Check finite values, reference order/context and supported whole-set transformations','Write original A1 CSV + evidence/audit; valid, inconsistent and unresolved findings remain explicit'],'teal'),
 ('A2',20,680,510,125,['5a. Optional A2: lookup proposals','Grouped A1 report → candidates/pages → local model','Verify selected code/name/unit against actual terminology','Record proposal, sources and unresolved context'],'normal'),
 ('A3',570,680,510,125,['5b. Optional A3: study evidence','Grouped A1 report → screened Europe PMC / PubMed','Local extraction → exact quote/analyte/number/unit guards','Retain statistic, interval, context and source'],'normal'),
 ('REVIEW',20,920,1060,170,['6. Review the complete set','HTML field table / scalar CSV: source → finding → recommendation, with sources and status','Verify specimen, assay, population, unit conversion and each reference interval','Clinician approves / rejects / defers; source data is retained','Reuse compatible approved laboratory mappings after rechecking targets and factors'],'teal')], [
 ('CONTRACT','FHIR','M550 145 V195','',False),('FHIR','GROUP','M550 295 V345','',False),
 ('GROUP','A1','M550 445 V495','',False),('A1','A2','M275 620 V680','lookup',True),
 ('A1','A3','M825 620 V680','studies',True),('A1','REVIEW','M550 620 V920','A1 review',False),
 ('A2','REVIEW','M275 805 V920','',False),('A3','REVIEW','M825 805 V920','',False)])
