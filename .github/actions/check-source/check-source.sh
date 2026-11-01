#!/usr/bin/env bash
set -euo pipefail

if [[ ! "$SOURCE_SHA" =~ ^[0-9a-f]{40}$ || "$(git rev-parse HEAD)" != "$SOURCE_SHA" ]]; then
  echo 'Source checkout does not match the selected full commit SHA.' >&2
  exit 1
fi

case "$CHECK_CURRENT" in
  true|false) ;;
  *) echo 'check-current must be true or false.' >&2; exit 1 ;;
esac

if [[ "$CHECK_CURRENT" == true ]]; then
  if [[ "$GITHUB_EVENT_NAME" == pull_request ]]; then
    if [[ ! "$PR_NUMBER" =~ ^[0-9]+$ ]]; then
      echo 'A PR number is required.' >&2
      exit 1
    fi

    gh api "repos/$GITHUB_REPOSITORY/pulls/$PR_NUMBER" |
      jq -e --arg sha "$SOURCE_SHA" '.state == "open" and .head.sha == $sha' >/dev/null
  elif [[ "$GITHUB_REF" == refs/heads/main ]]; then
    current=$(gh api "repos/$GITHUB_REPOSITORY/git/ref/heads/main" --jq '.object.sha')
    if [[ "$current" != "$SOURCE_SHA" ]]; then
      echo 'Main has advanced beyond this run.' >&2
      exit 1
    fi
  fi
fi

echo "revision=$SOURCE_SHA" >> "$GITHUB_OUTPUT"
