#!/usr/bin/env bash
set -euo pipefail

fail() { echo "::error::$*" >&2; exit 1; }

[[ -f "$CHART/Chart.yaml" && -f "$VALUES" ]] || fail 'A chart and release values file are required.'
[[ "$SOURCE_SHA" =~ ^[0-9a-f]{40}$ ]] || fail 'A full application source SHA is required.'
[[ -n "$VERSION" ]] || fail 'A release version is required.'
[[ "$DESTINATION" == oci://* && "$DESTINATION" != *$'\n'* ]] || fail 'An OCI destination is required.'

[[ "${PUBLICATION_REPOSITORY:-}" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || fail 'A GitHub publication repository is required.'
[[ "${PUBLICATION_WORKFLOW_REF:-}" == "$PUBLICATION_REPOSITORY/.github/workflows/"*'@'* ]] || fail 'A matching GitHub workflow reference is required.'
[[ "${PUBLICATION_RUN_ID:-}" =~ ^[1-9][0-9]*$ && "${PUBLICATION_RUN_ATTEMPT:-}" =~ ^[1-9][0-9]*$ ]] || fail 'Positive GitHub run ID and attempt are required.'

for file in release-values.yaml publication.json; do
  [[ ! -e "$CHART/$file" && ! -L "$CHART/$file" ]] || fail "$file is reserved for the generated release."
done

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/chart" "$work/packages"

# Keep central defaults/templates unchanged; add a values overlay to the package.
tar --exclude=.git --exclude=.github -C "$CHART" -cf - . | tar -C "$work/chart" -xf -
cp "$VALUES" "$work/chart/release-values.yaml"

# Identify the run for an independent success check; this is not a success claim.
jq -n --arg repository "$PUBLICATION_REPOSITORY" --arg workflowRef "$PUBLICATION_WORKFLOW_REF" \
  --arg runId "$PUBLICATION_RUN_ID" --arg runAttempt "$PUBLICATION_RUN_ATTEMPT" \
  '{repository: $repository, workflowRef: $workflowRef, runId: $runId, runAttempt: $runAttempt}' \
  > "$work/chart/publication.json"

helm lint "$work/chart" -f "$work/chart/release-values.yaml"
helm package "$work/chart" --version "$VERSION" --app-version "$SOURCE_SHA" --destination "$work/packages"
packages=("$work/packages/"*.tgz)
[[ "${#packages[@]}" == 1 && -f "${packages[0]}" ]] || fail 'Expected one packaged chart.'
tar -tzf "${packages[0]}" > "$work/files"
for file in release-values.yaml publication.json; do
  grep -Eq "^[^/]+/${file//./\\.}$" "$work/files" || fail "Chart ignore rules excluded $file."
done

push_args=(push "${packages[0]}" "$DESTINATION")
if [[ -n "$REGISTRY_CONFIG" ]]; then push_args+=(--registry-config "$REGISTRY_CONFIG"); fi
if ! helm "${push_args[@]}" > "$work/push.log" 2>&1; then
  cat "$work/push.log" >&2
  fail 'Helm release publication failed.'
fi
cat "$work/push.log"

reference=$(awk '$1 == "Pushed:" {value=$2} END {print value}' "$work/push.log")
digest=$(awk '$1 == "Digest:" {value=$2} END {print value}' "$work/push.log")
[[ -n "$reference" && "$digest" =~ ^sha256:[0-9a-f]{64}$ ]] || fail 'Helm did not report a published reference and digest.'
printf 'reference=%s\ndigest=%s\n' "$reference" "$digest" >> "$GITHUB_OUTPUT"
