"""Laboratory configuration, source mapping and the single export-review workflow."""

from __future__ import annotations
from dataclasses import dataclass
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import tomllib
from urllib.parse import quote
from .audit import file_records
from .db_source import _identifier_parts, record_from_row, scan, validate_mapping
from .deployment import Deployment, table

ROLE_ALIASES = {
    "analyte_name": "code_text",
    "reference_low": "range_low",
    "reference_high": "range_high",
    "reference_unit": "range_low_code",
    "reference_unit_system": "range_low_system",
    "specimen": "loinc_system",
    "component": "loinc_component",
    "property": "loinc_property",
    "time_aspect": "loinc_time_aspect",
    "scale": "loinc_scale",
    "source_id": "id",
    "value_comparator": "comparator",
}
GROUP_FIELDS = {
    "code",
    "code_system",
    "code_version",
    "value",
    "unit",
    "unit_code",
    "unit_system",
    "reference_range",
    "specimen",
    "method",
}
REQUIRED_GROUP = {"code", "code_system", "unit", "unit_system", "reference_range"}


def roles(values):
    if not isinstance(values, dict) or any((not isinstance(value, str) for value in values.values())):
        raise ValueError("Column/default mappings must be tables of string values")
    result = {ROLE_ALIASES.get(key, key): value for key, value in values.items()}
    if "reference_unit" in values:
        result["range_high_code"] = values["reference_unit"]
    if "reference_unit_system" in values:
        result["range_high_system"] = values["reference_unit_system"]
    return result


@dataclass(frozen=True)
class BatchResult:
    csv: Path
    input_records: int
    distinct_tuples: int
    statuses: dict[str, int]
    skipped_resources: int
    summary: Path
    report: Path
    log: Path
    fhir_validation_state: str = "disabled"


