# Sourced by run-podman-examples.sh; no commands run at import time.
# Fingerprints include relative filenames and content, never timestamps or paths
# to the checkout. Keep build inputs aligned with the Dockerfile COPY statements.
sqc_hash() {
  if command -v sha256sum >/dev/null; then sha256sum; else shasum -a 256; fi
}

sqc_fingerprint() {
  (
    set -e
    cd "$1" || exit 1
    shift
    printf 'sqc-build-format=1\n'
    for input in "$@"; do
      if [[ -d "$input" ]]; then
        find "$input" -type f ! -name '*.pyc' ! -path '*/__pycache__/*' -print || exit 1
      else
        printf '%s\n' "$input"
      fi
    done | LC_ALL=C sort | while IFS= read -r file; do
      printf '%s\n' "$file"
      sqc_hash < "$file" || exit 1
    done
  ) | sqc_hash | awk '{print $1}'
}

sqc_ensure_image() {
  local service=$1 image=$2 fingerprint=$3 existing=
  if "$DRY_RUN"; then
    if "$REBUILD"; then
      step "Force build: $service" pc build "$service"
    else
      printf '  Reuse %s when its build fingerprint matches; otherwise build %s.\n' "$image" "$service"
    fi
    return 0
  fi
  if ! "$REBUILD" && podman image exists "$image"; then
    existing="$(podman image inspect --format '{{index .Config.Labels "io.semantic-plausibility.build-inputs"}}' "$image")"
  fi
  if [[ "$existing" != "$fingerprint" ]]; then
    step "Build changed/missing image: $service" pc build "$service" || return $?
  else
    printf 'Reusing %s (build inputs %s).\n' "$image" "$fingerprint"
  fi
  existing="$(podman image inspect --format '{{index .Config.Labels "io.semantic-plausibility.build-inputs"}}' "$image")"
  if [[ "$existing" != "$fingerprint" ]]; then
    printf 'Image fingerprint verification failed: %s\n' "$image" >&2
    return 1
  fi
  podman image inspect --format 'Image: {{.Id}}' "$image"
}
