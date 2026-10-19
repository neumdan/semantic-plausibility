"""One field per row, with separate source and proposal states. Offline rendering only."""
from dataclasses import dataclass, field
from html import escape
from urllib.parse import urlsplit

from .report_sources import source_html


CSS = """
*{box-sizing:border-box}body{font:14px/1.5 system-ui,sans-serif;color:#24343e;background:#f6f7f8;margin:0}
main{max-width:1460px;margin:auto;padding:24px}h1{font-size:24px;margin:0 0 6px}h2{font-size:17px;margin:0 0 6px}h3{font-size:14px}p{margin:5px 0 10px}a{color:#116a64}small,.muted,.source-meta{font-size:12px;color:#53636d}
section.item,.run-details{background:white;border:1px solid #dbe1e5;border-radius:6px;padding:16px;margin:20px 0}
.item-heading{display:flex;align-items:baseline;justify-content:space-between;gap:16px}.item-heading p{font-size:12px;color:#53636d;flex-shrink:0}
.scroll{overflow:auto}table{width:100%;border-collapse:collapse;text-align:left;font-size:13px}td,th{vertical-align:top;padding:10px;border-bottom:1px solid #e3e7ea;overflow-wrap:anywhere}thead th{font-size:12px;background:#f1f3f5;color:#43535d;font-weight:600}
.item-table{table-layout:fixed;min-width:980px}.item-table th:nth-child(1){width:12%}.item-table th:nth-child(2){width:19%}.item-table th:nth-child(3){width:27%}.item-table th:nth-child(4){width:24%}.item-table th:nth-child(5),.item-table th:nth-child(6){width:9%}
.item-table tbody th{font-weight:600}.input-value,.proposed-value{white-space:pre-line}.finding{margin:0 0 5px}.finding:last-child{margin-bottom:0}.citation{font-size:11px;margin-left:4px;white-space:nowrap}.action{font-weight:600;display:block;margin-bottom:3px}
.badge{display:inline-block;font-size:11px;line-height:1.4;font-weight:600;border-radius:4px;padding:4px 6px;border:1px solid transparent}.badge.green{color:#205a3c;background:#edf6ee;border-color:#cbdccb}.badge.yellow{color:#765210;background:#fff6dd;border-color:#e7d8b0}.badge.red{color:#963e36;background:#fceeed;border-color:#eacac6}.badge.grey{color:#58636b;background:#f0f2f3;border-color:#dce1e4}
.badge.conditional{color:#245878;background:#edf5fb;border-color:#bcd7e8}.review-reason,.condition{display:block;margin-top:5px;font-size:12px}.assessment-table{margin-top:16px}.assessment-table caption{text-align:left;font-weight:600;padding:6px 0}
.legend{font-size:12px;color:#53636d;margin:12px 0}.legend .badge{margin-right:4px}.kpis{display:flex;flex-wrap:wrap;gap:8px 20px;padding:12px 0;border-block:1px solid #dbe1e5;margin:14px 0}.kpis span{font-size:13px}
summary{cursor:pointer;font-weight:600}summary:focus-visible,a:focus-visible{outline:2px solid #116a64;outline-offset:3px}.set-details{margin-top:12px}.set-details>summary{color:#116a64;font-size:12px}.set-details[open]{padding-top:8px}.set-details details{margin:12px 0}.source-list li{padding:4px 0}.source-list .source-meta{display:block}.field-reasons{font-size:13px}.field-reasons dt{font-weight:600;margin-top:8px}.field-reasons dd{margin:0}.run-details>section{border-top:1px solid #dbe1e5;padding:16px 0;margin-top:16px}
.source-meta{overflow-wrap:anywhere}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px;background:#f6f7f8;padding:10px}code{font-size:12px}.note,.approval{border-left:3px solid #7b9894;padding:8px 12px;background:#f1f6f5}.warning,.blocked{color:#963e36}.warning{border-left:3px solid #963e36;padding:8px 12px;background:#fceeed}blockquote{margin:8px 0;padding-left:12px;border-left:2px solid #7b9894}.recommendation{padding:8px 0;border-bottom:1px solid #dbe1e5}
@media(max-width:700px){main{padding:12px}section.item{padding:10px}.item-heading{display:block}}
@media print{main{padding:0}.scroll{overflow:visible}.item-table{min-width:0}.item-table th,.item-table td{padding:5px}.badge{border-color:currentColor}}
"""


@dataclass
class Field:
    name: str
    value: str
    findings: list[str]
    action: str
    recommendation: str
    source_state: str = "yellow"
    proposal_state: str = "red"
    reason: str = ""
    sources: list[dict] = field(default_factory=list)
    condition: str = ""
    support_level: str = ""


