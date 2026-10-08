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
            size = ' style="font-size:16px"' if w == 290 and i > 0 else ""
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


write('architecture', 'Local laboratory export review — architecture', 1180, [
 ('INPUT',350,20,400,132,['Hospital export boundary','LIS / FHIR / CDS Hub → files/view','TORCH/CQL or SQL selection upstream','Matched scope + export manifest'],'normal'),
 ('CONFIG',20,210,290,157,['TOML + secrets','Columns / native element mapping','Pinned R4 / selected profile rules','Grouping + distinct or items mode','Explicit local or remote sources'],'normal'),
 ('NORMAL',350,210,400,106,['Validate and standardise','Codes + units + values + references','Preserve versions/context; no rewrite'],'normal'),
 ('GROUP',350,380,400,132,['Embedded DuckDB grouping','Distinct: complete guarded sets','Known counts → mean/SE, median/IQR','Items: retain ID / component path'],'normal'),
 ('REF',20,580,290,182,['Local reference evidence','Pack: UCUM + LOINC + factors','CodeSystem / ValueSet artifacts','Versioned CSV / SQLite lists','Default offline; no HTTP fallback','Optional explicitly enabled APIs'],'normal'),
 ('CHECK',350,580,400,157,['Shared laboratory check kernel','Exact lookup → fit → numeric bounds','Transformations need verified factors','Unknown evidence stays unknown','Every partial finding stays visible'],'normal'),
 ('RESEARCH',790,580,290,182,['Optional research module','Local model only','Catalogue spelling candidates','Quoted terminology pages','Bounded PubMed abstracts','Expert applicability required'],'teal'),
 ('QC',20,835,290,132,['Other MII quality checks','FHIR Validator: conformance','FDE: availability/completeness','Separate scopes and outputs'],'normal'),
 ('OUTPUT',350,835,400,157,['Local review artifacts','CSV + HTML + summary + evidence','Terminology credibility / readiness','Suggested mapping / transformation','Chained log + hashes + versions'],'teal'),
 ('STUDY',790,835,290,132,['Matched export studies','LIS ↔ FHIR ↔ CDS Hub','Groups + known frequencies','Context changes / loss candidates'],'normal'),
 ('EXPERT',350,1060,400,106,['Laboratory physician review','Approve proposals in a separate copy','Apply/revalidate in the site ETL'],'teal')], [
 ('INPUT','NORMAL','M550 152 V207','',False),('CONFIG','NORMAL','M310 263 H347','',False),('NORMAL','GROUP','M550 316 V377','',False),
 ('GROUP','CHECK','M550 512 V577','',False),('REF','CHECK','M310 650 H347','',False),('CHECK','RESEARCH','M750 650 H787','optional',True),
 ('CHECK','OUTPUT','M550 737 V832','',False),('RESEARCH','OUTPUT','M935 762 V800 H650 V832','candidate evidence',True),
 ('OUTPUT','STUDY','M750 900 H787','',False),('OUTPUT','EXPERT','M550 992 V1057','',False),('QC','EXPERT','M165 967 V1020 H400 V1057','complementary',True)], [(789,810,'candidate evidence')])

