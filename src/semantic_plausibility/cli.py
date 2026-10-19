"""Command line interface for the local laboratory and medication export workflows."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tomllib


def log_configuration(configuration):
    """Explain the selected workflow without logging credentials or source values."""
    from .progress import LOGGER

    LOGGER.info("Configuration file: %s", configuration.path)
    LOGGER.info("Input: %s; resource: %s; mapped columns: %s", configuration.input_type,
                configuration.kind, len(configuration.input.get("columns", {})))
    LOGGER.info("Input granularity: %s -> output: %s; network: %s",
                configuration.input.get("granularity", "individual"),
                configuration.output.get("mode", "distinct"), configuration.network_mode)
    LOGGER.info("Target profile: %s; full-resource validation: %s", configuration.profile,
                "enabled" if configuration.validation.get("enabled") else "disabled")
    if configuration.data.get("decision", {}).get("enabled"):
        LOGGER.info("Optional Laya input assessment enabled; model suggestions are recorded for review")
    for alias, lookup in configuration.data.get("lookups", {}).items():
        if lookup.get("enabled", False):
            LOGGER.info("Terminology %s: %s (%s); %s", alias, lookup.get("type"), lookup.get("role", "primary"),
                        lookup.get("path") or lookup.get("endpoint") or "configured endpoint")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read CSV, FHIR or database data; group laboratory values; check terminology and plausibility.",
        epilog="Start: config-check → check → research-evidence --scope studies. Configure pipeline.toml; see examples/lab/README.md."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    batch = sub.add_parser("batch", help="Review an export and write CSV, HTML, evidence and an audit log")
    batch.add_argument("--config", type=Path, default=Path("pipeline.toml"))
    check = sub.add_parser("check", help="Read and group source data; check code/name/value/unit/reference context; write CSV + HTML")
    check.add_argument("--config", type=Path, default=Path("pipeline.toml"))
    check.add_argument("--out", type=Path, help="Fresh CSV output prefix; relative to the selected TOML")
    research = sub.add_parser("research-evidence", help="Add sourced AI proposals to a checked, aggregated laboratory report")
    research.add_argument("--config", type=Path, default=Path("pipeline.toml"))
    research.add_argument("--report", type=Path)
    research.add_argument("--out", type=Path)
    research.add_argument("--plan", action="store_true", help="Print exact prompts/planned searches without network/model calls")
    research.add_argument("--scope", choices=("lookup", "studies"), help="Override TOML: terminology-only AI or AI plus literature retrieval")
    feedback = sub.add_parser("review-import", help="Import clinician decisions into a scoped, versioned mapping registry")
    feedback.add_argument("--config", type=Path, default=Path("pipeline.toml"))
    feedback.add_argument("--report", type=Path, required=True)
    feedback.add_argument("--review", type=Path, required=True)
    feedback.add_argument("--registry", type=Path, required=True)
    compare = sub.add_parser("compare", help="Compare matched LIS/FHIR/CDS Hub distinct reports")
    compare.add_argument("--baseline", type=Path, required=True)
    compare.add_argument("--target", type=Path, action="append", required=True)
    compare.add_argument("--out", type=Path, required=True)
    verify = sub.add_parser("verify", help="Verify a completed run's log and artifact hashes")
    verify.add_argument("audit", type=Path)
    profile = sub.add_parser(
        "profile-import", help="Project selected laboratory rules from a pinned R4 snapshot"
    )
    profile.add_argument("structure_definition", type=Path)
    profile.add_argument("--out", type=Path, required=True)
    acquire = sub.add_parser("profile-init", help="Download the pinned profile and bindings into local runtime files")
    acquire.add_argument("--config", type=Path, default=Path("pipeline.toml"))
    config = sub.add_parser("config-check", help="Check TOML setup; optional explicit connection probes")
    config.add_argument("--config", type=Path, default=Path("pipeline.toml"))
    config.add_argument("--connections", action="store_true")
    config.add_argument("--out", type=Path)
    packages = sub.add_parser("package-init", help="Install explicitly enabled, pinned BfArM FHIR packages locally")
    packages.add_argument("--config", type=Path, default=Path("pipeline.toml"))
    packages.add_argument("--package", default=None, help="Optional configured package alias")
    updates = sub.add_parser("package-check", help="Check configured package release metadata; never download content")
    updates.add_argument("--config", type=Path, default=Path("pipeline.toml"))
    lookup = sub.add_parser("lookup", help="Inspect one configured code or name source; no batch/model is started")
    lookup.add_argument("--config", type=Path, default=Path("pipeline.toml"))
    lookup.add_argument("--lookup", required=True, help="Alias in [lookups]")
    lookup.add_argument("--system", default="")
    lookup.add_argument("--code", default="")
    lookup.add_argument("--version", default="")
    lookup.add_argument("--name", default="")
    validator = sub.add_parser("validator-init", help="Prepare pinned Matchbox/MII configuration from the selected FHIR guide")
    validator.add_argument("--config", type=Path, default=Path("pipeline.toml"))
    validator.add_argument("--overwrite", action="store_true")
    terms = sub.add_parser("terminology-init", help="Install the explicitly supplied real guide terminology in the local validator TX service")
    terms.add_argument("--config", type=Path, default=Path("pipeline.toml"))
    validate = sub.add_parser("validate-fhir", help="Validate actual FHIR resources locally with Matchbox or the MII validator")
    validate.add_argument("--config", type=Path, default=Path("pipeline.toml"))
    validate.add_argument("--input", type=Path)
    validate.add_argument("--out", type=Path)
    decision = sub.add_parser("decision-serve", help="Serve the optional pinned Laya decision model locally")
    decision.add_argument("--config", type=Path, default=Path("pipeline.toml"))
    for name, help_text in (
        ("model-init", "Provision the configured local Ollama model"),
        ("catalog-init", "Prepare the configured local unit catalogue"),
    ):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("--config", type=Path, default=Path("pipeline.toml"))
        if name != "catalog-init":
            command.add_argument("--model-alias", default=None)
    from .progress import LEVELS, configure, LOGGER
    for command in sub.choices.values():
        command.add_argument("--log-level", choices=LEVELS, help="Console verbosity; the audit always retains every event")
    args = parser.parse_args(argv)
    try:
        settings = tomllib.loads(args.config.read_text()).get("logging", {}) if hasattr(args, "config") else {}
        configure(args.log_level or settings.get("level", "INFO"))
        if args.command in {"batch", "check"}:
            from .batch import BatchConfiguration, run_batch, run_plausibility_check

            configuration = BatchConfiguration(args.config, research=False if args.command == "check" else None,
                                               output_path=getattr(args, "out", None))
            log_configuration(configuration)
            LOGGER.info("Processing: read input -> group complete semantic sets -> check terminology and field consistency -> CSV + HTML")
            result = run_plausibility_check(args.config, output=args.out) if args.command == "check" else run_batch(args.config)
            print(f"{result.input_records} source records -> {result.distinct_tuples} {configuration.kind} review rows.")
            print("Status counts: " + json.dumps(result.statuses, sort_keys=True))
            print(f"Review CSV: {result.csv}")
            print(f"HTML report: {result.report}")
            print(f"Audit log: {result.log}")
            summary = json.loads(result.summary.read_text())
            validation = summary.get("fhir_validation", {})
            print(f"FHIR resource validation: {validation.get('state', 'disabled')}")
            metric = summary["metrics"].get("mii_numeric_set_ready", summary["metrics"].get("terminology_and_dose_consistent"))
            print(
                f"Verified check coverage: {metric['numerator']}/{metric['denominator']} (not full FHIR validation)."
            )
            if configuration.validation.get("fail_on_error") and validation.get("state") in {"invalid", "incomplete", "unavailable"}:
                return 1
        elif args.command == "research-evidence":
            from .evidence_research import research_evidence, research_plan
            result = research_plan(args.config, args.report, scope=args.scope) if args.plan else research_evidence(args.config, args.report, args.out,
                scope=args.scope, progress=lambda message: print(message, flush=True))
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == "profile-init":
            from .profile_setup import initialise_profile
            print(json.dumps(initialise_profile(args.config), indent=2))
        elif args.command == "validator-init":
            from .fhir_validation import initialise_validator
            print(json.dumps(initialise_validator(args.config, overwrite=args.overwrite), indent=2))
        elif args.command == "validate-fhir":
            from .fhir_validation import validate_fhir
            result = validate_fhir(args.config, args.input, args.out)
            print(json.dumps(result, indent=2))
            return 1 if result["state"] in {"invalid", "incomplete", "unavailable"} else 0
        elif args.command == "package-check":
            from .batch import BatchConfiguration
            from .terminology_packages import check_package_updates
            print(json.dumps(check_package_updates(BatchConfiguration(args.config, research=False)), indent=2))
        elif args.command == "terminology-init":
            from .fhir_validation import initialise_terminology
            print(json.dumps(initialise_terminology(args.config), indent=2))
        elif args.command == "review-import":
            from .review_feedback import import_reviews
            print(json.dumps(import_reviews(args.report, args.review, args.registry, args.config), indent=2))
        elif args.command == "compare":
            from .study_compare import compare_reports

            print(json.dumps(compare_reports(args.baseline, args.target, args.out), indent=2))
        elif args.command == "verify":
            from .study_compare import verify_audit

            print(json.dumps(verify_audit(args.audit)))
        elif args.command == "profile-import":
            from .profile_import import import_structure_definition

            if args.out.exists():
                raise ValueError("Profile output already exists")
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(
                json.dumps(import_structure_definition(args.structure_definition), indent=2) + "\n"
            )
            print(f"Selected laboratory policy: {args.out}")
        elif args.command == "config-check":
            from .batch import BatchConfiguration
            from .deployment_checks import check_configuration

            log_configuration(BatchConfiguration(args.config))
            report = check_configuration(args.config, connections=args.connections)
            target = args.out or args.config.resolve().parent / "output/config-check.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(report, indent=2) + "\n")
            print(json.dumps(report, indent=2))
            print(f"Configuration report: {target}")
            return 0 if report["passed"] else 1
        elif args.command == "decision-serve":
            from .input_decision import serve
            serve(args.config)
        elif args.command == "model-init":
            from .deployment_checks import initialise_model

            print(
                json.dumps(
                    initialise_model(args.config, model_alias=args.model_alias, progress=print), indent=2
                )
            )
        elif args.command == "catalog-init":
            from .deployment_checks import sync_catalogue

            print(json.dumps(sync_catalogue(args.config), indent=2))
        elif args.command == "package-init":
            from .terminology_packages import initialise_packages

            installed = initialise_packages(args.config, args.package)
            print(json.dumps({alias: {key: row[key] for key in ("name", "version", "path", "archive_sha256", "downloaded")}
                              for alias, row in installed.items()}, ensure_ascii=False, indent=2))
        elif args.command == "lookup":
            from .terminology_lookup import lookup_terminology

            print(json.dumps(lookup_terminology(args.config, args.lookup, system=args.system, code=args.code,
                version=args.version, name=args.name), ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, RuntimeError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    return 2


def run_main(argv=None):
    """The named runner defaults to the deterministic check; legacy CLI stays available."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    commands = {"batch", "check", "research-evidence", "review-import", "compare", "verify", "lookup", "decision-serve",
                "profile-import", "profile-init", "config-check", "model-init", "catalog-init", "package-init", "package-check", "validator-init", "terminology-init", "validate-fhir"}
    if not arguments or arguments[0] not in commands and arguments[0] not in {"-h", "--help"}:
        arguments.insert(0, "check")
    return main(arguments)


def research_main():
    return main(["research-evidence", *sys.argv[1:]])


if __name__ == "__main__":
    raise SystemExit(main())
