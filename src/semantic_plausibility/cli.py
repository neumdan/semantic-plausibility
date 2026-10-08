"""Command line interface for the current local laboratory export workflow."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Local laboratory export review; proposals for expert assessment"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    batch = sub.add_parser("batch", help="Review an export and write CSV, HTML, evidence and an audit log")
    batch.add_argument("--config", type=Path, default=Path("pipeline.toml"))
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
    for name, help_text in (
        ("model-init", "Provision the configured local Ollama model"),
        ("catalog-init", "Prepare the configured local unit catalogue"),
        ("model-test", "Check the configured model with synthetic extraction cases"),
    ):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("--config", type=Path, default=Path("pipeline.toml"))
        if name != "catalog-init":
            command.add_argument("--model-alias", default=None)
        if name == "model-test":
            command.add_argument("--output", type=Path, default=Path("output/model-check"))
    args = parser.parse_args(argv)
    try:
        if args.command == "batch":
            from .batch import run_batch

            result = run_batch(args.config)
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
        elif args.command == "model-test":
            from .deployment_checks import model_contract_test

            report = model_contract_test(args.config, output=args.output, model_alias=args.model_alias)
            print(
                f"Model {report['model']}: {report['passed']}/{report['total']} synthetic extraction cases passed."
            )
            print(f"Results: {args.output}.csv and {args.output}.json; no clinical accuracy claim.")
            return 1 if report["failed"] else 0
        return 0
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
