"""OLS name candidates and exact WHO ICD-11 MMS stem-code lookup.

Names are review evidence, never code validity. WHO credentials are used only
for the explicitly configured OAuth endpoint; no redirects are followed.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import time
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, build_opener

from .observation_services import _HttpSession, _NoModelRedirects
from .terminology_packages import https_url

ICD11_MMS = "http://id.who.int/icd/release/11/mms"


class OlsNameSource:
    def __init__(self, base_url, *, ontology, limit=5):
        self.base_url = https_url(base_url)
        if (
            not isinstance(ontology, str)
            or not re.fullmatch(r"[a-zA-Z0-9_-]+", ontology)
            or type(limit) is not int
            or not 1 <= limit <= 10
        ):
            raise ValueError("OLS needs one explicit ontology and limit=1..10")
        self.ontology, self.limit = ontology, limit
        self.session, self._cache = _HttpSession(), {}
        self._failures = 0

    def check(self, system, code, version=""):
        return {
            "state": "unknown",
            "source": self.base_url,
            "metadata": None,
            "reason": "OLS search supplies candidates, not authoritative code membership",
        }

    def search_names(self, name):
        if not isinstance(name, str) or not name.strip() or len(name) > 300:
            raise ValueError("Name search requires 1..300 characters of terminology text")
        if name in self._cache:
            return self._cache[name]
        if self._failures >= 3:
            raise RuntimeError("OLS name lookup circuit open after three consecutive failures")
        try:
            response = self.session.get(
                self.base_url + "/search",
                params={
                    "q": name,
                    "ontology": self.ontology,
                    "rows": self.limit,
                    "type": "class",
                    "obsoletes": "false",
                },
            )
            response.raise_for_status()
            payload = response.json().get("response", {})
            if not isinstance(payload.get("docs"), list) or type(payload.get("numFound")) is not int:
                raise ValueError("OLS returned no usable search response")
        except OSError, ValueError, RuntimeError:
            self._failures += 1
            raise
        self._failures = 0
        results = []
        for row in payload["docs"][: self.limit]:
            if row.get("ontology_name") != self.ontology or not row.get("iri") or not row.get("label"):
                continue
            candidate = {
                "state": "candidate",
                "label": row["label"],
                "iri": row["iri"],
                "ontology": self.ontology,
                "source": self.base_url + "/search",
                "method": "ols_name_search",
                "response_sha256": hashlib.sha256(response.text.encode()).hexdigest(),
                "requires_code_and_context_validation": True,
            }
            # An OLS short_form is not generally a code. Only the explicit LOINC
            # IRI/identifier agreement below permits a LOINC coding candidate.
            match = re.fullmatch(r"https?://loinc\.org/rdf/(\d+-\d)", row["iri"])
            if self.ontology == "loinc" and match and row.get("obo_id") == "LOINC:" + match[1]:
                candidate.update(code=match[1], system="http://loinc.org")
            results.append(candidate)
        self._cache[name] = results
        return results


class WhoIcdSource:
    """Version/language-pinned ICD-11 MMS API. Condition validation is later work."""

    def __init__(self, base_url, *, release, language, client_id, client_secret, token_endpoint):
        self.base_url, self.token_endpoint = https_url(base_url), https_url(token_endpoint)
        if (
            not isinstance(release, str)
            or not isinstance(language, str)
            or not re.fullmatch(r"\d{4}-\d{2}", release)
            or not re.fullmatch(r"[a-z]{2}(?:-[A-Z]{2})?", language)
        ):
            raise ValueError("WHO lookup needs a release such as 2026-01 and an explicit language")
        self.release, self.language = release, language
        self.client_id, self.client_secret = client_id, client_secret
        self.session, self._cache = _HttpSession(), {}
        self._token, self._expires, self._release_checked = "", 0, False

    def _headers(self):
        if time.monotonic() >= self._expires:
            if not self.client_id or not self.client_secret:
                raise ValueError("WHO ICD lookup requires the configured client ID and secret")
            credential = base64.b64encode((self.client_id + ":" + self.client_secret).encode()).decode(
                "ascii"
            )
            request = Request(
                self.token_endpoint,
                data=urlencode({"grant_type": "client_credentials", "scope": "icdapi_access"}).encode(),
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Authorization": "Basic " + credential,
                },
                method="POST",
            )
            try:
                with build_opener(_NoModelRedirects()).open(request, timeout=25) as response:
                    body = response.read(64001)
            except OSError:
                raise ValueError(
                    "WHO OAuth authentication failed; check the configured credentials"
                ) from None
            if len(body) > 64000:
                raise ValueError("WHO OAuth response exceeds budget")
            payload = json.loads(body)
            if (
                not isinstance(payload.get("access_token"), str)
                or not payload["access_token"]
                or payload.get("token_type", "").lower() != "bearer"
            ):
                raise ValueError("WHO OAuth returned no usable bearer token")
            expires = payload.get("expires_in")
            if type(expires) is not int or not 60 <= expires <= 86400:
                raise ValueError("WHO OAuth returned no usable token lifetime")
            self._token, self._expires = payload["access_token"], time.monotonic() + expires - 30
        return {
            "Authorization": "Bearer " + self._token,
            "API-Version": "v2",
            "Accept-Language": self.language,
            "Accept": "application/json",
        }

    def check(self, system, code, version=""):
        unknown = {"state": "unknown", "source": self.base_url, "metadata": None}
        if system != ICD11_MMS or version and version != self.release:
            return {
                **unknown,
                "reason": "Requested system/edition does not match configured ICD-11 MMS release",
            }
        if not re.fullmatch(r"[A-Z0-9]{4,7}(?:\.[A-Z0-9]{1,3})?", code):
            return {
                **unknown,
                "reason": "This adapter assesses stem codes only; postcoordination requires a separate review",
            }
        if code in self._cache:
            return self._cache[code]
        root = f"{self.base_url}/icd/release/11/{self.release}/mms"
        headers = self._headers()
        if not self._release_checked:
            response = self.session.get(root, headers=headers)
            response.raise_for_status()
            identifier = response.json().get("@id", "").replace("http://", "https://", 1)
            if identifier != root:
                raise ValueError("WHO did not confirm the configured MMS release")
            self._release_checked = True
        url = root + "/codeinfo/" + quote(code, safe="")
        response = self.session.get(
            url, params={"flexiblemode": "false", "convertToTerminalCodes": "false"}, headers=headers
        )
        if response.status_code == 404:
            result = {
                "state": "invalid",
                "source": url,
                "metadata": {"version": self.release, "language": self.language},
            }
        else:
            response.raise_for_status()
            info = response.json()
            identifier = str(info.get("stemId", "")).replace("http://", "https://", 1)
            if (
                info.get("code") != code
                or info.get("stemCode") != code
                or not identifier.startswith(root + "/")
                or urlsplit(identifier).query
                or urlsplit(identifier).fragment
            ):
                raise ValueError("WHO codeinfo did not confirm the exact requested stem/code/edition")
            entity = self.session.get(identifier, headers=headers)
            entity.raise_for_status()
            data = entity.json()
            title = data.get("title", {})
            display = title.get("@value", "") if isinstance(title, dict) else ""
            if (
                not display
                or str(data.get("@id", "")).replace("http://", "https://", 1) != identifier
                or data.get("code") != code
            ):
                raise ValueError("WHO entity does not match the requested MMS stem code")
            result = {
                "state": "valid",
                "source": url,
                "metadata": {
                    "display": display,
                    "version": self.release,
                    "language": title.get("@language", self.language),
                    "entity_uri": data["@id"],
                    "codeinfo": info,
                },
                "response_sha256": hashlib.sha256((response.text + entity.text).encode()).hexdigest(),
            }
        self._cache[code] = result
        return result
