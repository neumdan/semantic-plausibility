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


write('architecture', 'Laboratory and medication review architecture', 1250, [
 ('SCALAR',20,20,330,132,['Scalar exports','LIS / CDS Hub CSV or DB view','Individual rows or distinct sets','Explicit columns and real counts'],'normal'),
 ('FHIR',385,20,330,132,['Complete FHIR exports','Bundle / resource array / NDJSON','or CSV resource_json cells','TORCH / CQL selection upstream'],'normal'),
 ('CONFIG',750,20,330,132,['One TOML + secrets','Input, profile, real lookups, output','Local model and research budgets','Credentials in .env / secret files'],'normal'),
 ('VALIDATE',385,220,330,132,['2 · Optional FHIR validation','Full resources → local Matchbox','HL7 validator + pinned MII guide','Outcome stays separate from fit'],'teal'),
 ('NORMAL',385,420,330,132,['Standardise and group','Lab set or complete medication dose','DuckDB distinct groups; lab statistics','or items with original ID / locator'],'normal'),
 ('SOURCES',20,620,330,157,['Real terminology evidence','Local BfArM / site catalogues','UCUM / LOINC / medication APIs','Primary / source / optional roles','Retain editions, hashes, failures'],'normal'),
 ('CHECK',385,620,330,157,['1 · Deterministic check','Source identity + target profile rules','Code / name / unit fit and numbers','Lab ranges / medication dose checks','Supported unit transformations'],'teal'),
 ('MODEL',750,420,330,132,['Optional local model','Ollama: ministral-3:3b starter','or local compatible endpoint','Guarded JSON; sources required'],'normal'),
 ('AI',750,620,330,157,['3 · Laboratory terminology AI','Checked report → candidates','Real names, codes and unit lists','LOINC pages; no study queries','Exact rechecks; may abstain'],'teal'),
 ('OUTPUT',385,850,330,157,['CSV + HTML review report','Source → linked data → proposal','Checks and citations beside each set','Summary + evidence + audit hashes','Input remains unchanged'],'teal'),
 ('STUDIES',750,850,330,157,['4 · Laboratory AI + studies','Original report → same AI pass','Plus Europe PMC / PubMed','Quoted reported values and bounds','Independent sourced dossier'],'teal'),
 ('REGISTRY',20,1080,330,132,['Laboratory review registry','Approved / rejected / revoked','Source + profile + context scope','Reverify target and factor on reuse'],'normal'),
 ('EXPERT',385,1080,330,132,['Clinician review','Decide semantic correctness','Apply approved mappings in ETL','Validate resulting FHIR resources'],'teal'),
 ('MII',20,850,330,132,['Complementary MII checks','FHIR conformance: validator','Availability / completeness: FDE','This tool: semantic-set review'],'normal')], [
 ('SCALAR','NORMAL','M185 152 V486 H382','scalar path',False),
 ('FHIR','VALIDATE','M550 152 V217','if enabled',False),
 ('FHIR','NORMAL','M715 125 H732 V486 H718','otherwise',True),
 ('CONFIG','VALIDATE','M915 152 V285 H718','profile + settings',True),
 ('VALIDATE','NORMAL','M550 352 V417','outcomes retained',False),
 ('NORMAL','CHECK','M550 552 V617','',False),
 ('SOURCES','CHECK','M350 693 H382','lookups',False),
 ('CHECK','OUTPUT','M550 777 V847','',False),
 ('MODEL','AI','M915 552 V617','local inference',True),
 ('OUTPUT','AI','M715 905 H737 V715 H747','scope=lookup',True),
 ('OUTPUT','STUDIES','M715 970 H747','scope=studies',True),
 ('OUTPUT','EXPERT','M550 1007 V1077','',False),
 ('AI','EXPERT','M1080 695 H1090 V1040 H650 V1077','candidates',True),
 ('STUDIES','EXPERT','M915 1007 V1040 H650 V1077','evidence',True),
 ('EXPERT','REGISTRY','M385 1145 H353','review-import',False),
 ('REGISTRY','CHECK','M185 1080 V1030 H370 V750 H382','scoped reuse',True)], [])

