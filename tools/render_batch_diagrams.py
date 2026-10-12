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


write('architecture', 'Laboratory checks, separate evidence and clinician feedback', 1330, [
 ('INPUT',350,20,400,132,['Hospital export boundary','LIS / FHIR / CDS Hub → files/view','TORCH/CQL or SQL selection upstream','Explicit input + output selection'],'normal'),
 ('CONFIG',20,210,290,157,['TOML + secret references','Map input / selected profile rules','Choose local, API or both','Enable sources; select editions','Offline mode disables API calls'],'normal'),
 ('NORMAL',350,210,400,106,['Validate and standardise','Codes + units + values + references','Preserve versions/context; no rewrite'],'normal'),
 ('GROUP',350,380,400,132,['Embedded DuckDB grouping','Distinct: complete guarded sets','Known counts → mean/SE, median/IQR','Items: retain ID / component path'],'normal'),
 ('LOCAL',20,480,290,182,['A · Local terminology','Download once with package-init','or install real JSON/CSV/SQLite','Set lookup.path + systems','Offline: add UCUM/LOINC pack','Keep editions and file hashes'],'normal'),
 ('API',790,380,290,182,['B · API configuration','Set type, endpoint + systems','Credentials: .env / secret files','NLM / LOINC / own FHIR','network.mode = lookups','Enabled APIs called at runtime'],'normal'),
 ('CHECK',350,680,400,182,['Same laboratory check kernel','Source code / unit / reference checks','Target profile systems and bindings','LOINC axes / verified UCUM factors','Preserve whole-set context','Missing evidence → unverified'],'normal'),
 ('RESEARCH',790,680,290,182,['Separate evidence research','Name search → exact candidates','Cached per LOINC / edition','PubMed + Europe PMC + pages','Quoted values / units / ranges','Exact prompts / query trace'],'teal'),
 ('QC',20,935,290,132,['Other MII quality checks','FHIR Validator: conformance','FDE: availability/completeness','Separate scopes and outputs'],'normal'),
 ('OUTPUT',350,935,400,157,['Checked export and proposals','CSV + HTML + summary + evidence','Source and target checks separate','Mapping + reference transformations','Chained log + hashes + editions'],'teal'),
 ('STUDY',790,935,290,132,['Matched export studies','LIS ↔ FHIR ↔ CDS Hub','Groups + known frequencies','Context changes / loss candidates'],'normal'),
 ('REGISTRY',20,1160,290,132,['Scoped mapping registry','Source + profile + set scope','Recheck targets in later runs','Retain history and revocation'],'teal'),
 ('EXPERT',350,1160,400,106,['Laboratory physician review','Decision + reviewer + time + evidence','Apply/revalidate approved site plans'],'teal')], [
 ('INPUT','NORMAL','M550 152 V207','',False),('CONFIG','NORMAL','M310 263 H347','',False),('NORMAL','GROUP','M550 316 V377','',False),
 ('CONFIG','LOCAL','M165 367 V477','setup / path',True),('CONFIG','API','M310 340 H1020 V377','explicit selection',True),
 ('GROUP','CHECK','M550 512 V677','',False),('LOCAL','CHECK','M310 570 H330 V750 H347','local reads',False),('API','CHECK','M935 562 V630 H690 V677','selected API calls',False),
 ('OUTPUT','RESEARCH','M750 983 H775 V775 H787','separate pass',True),('CHECK','OUTPUT','M550 862 V932','',False),('RESEARCH','EXPERT','M1080 770 H1090 V1125 H700 V1157','dossier',True),
 ('EXPERT','REGISTRY','M350 1214 H313','decision',False),('REGISTRY','CHECK','M20 1220 H5 V770 H347','scoped reuse',True),
 ('OUTPUT','STUDY','M750 1000 H787','',False),('OUTPUT','EXPERT','M550 1092 V1157','',False),('QC','EXPERT','M165 1067 V1120 H400 V1157','complementary',True)], [(320,331,'Choose per lookup; both routes may be enabled')])

