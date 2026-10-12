"""Command line interface for the current local laboratory export workflow."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Local laboratory export review; proposals for expert assessment"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    batch = sub.add_parser("batch", help="Review an export and write CSV, HTML, evidence and an audit log")
    batch.add_argument("--config", type=Path, default=Path("pipeline.toml"))
    check = sub.add_parser("check", help="Check source sets and target profile; reuse approved mappings, no model research")
    check.add_argument("--config", type=Path, default=Path("pipeline.toml"))
    research = sub.add_parser("research-evidence", help="Research real evidence from a completed check report")
    research.add_argument("--config", type=Path, default=Path("pipeline.toml"))
    research.add_argument("--report", type=Path)
    research.add_argument("--out", type=Path)
    research.add_argument("--plan", action="store_true", help="Print exact prompts/planned searches without network/model calls")
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
    config = sub.add_parser("config-check", help="Check TOML setup; optional explicit connection probes")
    config.add_argument("--config", type=Path, default=Path("pipeline.toml"))
    config.add_argument("--connections", action="store_true")
    config.add_argument("--out", type=Path)
    packages = sub.add_parser("package-init", help="Install explicitly enabled, pinned BfArM FHIR packages locally")
    packages.add_argument("--config", type=Path, default=Path("pipeline.toml"))
    packages.add_argument("--package", default=None, help="Optional configured package alias")
    lookup = sub.add_parser("lookup", help="Inspect one configured code or name source; no batch/model is started")
    lookup.add_argument("--config", type=Path, default=Path("pipeline.toml"))
    lookup.add_argument("--lookup", required=True, help="Alias in [lookups]")
    lookup.add_argument("--system", default="")
    lookup.add_argument("--code", default="")
    lookup.add_argument("--version", default="")
    lookup.add_argument("--name", default="")
    for name, help_text in (
        ("model-init", "Provision the configured local Ollama model"),
        ("catalog-init", "Prepare the configured local unit catalogue"),
    ):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("--config", type=Path, default=Path("pipeline.toml"))
        if name != "catalog-init":
            command.add_argument("--model-alias", default=None)
    args = parser.parse_args(argv)
    try:
        if args.command in {"batch", "check"}:
            from .batch import BatchConfiguration, run_batch, run_plausibility_check

            configuration = BatchConfiguration(args.config, research=False if args.command == "check" else None)
            print(f"Input: {configuration.input_type}; {configuration.input.get('path', 'configured database view')}", flush=True)
            print(f"Grouping: {configuration.output.get('mode', 'distinct')}; network: {configuration.network_mode}", flush=True)
            for name, lookup in configuration.data.get("lookups", {}).items():
                if lookup.get("enabled", False):
                    print(f"Lookup {name}: {lookup.get('endpoint', lookup.get('path', 'configured source'))}", flush=True)
            print("Local-model research: " + ("enabled" if configuration.data.get("research", {}).get("enabled", False) else "disabled"), flush=True)
            print("Processing: read -> group -> terminology/fit -> numeric/reference checks -> report", flush=True)
            result = run_plausibility_check(args.config) if args.command == "check" else run_batch(args.config)
            print(f"{result.input_records} input rows -> {result.distinct_tuples} laboratory review rows.")
            print("Status counts: " + json.dumps(result.statuses, sort_keys=True))
            print(f"Review CSV: {result.csv}")
            print(f"HTML report: {result.report}")
            print(f"Audit log: {result.log}")
            summary = json.loads(result.summary.read_text())
            metric = summary["metrics"]["mii_numeric_set_ready"]
            print(
                f"Numeric semantic readiness: {metric['numerator']}/{metric['denominator']} (not full FHIR validation)."
            )
        elif args.command == "research-evidence":
            from .evidence_research import research_evidence, research_plan
            result = research_plan(args.config, args.report) if args.plan else research_evidence(args.config, args.report, args.out)
            print(json.dumps(result, ensure_ascii=False, indent=2))
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
            from .deployment_checks import check_configuration

            report = check_configuration(args.config, connections=args.connections)
            target = args.out or args.config.resolve().parent / "output/config-check.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(report, indent=2) + "\n")
            print(json.dumps(report, indent=2))
            print(f"Configuration report: {target}")
            return 0 if report["passed"] else 1
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
    commands = {"batch", "check", "research-evidence", "review-import", "compare", "verify", "lookup",
                "profile-import", "config-check", "model-init", "catalog-init", "package-init"}
    if not arguments or arguments[0] not in commands and arguments[0] not in {"-h", "--help"}:
        arguments.insert(0, "check")
    return main(arguments)


def research_main():
    return main(["research-evidence", *sys.argv[1:]])


if __name__ == "__main__":
    raise SystemExit(main())