class BatchConfiguration:
    """Validate operator settings and normalize source roles; no data/network access."""

    def __init__(self, path, *, research=None, output_path=None):
        self.path = Path(path).resolve()
        self.root = self.path.parent
        try:
            self.data = tomllib.loads(self.path.read_text())
        except tomllib.TOMLDecodeError, UnicodeError:
            raise ValueError("Invalid batch TOML configuration") from None
        self.operation = "batch" if research is None else "research-evidence" if research else "check"
        if research is not None:
            self.data.setdefault("research", {})["enabled"] = research
        if output_path is not None:
            self.data.setdefault("output", {})["path"] = str(output_path)
        data = table(
            self.data,
            "batch",
            {
                "config_version",
                "general",
                "input",
                "database",
                "specification",
                "aggregation",
                "output",
                "lookups",
                "models",
                "research",
                "network",
                "study",
                "quality",
                "packages",
                "validation",
            },
        )
        self.version = data.get("config_version")
        from .terminology_packages import validate_packages
        validate_packages(data.get("packages", {}))
        if self.version != 3:
            raise ValueError(
                "Use config_version=3 and the current pipeline.example.toml; retired config versions are unsupported"
            )
        general = table(data.get("general", {}), "general", {"PROJECT_NAME", "RESOURCE_TYPE", "MODEL_ALIAS"})
        self.kind = general.get("RESOURCE_TYPE", "Observation")
        if self.kind != "Observation":
            raise ValueError(
                "This batch supports laboratory Observation only; medication and diagnosis are later work"
            )
        self.input = table(
            data.get("input", {}),
            "input",
            {
                "type",
                "origin",
                "path",
                "granularity",
                "delimiter",
                "columns",
                "defaults",
                "elements",
                "snapshot",
                "context",
                "context_file",
                "manifest",
                "source_namespace",
            },
        )
        self.input_type = self.input.get("type", "csv")
        if self.input_type not in {"csv", "fhir_file", "postgres", "sqlite"}:
            raise ValueError(
                "input.type must be csv, fhir_file, postgres or sqlite; select FHIR resources upstream with TORCH/CQL"
            )
        spec = table(
            data.get("specification", {}),
            "specification",
            {
                "FHIR_VERSION",
                "PROFILE",
                "PROFILE_FILE",
                "PROFILE_VERSION",
                "IG_PACKAGE",
                "IG_VERSION",
                "IG_CANONICAL",
                "STRUCTURE_DEFINITION",
                "VALUE_SETS",
                "links",
            },
        )
        if spec.get("FHIR_VERSION", "4.0.1") != "4.0.1":
            raise ValueError(
                "This runner implements FHIR R4 (4.0.1); other versions need an upstream adapter"
            )
        self.profile = spec.get("PROFILE", "auto")
        from .fhir_validation import validation_settings
        self.validation = validation_settings(data)
        if not isinstance(spec.get("VALUE_SETS", []), list) or any(not isinstance(value, str) for value in spec.get("VALUE_SETS", [])):
            raise ValueError("specification.VALUE_SETS must be paths to installed real ValueSets")
        from urllib.parse import urlsplit
        links = spec.get("links", {})
        if not isinstance(links, dict) or len(links) > 8:
            raise ValueError("specification.links must be a table of up to eight documentation URLs")
        for label, url in links.items():
            try:
                parsed = urlsplit(url) if isinstance(url, str) else None
                valid = parsed and parsed.scheme in {"http", "https"} and parsed.hostname and not (parsed.username or parsed.password or parsed.query)
            except ValueError:
                valid = False
            if not isinstance(label, str) or not label.strip() or not valid:
                raise ValueError("Profile documentation links need HTTP(S) URLs without credentials or query strings")
        aggregation = table(data.get("aggregation", {}), "aggregation", {"engine", "group_by"})
        if aggregation.get("engine", "duckdb") != "duckdb":
            raise ValueError(
                "This workflow uses DuckDB grouping. SQLite is an input format; CQL is upstream extraction"
            )
        self.group_by = aggregation.get("group_by", sorted(REQUIRED_GROUP))
        if (
            not isinstance(self.group_by, list)
            or any((not isinstance(x, str) for x in self.group_by))
            or set(self.group_by) - GROUP_FIELDS
        ):
            raise ValueError("Unsupported aggregation.group_by field")
        if not REQUIRED_GROUP <= set(self.group_by):
            raise ValueError("group_by must include code, code_system, unit, unit_system and reference_range")
        self.output = table(
            data.get("output", {}),
            "output",
            {
                "format",
                "path",
                "overwrite",
                "include_evidence",
                "mode",
                "include_distribution",
                "lookup_pack",
            },
        )
        if self.output.get("format", "csv") != "csv":
            raise ValueError("Batch output format must be csv")
        if any((type(self.output.get(key, False)) is not bool for key in ("overwrite", "include_evidence"))):
            raise ValueError("Output switches must be true or false")
        self.network_mode = data.get("network", {}).get("mode", "offline")
        if self.network_mode not in {"offline", "lookups"}:
            raise ValueError("network.mode must be offline or lookups")
        if self.output.get("mode", "distinct") not in {"distinct", "items"}:
            raise ValueError("output.mode must be distinct or items")
        if self.output.get("mode") == "items" and self.input.get("granularity", "individual") != "individual":
            raise ValueError("Item mode needs individual input; distinct exports cannot restore source IDs")
        if spec.get("STRUCTURE_DEFINITION") and spec.get("PROFILE_FILE"):
            raise ValueError("Choose STRUCTURE_DEFINITION or PROFILE_FILE")
        if type(self.output.get("include_distribution", True)) is not bool:
            raise ValueError("include_distribution must be true or false")
        table(data.get("study", {}), "study", {"cohort", "period_start", "period_end", "source_label"})
        table(data.get("quality", {}), "quality", {"concept_map", "review_registry"})
        self.database = table(
            data.get("database", {}),
            "database",
            {
                "DB_HOST",
                "DB_PORT",
                "DB_NAME",
                "DB_SCHEMA",
                "DB_TABLE",
                "DB_USER_ENV",
                "DB_PASSWORD_ENV",
                "DB_PASSWORD_FILE",
                "DB_DSN_ENV",
                "DB_DSN_FILE",
                "DB_SSLMODE",
                "PATH_TO_DB_CONFIG_TOML",
                "DB_USER_KEY",
                "DB_PASSWORD_KEY",
            },
        )
        self.elements = self.input.get("elements", {})
        if self.elements and self.input_type != "fhir_file":
            raise ValueError("input.elements is for FHIR; use input.columns for CSV/SQL")
        if not isinstance(self.elements, dict):
            raise ValueError("input.elements must be a table of semantic roles to FHIR element paths")
        if self.elements:
            from .elements import set_fhir_path

            for role, element in self.elements.items():
                if role not in {
                    "code",
                    "code_system",
                    "code_version",
                    "value",
                    "unit",
                    "unit_code",
                    "unit_system",
                    "reference_low",
                    "reference_high",
                    "reference_unit",
                    "reference_unit_system",
                    "specimen",
                    "method",
                }:
                    raise ValueError("Unsupported input.elements role")
                if (
                    not isinstance(element, str)
                    or not element.startswith("Observation.")
                    or "component[" in element
                ):
                    raise ValueError(
                        "Scalar input.elements paths must start with Observation; components use the native FHIR adapter"
                    )
                set_fhir_path({}, element.removeprefix("Observation."), "validate")
        granularity = self.input.get("granularity", "individual")
        if granularity not in {"individual", "distinct"}:
            raise ValueError("input.granularity must be individual or distinct")
        columns = roles(self.input.get("columns", {}))
        defaults = roles(self.input.get("defaults", {}))
        if self.input_type == "fhir_file" and granularity != "individual":
            raise ValueError(
                "Native FHIR Observations are individual records; export distinct frequency sets as mapped CSV/DB rows"
            )
        if self.input_type == "fhir_file" and (columns or defaults):
            raise ValueError(
                "FHIR files use native elements/input.elements; input.columns/defaults are for CSV or database rows"
            )
        supported = {
            "id",
            "code",
            "code_system",
            "code_version",
            "code_display",
            "code_text",
            "unit",
            "unit_code",
            "unit_system",
            "value",
            "comparator",
            "value_min",
            "value_max",
            "count",
            "reference_ranges_json",
            "source_test",
            "loinc_system",
            "loinc_component",
            "loinc_property",
            "loinc_time_aspect",
            "loinc_scale",
            "method",
            "method_json",
            "modifier_extensions_json",
            "status",
            "resource_json",
        }
        supported |= {"range_text"}
        for prefix in ("range_low", "range_high", "range_age_low", "range_age_high"):
            supported |= {
                prefix,
                *(prefix + "_" + name for name in ("value", "unit", "code", "system", "comparator")),
            }
        for prefix in ("range_type", "range_applies_to"):
            supported |= {prefix + "_" + name for name in ("system", "code", "text")}
        if (set(columns) | set(defaults)) - supported:
            raise ValueError("Unknown semantic column/default role; use the documented laboratory field map")
        if "value" in columns and {"value_min", "value_max"} & set(columns):
            raise ValueError("Choose point values OR aggregate min/max, not an ambiguous combination")
        mapping = {
            "resource_type": "Observation",
            "profile": self.profile,
            "input_granularity": granularity,
            "fields": columns,
            "defaults": defaults,
        }
        source = {"kind": "file", "path": self.input.get("path"), "snapshot": self.input.get("snapshot")}
        if self.input_type in {"csv", "sqlite"}:
            source["columns"] = {
                key: mapping[key] for key in ("profile", "input_granularity", "fields", "defaults")
            }
        if self.input_type in {"postgres", "sqlite"}:
            schema, relation = (self.database.get("DB_SCHEMA", ""), self.database.get("DB_TABLE", ""))
            mapping["relation"] = f"{schema}.{relation}" if schema else relation
            validate_mapping(mapping)
        self.mapping = mapping
        postgres = {}
        if self.input_type == "postgres":
            source = {**source, "kind": "postgres", "mapping": mapping}
            source.pop("path", None)
            postgres = self.postgres_settings()
        research = table(
            data.get("research", {}),
            "research",
            {"enabled", "scope", "unit_catalog", "pubmed", "loinc_website_base_url", "sources", "europe_pmc", "max_articles_per_loinc"},
        )
        if type(research.get("enabled", False)) is not bool:
            raise ValueError("research.enabled must be true or false")
        pubmed = table(
            research.get("pubmed", {}),
            "research.pubmed",
            {"API_KEY_ENV", "API_KEY_FILE", "EMAIL", "MAX_RESULTS", "BASE_URL"},
        )
        sources = research.get("sources", ["pubmed"])
        if not isinstance(sources, list) or not set(sources) <= {"pubmed", "europe_pmc"} or len(sources) != len(set(sources)):
            raise ValueError("research.sources must select pubmed and/or europe_pmc without duplicates")
        europe = table(research.get("europe_pmc", {}), "research.europe_pmc", {"BASE_URL", "MAX_RESULTS", "QUERY_FILTER"})
        if not isinstance(europe.get("QUERY_FILTER", ""), str) or len(europe.get("QUERY_FILTER", "")) > 1000:
            raise ValueError("Europe PMC QUERY_FILTER must be a bounded public search expression")
        budget = research.get("max_articles_per_loinc", 3)
        if type(budget) is not int or not 1 <= budget <= 12:
            raise ValueError("research.max_articles_per_loinc must be 1..12")
        terminology = self.lookup_settings(data.get("lookups", {}))
        if self.network_mode == "offline":
            if research.get("enabled", False):
                raise ValueError("Offline mode cannot retrieve research evidence; use a local reference pack")
            terminology = {
                "sources": [
                    row
                    for row in terminology.get("sources", [])
                    if row["kind"] in {"csv", "sqlite", "fhir_artifacts"}
                ]
            }
        audit = {
            "profile": self.profile,
            "delimiter": self.input.get("delimiter", ","),
            "context": self.input.get("context", {}),
        }
        if spec.get("PROFILE_FILE"):
            audit["profile_config"] = spec["PROFILE_FILE"]
        if self.input.get("context_file"):
            audit["context_file"] = self.input["context_file"]
        translated = {
            "version": 1,
            "run": {
                "source": "input",
                "output": "output",
                "research": research.get("enabled", False),
                "model_alias": general.get("MODEL_ALIAS", "local"),
            },
            "audit": audit,
            "sources": {"input": source},
            "models": data.get("models", {}),
            "network": {key: value for key, value in data.get("network", {}).items() if key != "mode"},
            "postgres": postgres,
            "terminology": terminology,
            "research": {
                key: value
                for key, value in research.items()
                if key in {"scope", "unit_catalog", "loinc_website_base_url"}
            },
        }
        translated["research"]["pubmed"] = {
            "api_key_env"
            if key == "API_KEY_ENV"
            else "api_key_file"
            if key == "API_KEY_FILE"
            else "email"
            if key == "EMAIL"
            else "base_url"
            if key == "BASE_URL"
            else "max_results": value
            for key, value in pubmed.items()
        }
        translated["research"]["sources"] = sources
        translated["research"]["max_articles_per_loinc"] = budget
        translated["research"]["europe_pmc"] = {
            "base_url": research.get("europe_pmc", {}).get("BASE_URL", "https://www.ebi.ac.uk/europepmc/webservices/rest"),
            "max_results": research.get("europe_pmc", {}).get("MAX_RESULTS", 3),
            "query_filter": research.get("europe_pmc", {}).get("QUERY_FILTER", ""),
        }
        self.deployment = Deployment(self.path, data=translated)

    def path_value(self, value):
        path = Path(value)
        return path.resolve() if path.is_absolute() else (self.root / path).resolve()

    def postgres_settings(self):
        db = self.database
        if db.get("PATH_TO_DB_CONFIG_TOML"):
            return {"dsn_env": "SQC_BATCH_CDS_DSN"}
        names = {
            "DB_HOST": "host",
            "DB_PORT": "port",
            "DB_NAME": "database",
            "DB_USER_ENV": "username_env",
            "DB_PASSWORD_ENV": "password_env",
            "DB_PASSWORD_FILE": "password_file",
            "DB_DSN_ENV": "dsn_env",
            "DB_DSN_FILE": "dsn_file",
            "DB_SSLMODE": "sslmode",
        }
        return {names[key]: value for key, value in db.items() if key in names}

    def toolchain_database(self):
        """Reuse a local Toolchain file without copying its credentials into outputs."""
        from urllib.parse import urlencode

        path = self.path_value(self.database["PATH_TO_DB_CONFIG_TOML"])
        db = tomllib.loads(path.read_text())
        user = db.get(self.database.get("DB_USER_KEY", "DB_DATAPROCESSOR_USER"))
        password = db.get(self.database.get("DB_PASSWORD_KEY", "DB_DATAPROCESSOR_PASSWORD"))
        if not all((db.get(key) for key in ("DB_HOST", "DB_PORT", "DB_NAME"))) or not user or (not password):
            raise ValueError(
                "The referenced CDS Toolchain database file lacks the selected connection fields"
            )
        host = db["DB_HOST"]
        if not isinstance(host, str) or any((char in host for char in " /?#@")):
            raise ValueError("Invalid CDS Toolchain DB_HOST")
        host = f"[{host}]" if ":" in host else host
        return (
            f"postgresql://{quote(str(user), safe='')}:{quote(str(password), safe='')}@{host}:{int(db['DB_PORT'])}/{quote(db['DB_NAME'], safe='')}?"
            + urlencode({"sslmode": self.database.get("DB_SSLMODE", "require"), "connect_timeout": 10})
        )

    def lookup_settings(self, lookups):
        if not isinstance(lookups, dict):
            raise ValueError("lookups must contain named lookup tables")
        result = {"sources": []}
        for name, entry in lookups.items():
            row = table(
                entry,
                "lookup",
                {
                    "enabled",
                    "role",
                    "type",
                    "systems",
                    "endpoint",
                    "endpoint_env",
                    "username_env",
                    "username_file",
                    "password_env",
                    "password_file",
                    "token_env",
                    "token_file",
                    "path",
                    "complete",
                    "url_template",
                    "keywords",
                    "ontology",
                    "limit",
                    "release",
                    "language",
                    "token_endpoint",
                    "client_id_env",
                    "client_id_file",
                    "client_secret_env",
                    "client_secret_file",
                },
            )
            if type(row.get("enabled", False)) is not bool:
                raise ValueError("lookup.enabled must be true or false")
            if not row.get("enabled", False):
                continue
            if row.get("role", "primary") not in {"primary", "source", "optional"}:
                raise ValueError("lookup.role must be primary, source or optional")
            kind = row.get("type")
            if kind in {"ucum", "loinc"}:
                if row.get("role", "primary") != "primary":
                    raise ValueError("Dedicated UCUM/LOINC services are primary; configure generic optional sources for cross-checks")
                if kind in result:
                    raise ValueError("Choose one active UCUM service and one active LOINC service")
                result[kind] = {
                    "base_url"
                    if key == "endpoint"
                    else "base_url_env"
                    if key == "endpoint_env"
                    else key: value
                    for key, value in row.items()
                    if key
                    in {
                        "endpoint",
                        "endpoint_env",
                        "username_env",
                        "username_file",
                        "password_env",
                        "password_file",
                    }
                }
            elif kind in {"fhir", "csv", "sqlite", "website", "fhir_artifacts", "ols", "who_icd"}:
                if kind in {"ols", "who_icd"}:
                    from .terminology_apis import OlsNameSource, WhoIcdSource, ICD11_MMS
                    if kind == "ols":
                        OlsNameSource(row.get("endpoint", ""), ontology=row.get("ontology", ""), limit=row.get("limit", 5))
                    else:
                        if row.get("systems") != [ICD11_MMS]:
                            raise ValueError("WHO ICD source must explicitly select the ICD-11 MMS canonical system")
                        if not all(row.get(key + "_env") or row.get(key + "_file") for key in ("client_id", "client_secret")):
                            raise ValueError("WHO ICD source needs client_id and client_secret environment/file references")
                        WhoIcdSource(row.get("endpoint", ""), release=row.get("release", ""), language=row.get("language", "en"),
                            token_endpoint=row.get("token_endpoint", ""), client_id="", client_secret="")
                if row.get("endpoint_env"):
                    raise ValueError(
                        "Generic FHIR lookup endpoints use endpoint; credentials remain environment/file references"
                    )
                if row.get("username_env") or row.get("password_env"):
                    raise ValueError(
                        "Generic FHIR lookups support bearer tokens; use a gateway for other authentication. LOINC supports Basic Auth"
                    )
                result["sources"].append(
                    {
                        "kind": kind,
                        **{
                            "base_url" if key == "endpoint" else key: value
                            for key, value in row.items()
                            if key
                            in {
                                "systems",
                                "role",
                                "endpoint",
                                "token_env",
                                "token_file",
                                "path",
                                "complete",
                                "url_template",
                                "keywords",
                                "ontology",
                                "limit",
                                "release",
                                "language",
                                "token_endpoint",
                                "client_id_env",
                                "client_id_file",
                                "client_secret_env",
                                "client_secret_file",
                            }
                        },
                    }
                )
            else:
                raise ValueError(f"Unsupported active lookup type: {name}")
        return result