write('decision-flow','Source checks, target proposals, evidence and reviewed reuse',2050,[
 ('INPUT',130,20,840,106,['Input contract + selected profile','Valid headers/types, frequency contract, versions, full reference/context guards','Unsupported/malformed contracts fail with a redacted audit; valid rows continue'],'normal'),
 ('SOURCE',130,185,840,106,['Select configured evidence per lookup: A, B or both','A: previously installed files / captured pack · B: enabled API and credentials','Offline mode disables remote adapters; no fallback to an unconfigured API'],'normal'),
 ('LOCAL',20,345,500,157,['A · Ready local terminology files','package-init is a separate setup command','Accept conditions and pin editions before download','Reference lookup.path / systems; input.snapshot for pack','Supplements provide labels; coverage stays explicit'],'normal'),
 ('API',580,345,500,157,['B · Configured API and credentials','Set type, endpoint and systems where required','Use .env / secret-file references; select release/language','Require network.mode=lookups and lookup.enabled=true','Public services may need no credentials'],'normal'),
 ('LOOKUP',130,565,840,82,['Validate declared code/system and unit/system','Read local evidence and/or query selected services; retain source and edition'],'normal'),
 ('INVALID',20,700,330,106,['Confirmed invalid','Keep deterministic error','Assess remaining fields'],'error'),
 ('KNOWN',385,700,330,106,['Verified code / unit','Identity or syntax supported','Still check semantic fit'],'teal'),
 ('UNKNOWN',750,700,330,106,['Not verified','Absent / partial / unavailable','Unavailable is not invalid'],'normal'),
 ('FIT',130,865,840,82,['Check supplied aliases, LOINC axes / example units and context','Names and unit syntax alone cannot prove fit; examples are not a universal whitelist'],'normal'),
 ('MATCH',20,1000,330,132,['Documented unchanged fit','Continue to values/references','Clinical applicability remains','a separate review question'],'teal'),
 ('CONVERT',385,1000,330,132,['Verified linear conversion','Propose target + numeric factor','Check each reference bound too','Never rewrite source values'],'normal'),
 ('AMBIG',750,1000,330,132,['Ambiguous / mismatch','10*6 without a denominator','First example is a candidate','No guessed transformation'],'normal'),
 ('RESEARCH',130,1550,840,106,['Explicit candidate sources and optional local research','Names → real search → exact candidate + unit/axes → model choice or abstention','Catalogue spellings; quoted page/study intervals; offline has no remote research'],'teal'),
 ('NUMERIC',130,1195,840,132,['Values and reference metadata','Finite numbers, ordered bounds, supplied units and interval context','Out-of-reference is descriptive; it is not automatically a data error','Evidence of clinical plausibility requires method/population review'],'normal'),
 ('OUTPUT',130,1385,840,106,['Write separate checks, credibility and explicit readiness denominators','Priority: error → unclear → suggestion → passed','Readiness is a numeric semantic proxy, not MII certification or clinical approval'],'teal'),
 ('REVIEW',130,1720,840,106,['Clinician adjudication of the entire set','Record approval / rejection / defer / revoke with reviewer, evidence and time','Apply approved site transformations in ETL; validate actual FHIR resources'],'normal'),
 ('REGISTRY',130,1890,840,106,['Versioned source and profile scope for future mappings','Recheck target code / unit / factor and each reference bound on reuse','Changed context / catalogue / profile or revocation prevents automatic reuse'],'teal')], [
 ('INPUT','SOURCE','M550 126 V182','',False),('SOURCE','LOCAL','M550 291 V315 H270 V342','A',False),('SOURCE','API','M550 291 V315 H830 V342','B',False),
 ('LOCAL','LOOKUP','M270 502 V535 H400 V562','',False),('API','LOOKUP','M830 502 V535 H700 V562','',False),
 ('LOOKUP','INVALID','M550 647 V675 H185 V697','',False),('LOOKUP','KNOWN','M550 647 V697','',False),('LOOKUP','UNKNOWN','M550 647 V675 H915 V697','',False),
 ('INVALID','FIT','M185 806 V835 H550 V862','',False),('KNOWN','FIT','M550 806 V862','',False),('UNKNOWN','FIT','M915 806 V835 H550 V862','',False),
 ('FIT','MATCH','M550 947 V975 H185 V997','',False),('FIT','CONVERT','M550 947 V997','',False),('FIT','AMBIG','M550 947 V975 H915 V997','',False),
 ('MATCH','NUMERIC','M185 1132 V1165 H550 V1192','',False),('CONVERT','NUMERIC','M550 1132 V1192','',False),('AMBIG','NUMERIC','M915 1132 V1165 H550 V1192','',False),
 ('NUMERIC','OUTPUT','M550 1327 V1382','',False),('OUTPUT','RESEARCH','M550 1491 V1547','optional evidence pass',True),('OUTPUT','REVIEW','M130 1440 H70 V1775 H127','direct review',True),('RESEARCH','REVIEW','M550 1656 V1717','',False),('REVIEW','REGISTRY','M550 1826 V1887','',False),('REGISTRY','LOOKUP','M130 1940 H5 V610 H127','reviewed reuse',True)])

write('batch-flow','Source exports → guarded aggregation → review artifacts',760,[
 ('LIS',20,20,250,106,['LIS export','CSV / approved DB view','Vendor extraction upstream'],'normal'),
 ('HUB',290,20,250,106,['CDS Hub export','Mapped scalar DB / CSV','Read-only optional DB input'],'normal'),
 ('FHIR',560,20,250,106,['FHIR R4 export','Bundle / NDJSON files','TORCH / CQL upstream'],'normal'),
 ('CSV',830,20,250,106,['Export contract','Individual or distinct','Real count only if known'],'normal'),
 ('GROUP',130,225,840,132,['One selected semantic representation','code + code_system + value/bounds + unit + unit_system + full reference set','DuckDB: distinct guarded sets; optional items with ID/path','Keep status, versions, comparator, specimen/method and interval qualifiers'],'normal'),
 ('CHECK',130,425,840,106,['Exact checks and optional evidence candidates','Real configured terminology → code-unit fit → numbers/reference consistency','Reviewable coding/quantity proposals with independently supported transformations'],'teal'),
 ('OUT',130,595,840,132,['Local CSV report + HTML + summary + evidence + audit log','Original sets + eligible distributions + separate checks + readiness ratio','Compare matched LIS / FHIR / CDS Hub exports; physician review before mapping','No upload, message interception or aggregate-to-patient resource generation'],'teal')],[
 ('LIS','GROUP','M145 126 V180 H550 V222','',False),('HUB','GROUP','M415 126 V180 H550 V222','',False),('FHIR','GROUP','M685 126 V180 H550 V222','',False),('CSV','GROUP','M955 126 V180 H550 V222','',False),('GROUP','CHECK','M550 357 V422','',False),('CHECK','OUT','M550 531 V592','',False)])
