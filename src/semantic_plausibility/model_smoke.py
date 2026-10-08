"""Opt-in local-model smoke test with shipped, exclusively synthetic sources."""

from __future__ import annotations

import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import time
from typing import Any

from .model_config import select_model
from .observation_services import OpenAICompatibleJsonClient
from . import research_prompts as prompts


def cases() -> list[dict[str, Any]]:
    return json.loads(Path(__file__).with_name("model_cases.json").read_text(encoding="utf-8"))


def expectation_issues(expected: dict[str, Any], answer: dict[str, Any]) -> list[str]:
    issues = []
    for name, value in expected.items():
        if name == "units":
            matches = sorted(row["unit"] for row in answer["units"]) == sorted(value)
        elif name == "allowed_units":
            actual = sorted(row["unit"] for row in answer["units"])
            matches = any(actual == sorted(option) for option in value)
        elif name == "ranges":
            actual = answer["ranges"]
            matches = len(actual) == len(value) and all(
                any(all(row.get(key) == item for key, item in wanted.items()) for row in actual)
                for wanted in value
            )
        elif name == "claim_count":
            matches = len(answer["claims"]) == value
        else:
            matches = answer.get(name) == value
        if not matches:
            issues.append(f"Erwartung nicht erfüllt: {name}")
    return issues


def run_model_smoke(client: OpenAICompatibleJsonClient) -> dict[str, Any]:
    results = []
    for case in cases():
        instruction = getattr(prompts, case["task"])
        started = time.monotonic()
        answer, issues = None, []
        try:
            answer = client.json(instruction, case["evidence"])
            issues = expectation_issues(case["expected"], answer)
        except (ValueError, RuntimeError, OSError, KeyError, TypeError) as exc:
            issues = [f"Modellprüfung abgebrochen: {exc}"]
        abstained = not issues and "allowed_units" in case["expected"] and answer.get("units") == []
        results.append({
            **case, "status": "passed" if not issues else "failed", "response": answer,
            "outcome": "failed" if issues else "abstained" if abstained else "accepted",
            "issues": issues, "elapsed_seconds": round(time.monotonic() - started, 3),
            "prompt_sha256": hashlib.sha256(instruction.encode()).hexdigest(),
        })
    return {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "synthetic_extraction_smoke_test_not_clinical_validation",
        "model": client.model, "base_url": client.base_url,
        "temperature": client.temperature, "max_tokens": client.max_tokens,
        "system_prompt_sha256": hashlib.sha256(prompts.SYSTEM.encode()).hexdigest(),
        "total": len(results), "passed": sum(row["status"] == "passed" for row in results),
        "abstained": sum(row["outcome"] == "abstained" for row in results),
        "failed": sum(row["status"] == "failed" for row in results), "cases": results,
    }


def write_model_report(report: dict[str, Any], prefix: Path) -> None:
    prefix.parent.mkdir(parents=True, exist_ok=True)
    Path(str(prefix) + ".json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with Path(str(prefix) + ".csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["testfall", "pruefung", "status", "sekunden", "befund"])
        writer.writeheader()
        for row in report["cases"]:
            writer.writerow({
                "testfall": row["id"], "pruefung": row["title"],
                "status": "Enthalten (unklar)" if row["outcome"] == "abstained" else "Bestanden" if row["status"] == "passed" else "Prüfung erforderlich",
                "sekunden": row["elapsed_seconds"], "befund": "; ".join(row["issues"]),
            })


def model_test(models_file: Path | None, model_alias: str, output: Path) -> dict[str, Any]:
    config = models_file or (Path(os.environ["SQC_MODELS_FILE"]) if os.getenv("SQC_MODELS_FILE") else None)
    endpoint, api_key = select_model(model_alias, config)
    client = OpenAICompatibleJsonClient(
        base_url=endpoint.base_url, model=endpoint.model, api_key=api_key,
        temperature=endpoint.temperature, max_tokens=endpoint.max_tokens,
        timeout=endpoint.timeout_seconds, reasoning_effort=endpoint.reasoning_effort, response_mode=endpoint.response_mode,
        enable_thinking=endpoint.enable_thinking,
    )
    report = run_model_smoke(client)
    write_model_report(report, output)
    return report
