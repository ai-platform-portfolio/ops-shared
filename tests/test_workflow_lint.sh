#!/usr/bin/env bash
set -euo pipefail

# Regression: an unclosed deployment loop must fail before merge.
if actionlint - <<'YAML'
name: Broken deployment loop
on: pull_request
jobs:
  plan:
    runs-on: ubuntu-latest
    steps:
      - run: |
          for environment in central-plan central-apply; do
            echo "$environment"
YAML
then
  echo 'FAIL: workflow lint accepted an unclosed shell loop' >&2
  exit 1
fi
