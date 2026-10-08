# Adapted for the standalone semantic-plausibility project (2026-10-02).
"""Official terminology and literature adapters for observation_quality.

Network calls are opt-in at the CLI boundary. Only terminology context is sent to
the model and PubMed; patient values and identifiers are never sent by these adapters.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from pathlib import Path
from threading import Lock
from typing import Any
from urllib.parse import quote, urlparse, urlsplit, urlunsplit
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener
from urllib.error import HTTPError
import base64
import json
from json import dumps as _json_dumps
import os
import re
import time
import xml.etree.ElementTree as ET

from .catalog_evidence import ObservationDefinitionCatalog
from .observation_quality import LoincTerm, ResearchCandidate, UnitEvidence
from .model_config import ModelEndpoint, allowed_endpoint, select_model
from .model_schemas import explicit_token as _explicit_token
from .model_schemas import response_schema, validate_grounding, validate_response
from . import research_prompts as prompts


class _Response:
    def __init__(self, url: str, body: bytes, status_code: int):
        parsed = urlsplit(url)
        self.url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
        self.text = body.decode("utf-8", errors="replace")
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code} from {self.url}")

    def json(self) -> dict[str, Any]:
        return json.loads(self.text)


class _HttpSession:
    def get(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        auth: tuple[str, str] | None = None,
        timeout: int = 25,
    ) -> _Response:
        full_url = url + ("?" + urlencode(params) if params else "")
        actual_headers = dict(headers or {})
        if auth:
            token = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode("ascii")
            actual_headers["Authorization"] = f"Basic {token}"
        request = Request(full_url, headers=actual_headers)
        try:
            with build_opener(_NoModelRedirects()).open(request, timeout=timeout) as answer:
                body = answer.read(4 * 1024 * 1024 + 1)
                if len(body) > 4 * 1024 * 1024:
                    raise ValueError("Lookup response exceeds the four-megabyte budget")
                return _Response(full_url, body, answer.status)
        except HTTPError as exc:
            try:
                return _Response(full_url, exc.read(4 * 1024 * 1024), exc.code)
            finally:
                exc.close()

    def post(
        self, url: str, *, json: dict[str, Any], timeout: int = 120, headers: dict[str, str] | None = None
    ) -> _Response:
        body = _json_dumps(json).encode("utf-8")
        request = Request(
            url, data=body, headers={"Content-Type": "application/json", **(headers or {})}, method="POST"
        )
        try:
            with build_opener(_NoModelRedirects()).open(request, timeout=timeout) as answer:
                body = answer.read(64001)
                if len(body) > 64000:
                    raise ValueError("Model server response exceeds the 64000-byte budget")
                return _Response(url, body, answer.status)
        except HTTPError as exc:
            try:
                return _Response(url, exc.read(64000), exc.code)
            finally:
                exc.close()


class _NoModelRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward model credentials or supplied evidence to another URL.
        return None


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Model returned duplicate JSON fields")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError("Model returned a non-finite JSON number")


class NlmUcumClient:
    def __init__(
        self,
        base_url: str = "https://ucum.nlm.nih.gov/ucum-service/v1",
        session: Any | None = None,
        timeout: int = 20,
    ):
        self.base_url = base_url.rstrip("/")
        self.source = self.base_url
        self.session = session or _HttpSession()
        self.timeout = timeout
        self._valid: dict[str, bool] = {}
        self._factors: dict[tuple[str, str], Decimal | None] = {}

    def is_valid(self, unit: str) -> bool:
        if unit in self._valid:
            return self._valid[unit]
        url = f"{self.base_url}/isValidUCUM/{quote(unit, safe='/')}"
        response = self.session.get(url, timeout=self.timeout)
        response.raise_for_status()
        answer = response.text.strip().lower()
        if answer not in {"true", "false"}:
            raise ValueError(f"Unexpected UCUM response for {unit!r}")
        self._valid[unit] = answer == "true"
        return self._valid[unit]

    def factor(self, source: str, target: str) -> Decimal | None:
        if source == target:
            return Decimal("1")
        key = (source, target)
        if key in self._factors:
            return self._factors[key]

        def transform(amount: int) -> Decimal | None:
            url = (
                f"{self.base_url}/ucumtransform/{amount}/from/{quote(source, safe='/')}"
                f"/to/{quote(target, safe='/')}"
            )
            response = self.session.get(url, headers={"Accept": "application/xml"}, timeout=self.timeout)
            if response.status_code in {400, 404, 422}:
                return None
            response.raise_for_status()
            try:
                root = ET.fromstring(response.text)
                if root.find(".//Error") is not None or root.find(".//ERROR") is not None:
                    return None
                if (root.findtext(".//Response") or "").strip().startswith("ERROR:"):
                    return None
                value = Decimal(str(root.findtext(".//ResultQuantity")))
                if root.findtext(".//TargetUnit") != target or not value.is_finite():
                    raise ValueError("UCUM conversion response did not match target")
                return value
            except (ET.ParseError, InvalidOperation, TypeError) as exc:
                raise ValueError("Unparseable UCUM conversion response") from exc

        # The kernel applies factors as value * factor. Celsius/Kelvin and
        # other transformations with an offset cannot use that interface.
        factor = transform(1)
        if factor is None or transform(0) != 0 or factor <= 0:
            self._factors[key] = None
            return None
        self._factors[key] = factor
        return factor


class LoincFhirClient:
    def __init__(
        self,
        base_url: str = "https://fhir.loinc.org",
        username: str = "",
        password: str = "",
        session: Any | None = None,
        timeout: int = 25,
    ):
        self.base_url = base_url.rstrip("/")
        self.auth = (username, password) if username and password else None
        self.session = session or _HttpSession()
        self.timeout = timeout
        self._cache: dict[tuple[str, str], LoincTerm | None] = {}

    def lookup(self, code: str, version: str = "") -> LoincTerm | None:
        cache_key = (code, version)
        if cache_key in self._cache:
            return self._cache[cache_key]
        url = f"{self.base_url}/CodeSystem/$lookup"
        params = {"system": "http://loinc.org", "code": code}
        if version:
            params["version"] = version
        response = self.session.get(
            url,
            params=params,
            headers={"Accept": "application/fhir+json"},
            auth=self.auth,
            timeout=self.timeout,
        )
        if response.status_code in {400, 404}:
            self._cache[cache_key] = None
            return None
        response.raise_for_status()
        payload = response.json()
        if (
            not isinstance(payload, dict)
            or payload.get("resourceType") != "Parameters"
            or not isinstance(payload.get("parameter"), list)
            or not payload["parameter"]
        ):
            raise ValueError("LOINC lookup returned no usable Parameters evidence")
        returned_code = next((entry.get("valueCode") for entry in payload["parameter"]
                              if entry.get("name") == "code"), None)
        returned_system = next((entry.get("valueUri") for entry in payload["parameter"]
                                if entry.get("name") == "system"), None)
        if returned_code and returned_code != code or returned_system and returned_system != "http://loinc.org":
            raise ValueError("LOINC lookup returned another code/system")
        returned_version = next(
            (
                entry.get("valueString", "")
                for entry in payload.get("parameter", [])
                if entry.get("name") == "version"
            ),
            "",
        )
        if version and returned_version != version:
            self._cache[cache_key] = None
            return None
        properties: dict[str, list[str]] = {}
        part_codes: dict[str, str] = {}
        display = ""
        for entry in payload.get("parameter", []):
            if entry.get("name") == "display":
                display = entry.get("valueString") or ""
            if entry.get("name") != "property":
                continue
            parts = {part.get("name"): part for part in entry.get("part", [])}
            key = (parts.get("code") or {}).get("valueCode")
            value_part = parts.get("value") or {}
            part_code = value_part.get("valueCode") or (value_part.get("valueCoding") or {}).get("code")
            if key and isinstance(part_code, str) and part_code.startswith("LP"):
                # Some real servers expose LOINC part identifiers rather than axis text.
                # Preserve them; do not compare an unresolved part ID with a specimen label.
                part_codes[key] = part_code
            value = (
                value_part.get("valueString") or (value_part.get("valueCoding") or {}).get("display") or ""
            )
            if key and value:
                properties.setdefault(key, []).append(value)

        def one(key: str) -> str:
            return (properties.get(key) or [""])[0]

        def many(key: str) -> tuple[str, ...]:
            values: list[str] = []
            for field in properties.get(key, []):
                values.extend(part.strip() for part in field.split(";") if part.strip())
            return tuple(dict.fromkeys(values))

        if not display and not one("LONG_COMMON_NAME") and not properties:
            raise ValueError("LOINC lookup contains no concept evidence")
        term = LoincTerm(
            code=code,
            display=display or one("LONG_COMMON_NAME"),
            component=one("COMPONENT"),
            property=one("PROPERTY"),
            system=one("SYSTEM"),
            scale=one("SCALE_TYP"),
            method=one("METHOD_TYP"),
            example_ucum_units=many("EXAMPLE_UCUM_UNITS"),
            version=returned_version,
            time_aspect=one("TIME_ASP"),
            example_units=many("EXAMPLE_UNITS"),
            source=response.url,
            part_codes=part_codes,
        )
        self._cache[cache_key] = term
        return term


class _PlainText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self.skip += 1
        if tag in {"tr", "p", "h1", "h2", "h3", "li"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self.skip:
            self.skip -= 1
        if tag in {"td", "th"}:
            self.parts.append("\t")

    def handle_data(self, data: str) -> None:
        if not self.skip:
            self.parts.append(data)

    def text(self) -> str:
        return re.sub(r"[ \t]+", " ", "".join(self.parts))


class OpenAICompatibleJsonClient:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:11434/v1",
        model: str = "ministral-3:3b",
        session: Any | None = None,
        timeout: int = 120,
        api_key: str = "",
        temperature: float = 0.0,
        max_tokens: int = 1024,
        reasoning_effort: str = "",
        response_mode: str = "json_schema",
        enable_thinking: bool | None = None,
    ):
        parsed = urlparse(base_url)
        if not allowed_endpoint(base_url) or parsed.query or parsed.fragment:
            raise ValueError("Model endpoint needs HTTPS or approved local HTTP, without query or fragment")
        settings = ModelEndpoint("client", base_url, model, temperature=temperature, max_tokens=max_tokens,
                                 timeout_seconds=timeout, reasoning_effort=reasoning_effort, response_mode=response_mode,
                                 enable_thinking=enable_thinking)
        root = base_url.rstrip("/")
        self.base_url = root if root.endswith("/v1") else f"{root}/v1"
        self.model = model
        self.session = session or _HttpSession()
        self.timeout = timeout
        self.api_key = api_key
        self.temperature = settings.temperature
        self.max_tokens = settings.max_tokens
        self.reasoning_effort = settings.reasoning_effort
        self.response_mode = settings.response_mode
        self.enable_thinking = settings.enable_thinking

    def json(self, instruction: str, evidence: dict[str, Any]) -> dict[str, Any]:
        schema = response_schema(instruction)
        supplied = json.dumps({"instruction": instruction, "evidence": evidence}, ensure_ascii=False)
        if len(supplied) > 16000:
            raise ValueError("Model evidence exceeds the 16000-character input budget; split the source")
        payload = {
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": False,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "evidence_extraction", "strict": True, "schema": schema},
            },
            "messages": [
                {"role": "system", "content": prompts.SYSTEM + " Required JSON schema: " + json.dumps(schema)},
                {
                    "role": "user",
                    "content": supplied,
                },
            ],
        }
        if self.response_mode == "json_object":
            payload["response_format"] = {"type": "json_object"}
        if self.reasoning_effort:
            payload["reasoning_effort"] = self.reasoning_effort
        if self.enable_thinking is not None:
            payload["chat_template_kwargs"] = {"enable_thinking": self.enable_thinking}
        extra = {"headers": {"Authorization": f"Bearer {self.api_key}"}} if self.api_key else {}
        response = self.session.post(
            f"{self.base_url}/chat/completions", json=payload, timeout=self.timeout, **extra
        )
        response.raise_for_status()
        if getattr(response, "status_code", 200) != 200:
            raise ValueError("Model endpoint returned a redirect or unexpected HTTP status")
        try:
            choice = response.json()["choices"][0]
            message = choice["message"]
            content = message.get("content")
        except (KeyError, IndexError, TypeError):
            raise ValueError("Model server returned no chat completion") from None
        if choice.get("finish_reason") not in {None, "stop"} or message.get("refusal"):
            raise ValueError("Model response was incomplete, refused or requested a tool")
        if not isinstance(content, str) or len(content) > 64000:
            raise ValueError("Model returned no bounded JSON text")
        # Some compatible servers wrap an otherwise structured reply in one
        # Markdown block even when a schema was requested. Normalize this one
        # envelope only; prose, multiple blocks and schema violations still fail.
        block = re.fullmatch(r"\s*```(?:json)?[ \t]*\r?\n(.*?)\r?\n```\s*", content, re.S | re.I)
        if block:
            content = block.group(1)
        result = json.loads(content, object_pairs_hook=_unique_json_object, parse_constant=_reject_json_constant)
        validate_response(result, schema)
        validate_grounding(instruction, result, evidence)
        return result


GptOssClient = OpenAICompatibleJsonClient  # compatibility with earlier imports


class PublicEvidenceClient:
    """LOINC website fallback and bounded PubMed discovery/extraction."""

    def __init__(
        self,
        llm: OpenAICompatibleJsonClient | None = None,
        session: Any | None = None,
        timeout: int = 25,
        pubmed_retmax: int = 3,
        ncbi_api_key: str = "",
        ncbi_tool: str = "",
        ncbi_email: str = "",
        ncbi_min_interval: float | None = None,
        observation_definitions_file: str = "",
        scope: str = "studies",
        loinc_website_base_url: str = "https://loinc.org",
        pubmed_base_url: str = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils",
    ):
        if scope not in {"lookup", "studies"}:
            raise ValueError("Evidence scope must be lookup or studies")
        self.scope = scope
        for endpoint in (loinc_website_base_url, pubmed_base_url):
            if not allowed_endpoint(endpoint) or urlsplit(endpoint).query or urlsplit(endpoint).fragment:
                raise ValueError("Research APIs require HTTPS or approved local HTTP endpoints")
        self.loinc_website_base_url = loinc_website_base_url.rstrip("/")
        self.pubmed_base_url = pubmed_base_url.rstrip("/")
        self.llm = llm
        self.session = session or _HttpSession()
        self.timeout = timeout
        self.pubmed_retmax = pubmed_retmax
        self.ncbi_api_key = ncbi_api_key
        self.ncbi_tool = ncbi_tool
        self.ncbi_email = ncbi_email
        # E-utilities permits 3 requests/second without a key and 10 with one.
        # Test sessions are not delayed unless a rate is explicitly requested.
        self.ncbi_min_interval = (
            ncbi_min_interval
            if ncbi_min_interval is not None
            else (0.11 if ncbi_api_key else 0.34)
            if session is None
            else 0.0
        )
        self._ncbi_lock = Lock()
        self._last_ncbi_request = 0.0
        self._website_cache: dict[str, list[UnitEvidence]] = {}
        self._website_range_cache: dict[str, list[ResearchCandidate]] = {}
        self._website_text_cache: dict[str, str] = {}
        self._study_cache: dict[str, list[ResearchCandidate]] = {}
        self.catalog = (
            ObservationDefinitionCatalog(Path(observation_definitions_file))
            if observation_definitions_file
            else None
        )

    def catalog_evidence(self, code: str) -> tuple[list[UnitEvidence], list[ResearchCandidate]]:
        return self.catalog.for_loinc(code) if self.catalog else ([], [])

    def _ncbi_get(self, endpoint: str, params: dict[str, Any]) -> Any:
        if self.ncbi_api_key:
            params = {**params, "api_key": self.ncbi_api_key}
        if self.ncbi_tool:
            params = {**params, "tool": self.ncbi_tool}
        if self.ncbi_email:
            params = {**params, "email": self.ncbi_email}
        with self._ncbi_lock:
            remaining = self.ncbi_min_interval - (time.monotonic() - self._last_ncbi_request)
            if remaining > 0:
                time.sleep(remaining)
            self._last_ncbi_request = time.monotonic()
            response = self.session.get(
                f"{self.pubmed_base_url}/{endpoint}",
                params=params,
                timeout=self.timeout,
            )
        if response.status_code >= 400:
            # The request URL may contain the API key; do not put it in errors.
            raise RuntimeError(f"NCBI E-utilities HTTP {response.status_code}")
        return response

    def website_units(self, code: str, term: LoincTerm) -> list[UnitEvidence]:
        if self.llm is None:
            return []
        if code in self._website_cache:
            return list(self._website_cache[code])
        url = f"{self.loinc_website_base_url}/{quote(code)}/"
        text = self._website_text(code)
        marker = text.find("Example Units")
        if marker < 0:
            return []
        snippet = text[marker : marker + 4500]
        answer = self.llm.json(
            prompts.LOINC_UNITS,
            {"loinc_code": code, "page_url": url, "page_excerpt": snippet},
        )
        result: list[UnitEvidence] = []
        for row in answer.get("units", []):
            unit = str(row.get("unit") or "").strip()
            excerpt = str(row.get("excerpt") or "").strip()
            if unit and excerpt and _explicit_token(unit, excerpt) and excerpt in snippet:
                result.append(UnitEvidence(unit, "website_example", url, excerpt[:300]))
        self._website_cache[code] = result[:15]
        return list(self._website_cache[code])

    def _website_text(self, code: str) -> str:
        if code not in self._website_text_cache:
            response = self.session.get(f"{self.loinc_website_base_url}/{quote(code)}/", timeout=self.timeout)
            response.raise_for_status()
            parser = _PlainText()
            parser.feed(response.text)
            self._website_text_cache[code] = parser.text()
        return self._website_text_cache[code]

    def website_ranges(self, code: str, term: LoincTerm) -> list[ResearchCandidate]:
        """LLM extracts page claims only; each numeric token must occur in source text."""
        if self.llm is None:
            return []
        if code in self._website_range_cache:
            return list(self._website_range_cache[code])
        text = self._website_text(code)
        keywords = re.compile(
            r"reference (?:range|interval)|analytical (?:range|interval)|"
            r"reportable range|critical range|normal range",
            re.I,
        )
        spans = []
        for match in list(keywords.finditer(text))[:8]:
            spans.append(text[max(0, match.start() - 120) : match.end() + 900])
        snippet = "\n...\n".join(spans)[:6000]
        if not snippet:
            self._website_range_cache[code] = []
            return []
        answer = self.llm.json(
            prompts.LOINC_RANGES,
            {"loinc_code": code, "page_url": f"{self.loinc_website_base_url}/{quote(code)}/", "page_excerpt": snippet},
        )
        found = []
        markers = {
            "reference_interval": re.compile(r"reference|normal range", re.I),
            "analytical_interval": re.compile(r"analytical|reportable", re.I),
            "documented_extreme": re.compile(r"critical range|extreme", re.I),
        }
        for row in answer.get("ranges", [])[:8]:
            kind = str(row.get("evidence_type") or "")
            excerpt = str(row.get("excerpt") or "").strip()
            unit = str(row.get("unit") or "").strip()
            low, high = row.get("low"), row.get("high")
            if (
                kind not in markers
                or not excerpt
                or excerpt not in snippet
                or not markers[kind].search(excerpt)
                or not unit
                or not _explicit_token(unit, excerpt)
                or low is None
                and high is None
            ):
                continue
            try:
                bounds = [Decimal(str(x)) for x in (low, high) if x is not None]
                if any(not x.is_finite() for x in bounds) or len(bounds) == 2 and bounds[0] > bounds[1]:
                    continue
            except InvalidOperation, TypeError:
                continue
            if any(not _explicit_token(str(bound), excerpt) for bound in (low, high) if bound is not None):
                continue
            anchored = {}
            for field in ("population", "specimen", "method"):
                label = str(row.get(field) or "").strip()
                anchored[field] = label if label and label.casefold() in excerpt.casefold() else ""
            found.append(
                ResearchCandidate(
                    kind,
                    f"{self.loinc_website_base_url}/{quote(code)}/",
                    low=None if low is None else str(low),
                    high=None if high is None else str(high),
                    unit=unit,
                    population=anchored["population"],
                    specimen=anchored["specimen"],
                    method=anchored["method"],
                    excerpt=excerpt,
                    status="candidate",
                )
            )
        self._website_range_cache[code] = found
        return list(found)

    def suggest_units(
        self, source_unit: str, term: LoincTerm, examples: list[UnitEvidence]
    ) -> list[UnitEvidence]:
        if self.llm is None:
            return []
        answer = self.llm.json(
            prompts.UNIT_CANDIDATES,
            {
                "source_unit": source_unit,
                "loinc_code": term.code,
                "axes": {
                    "component": term.component,
                    "property": term.property,
                    "system": term.system,
                    "scale": term.scale,
                    "method": term.method,
                },
                "documented_examples": [e.__dict__ for e in examples],
            },
        )
        result: list[UnitEvidence] = []
        documented = {e.unit for e in examples}
        for row in answer.get("units", [])[:3]:
            unit = str(row.get("unit") or "").strip()
            if unit in documented:
                result.append(
                    UnitEvidence(unit, "llm_candidate", self.llm.model, str(row.get("reason") or ""))
                )
        return result

    def search_studies(self, term: LoincTerm) -> list[ResearchCandidate]:
        if self.scope == "lookup":
            return []
        if term.code in self._study_cache:
            return list(self._study_cache[term.code])
        # Search terms include no observation value, patient identifier or local interval.
        topic = " ".join(x for x in (term.component or term.display, term.system) if x).strip()
        searches = {
            "reference_interval": f'({topic}) AND ("reference interval" OR "reference range")',
            "analytical_interval": f'({topic}) AND ("analytical measuring interval" OR "reportable range")',
            "documented_extreme": f'({topic}) AND ("extreme value" OR "case report")',
        }
        candidates: list[ResearchCandidate] = []
        seen: set[tuple[str, str]] = set()
        for evidence_type, query in searches.items():
            ids = self._pubmed_ids(query)
            records = self._pubmed_records(ids)
            extracted = self._extract_study_candidates(evidence_type, records, term)
            for item in extracted:
                key = (item.evidence_type, item.source)
                if key not in seen:
                    seen.add(key)
                    candidates.append(item)
        self._study_cache[term.code] = candidates
        return list(candidates)

    def _pubmed_ids(self, query: str) -> list[str]:
        response = self._ncbi_get(
            "esearch.fcgi", {"db": "pubmed", "term": query, "retmode": "json", "retmax": self.pubmed_retmax}
        )
        ids = response.json().get("esearchresult", {}).get("idlist", [])
        return [str(pmid) for pmid in ids if str(pmid).isdigit()]

    def _pubmed_records(self, ids: list[str]) -> list[dict[str, str]]:
        if not ids:
            return []
        response = self._ncbi_get("efetch.fcgi", {"db": "pubmed", "id": ",".join(ids), "retmode": "xml"})
        root = ET.fromstring(response.text)
        records: list[dict[str, str]] = []
        for article in root.findall(".//PubmedArticle"):
            pmid = article.findtext(".//MedlineCitation/PMID") or ""
            title_node = article.find(".//ArticleTitle")
            abstracts = article.findall(".//Abstract/AbstractText")
            title = "".join(title_node.itertext()) if title_node is not None else ""
            abstract = " ".join("".join(node.itertext()) for node in abstracts)
            if pmid in ids:
                records.append(
                    {
                        "pmid": pmid,
                        "title": title,
                        "abstract": abstract,
                        "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                    }
                )
        return records

    def _extract_study_candidates(
        self, evidence_type: str, records: list[dict[str, str]], term: LoincTerm
    ) -> list[ResearchCandidate]:
        if self.llm is None:
            return [
                ResearchCandidate(evidence_type, record["url"], excerpt=record["title"]) for record in records
            ]
        result: list[ResearchCandidate] = []
        for record in records:
            answer = self.llm.json(
                prompts.STUDY_BOUNDS,
                {
                    "evidence_type": evidence_type,
                    "loinc_code": term.code,
                    "pmid": record["pmid"],
                    "title": record["title"],
                    "abstract": record["abstract"][:6000],
                },
            )
            excerpt = str(answer.get("excerpt") or "").strip()
            if excerpt and excerpt not in record["abstract"]:
                continue
            low = answer.get("low")
            high = answer.get("high")
            if low is not None or high is not None:
                try:
                    low_number = Decimal(str(low)) if low is not None else None
                    high_number = Decimal(str(high)) if high is not None else None
                    if (
                        (low_number is not None and not low_number.is_finite())
                        or (high_number is not None and not high_number.is_finite())
                        or (low_number is not None and high_number is not None and low_number > high_number)
                    ):
                        continue
                except InvalidOperation:
                    continue
                if not answer.get("unit") or not excerpt:
                    continue
                if any(
                    not _explicit_token(str(bound), excerpt) for bound in (low, high) if bound is not None
                ):
                    continue
                if not _explicit_token(str(answer["unit"]), excerpt):
                    continue
            anchored = {}
            for field in ("population", "specimen", "method"):
                label = str(answer.get(field) or "").strip()
                anchored[field] = label if label and label.casefold() in record["abstract"].casefold() else ""
            unit = str(answer.get("unit") or "").strip()
            if not excerpt or not unit or not _explicit_token(unit, excerpt):
                unit = ""
            # Search category alone cannot classify a patient value as a
            # reference interval. Require an explicit marker in the excerpt.
            kind = evidence_type
            marker = {
                "reference_interval": r"reference (?:range|interval)|normal range",
                "analytical_interval": r"analytical (?:measuring )?(?:range|interval)|reportable range",
                "documented_extreme": r"case report|extreme|case of",
            }.get(kind, "")
            if (low is not None or high is not None) and marker and not re.search(marker, excerpt, re.I):
                kind = "unclassified_numeric_range"
            result.append(
                ResearchCandidate(
                    evidence_type=kind,
                    source=record["url"],
                    low=None if low is None else str(low),
                    high=None if high is None else str(high),
                    unit=unit or None,
                    population=anchored["population"],
                    specimen=anchored["specimen"],
                    method=anchored["method"],
                    excerpt=excerpt or record["title"],
                    status="candidate",
                )
            )
        return result


def services_from_environment(
    *, use_llm: bool = False, model_alias: str = "local", models_file: str | None = None,
    research_scope: str = "studies"
) -> tuple[NlmUcumClient, LoincFhirClient, PublicEvidenceClient | None]:
    llm = None
    if use_llm:
        file = models_file or os.getenv("SQC_MODELS_FILE")
        endpoint, api_key = select_model(model_alias, Path(file) if file else None)
        llm = OpenAICompatibleJsonClient(
            base_url=endpoint.base_url, model=endpoint.model, api_key=api_key,
            temperature=endpoint.temperature, max_tokens=endpoint.max_tokens,
            timeout=endpoint.timeout_seconds, reasoning_effort=endpoint.reasoning_effort,
            response_mode=endpoint.response_mode,
            enable_thinking=endpoint.enable_thinking,
        )
    return (
        NlmUcumClient(base_url=os.getenv("UCUM_NLM_BASE_URL", "https://ucum.nlm.nih.gov/ucum-service/v1")),
        LoincFhirClient(
            base_url=os.getenv("LOINC_FHIR_BASE_URL", "https://fhir.loinc.org"),
            username=os.getenv("LOINC_FHIR_USERNAME", ""),
            password=os.getenv("LOINC_FHIR_PASSWORD", ""),
        ),
        PublicEvidenceClient(
            scope=research_scope,
            llm=llm,
            pubmed_retmax=int(os.getenv("SQC_PUBMED_RETMAX", "3")),
            ncbi_api_key=os.getenv("NCBI_API_KEY") or os.getenv("PUBMED_API_KEY", ""),
            ncbi_tool=os.getenv("PUBMED_TOOL", ""),
            ncbi_email=os.getenv("PUBMED_EMAIL", ""),
            observation_definitions_file=os.getenv("SQC_OBSERVATION_DEFINITIONS", ""),
            loinc_website_base_url=os.getenv("LOINC_WEBSITE_BASE_URL", "https://loinc.org"),
            pubmed_base_url=os.getenv("PUBMED_EUTILS_BASE_URL", "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"),
        ) if use_llm else None,
    )
