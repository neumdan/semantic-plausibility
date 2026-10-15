#!/usr/bin/env bash
# Real-service laboratory and medication example runs. Requires Bash, Podman and podman-compose.
set -Eeuo pipefail
umask 077

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
DRY_RUN=false
REBUILD=false
LOOKUP_ONLY=false
LAB_ONLY=false
for argument in "$@"; do
case "$argument" in
  --dry-run) DRY_RUN=true ;;
  --rebuild) REBUILD=true ;;
  --lookup-only) LOOKUP_ONLY=true ;;
  --lab-only) LAB_ONLY=true ;;
  -h|--help)
    printf '%s\n' 'Usage: bash tools/run-podman-examples.sh [--lab-only] [--lookup-only] [--dry-run] [--rebuild]' \
      'Runs four laboratory modes, four medication types and a medication terminology example.' \
      'Uses real APIs, local Matchbox/Blaze and Ollama; first use downloads images/model.' \
      'Writes separate reports under output/podman-examples-<timestamp>-<unique suffix>/.' \
      '--lab-only runs four laboratory modes; omit it to also run five medication A1 examples.' \
      '--lookup-only runs six A1 reports (one with --lab-only), without Ollama or the FHIR validator.' \
      '--dry-run prints the plan without creating files, downloading or running containers.' \
      '--rebuild explicitly rebuilds the runner and validator, retaining cached layers/volumes.'
    exit 0 ;;
  *) printf 'Unknown argument: %s\n' "$argument" >&2; exit 2 ;;
esac
done

# shellcheck source=container-cache.sh
source "$ROOT/tools/container-cache.sh"

cd "$ROOT"
STATE="$ROOT/local/podman-examples"
# Stable across checkout moves. Run this example stack from one checkout at a time.
PROJECT=semantic-plausibility-examples
RUNNER_SHA=dry-run
MATCHBOX_SHA=dry-run
RUN_NAME=podman-examples-DRY-RUN
FAILURES=0

pc() {
  # Only these public test settings override the operator's Compose selection.
  # Secrets remain in .env; no environment dump or shell tracing is used.
  env SQC_CONFIG_FILE=pipeline.example.toml SQC_INPUT_DIR="$STATE/empty-input" \
    SQC_LOCAL_DIR="$STATE" SQC_OLLAMA_PORT= SQC_MATCHBOX_PORT= SQC_BLAZE_PORT= \
    SQC_RUNNER_BUILD_INPUTS="$RUNNER_SHA" SQC_MATCHBOX_BUILD_INPUTS="$MATCHBOX_SHA" \
    podman-compose -f "$ROOT/compose.yaml" --env-file "$ROOT/.env" \
    -p "$PROJECT" --profile validation --profile research "$@"
}

step() {
  printf '\n>>> %s\n' "$1"
  shift
  if "$DRY_RUN"; then printf '  '; printf '%q ' "$@"; printf '\n'; return 0; fi
  "$@"
}

stop_services() {
  if "$DRY_RUN"; then printf '  pc stop'; printf ' %s' "$@"; printf '\n'; return 0; fi
  local service container
  for service in "$@"; do
    container="$(podman ps -aq --filter "label=com.docker.compose.project=$PROJECT" --filter "label=com.docker.compose.service=$service")"
    if [[ -n "$container" ]]; then pc stop "$service" || return $?; fi
  done
}

cleanup() {
  local status=$?
  trap - EXIT
  # Stop only this script's project; keep model/terminology volumes and reports.
  if ! "$LOOKUP_ONLY" && ! stop_services ollama matchbox blaze; then
    printf 'Could not stop test services. Project: %s\n' "$PROJECT" >&2
    status=1
  fi
  rmdir "$STATE/.run.lock" || true
  printf '\nReports and run log: %s\n' "$RUN_DIR"
  exit "$status"
}

