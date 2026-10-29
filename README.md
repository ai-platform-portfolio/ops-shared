# Shared operations workflows

Reusable GitHub Actions workflow templates. Callers choose their events and inputs.

| Callable workflow | Purpose |
| --- | --- |
| `.github/workflows/linear-issue-check.yml` | Validate PR branch issue IDs through Linear read-only OAuth |
| `.github/workflows/validate.yml` | Terraform formatting and validation, with optional Helm rendering |
| `.github/workflows/docker.yml` | Build or publish to the platform ACR with image-scoped BuildKit caching |

## Usage

After merging, reference the workflows from caller repositories using the merged
commit SHA:

```yaml
jobs:
  linear:
    uses: ai-platform-portfolio/ops-shared/.github/workflows/linear-issue-check.yml@<merged-commit-sha>
    secrets:
      LINEAR_CLIENT_ID: ${{ secrets.LINEAR_CLIENT_ID }}
      LINEAR_CLIENT_SECRET: ${{ secrets.LINEAR_CLIENT_SECRET }}

  validate:
    uses: ai-platform-portfolio/ops-shared/.github/workflows/validate.yml@<merged-commit-sha>
    with:
      terraform-directories: '["modules/*/*"]'
      terraform-version: '1.5.7'
```

The Linear workflow requires a `pull_request` event and both OAuth secrets.
It fetches `scripts/linear_issue.py` from this repository at a pinned commit and
runs it against the caller's PR metadata. When changing the script, update the
workflow's checkout pin to a commit containing that change.

`terraform-directories` is a nonempty JSON array of caller-relative paths or glob
patterns, such as `["bootstrap"]` or `["modules/*/*"]`. Each matrix job expands
its pattern and loops through matching directories, running backend-disabled init
and validate. Non-directory matches are ignored; no matching directories fails
the job.

For Helm rendering, also supply `helm-chart`, `helm-chart-version`, and optionally
`helm-repository`, `helm-release`, `helm-namespace`, `helm-values-file`, and
`helm-version`. Omitting `helm-chart` skips rendering. Remote charts require a
pinned chart version. Input defaults are declared in the callable workflow.

## Docker images

Call `docker.yml` once per image. The caller supplies a repository path within
`localacrk8s.azurecr.io` without a tag. The last path segment becomes the
service name in the tag. `context` defaults to the repository root, and `file`
defaults to the Dockerfile in that context. A pull request
builds without publishing unless `publish-preview: true` is set for a
same-repository PR. Fork PRs remain build-only. A push to `main` publishes,
including a merge to `main`; a manual dispatch on `main` is the fallback publish path. Manual runs
from other branches build without publishing. The reusable workflow enforces
this rule. The optional `tag` input replaces the generated tag; pass only the
tag, not a full image reference. The workflow returns `image` and `digest`
outputs. `digest` is empty on build-only runs.

For one image in a monorepo:

```yaml
name: Docker

on:
  pull_request:
  push:
    branches: [main]
  workflow_dispatch:

jobs:
  api:
    permissions:
      contents: read

    uses: ai-platform-portfolio/ops-shared/.github/workflows/docker.yml@<merged-commit-sha>
    with:
      image: portfolio/api
      context: .
      file: services/api/Dockerfile

    secrets:
      AZURE_CREDENTIALS: ${{ secrets.AZURE_CREDENTIALS }}
```

For several images, replace the `jobs` block with a matrix:

```yaml
jobs:
  images:
    permissions:
      contents: read

    strategy:
      fail-fast: false
      matrix:
        include:
          - image: portfolio/api
            context: .
            file: services/api/Dockerfile

          - image: portfolio/worker
            context: .
            file: services/worker/Dockerfile

    uses: ai-platform-portfolio/ops-shared/.github/workflows/docker.yml@<merged-commit-sha>
    with:
      image: ${{ matrix.image }}
      context: ${{ matrix.context }}
      file: ${{ matrix.file }}

    secrets:
      AZURE_CREDENTIALS: ${{ secrets.AZURE_CREDENTIALS }}
```

The organization secret `AZURE_CREDENTIALS` must be available to each caller
repository and contain Azure service principal JSON authorized to push to
`localacrk8s`. It is required for publishing runs and optional for build-only PRs;
the workflow fails early if a publish run has no credential. The manual dispatch
trigger belongs in the caller workflow, which provides the application source.

For a PR preview, opt in explicitly and tag the PR head commit. The caller must
trust same-repository PR authors with access to its publishing credential:

```yaml
with:
  image: portfolio/api
  tag: preview-pr-${{ github.event.pull_request.number }}-${{ github.event.pull_request.head.sha }}
  publish-preview: true
```

Published PR images must use a `preview-` tag. Use the deterministic tag in an
ApplicationSet PR generator, then use the published digest for reviewed
environment promotions. ACR retention can remove `preview-*` tags after their
PRs close.

Buildx imports and exports all build stages through the GitHub Actions cache.
Each image has its own cache scope, so matrix builds do not overwrite one
another. GitHub's cache branch access and storage limits still apply. The
workflow tags each build as `{service_name}_{UTC datetime}_{12-character SHA}`,
for example `api_20261029T202500Z_a1b2c3d4e5f6`. Each build gets one tag;
published `main` builds use the same format unless `tag` is supplied. Use a
`.dockerignore` in each build context to keep irrelevant files out of the build.
