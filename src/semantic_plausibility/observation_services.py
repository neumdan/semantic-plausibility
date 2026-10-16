# Adapted for the standalone semantic-plausibility project (2026-10-02).
"""Official terminology and literature adapters for observation_quality.

Network calls are opt-in at the CLI boundary. Only terminology context is sent to
the model and PubMed; patient values and identifiers are never sent by these adapters.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
from pathlib import Path
from threading import Lock
from typing import Any
from urllib.parse import quote, urlparse, urlsplit, urlunsplit
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener
from urllib.error import HTTPError, URLError
import base64
import json
from json import dumps as _json_dumps
import os
import re
import ssl
import time
import xml.etree.ElementTree as ET

from .catalog_evidence import ObservationDefinitionCatalog
from .observation_quality import LoincTerm, ResearchCandidate, UnitEvidence
from .model_config import ModelEndpoint, allowed_endpoint, select_model
from .model_schemas import explicit_token as _explicit_token
from .model_schemas import evidence_schema, model_evidence, resolve_sentences, ground_study_units, guard_study_parts, response_schema, validate_grounding, validate_response, supports_evidence_type
from . import research_prompts as prompts
from .progress import LOGGER, activity
from .study_sources import TEXT_RENDERING, study_text, study_queries, analyte_aliases, screen_record, ucum_spelling, provider_query


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

    def _get(self, url, **kwargs):
        # Retry one transient transport/server failure; never bypass TLS verification.
        for attempt in range(2):
            try:
                response = self.session.get(url, timeout=self.timeout, **kwargs)
                if attempt == 0 and response.status_code in {429, 502, 503, 504}:
                    LOGGER.warning("UCUM service temporarily unavailable (HTTP %s); retrying once", response.status_code)
                else:
                    return response
            except (URLError, TimeoutError) as exc:
                if attempt or isinstance(getattr(exc, 'reason', None), ssl.SSLCertVerificationError):
                    raise
                LOGGER.warning("UCUM connection failed (%s); retrying once", type(getattr(exc, 'reason', exc)).__name__)
            time.sleep(0.5)

    def is_valid(self, unit: str) -> bool:
        if unit in self._valid:
            return self._valid[unit]
        url = f"{self.base_url}/isValidUCUM/{quote(unit, safe='/')}"
        response = self._get(url)
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
            response = self._get(url, headers={"Accept": "application/xml"})
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
        self._parts = {}
        self._names = {}

    def part(self, code, version):
        key = (code, version)
        if key not in self._parts:
            response = self.session.get(self.base_url + "/CodeSystem/$lookup",
                params={"system": "http://loinc.org", "code": code, "version": version},
                headers={"Accept": "application/fhir+json"}, auth=self.auth, timeout=self.timeout)
            response.raise_for_status()
            data = response.json()
            if data.get("resourceType") != "Parameters":
                raise ValueError("LOINC part lookup returned no Parameters")
            values = {row["name"]: next((value for key, value in row.items() if key.startswith("value")), "")
                      for row in data.get("parameter", []) if row.get("name") != "property"}
            if values.get("version") != version or values.get("code", code) != code or values.get("system", "http://loinc.org") != "http://loinc.org":
                raise ValueError("LOINC part lookup returned another identity/edition")
            label = values.get("display")
            if not isinstance(label, str) or not label or label.startswith("LP"):
                raise ValueError("LOINC part lookup returned no resolved label")
            self._parts[key] = {"part_code": code, "display": label, "source": response.url, "version": version,
                                "source_sha256": hashlib.sha256(response.text.encode()).hexdigest()}
        return self._parts[key]

    def search_names(self, name):
        """Bounded discovery; returned numeric codes still require exact lookup."""
        if name in self._names:
            return self._names[name]
        query = name + " [" if "[" not in name else name
        response = self.session.get(self.base_url + "/ValueSet/$expand",
            params={"url": "http://loinc.org/vs", "filter": query, "count": 200},
            headers={"Accept": "application/fhir+json"}, auth=self.auth, timeout=self.timeout)
        response.raise_for_status()
        data = response.json()
        if data.get("resourceType") != "ValueSet":
            raise ValueError("LOINC name search returned no ValueSet")
        rows = [{"system": row["system"], "code": row["code"], "label": row.get("display", ""),
                 "version": row.get("version", ""), "source": response.url, "method": "primary_fhir_name_expansion",
                 "source_sha256": hashlib.sha256(response.text.encode()).hexdigest()}
                for row in data.get("expansion", {}).get("contains", [])[:200]
                if row.get("system") == "http://loinc.org" and re.fullmatch(r"\d+-\d", row.get("code", ""))]
        if not rows and query != name:
            response = self.session.get(self.base_url + "/ValueSet/$expand",
                params={"url": "http://loinc.org/vs", "filter": name, "count": 200},
                headers={"Accept": "application/fhir+json"}, auth=self.auth, timeout=self.timeout)
            response.raise_for_status()
            data = response.json()
            if data.get("resourceType") != "ValueSet":
                raise ValueError("LOINC name search returned no ValueSet")
            rows = [{"system": row["system"], "code": row["code"], "label": row.get("display", ""),
                     "version": row.get("version", ""), "source": response.url, "method": "primary_fhir_name_expansion",
                     "source_sha256": hashlib.sha256(response.text.encode()).hexdigest()}
                    for row in data.get("expansion", {}).get("contains", [])[:200]
                    if row.get("system") == "http://loinc.org" and re.fullmatch(r"\d+-\d", row.get("code", ""))]
        folded = name.casefold()
        rows.sort(key=lambda row: (not row["label"].casefold().startswith(folded + " ["),
                                  not row["label"].casefold().startswith(folded), len(row["label"])))
        self._names[name] = rows[:20]
        return self._names[name]

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
        designations = []
        for entry in payload.get("parameter", []):
            if entry.get("name") == "display":
                display = entry.get("valueString") or ""
            if entry.get("name") == "designation":
                parts = {part.get("name"): part for part in entry.get("part", [])}
                label = parts.get("value", {}).get("valueString", "")
                if label:
                    designations.append({"value": label, "language": parts.get("language", {}).get("valueCode", ""),
                        "use": parts.get("use", {}).get("valueCoding", {}).get("code", "")})
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
                value_part.get("valueString") or (value_part.get("valueCoding") or {}).get("display")
                or (part_code if isinstance(part_code, str) and not part_code.startswith("LP") else "")
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
        axis_sources = {}
        if not code.startswith("LP") and returned_version:
            for axis in ("COMPONENT", "PROPERTY", "SYSTEM", "SCALE_TYP", "METHOD_TYP", "TIME_ASPCT"):
                if axis in part_codes and not one(axis):
                    try:
                        evidence = self.part(part_codes[axis], returned_version)
                        properties[axis] = [evidence["display"]]
                        axis_sources[axis] = evidence
                        part_codes.pop(axis)
                    except (OSError, ValueError, RuntimeError):
                        pass  # Keep the unresolved part code; never guess an axis.
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
            time_aspect=one("TIME_ASPCT") or one("TIME_ASP"),
            example_units=many("EXAMPLE_UNITS"),
            source=response.url,
            part_codes=part_codes,
            designations=tuple(designations),
            synonyms=tuple(dict.fromkeys((*many("RELATEDNAMES2"), *many("SHORTNAME")))),
            status=one("STATUS"),
            axis_sources=axis_sources,
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
        self.audit_records = []
        self.trace = []
        self._failures = 0

    def json(self, instruction: str, evidence: dict[str, Any]) -> dict[str, Any]:
        if self._failures >= 3:
            raise RuntimeError("Local model circuit open after three consecutive failures")
        schema = evidence_schema(instruction, evidence)
        trace = {"model": self.model, "endpoint": self.base_url, "instruction": instruction,
                 "system_prompt": prompts.SYSTEM + " Required JSON schema: " + json.dumps(schema),
                 "user_prompt": json.dumps({"instruction": instruction, "evidence": model_evidence(instruction, evidence)}, ensure_ascii=False),
                 "response_schema": schema, "response_mode": self.response_mode,
                 "temperature": self.temperature, "max_tokens": self.max_tokens,
                 "evidence": evidence, "state": "requested"}
        self.trace.append(trace)
        started = time.monotonic()
        task = next((name for name, value in vars(prompts).items() if value == instruction), "EXTRACTION")
        trace.update(task=task, started_at=datetime.now(timezone.utc).isoformat())
        try:
            with activity(f"Ollama/model call {len(self.trace)} · {task}"):
                result = self._json(instruction, evidence)
        except (OSError, ValueError, RuntimeError) as exc:
            # A rejected extraction is not a service outage. Other bounded tasks
            # still run; only consecutive transport/HTTP failures open the circuit.
            self._failures = 0 if isinstance(exc, ValueError) else self._failures + 1
            trace.update(state="rejected_or_unavailable", error_type=type(exc).__name__, elapsed_seconds=round(time.monotonic()-started, 3))
            LOGGER.warning("Model call %s not accepted (%s); see report.trace.jsonl", len(self.trace), type(exc).__name__)
            diagnostic = str(exc)
            if isinstance(exc, ValueError) and diagnostic.startswith(("Model schema mismatch", "Model returned", "Model selected", "Model proposed", "Study bounds", "Study evidence")):
                trace["guard_diagnostic"] = diagnostic[:600]
                LOGGER.warning("Source guard: %s", diagnostic[:600])
            raise
        self._failures = 0
        trace.update(state="accepted_by_source_guards", response=result, elapsed_seconds=round(time.monotonic()-started, 3))
        LOGGER.info("Model call %s accepted by source guards; clinical applicability remains unverified", len(self.trace))
        return result

    def _json(self, instruction: str, evidence: dict[str, Any]) -> dict[str, Any]:
        schema = evidence_schema(instruction, evidence)
        supplied = json.dumps({"instruction": instruction, "evidence": model_evidence(instruction, evidence)}, ensure_ascii=False)
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
        if instruction == prompts.STUDY_EXTRACT:
            self.trace[-1]["model_response"] = result
            result = resolve_sentences(result, evidence)
            result, changes = ground_study_units(result)
            self.trace[-1]["unit_grounding"] = changes
            validate_response(result, response_schema(instruction))
            result, rejected = guard_study_parts(result, evidence)
            self.trace[-1]["extraction_rejections"] = rejected
            for part in rejected:
                LOGGER.warning("Study source guard rejected %s: %s", part["part"], part["reason"])
            if rejected and not result["measurements"] and result["bounds"]["low"] is None and result["bounds"]["high"] is None:
                raise ValueError("Study evidence has no supported claims after individual source guards")
        validate_grounding(instruction, result, evidence)
        self.audit_records.append({
            "model": self.model, "endpoint": self.base_url,
            "task_sha256": hashlib.sha256(instruction.encode()).hexdigest(),
            "input_sha256": hashlib.sha256(supplied.encode()).hexdigest(),
            "response_sha256": hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest(),
            "checked_at": datetime.now(timezone.utc).isoformat(),
        })
        return result




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
        research_sources: tuple[str, ...] = ("pubmed",),
        europe_pmc_base_url: str = "https://www.ebi.ac.uk/europepmc/webservices/rest",
        europe_pmc_retmax: int = 3,
        max_articles_per_loinc: int = 3,
        europe_pmc_query_filter: str = "",
        ucum: Any | None = None,
    ):
        if scope not in {"lookup", "studies"}:
            raise ValueError("Evidence scope must be lookup or studies")
        self.scope = scope
        for endpoint in (loinc_website_base_url, pubmed_base_url, europe_pmc_base_url):
            if not allowed_endpoint(endpoint) or urlsplit(endpoint).query or urlsplit(endpoint).fragment:
                raise ValueError("Research APIs require HTTPS or approved local HTTP endpoints")
        self.loinc_website_base_url = loinc_website_base_url.rstrip("/")
        self.pubmed_base_url = pubmed_base_url.rstrip("/")
        if not set(research_sources) <= {"pubmed", "europe_pmc"} or type(europe_pmc_retmax) is not int or not 1 <= europe_pmc_retmax <= 10:
            raise ValueError("Select supported study sources and europe_pmc_retmax=1..10")
        self.research_sources = research_sources
        self.europe_pmc_base_url = europe_pmc_base_url.rstrip("/")
        self.europe_pmc_retmax = europe_pmc_retmax
        if not isinstance(europe_pmc_query_filter, str) or len(europe_pmc_query_filter) > 1000:
            raise ValueError("Europe PMC query filter must be a bounded public search expression")
        self.europe_pmc_query_filter = europe_pmc_query_filter
        if type(max_articles_per_loinc) is not int or not 1 <= max_articles_per_loinc <= 12:
            raise ValueError("max_articles_per_loinc must be 1..12")
        self.max_articles_per_loinc = max_articles_per_loinc
        self.ucum = ucum
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
        self._website_errors = {}
        self._website_failures = 0
        self._study_cache: dict[str, list[ResearchCandidate]] = {}
        self.source_records = {}
        self.query_log = []
        self.catalog = (
            ObservationDefinitionCatalog(Path(observation_definitions_file))
            if observation_definitions_file
            else None
        )

    def catalog_evidence(self, code: str) -> tuple[list[UnitEvidence], list[ResearchCandidate]]:
        return self.catalog.for_loinc(code) if self.catalog else ([], [])

    def _ncbi_get(self, endpoint: str, params: dict[str, Any]) -> Any:
        self.query_log.append({"source": "pubmed", "endpoint": endpoint,
            "parameters": {key: value for key, value in params.items() if key in {"db", "term", "id", "retmax", "retmode"}}})
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
        digest = hashlib.sha256(response.text.encode()).hexdigest()
        self.source_records[digest] = {"source": f"{self.pubmed_base_url}/{endpoint}",
            "sha256": digest, "retrieved_at": datetime.now(timezone.utc).isoformat()}
        return response

    def website_units(self, code: str, term: LoincTerm) -> list[UnitEvidence]:
        if self.llm is None:
            return []
        if code in self._website_cache:
            return list(self._website_cache[code])
        url = f"{self.loinc_website_base_url}/{quote(code, safe='')}"
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
        if code in self._website_errors:
            raise RuntimeError(self._website_errors[code])
        if self._website_failures >= 3:
            raise RuntimeError("LOINC website circuit open after three consecutive failures")
        if code not in self._website_text_cache:
            url = f"{self.loinc_website_base_url}/{quote(code, safe='')}"
            self.query_log.append({"source": "loinc_website", "url": url})
            try:
                response = self.session.get(url, timeout=self.timeout,
                    headers={"User-Agent": "semantic-plausibility (+https://github.com/neumdan/semantic-plausibility)"})
                response.raise_for_status()
                if response.status_code != 200:
                    raise ValueError("LOINC page returned an unexpected status; configure its canonical base URL")
            except (OSError, ValueError, RuntimeError) as exc:
                self._website_failures += 1
                message = str(exc) if isinstance(exc, RuntimeError) else "LOINC page unavailable: " + type(exc).__name__
                self._website_errors[code] = message
                self.source_records["unavailable:" + code] = {"source": url, "state": "unavailable",
                    "retrieved_at": datetime.now(timezone.utc).isoformat()}
                raise
            self._website_failures = 0
            parser = _PlainText()
            parser.feed(response.text)
            self._website_text_cache[code] = parser.text()
            digest = hashlib.sha256(response.text.encode()).hexdigest()
            self.source_records[digest] = {"source": f"{self.loinc_website_base_url}/{quote(code, safe='')}",
                "sha256": digest, "retrieved_at": datetime.now(timezone.utc).isoformat()}
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
            {"loinc_code": code, "page_url": f"{self.loinc_website_base_url}/{quote(code, safe='')}", "page_excerpt": snippet},
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
                    f"{self.loinc_website_base_url}/{quote(code, safe='')}",
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

    study_queries = staticmethod(study_queries)

    @staticmethod
    def _abstract_payload(record):
        text = record["abstract"][:6000]
        excerpts = re.split(r"(?<=[.!?])\s+(?=[A-Z])", text)
        return {"abstract": text, "excerpt_candidates": [part for part in excerpts if part]}

    def search_studies(self, term: LoincTerm) -> list[ResearchCandidate]:
        if self.scope == "lookup":
            return []
        key = (term.code, term.version)
        if key in self._study_cache:
            LOGGER.info("LOINC %s: reuse cached study evidence", term.code)
            return list(self._study_cache[key])
        LOGGER.info("LOINC %s: retrieve up to %s distinct articles from %s", term.code, self.max_articles_per_loinc, ", ".join(self.research_sources))
        records = {}
        aliases = analyte_aliases(term)
        # Screen a bounded result pool before spending the model article budget.
        # Omitted articles remain in the trace with deterministic reasons.
        for category, query in self.study_queries(term).items():
            for source in self.research_sources:
                LOGGER.info("Study search: %s · %s · LOINC %s", source, category, term.code)
                try:
                    search = provider_query(query, source)
                    found = (self._pubmed_records(self._pubmed_ids(search)) if source == "pubmed"
                             else self._europe_pmc_records(search))
                    for record in found:
                        identity = record.get("doi", "").casefold() or record.get("record_key") or "pmid:" + record["pmid"]
                        if identity not in records or len(record["abstract"]) > len(records[identity]["abstract"]):
                            records[identity] = record
                except (OSError, ValueError, RuntimeError) as exc:
                    self.query_log.append({"source": source, "state": "unavailable", "error_type": type(exc).__name__})
            if sum(screen_record(r, aliases, term.property)[0] > 0 for r in records.values()) >= self.max_articles_per_loinc:
                break
        ranked = sorted(records.values(), key=lambda r: -screen_record(r, aliases, term.property)[0])
        selected = [r for r in ranked if screen_record(r, aliases, term.property)[0] > 0][:self.max_articles_per_loinc]
        for record in ranked:
            score, reason = screen_record(record, aliases, term.property)
            self.query_log.append({"source": record["url"], "task": "study_screen", "state": "selected" if record in selected else "excluded",
                "loinc": term.code, "title": record["title"], "score": score,
                "reason": reason if score == 0 or record in selected else "model_article_budget",
                "aliases": aliases, "terminology_source": term.source, "terminology_version": term.version,
                "source_sha256": record["source_sha256"], "abstract": record["abstract"],
                "source_text_rendering": TEXT_RENDERING})
        LOGGER.info("LOINC %s: screened %s articles; selected %s for extraction", term.code, len(records), len(selected))
        candidates = []
        for article_index, record in enumerate(selected, 1):
            before = len(candidates)
            extraction_unavailable = False
            LOGGER.info("LOINC %s: article %s/%s, source %s", term.code, article_index, len(selected), record["url"])
            if self.llm is not None:
                try:
                    answer = self.llm.json(prompts.STUDY_EXTRACT, {
                        "loinc_code": term.code, "analyte": term.component or term.display,
                        "analyte_definition": {"display": term.display, "component": term.component, "property": term.property,
                                               "system": term.system, "scale": term.scale, "method": term.method,
                                               "example_units": list(term.example_ucum_units), "aliases": aliases},
                        "title": record["title"], **self._abstract_payload(record)})
                    candidates += self._extract_study_candidates("unclassified_discovery", [record], term, answers=[answer["bounds"]])
                    for row in answer["measurements"]:
                        candidates.append(ResearchCandidate("reported_measurement", record["url"],
                            unit=row["unit"], population=row["population"], specimen=row["specimen"],
                            method=row["method"], excerpt=row["excerpt"], reported_value=row["reported_value"],
                            value_kind=row["value_kind"], **self._study_provenance(record)))
                except (OSError, ValueError, RuntimeError) as exc:
                    extraction_unavailable = True
                    self.query_log.append({"source": record["url"], "task": "study_extract", "state": "unavailable", "error_type": type(exc).__name__})
            if len(candidates) == before:
                # Retrieval is evidence of a search result, even when extraction fails/abstains.
                candidates.append(ResearchCandidate("unclassified_discovery", record["url"],
                    excerpt=record["title"], status="extraction_unavailable" if extraction_unavailable else "no_supported_extraction",
                    **self._study_provenance(record)))
        # Retain the literal unit; validate a separate typography-derived UCUM spelling.
        for index, candidate in enumerate(candidates):
            if not candidate.unit or self.ucum is None:
                continue
            spelling = ucum_spelling(candidate.unit)
            try:
                valid = self.ucum.is_valid(spelling)
                state = "valid" if valid is True else "invalid" if valid is False else "unknown"
            except (OSError, ValueError, RuntimeError):
                state = "unknown"
            source = getattr(self.ucum, "source", "") or ""
            self.query_log.append({"task": "study_unit_validation", "state": state, "source": source,
                "unit_source": candidate.unit, "ucum_candidate": spelling, "loinc": term.code})
            candidates[index] = replace(candidate, unit_ucum=spelling if state == "valid" else "",
                unit_validation_state=state, unit_validation_source=source)
        self._study_cache[key] = candidates
        return list(candidates)

    def _europe_pmc_records(self, query):
        if self.europe_pmc_query_filter:
            query = f"({query}) AND ({self.europe_pmc_query_filter})"
        self.query_log.append({"source": "europe_pmc", "query": query, "page_size": self.europe_pmc_retmax})
        response = self.session.get(self.europe_pmc_base_url + "/search", params={
            "query": query, "format": "json", "resultType": "core", "pageSize": self.europe_pmc_retmax})
        response.raise_for_status()
        digest = hashlib.sha256(response.text.encode()).hexdigest()
        retrieved_at = datetime.now(timezone.utc).isoformat()
        self.source_records[digest] = {"source": self.europe_pmc_base_url + "/search", "sha256": digest,
                                     "retrieved_at": retrieved_at}
        rows = response.json().get("resultList", {}).get("result", [])
        if not isinstance(rows, list):
            raise ValueError("Europe PMC returned no usable article records")
        records = []
        for row in rows[:self.europe_pmc_retmax]:
            source, identifier = row.get("source", ""), str(row.get("id", ""))
            if not re.fullmatch(r"[A-Z]+", source) or not re.fullmatch(r"[A-Za-z0-9_-]+", identifier):
                continue
            records.append({"pmid": identifier if source == "MED" else "",
                "record_key": "pmid:" + identifier if source == "MED" else source + ":" + identifier,
                "title": study_text(row.get("title", "")), "abstract": study_text(row.get("abstractText", "")),
                "source_sha256": digest, "retrieved_at": retrieved_at, "doi": str(row.get("doi", "")),
                "url": f"https://europepmc.org/article/{source}/{identifier}"})
        return records

    def _pubmed_ids(self, query: str) -> list[str]:
        response = self._ncbi_get(
            "esearch.fcgi", {"db": "pubmed", "term": query, "retmode": "json", "retmax": self.pubmed_retmax, "sort": "relevance"}
        )
        ids = response.json().get("esearchresult", {}).get("idlist", [])
        return [str(pmid) for pmid in ids if str(pmid).isdigit()]

    def _pubmed_records(self, ids: list[str]) -> list[dict[str, str]]:
        if not ids:
            return []
        response = self._ncbi_get("efetch.fcgi", {"db": "pubmed", "id": ",".join(ids), "retmode": "xml"})
        digest = hashlib.sha256(response.text.encode()).hexdigest()
        retrieved_at = self.source_records[digest]["retrieved_at"]
        root = ET.fromstring(response.text)
        records: list[dict[str, str]] = []
        for article in root.findall(".//PubmedArticle"):
            pmid = article.findtext(".//MedlineCitation/PMID") or ""
            title_node = article.find(".//ArticleTitle")
            abstracts = article.findall(".//Abstract/AbstractText")
            title = study_text(ET.tostring(title_node, encoding="unicode")) if title_node is not None else ""
            abstract = study_text(" ".join(ET.tostring(node, encoding="unicode") for node in abstracts))
            if pmid in ids:
                records.append(
                    {
                        "pmid": pmid,
                        "title": title,
                        "abstract": abstract,
                        "source_sha256": digest, "retrieved_at": retrieved_at,
                        "doi": article.findtext(".//PubmedData/ArticleIdList/ArticleId[@IdType='doi']") or "",
                        "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                    }
                )
        return records

    def _extract_study_candidates(
        self, evidence_type: str, records: list[dict[str, str]], term: LoincTerm, *, answers=None
    ) -> list[ResearchCandidate]:
        if self.llm is None:
            return [
                ResearchCandidate(evidence_type, record["url"], excerpt=record["title"], **self._study_provenance(record)) for record in records
            ]
        result: list[ResearchCandidate] = []
        for index, record in enumerate(records):
            answer = answers[index] if answers is not None else self.llm.json(
                prompts.STUDY_BOUNDS,
                {
                    "evidence_type": evidence_type,
                    "loinc_code": term.code,
                    "analyte_definition": {"display": term.display, "component": term.component, "property": term.property,
                                           "system": term.system, "scale": term.scale, "method": term.method,
                                           "example_units": list(term.example_ucum_units)},
                    "pmid": record["pmid"],
                    "title": record["title"],
                    **self._abstract_payload(record),
                },
            )
            excerpt = str(answer.get("excerpt") or "").strip()
            if excerpt and excerpt not in record["abstract"]:
                continue
            low = answer.get("low")
            high = answer.get("high")
            if low is None and high is None:
                continue
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
            kind = answer["evidence_type"]
            if kind is None:
                continue
            if not supports_evidence_type(kind, excerpt, record["abstract"]):
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
                    **self._study_provenance(record),
                )
            )
        return result

    @staticmethod
    def _study_provenance(record):
        """Citation metadata comes from the retrieved provider record, never the model."""
        return {"source_title": record.get("title", ""), "source_sha256": record.get("source_sha256", ""),
                "retrieved_at": record.get("retrieved_at", ""), "doi": record.get("doi", ""),
                "source_text_rendering": TEXT_RENDERING}


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
            research_sources=tuple(filter(None, os.getenv("SQC_RESEARCH_SOURCES", "pubmed").split(","))),
            europe_pmc_base_url=os.getenv("EUROPE_PMC_BASE_URL", "https://www.ebi.ac.uk/europepmc/webservices/rest"),
            europe_pmc_retmax=int(os.getenv("SQC_EUROPE_PMC_RETMAX", "3")),
            europe_pmc_query_filter=os.getenv("SQC_EUROPE_PMC_QUERY_FILTER", ""),
            max_articles_per_loinc=int(os.getenv("SQC_MAX_ARTICLES_PER_LOINC", "3")),
        ) if use_llm else None,
    )
