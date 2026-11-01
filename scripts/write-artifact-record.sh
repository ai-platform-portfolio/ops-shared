#!/usr/bin/env bash
set -euo pipefail

if [[ ! "$SOURCE_SHA" =~ ^[0-9a-f]{40}$ ]]; then
  echo 'A full source commit SHA is required.' >&2
  exit 1
fi

checksum=$(shasum -a 256 "$MIGRATION_DIRECTORY/atlas.sum" | cut -d ' ' -f 1)

# A map preserves every caller-named image; a reusable matrix output would not.
jq -en --arg sha "$SOURCE_SHA" --arg checksum "$checksum" \
  --argjson images "$IMAGES_JSON" --argjson migration "$MIGRATION_JSON" '
  ($images | type == "object" and length > 0 and all(to_entries[];
    (.key | length > 0) and
    (.value | type == "object" and .revision == $sha and
      (.image | type == "string" and test("^[^[:space:]@]+:[^/:[:space:]]+$")) and
      (.digest | type == "string" and test("^sha256:[0-9a-f]{64}$"))))) and
  ($migration | type == "object" and .published == true and
    .revision == $sha and .tag == $sha and .checksum == $checksum and
    (.repository | type == "string" and test("^[^[:space:]?/#]+$")))
' >/dev/null || {
  echo 'Artifact metadata must contain published images and a matching published migration.' >&2
  exit 1
}

jq -n \
  --arg repository "$GITHUB_REPOSITORY" --arg revision "$SOURCE_SHA" \
  --arg run_id "$GITHUB_RUN_ID" --arg attempt "$GITHUB_RUN_ATTEMPT" \
  --argjson images "$IMAGES_JSON" --argjson migration "$MIGRATION_JSON" \
  '{
    source: {repository: $repository, revision: $revision},
    workflow: {runId: $run_id, attempt: $attempt},
    images: ($images | with_entries(.value |= {
      tag: .image, digest: .digest,
      reference: ((.image | sub(":[^/:]+$"; "")) + "@" + .digest)
    })),
    migrations: {repository: $migration.repository, tag: $migration.tag,
      url: ("atlas://" + $migration.repository + "?tag=" + $migration.tag),
      atlasSumSha256: $migration.checksum}
  }' > artifacts.json
