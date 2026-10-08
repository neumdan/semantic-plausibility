# Adapted for the standalone semantic-plausibility project (2026-10-02).
"""FHIR terminology membership checks. A missing service never becomes a valid code."""

from __future__ import annotations

import json
import re
import ssl
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .model_config import allowed_endpoint


class FhirTerminologyClient:
    def __init__(
        self,
        base_url: str,
        bearer_token: str = "",
        timeout: int = 25,
        client_cert: str = "",
        client_key: str = "",
    ):
        if not allowed_endpoint(base_url):
            raise ValueError("Terminology endpoint must use HTTPS or an approved internal HTTP host")
        self.base_url = base_url.rstrip("/")
        self.bearer_token = bearer_token
        self.timeout = timeout
        if bool(client_cert) != bool(client_key):
            raise ValueError("mTLS requires both client_cert and client_key")
        self.tls_context = None
        if client_cert:
            self.tls_context = ssl.create_default_context()
            self.tls_context.load_cert_chain(client_cert, client_key)
        self._cache: dict[tuple[str, str, str], bool | None] = {}
        self._value_set_cache: dict[tuple[str, str, str, str, str], bool | None] = {}
        self._lookup_cache: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.validation_details: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.value_set_details: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}

    @staticmethod
    def _result(payload: dict[str, Any]) -> tuple[bool | None, dict[str, Any]]:
        parameters = payload.get("parameter") or []
        values = [part.get("valueBoolean") for part in parameters if part.get("name") == "result"]
        if len(values) != 1 or not isinstance(values[0], bool):
            raise ValueError("Terminology response has no Boolean result")
        messages = [
            str(part["valueString"])[:2000]
            for part in parameters
            if part.get("name") == "message" and part.get("valueString")
        ]
        issues = [
            issue
            for part in parameters
            if part.get("name") == "issues"
            for issue in (part.get("resource") or {}).get("issue") or []
        ][:30]
        unavailable = False
        for issue in issues:
            # Some servers return result=true for an unknown code in a fragment.
            # Such a warning does not establish exact code membership.
            codes = {coding.get("code", "") for coding in (issue.get("details") or {}).get("coding") or []}
            ids = {
                extension.get("valueString", "").upper()
                for extension in issue.get("extension") or []
                if extension.get("url", "").endswith("operationoutcome-message-id")
            }
            unavailable |= (
                issue.get("code") in {"not-supported", "exception", "timeout", "incomplete"}
                or bool(codes & {"not-found-cs", "not-found-vs", "not-found-version"})
                or any(value.startswith(("UNKNOWN_CODESYSTEM", "UNKNOWN_VALUESET")) for value in ids)
                or issue.get("code") == "not-found"
                and any(
                    expression.rsplit(".", 1)[-1] in {"system", "url", "version", "valueSet"}
                    for expression in issue.get("expression") or []
                )
                or "UNKNOWN_CODE_IN_FRAGMENT" in ids
                or values[0] is True
                and issue.get("severity") == "warning"
                and (issue.get("code") == "code-invalid" or "invalid-code" in codes)
            )
        for message in messages:
            unavailable |= bool(
                re.search(
                    r"\b(?:unknown|unsupported|unrecognized) (?:code ?system|value ?set|system|version)\b"
                    r"|\b(?:code ?system|value ?set|version)\s+(?:['\"][^'\"]*['\"]\s+)?"
                    r"(?:is\s+|could\s+)?(?:not\s+(?:be\s+)?(?:found|known|supported))\b",
                    message,
                    flags=re.I,
                )
            )
        details = {
            "server_result": values[0],
            "messages": messages,
            "issues": issues,
            "state": "unknown" if unavailable else "valid" if values[0] else "invalid",
        }
        return None if unavailable else values[0], details

    def _parameters(
        self, operation: str, params: dict[str, str], resource_type: str = "CodeSystem"
    ) -> dict[str, Any]:
        url = self.base_url + "/" + resource_type + "/$" + operation + "?" + urlencode(params)
        headers = {"Accept": "application/fhir+json"}
        if self.bearer_token:
            headers["Authorization"] = "Bearer " + self.bearer_token
        options = {"context": self.tls_context} if self.tls_context else {}
        with urlopen(Request(url, headers=headers), timeout=self.timeout, **options) as response:
            payload: dict[str, Any] = json.load(response)
        if payload.get("resourceType") != "Parameters":
            raise ValueError("Terminology server did not return FHIR Parameters")
        return payload

    def validate_value_set(
        self, value_set: str, system: str, code: str, version: str = "", value_set_version: str = ""
    ) -> bool | None:
        key = (value_set, value_set_version, system, code, version)
        if key in self._value_set_cache:
            return self._value_set_cache[key]
        params = {"url": value_set, "system": system, "code": code}
        if version:
            params["systemVersion"] = version
        if value_set_version:
            params["valueSetVersion"] = value_set_version
        payload = self._parameters("validate-code", params, "ValueSet")
        result, details = self._result(payload)
        self.value_set_details[key] = details
        self._value_set_cache[key] = result
        return result

    def validate_code(self, system: str, code: str, version: str = "") -> bool | None:
        key = (system, code, version)
        if key in self._cache:
            return self._cache[key]
        params = {"url": system, "code": code}
        if version:
            params["version"] = version
        payload = self._parameters("validate-code", params)
        result, details = self._result(payload)
        self.validation_details[key] = details
        self._cache[key] = result
        return result

    def lookup_code(self, system: str, code: str, version: str = "") -> dict[str, Any]:
        key = (system, code, version)
        if key in self._lookup_cache:
            return self._lookup_cache[key]
        params = {"system": system, "code": code}
        if version:
            params["version"] = version
        payload = self._parameters("lookup", params)
        display = next(
            (
                part.get("valueString")
                for part in payload.get("parameter") or []
                if part.get("name") == "display"
            ),
            None,
        )
        properties = []
        for part in payload.get("parameter") or []:
            if part.get("name") != "property":
                continue
            children = {child.get("name"): child for child in part.get("part") or []}
            property_code = (children.get("code") or {}).get("valueCode")
            value = children.get("value") or {}
            property_value = next((v for k, v in value.items() if k.startswith("value")), None)
            if property_code and property_value is not None:
                properties.append({"code": property_code, "value": property_value})
        metadata = {
            "display": display,
            "properties": properties[:30],
            "source": self.base_url + "/CodeSystem/$lookup",
        }
        self._lookup_cache[key] = metadata
        return metadata