def display(value):
    return escape(str(value)) if value not in (None, "") else "Not supplied"


def badge(state, *, proposal=False):
    labels = ({"green": "Keep", "conditional": "Confirm condition", "yellow": "Needs review", "red": "No proposal", "grey": "N/A"}
        if proposal else {"green": "Checked", "conditional": "Conditional match", "yellow": "Not established", "red": "Problem", "grey": "N/A"})
    if state not in labels:
        raise ValueError("Unknown report field state")
    return '<span class="badge ' + state + '">' + labels[state] + '</span>'


def legend_html():
    return ('<div class="legend"><p><strong>Source:</strong> ' + badge("green") + ' checked · '
        + badge("conditional") + ' supported under the stated system/context · '
        + badge("yellow") + ' unresolved · ' + badge("red") + ' issue found</p>'
        + '<p><strong>Recommendation</strong> · ' + badge("green", proposal=True) + ' keep within the stated check · '
        + badge("conditional", proposal=True) + ' confirm metadata · '
        + badge("yellow", proposal=True) + ' verify proposal · ' + badge("red", proposal=True)
        + ' clarify the source; no supported replacement · ' + badge("grey", proposal=True) + ' not applicable</p>'
        + '<p>Field checks are separate. Green does not mean that the whole set is clinically approved. No source values are changed.</p></div>')


def citations(fields):
    records, seen = [], set()
    for item in fields:
        for record in item.sources:
            if not isinstance(record, dict) or not record.get("source"):
                continue
            key = (str(record["source"]), str(record.get("version") or ""))
            if key not in seen:
                records.append(record)
                seen.add(key)
    return records


def field_table_html(fields, *, prefix):
    records = citations(fields)
    indices = {(str(r["source"]), str(r.get("version") or "")): i for i, r in enumerate(records, 1)}
    body = []
    for item in fields:
        numbers = sorted({indices[(str(r["source"]), str(r.get("version") or ""))] for r in item.sources if r.get("source")})
        refs = ''.join(citation_link(records[n - 1], n, prefix) for n in numbers)
        findings = ''.join('<p class="finding">' + display(f) + '</p>' for f in item.findings)
        condition = '<span class="condition">' + display(item.condition) + '</span>' if item.condition else ''
        reason = '<small class="review-reason">Why: ' + display(item.reason) + '</small>' if item.reason else ''
        body.append('<tr><th scope="row">' + display(item.name) + '</th><td class="input-value">' + display(item.value)
            + '</td><td>' + findings + condition + refs + '</td><td class="proposed-value"><span class="action">' + display(item.action)
            + '</span>' + display(item.recommendation) + reason + '</td><td>' + badge(item.source_state) + '</td><td>'
            + badge(item.proposal_state, proposal=True) + '</td></tr>')
    return ('<div class="scroll"><table class="item-table"><thead><tr>'
        + ''.join('<th scope="col">' + label + '</th>' for label in ("Data field", "Input value", "Method + finding", "Recommendation", "Source", "→ Proposal"))
        + '</tr></thead><tbody>' + ''.join(body) + '</tbody></table></div>')


def citation_link(record, number, prefix):
    """External sources open directly; local/source assertions point to the details."""
    source = str(record['source'])
    try:
        url = urlsplit(source)
        external = url.scheme in {'https', 'http'} and url.hostname and not (url.username or url.password)
    except ValueError:
        external = False
    href = source if external else '#' + prefix + '-source-' + str(number)
    return ('<a class="citation" href="' + escape(href, quote=True) + '" title="' + escape(source, quote=True)
        + '"' + (' target="_blank" rel="noopener noreferrer"' if external else '') + '>[' + str(number) + ']</a>')


def field_details_html(fields, *, prefix):
    sources = ''.join('<li id="' + escape(prefix, quote=True) + '-source-' + str(i) + '">'
        + source_html(r['source'], title=r.get('title') or r.get('source_title') or r['source'], version=r.get('version') or '',
            excerpt=r.get('excerpt') or '', sha256=r.get('sha256') or '', retrieved_at=r.get('retrieved_at') or '') + '</li>'
        for i, r in enumerate(citations(fields), 1))
    reasons = ''.join('<dt>' + display(item.name) + '</dt><dd>' + display(item.reason) + '</dd>' for item in fields if item.reason)
    return ('<h3>Sources used in this table</h3><ol class="source-list">' + sources + '</ol>' if sources else
        '<p>No external source established a field-specific result. See lookup attempts below.</p>') + '<h3>Scope and reasons by field</h3><dl class="field-reasons">' + reasons + '</dl>'
