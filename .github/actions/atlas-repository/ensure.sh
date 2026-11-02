#!/usr/bin/env bash
set -euo pipefail

fail() { echo "::error::$*" >&2; exit 1; }

[[ "$ATLAS_REPOSITORY" =~ ^[a-z0-9][a-z0-9._/-]*$ ]] || fail 'An Atlas repository slug is required.'
[[ "$ATLAS_REPOSITORY_TYPE" == schema || "$ATLAS_REPOSITORY_TYPE" == migration_directory ]] || fail 'Type must be schema or migration_directory.'
[[ "$ATLAS_DRIVER" =~ ^[a-z][a-z0-9_]*$ ]] || fail 'A lowercase Atlas driver is required.'

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

# Listing distinguishes absence from auth/network errors without parsing error text.
# The pinned CLI lacks --skip-if-exists; check every page before creating anything.
page=1
found=false
while :; do
  atlas cloud repo list --page "$page" --format '{{ json . }}' > "$work/page.json" 2> "$work/error" || fail 'Unable to list Atlas repositories.'
  jq -e --argjson page "$page" '
    (.Repos | type == "array") and .PageInfo.page == $page and
    (.PageInfo.pageSize | type == "number" and . > 0) and
    (.PageInfo.totalItems | type == "number" and . >= 0)
  ' "$work/page.json" > /dev/null 2>&1 || fail 'Invalid Atlas repository list response.'
  if jq -e --arg name "$ATLAS_REPOSITORY" '.Repos[] | select(.Slug == $name)' "$work/page.json" > /dev/null; then
    found=true
    break
  fi
  jq -e '.PageInfo.page * .PageInfo.pageSize < .PageInfo.totalItems' "$work/page.json" > /dev/null || break
  page=$((page + 1))
done

if [[ "$found" == false ]]; then
  # A competing caller may create the same repository; verification below is authoritative.
  atlas cloud repo create --name "$ATLAS_REPOSITORY" --type "$ATLAS_REPOSITORY_TYPE" --driver "$ATLAS_DRIVER" > "$work/create" 2> "$work/error" || :
fi

atlas cloud repo describe --slug "$ATLAS_REPOSITORY" --format '{{ json . }}' > "$work/repo.json" 2> "$work/error" || fail 'Unable to create or verify the Atlas repository.'
jq -e --arg name "$ATLAS_REPOSITORY" --arg type "$ATLAS_REPOSITORY_TYPE" --arg driver "$ATLAS_DRIVER" '
  .Slug == $name and .Type == ($type | ascii_upcase) and
  (.Driver | ascii_downcase | if . == "postgresql" then "postgres" else . end) == $driver
' "$work/repo.json" > /dev/null 2>&1 || fail 'Atlas repository type or driver does not match; existing repositories are never modified.'

printf 'name=%s\nurl=atlas://%s\n' "$ATLAS_REPOSITORY" "$ATLAS_REPOSITORY" >> "$GITHUB_OUTPUT"
echo "Atlas repository verified: $ATLAS_REPOSITORY"