write('decision-flow', 'Check the source set, select evidence scope, review the proposal', 1680, [
 ('CONTRACT',130,20,840,106,['1. Check input contract','Observation / MedicationRequest / MedicationAdministration; explicit source mapping','Malformed input fails; preserve every reference interval or dosage instruction'],'normal'),
 ('VALIDATION',130,185,840,106,['2. Formal validation requested?','Full FHIR file or CSV resource_json → local Matchbox/HL7 + pinned guide','Scalar sets → not_applicable; unavailable/invalid validation remains explicit'],'normal'),
 ('LOOKUP',130,350,840,106,['3. Verify the declared code/system and unit/system using configured sources','Local real catalogue and/or enabled API; preserve edition and source','Lab: selected profile rules + LOINC axes; medication: supplied coding and dose context'],'normal'),
 ('INVALID',20,515,330,106,['Confirmed invalid','Record deterministic error','Continue other independent checks'],'error'),
 ('VERIFIED',385,515,330,106,['Verified identity / syntax','Check code-unit-context fit','Membership alone is insufficient'],'teal'),
 ('UNKNOWN',750,515,330,106,['Unverified / unavailable','Keep evidence gap visible','Do not call absence invalid'],'normal'),
 ('FIT',130,685,840,132,['4. Assess unit fit and transformation','Lab: documented code–unit fit; verified conversion → factor for value and bounds','Medication: dose × source strength → conditional conversion with sources and factor','Missing evidence / ambiguous strength or count units → expert review, no forced map'],'normal'),
 ('NUMERIC',130,880,840,106,['5. Check quantities and context; write the checked CSV','Lab: numeric values and reference bounds; medication: dose, timing, range and units','Infusion rate × duration is conditional unless constant uninterrupted delivery is asserted'],'teal'),
 ('NO_AI',20,1050,330,132,['Modes 1 / 2: lab or medication','Deterministic findings + sources','Optional full FHIR outcome','No model calls'],'normal'),
 ('AI',385,1050,330,132,['Lab mode 3: scope=lookup','Local AI suggests code/name/unit','Real candidate retrieval + recheck','No literature retrieval'],'teal'),
 ('EVIDENCE',750,1050,330,132,['Lab mode 4: scope=studies','Same AI + configured study APIs','Quote values / units / intervals','Keep gaps and failures visible'],'teal'),
 ('REVIEW',130,1255,840,132,['6. Review the whole proposal with its evidence','Original set → linked code/analyte/name/units → proposal with transformations','Physician records decision, reviewer, time and rationale; source remains unchanged','Numeric readiness, FHIR conformance and clinical correctness stay distinct'],'normal'),
 ('REUSE',130,1460,840,157,['7. Optional laboratory mapping reuse','Import clinician decisions; recheck target code, unit, factor and reference bounds','Source, catalogue, profile or context changes require renewed review','Apply approved transformations in institutional ETL and validate the resulting FHIR','No automated resource rewrite or model retraining'],'teal')], [
 ('CONTRACT','VALIDATION','M550 126 V182','',False),('VALIDATION','LOOKUP','M550 291 V347','',False),
 ('LOOKUP','INVALID','M550 456 V485 H185 V512','',False),('LOOKUP','VERIFIED','M550 456 V512','',False),('LOOKUP','UNKNOWN','M550 456 V485 H915 V512','',False),
 ('INVALID','FIT','M185 621 V650 H450 V682','',False),('VERIFIED','FIT','M550 621 V682','',False),('UNKNOWN','FIT','M915 621 V650 H650 V682','',False),
 ('FIT','NUMERIC','M550 817 V877','',False),
 ('NUMERIC','NO_AI','M550 986 V1018 H185 V1047','',False),('NUMERIC','AI','M550 986 V1047','explicit second pass',True),('NUMERIC','EVIDENCE','M550 986 V1018 H915 V1047','explicit second pass',True),
 ('NO_AI','REVIEW','M185 1182 V1220 H450 V1252','',False),('AI','REVIEW','M550 1182 V1252','',False),('EVIDENCE','REVIEW','M915 1182 V1220 H650 V1252','',False),
 ('REVIEW','REUSE','M550 1387 V1457','',False)])

write('batch-flow', 'Input representations and domain data models', 1080, [
 ('CSV',20,20,330,132,['CSV / LIS / CDS Hub DB','Individual or distinct source rows','Column map + actual source counts','Read-only site-specific view'],'normal'),
 ('FHIR',385,20,330,132,['Complete FHIR documents','Bundle / array / NDJSON / JSON','or resource_json in a CSV cell','Resolve exported Medication locally'],'normal'),
 ('CONFIG',750,20,330,132,['Explicit domain selection','Observation / MedicationRequest','or MedicationAdministration','TOML profile, lookups and secrets'],'normal'),
 ('LAB',20,245,515,182,['Laboratory semantic set','code + system/edition + analyte/name','value or observed bounds + unit/system','Every reference range and its qualifiers','Specimen, method, comparator and modifiers'],'normal'),
 ('MED',565,245,515,182,['Medication semantic set','Product + system/edition + form/ingredients','Strength + all dose/unit, route and timing data','Status/intent + actual duration + modifiers','No averaging different doses or regimens'],'normal'),
 ('GROUP',130,515,840,132,['Choose output representation','distinct: complete guarded sets + known counts; laboratory numeric distributions','items: individual inputs with original resource/database ID and source locator','Missing counts and unresolved references remain explicit; no missing context is invented'],'normal'),
 ('CHECK',130,710,840,106,['Domain checks → CSV + HTML with sources','Lab: lookups, code–unit–range fit; optional local AI and literature evidence','Medication: terminology + numeric/dimensional dose checks and conditional calculations'],'teal'),
 ('REVIEW',130,885,840,132,['Clinician review of the complete set','Each source set → linked definitions → explanations, transformations and sources','FHIR validation is separate and applies only to full resources; input stays unchanged','Clinical dose safety and medication AI/evidence research are not implemented'],'normal')], [
 ('CSV','LAB','M185 152 V242','',False),('CSV','MED','M185 152 V198 H730 V242','selected type',False),
 ('FHIR','LAB','M550 152 V220 H450 V242','',False),('FHIR','MED','M550 152 V220 H650 V242','',False),
 ('CONFIG','MED','M915 152 V242','',True),
 ('LAB','GROUP','M280 427 V470 H450 V512','',False),('MED','GROUP','M825 427 V470 H650 V512','',False),
 ('GROUP','CHECK','M550 647 V707','',False),('CHECK','REVIEW','M550 816 V882','',False)])