write('decision-flow','Laboratory decisions — evidence, numeric checks and review',1450,[
 ('INPUT',130,20,840,106,['Input contract + selected profile','Valid headers/types, frequency contract, versions, full reference/context guards','Unsupported/malformed contracts fail with a redacted audit; valid rows continue'],'normal'),
 ('LOOKUP',130,185,840,82,['Validate declared code/system and unit/system','Use only the configured source and edition; no default remote fallthrough'],'normal'),
 ('INVALID',20,320,330,106,['Confirmed invalid','Keep deterministic error','Assess remaining fields'],'error'),
 ('KNOWN',385,320,330,106,['Verified code / unit','Identity or syntax supported','Still check semantic fit'],'teal'),
 ('UNKNOWN',750,320,330,106,['Not verified','Missing source/version/context','Unavailable is not invalid'],'normal'),
 ('FIT',130,485,840,82,['Check LOINC axes / example units and supplied context','Unit syntax alone cannot prove code-unit fit; examples are not a universal whitelist'],'normal'),
 ('MATCH',20,620,330,132,['Documented unchanged fit','Continue to values/references','Clinical applicability remains','a separate review question'],'teal'),
 ('CONVERT',385,620,330,132,['Verified linear conversion','Propose target + numeric factor','Check each reference bound too','Never rewrite source values'],'normal'),
 ('AMBIG',750,620,330,132,['Ambiguous / mismatch','10*6 without a denominator','First example is a candidate','No guessed transformation'],'normal'),
 ('RESEARCH',130,815,840,106,['Optional local research pass','Catalogue → local model → quoted lookup pages → bounded PubMed candidates','Schema/source validation; missing evidence remains unresolved'],'teal'),
 ('NUMERIC',130,980,840,132,['Values and reference metadata','Finite numbers, ordered bounds, supplied units and interval context','Out-of-reference is descriptive; it is not automatically a data error','Evidence of clinical plausibility requires method/population review'],'normal'),
 ('OUTPUT',130,1175,840,106,['Write separate checks, credibility and explicit readiness denominators','Priority: error → unclear → suggestion → passed','Readiness is a numeric semantic proxy, not MII certification or clinical approval'],'teal'),
 ('REVIEW',130,1340,840,82,['Expert adjudication → approved site mapping → formal FHIR validation','Patient Observations are never generated from aggregate means'],'normal')], [
 ('INPUT','LOOKUP','M550 126 V182','',False),('LOOKUP','INVALID','M550 267 V295 H185 V317','',False),('LOOKUP','KNOWN','M550 267 V317','',False),('LOOKUP','UNKNOWN','M550 267 V295 H915 V317','',False),
 ('INVALID','FIT','M185 426 V455 H550 V482','',False),('KNOWN','FIT','M550 426 V482','',False),('UNKNOWN','FIT','M915 426 V455 H550 V482','',False),
 ('FIT','MATCH','M550 567 V595 H185 V617','',False),('FIT','CONVERT','M550 567 V617','',False),('FIT','AMBIG','M550 567 V595 H915 V617','',False),
 ('MATCH','RESEARCH','M185 752 V785 H550 V812','',False),('CONVERT','RESEARCH','M550 752 V812','',False),('AMBIG','RESEARCH','M915 752 V785 H550 V812','',False),
 ('RESEARCH','NUMERIC','M550 921 V977','',False),('NUMERIC','OUTPUT','M550 1112 V1172','',False),('OUTPUT','REVIEW','M550 1281 V1337','',False)])

write('batch-flow','Source exports → guarded aggregation → review artifacts',760,[
 ('LIS',20,20,250,106,['LIS export','CSV / approved DB view','Vendor extraction upstream'],'normal'),
 ('HUB',290,20,250,106,['CDS Hub export','Mapped scalar DB / CSV','Read-only optional DB input'],'normal'),
 ('FHIR',560,20,250,106,['FHIR R4 export','Bundle / NDJSON files','TORCH / CQL upstream'],'normal'),
 ('CSV',830,20,250,106,['Export contract','Individual or distinct','Real count only if known'],'normal'),
 ('GROUP',130,225,840,132,['One selected semantic representation','code + code_system + value/bounds + unit + unit_system + full reference set','DuckDB: distinct guarded sets; optional items with ID/path','Keep status, versions, comparator, specimen/method and interval qualifiers'],'normal'),
 ('CHECK',130,425,840,106,['Exact checks and optional evidence candidates','Local pack / configured terminology → code-unit fit → numbers/reference consistency','Reviewable coding/quantity proposals with independently supported transformations'],'teal'),
 ('OUT',130,595,840,132,['Local CSV report + HTML + summary + evidence + audit log','Original sets + eligible distributions + separate checks + readiness ratio','Compare matched LIS / FHIR / CDS Hub exports; physician review before mapping','No upload, message interception or aggregate-to-patient resource generation'],'teal')],[
 ('LIS','GROUP','M145 126 V180 H550 V222','',False),('HUB','GROUP','M415 126 V180 H550 V222','',False),('FHIR','GROUP','M685 126 V180 H550 V222','',False),('CSV','GROUP','M955 126 V180 H550 V222','',False),('GROUP','CHECK','M550 357 V422','',False),('CHECK','OUT','M550 531 V592','',False)])