def selected_elements(record, config):
    """Explicit scalar element selection, preserving unselected FHIR context."""
    if not config.elements:
        return record
    from copy import deepcopy
    from .elements import set_fhir_path
    import re

    def get(path):
        current = record.resource
        for token, index in re.findall("([A-Za-z][A-Za-z0-9]*)(?:\\[(\\d+)\\])?", path)[1:]:
            current = current.get(token) if isinstance(current, dict) else None
            if index:
                current = (
                    current[int(index)] if isinstance(current, list) and len(current) > int(index) else None
                )
        return current

    resource = deepcopy(record.resource)
    if (
        any((role.startswith("reference_") for role in config.elements))
        and len(record.resource.get("referenceRange", [])) > 1
    ):
        raise ValueError(
            "Scalar reference element selection cannot discard multiple reference ranges; use native FHIR elements"
        )
    targets = {
        "code": "Observation.code.coding[0].code",
        "code_system": "Observation.code.coding[0].system",
        "code_version": "Observation.code.coding[0].version",
        "value": "Observation.valueQuantity.value",
        "unit": "Observation.valueQuantity.unit",
        "unit_code": "Observation.valueQuantity.code",
        "unit_system": "Observation.valueQuantity.system",
        "reference_low": "Observation.referenceRange[0].low.value",
        "reference_high": "Observation.referenceRange[0].high.value",
    }
    for role, path in config.elements.items():
        value = get(path)
        if value is None:
            raise ValueError(f"Selected FHIR element is missing: {path}")
        if role in {"specimen", "method"}:
            record.context["loinc_system" if role == "specimen" else role] = (
                "" if value is None else str(value)
            )
        elif role in {"reference_unit", "reference_unit_system"}:
            for side in ("low", "high"):
                set_fhir_path(
                    resource,
                    f"referenceRange[0].{side}.{('code' if role == 'reference_unit' else 'system')}",
                    value,
                )
        else:
            set_fhir_path(resource, targets[role].removeprefix("Observation."), value)
    record.resource = resource
    return record