if ! "$DRY_RUN"; then
  for dependency in podman podman-compose; do
    command -v "$dependency" >/dev/null || { printf 'Install %s first.\n' "$dependency" >&2; exit 2; }
  done
  if ! podman info >/dev/null; then
    printf 'Podman is unavailable. On macOS/Windows, start your Podman machine first.\n' >&2
    exit 2
  fi
  if [[ -n "$(podman ps --filter "label=com.docker.compose.project=$PROJECT" --format '{{.ID}}')" ]]; then
    printf 'The persistent example stack is already running. Finish that run before starting another.\n' >&2
    exit 2
  fi
  mkdir -p "$STATE/empty-input" "$ROOT/output"
  if ! mkdir "$STATE/.run.lock" 2>/dev/null; then
    printf 'Another example run holds %s/.run.lock. If it was interrupted, confirm its process has stopped before removing that empty lock directory.\n' "$STATE" >&2
    exit 2
  fi
  if ! RUN_DIR="$(mktemp -d "$ROOT/output/podman-examples-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")"; then
    rmdir "$STATE/.run.lock"
    exit 2
  fi
  RUN_NAME="${RUN_DIR##*/}"
  trap cleanup EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  if [[ ! -e .env ]]; then cp .env.example .env; fi
  exec > >(tee -a "$RUN_DIR/run.log") 2>&1
  printf 'mode\texit_code\tstate\treport\n' > "$RUN_DIR/summary.tsv"
  printf '%s\n' '<!doctype html><html lang="en"><meta charset="utf-8"><title>Example runs</title><h1>Example runs</h1><p>Completed means the command finished; inspect each report for findings, missing evidence and model failures.</p><ul>' > "$RUN_DIR/index.html"
fi
RUN_DIR="$ROOT/output/$RUN_NAME"
CONTAINER_RUN="/app/output/$RUN_NAME"

wait_healthy() {
  local service=$1 container attempt=0 deadline=$((SECONDS + 600))
  if "$DRY_RUN"; then printf '  Wait for %s health (maximum 10 minutes).\n' "$service"; return 0; fi
  container="$(podman ps -a --filter "label=com.docker.compose.project=$PROJECT" \
    --filter "label=com.docker.compose.service=$service" --format '{{.ID}}')"
  if [[ -z "$container" || "$container" == *$'\n'* ]]; then
    printf 'Expected one %s container in project %s.\n' "$service" "$PROJECT" >&2
    return 1
  fi
  while (( SECONDS < deadline )); do
    if podman healthcheck run "$container" >/dev/null 2>&1; then return 0; fi
    if (( attempt % 6 == 0 )); then printf 'Waiting for %s to become healthy…\n' "$service"; fi
    sleep 5
    attempt=$((attempt + 1))
  done
  printf '%s did not become healthy.\n' "$service" >&2
  pc logs --tail=40 "$service" || true
  return 1
}

record() {
  local mode=$1 code=$2 state=$3
  if [[ "$code" != 0 ]]; then FAILURES=$((FAILURES + 1)); fi
  if ! "$DRY_RUN"; then
    printf '%s\t%s\t%s\t%s\n' "$mode" "$code" "$state" "$mode/report.csv" >> "$RUN_DIR/summary.tsv"
    if [[ -f "$RUN_DIR/$mode/report.html" ]]; then
      printf '<li><a href="%s/report.html">%s</a>: %s (exit %s)</li>\n' "$mode" "$mode" "$state" "$code" >> "$RUN_DIR/index.html"
    else
      printf '<li>%s: %s (exit %s); see run.log</li>\n' "$mode" "$state" "$code" >> "$RUN_DIR/index.html"
    fi
  fi
}

report() {
  local mode=$1 code=0
  shift
  if ! "$DRY_RUN"; then mkdir "$RUN_DIR/$mode"; fi
  step "$mode" pc run --rm "$@" --out "$CONTAINER_RUN/$mode/report.csv" || code=$?
  if [[ "$code" == 0 ]]; then record "$mode" 0 completed; else record "$mode" "$code" nonzero; fi
  return "$code"
}

