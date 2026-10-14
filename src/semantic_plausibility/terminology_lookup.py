"""Inspect a single configured terminology source without running a batch/model."""

from __future__ import annotations

from dataclasses import asdict
import re

from .batch import BatchConfiguration
from .deployment import environment
from .name_checks import check_name
from .observation_services import LoincFhirClient, NlmUcumClient
from .terminology_router import TerminologyRouter


def lookup_terminology(path, alias, *, system="", code="", version="", name=""):
    config = BatchConfiguration(path)
    hosts = config.data.get("network", {}).get("allowed_http_hosts", [])
    if not isinstance(hosts, list) or any(
        not isinstance(host, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]+", host) for host in hosts
    ):
        raise ValueError("allowed_http_hosts must be a hostname list")
    with environment({"SQC_ALLOWED_HTTP_HOSTS": ",".join(hosts)}):
        return _configured_lookup(config, alias, system=system, code=code, version=version, name=name)


def _configured_lookup(config, alias, *, system, code, version, name):
    row = config.data.get("lookups", {}).get(alias)
    if not row or not row.get("enabled"):
        raise ValueError("Enable the named lookup in the TOML first")
    if not code and not name:
        raise ValueError("Supply a code and/or a terminology name")
    kind = row["type"]
    if config.network_mode == "offline" and kind not in {"csv", "sqlite", "fhir_artifacts", "loinc_csv"}:
        raise ValueError("This lookup needs network.mode=lookups")
    expected = {"loinc": "http://loinc.org", "ucum": "http://unitsofmeasure.org"}.get(kind)
    systems = [expected] if expected else row.get("systems", [])
    system = system or (systems[0] if len(systems) == 1 else "")
    if not system or system not in systems:
        raise ValueError("Select a system explicitly covered by the named lookup")
    if kind == "ucum":
        if not code or name or version:
            raise ValueError(
                "The UCUM grammar lookup requires --code; translations use the installed artifacts"
            )
        client = NlmUcumClient(
            config.deployment.url(
                {"base_url": row.get("endpoint", ""), "base_url_env": row.get("endpoint_env", "")},
                default="https://ucum.nlm.nih.gov/ucum-service/v1",
            )
        )
        return {
            "state": "valid" if client.is_valid(code) else "invalid",
            "system": system,
            "code": code,
            "source": client.source,
        }
    if kind == "loinc":
        if not code:
            raise ValueError("LOINC exact lookup needs --code; configure OLS for name candidates")
        client = LoincFhirClient(
            base_url=config.deployment.url(
                {"base_url": row.get("endpoint", ""), "base_url_env": row.get("endpoint_env", "")},
                default="https://fhir.loinc.org",
            ),
            username=config.deployment.secret(row, "username"),
            password=config.deployment.secret(row, "password"),
        )
        term = client.lookup(code, version)
        return {
            "state": "valid" if term else "unknown",
            "system": system,
            "code": code,
            "metadata": asdict(term) if term else None,
            "name_check": check_name(name, system, code, version, term=term) if name else None,
        }
    entry = config.lookup_settings({alias: row})["sources"][0]
    if entry.get("path"):
        entry["path"] = str(config.path_value(entry["path"]))
    updates = {}
    for key in ("token", "client_id", "client_secret"):
        if entry.get(key + "_file") or entry.get(key + "_env"):
            variable = "SQC_QUERY_" + key.upper()
            updates[variable] = config.deployment.secret(row, key, required=True)
            entry.pop(key + "_file", None)
            entry[key + "_env"] = variable
    with environment(updates):
        router = TerminologyRouter([entry])
        try:
            result = (
                router.check(system, code, version)
                if code
                else {"state": "unknown", "reason": "Name search only; no code membership assessed"}
            )
            if name:
                result["name_check"] = check_name(name, system, code, version, router=router, search=True)
            return {**result, "system": system, "code": code}
        finally:
            router.close()