def records(config, prepared):
    source = prepared.source
    context = dict(prepared.audit.context)
    if config.input_type == "csv":
        yield from file_records(
            source["path"], source.get("columns"), delimiter=prepared.audit.delimiter, context=context
        )
    elif config.input_type == "postgres":
        for index, row in enumerate(scan(config.mapping, os.environ["SQC_CFG_DB_DSN"]), 1):
            yield record_from_row(row, config.mapping, f"db-row:{index}", context)
    elif config.input_type == "sqlite":
        columns = ", ".join(
            (f'"{column}" AS "{logical}"' for logical, column in config.mapping["fields"].items())
        )
        relation = ".".join(
            ('"' + part + '"' for part in _identifier_parts(config.mapping["relation"], relation=True))
        )
        uri = "file:" + quote(str(source["path"]), safe="/") + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA query_only=ON")
            for index, row in enumerate(db.execute(f"SELECT {columns} FROM {relation}"), 1):
                yield record_from_row(dict(row), config.mapping, f"db-row:{index}", context)
    else:
        raise ValueError("Native FHIR exports use the laboratory FHIR reader")


def run_batch(path: str | Path) -> BatchResult:
    """Review a laboratory export with one explicit TOML configuration."""
    from .export_review import review_export

    return review_export(BatchConfiguration(path))


def run_plausibility_check(path: str | Path) -> BatchResult:
    """Deterministic source/target checks and approved mapping reuse; no model research."""
    from .export_review import review_export
    return review_export(BatchConfiguration(path, research=False))