SQC_CONFIGURATION_SUMMARY='pipeline.example.toml'
if ! "$LOOKUP_ONLY"; then SQC_CONFIGURATION_SUMMARY+='; examples/lab/fhir-csv.toml'; fi
if ! "$LAB_ONLY"; then SQC_CONFIGURATION_SUMMARY+='; examples/medication/{request,administration,product,statement,terminology}.toml'; fi
printf '%s\n' 'Example inputs; real terminology APIs; selected local services.' \
  "Configuration: $SQC_CONFIGURATION_SUMMARY" \
  "Private test caches: $STATE" "Compose project: $PROJECT" \
  'Host ports are assigned dynamically on localhost; services communicate inside the stack.'
if ! "$DRY_RUN"; then
  RUNNER_SHA="$(sqc_fingerprint "$ROOT" compose.yaml .dockerignore deploy/Dockerfile pyproject.toml uv.lock README.md LICENSE NOTICE src examples terminology.example.toml)"
fi
sqc_ensure_image run-plausibility-check semantic-plausibility-stack:local "$RUNNER_SHA"

prepare_matchbox() {
  if ! "$DRY_RUN"; then
    MATCHBOX_SHA="$(sqc_fingerprint "$STATE/validation/matchbox" .)" || return $?
  fi
  sqc_ensure_image matchbox semantic-plausibility-matchbox:4.1.21 "$MATCHBOX_SHA" \
    && step 'Start cached Matchbox/HL7' pc up -d matchbox && wait_healthy matchbox
}

lookup_ready=false
if report 01-lab-lookup run-plausibility-check check --config /app/pipeline.toml; then lookup_ready=true; fi

if ! "$LOOKUP_ONLY"; then
if step 'Prepare pinned MII laboratory validator' pc run --rm validator-init validator-init \
     --config /app/examples/lab/fhir-csv.toml --overwrite \
   && step 'Start local terminology service' pc up -d blaze && wait_healthy blaze \
   && step 'Install real guide terminology' pc run --rm terminology-init terminology-init \
     --config /app/examples/lab/fhir-csv.toml \
   && prepare_matchbox; then
  report 02-lab-fhir-validation run-plausibility-check check --config /app/examples/lab/fhir-csv.toml || true
else
  record 02-lab-fhir-validation - setup_failed
fi
step 'Release validator memory before model inference' stop_services matchbox blaze || true

if "$lookup_ready" \
   && step 'Start local Ollama' pc up -d ollama && wait_healthy ollama \
   && step 'Provision configured model' pc run --rm model-init \
   && step 'Download real UCUM catalogue' pc run --rm catalog-init; then
  report 03-lab-ai-recommendations research-evidence research-evidence --config /app/pipeline.toml \
    --scope lookup --report "$CONTAINER_RUN/01-lab-lookup/report.csv" || true
  report 04-lab-ai-evidence research-evidence research-evidence --config /app/pipeline.toml \
    --scope studies --report "$CONTAINER_RUN/01-lab-lookup/report.csv" || true
else
  record 03-lab-ai-recommendations - dependency_failed
  record 04-lab-ai-evidence - dependency_failed
fi

fi # Full lab workflow; --lookup-only skips validator and model services.

if ! "$LAB_ONLY"; then
report 05-medication-request run-plausibility-check check --config /app/examples/medication/request.toml || true
report 06-medication-administration run-plausibility-check check --config /app/examples/medication/administration.toml || true

report 07-medication-product run-plausibility-check check --config /app/examples/medication/product.toml || true
report 08-medication-statement run-plausibility-check check --config /app/examples/medication/statement.toml || true
report 09-medication-terminology run-plausibility-check check --config /app/examples/medication/terminology.toml || true
fi

if ! "$DRY_RUN"; then
  printf '%s\n' '</ul><p><a href="summary.tsv">Exit summary</a> · <a href="run.log">Run log</a></p></html>' >> "$RUN_DIR/index.html"
  printf '\n'; cat "$RUN_DIR/summary.tsv"
fi
printf '\nEach mode writes its own report.csv, report.html and provenance files.\n'
if (( FAILURES > 0 )); then printf '%s mode(s) need attention; see summary.tsv and run.log.\n' "$FAILURES" >&2; exit 1; fi
