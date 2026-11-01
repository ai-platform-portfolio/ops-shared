#!/usr/bin/env bash
set -euo pipefail

fail() { echo "::error::$*" >&2; exit 1; }

[[ "$PR_NUMBER" =~ ^[1-9][0-9]*$ ]] || fail 'A positive PR number is required.'
[[ "$SOURCE_SHA" =~ ^[0-9a-f]{40}$ ]] || fail 'A full source SHA is required.'
[[ "$GITHUB_REPOSITORY" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || fail 'Invalid repository.'
git check-ref-format "refs/heads/$RECORD_BRANCH" || fail 'Invalid record branch.'
[[ -n "$GH_TOKEN" && -f "$RECORD" ]] || fail 'A token and JSON record file are required.'

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
record_path="previews/pr-$PR_NUMBER.json"
repository="repos/$GITHUB_REPOSITORY"
branch_uri=$(jq -rn --arg branch "$RECORD_BRANCH" '$branch | @uri')

# Copy once: subsequent API checks must not change the payload being published.
jq -ces --arg repo "$GITHUB_REPOSITORY" --arg sha "$SOURCE_SHA" '
  if length == 1 and (.[0] | type == "object") and
    .[0].source.repository == $repo and .[0].source.revision == $sha
  then .[0] else error("Record source must match the selected repository and revision") end
' "$RECORD" > "$work/record.json"

# Inspect HTTP status explicitly; only documented not-found/conflict cases are retried.
api() {
  gh api --include "$@" > "$work/response" 2> "$work/error" || true
  status=$(awk 'NR == 1 {print $2}' "$work/response")
  [[ "$status" =~ ^[0-9]{3}$ ]] || fail 'GitHub API returned no HTTP response.'
  awk 'body {print} /^\r?$/ {body=1}' "$work/response" > "$work/body"
}

check_source() {
  api --method GET "$repository/pulls/$PR_NUMBER"
  [[ "$status" == 200 ]] || fail "Cannot read PR (HTTP $status)."
  jq -e --arg repo "$GITHUB_REPOSITORY" --arg sha "$SOURCE_SHA" --arg branch "$RECORD_BRANCH" '
    .state == "open" and .head.sha == $sha and
    .head.repo.full_name == $repo and .base.repo.full_name == $repo and
    .head.ref != $branch and .base.ref != $branch and
    (.base.repo.default_branch | type == "string") and .base.repo.default_branch != $branch
  ' "$work/body" > /dev/null || fail 'PR is stale, closed, untrusted or uses the record branch.'
  base_sha=$(jq -r '.base.sha' "$work/body")
  [[ "$base_sha" =~ ^[0-9a-f]{40}$ ]] || fail 'PR base SHA is invalid.'
}

check_source
api --method GET "$repository/git/ref/heads/$branch_uri"
if [[ "$status" == 404 ]]; then
  check_source
  jq -n --arg ref "refs/heads/$RECORD_BRANCH" --arg sha "$base_sha" '{ref:$ref,sha:$sha}' > "$work/request"
  api --method POST "$repository/git/refs" --input "$work/request"
  if [[ "$status" == 422 ]]; then
    # Another PR may have initialized the same branch while this run was checking.
    api --method GET "$repository/git/ref/heads/$branch_uri"
    [[ "$status" == 200 ]] || fail 'Record branch initialization failed.'
  elif [[ "$status" != 201 ]]; then
    fail "Record branch initialization failed (HTTP $status)."
  fi
elif [[ "$status" != 200 ]]; then
  fail "Cannot read record branch (HTTP $status)."
fi

for attempt in 1 2 3; do
  api --method GET "$repository/contents/$record_path" -f "ref=$RECORD_BRANCH"
  file_sha=''
  identical=false
  if [[ "$status" == 200 ]]; then
    jq -e '.type == "file" and .encoding == "base64"' "$work/body" > /dev/null || fail 'Record path is not a readable file.'
    file_sha=$(jq -r '.sha' "$work/body")
    [[ "$file_sha" =~ ^[0-9a-f]{40}$ ]] || fail 'Existing record SHA is invalid.'
    jq -r '.content | gsub("\\s"; "") | @base64d' "$work/body" | jq -c . > "$work/existing.json"
    if cmp -s "$work/record.json" "$work/existing.json"; then identical=true; fi
  elif [[ "$status" != 404 ]]; then
    fail "Cannot read record (HTTP $status)."
  fi

  check_source
  if [[ "$identical" == true ]]; then
    api --method GET "$repository/git/ref/heads/$branch_uri"
    [[ "$status" == 200 ]] || fail "Cannot read published commit (HTTP $status)."
    commit=$(jq -r '.object.sha' "$work/body")
    break
  fi

  jq -n --arg branch "$RECORD_BRANCH" --arg sha "$file_sha" --arg pr "$PR_NUMBER" \
    --rawfile record "$work/record.json" '
    {branch:$branch,message:("Publish preview record for PR #"+$pr),content:($record|@base64)} +
    (if $sha == "" then {} else {sha:$sha} end)
  ' > "$work/request"
  api --method PUT "$repository/contents/$record_path" --input "$work/request"
  if [[ "$status" == 200 || "$status" == 201 ]]; then
    commit=$(jq -r '.commit.sha' "$work/body")
    break
  fi
  [[ "$status" == 409 || "$status" == 422 ]] || fail "Record publication failed (HTTP $status)."
  [[ "$attempt" != 3 ]] || fail 'Record publication conflicted three times; retry the job.'
done

# PR state and a Git write cannot be one transaction. Fail if the head advanced.
check_source
[[ "$commit" =~ ^[0-9a-f]{40}$ ]] || fail 'Published commit SHA is invalid.'
printf 'commit=%s\npath=%s\n' "$commit" "$record_path" >> "$GITHUB_OUTPUT"
