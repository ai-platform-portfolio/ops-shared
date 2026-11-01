# Shared operations workflows

Reusable GitHub Actions workflow templates. Callers choose their events and inputs.

| Callable workflow | Purpose |
| --- | --- |
| `.github/workflows/linear-issue-check.yml` | Validate PR branch issue IDs through Linear read-only OAuth |
| `.github/workflows/validate.yml` | Terraform formatting and validation, with optional Helm rendering |
| `.github/workflows/docker.yml` | Build or publish to the platform ACR with image-scoped BuildKit caching |
| `.github/workflows/atlas.yml` | Validate, lint, test and optionally publish one caller-owned migration directory |
| `.github/workflows/artifact-record.yml` | Verify and record named images plus one published Atlas migration directory |

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
tag, not a full image reference. The workflow returns `image`, `digest` and
`revision` outputs. `digest` is empty on build-only runs; `revision` is the full
source commit SHA verified before building.

The optional `revision` input selects a full 40-character lowercase commit SHA.
When omitted or empty, it uses the event's `github.sha`, preserving the existing
behavior: PR builds use GitHub's synthetic merge commit, while push and manual
runs use their event commit. Branch names, tags and abbreviated SHAs are not
accepted as revision inputs.

Preview callers explicitly pass `github.event.pull_request.head.sha` to build the
reviewed PR source. Checkout must match the selected SHA before any registry login
or build; generated tags, the OCI revision label and the `revision` output all
identify that commit. An explicit image tag does not select the source revision.
Consumers must still reject stale runs before deployment.
See [GitHub's checkout guidance](https://github.com/actions/checkout#checkout-pull-request-head-commit-instead-of-merge-commit).

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
  revision: ${{ github.event.pull_request.head.sha }}
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


## Atlas migrations and artifact records

Call `atlas.yml` once per migration directory. The application owns SQL, tests and
Atlas configuration; the workflow validates/replays migrations, optionally runs
Pro lint/tests, and optionally publishes them. It never applies to an application
database. Publication defaults off; main publishing requires `publish: true`, and
trusted same-repository PR publishing additionally requires `publish-preview: true`.
Forks cannot publish. PR pushes tag the source SHA without advancing Atlas `latest`.

```yaml
jobs:
  migrations:
    uses: ai-platform-portfolio/ops-shared/.github/workflows/atlas.yml@<merged-commit-sha>
    with:
      config: file://atlas.ci.hcl
      environment: ci
      directory: migrations
      registry: ledger-schema
      revision: ${{ github.event.pull_request.head.sha || github.sha }}
      publish: true
      publish-preview: true
    secrets:
      ATLAS_CLOUD_TOKEN: ${{ secrets.ATLAS_CLOUD_TOKEN }}
```

Only `environment` is required. Config/directory default to `file://atlas.hcl` and
`migrations`; empty `revision` preserves event `github.sha` (the merge SHA on PRs).
Optional `dev-url` overrides the config's disposable dev database. Atlas is pinned
to 1.3.6. Lint/tests default on and require a bot; for anonymous fork replay, set
`lint: false` and `test: false`, use a config without a Cloud login requirement,
and omit publishing credentials. Trusted callers should pin their Atlas organization
in config. No application-database credentials belong in these jobs.

Outputs are `revision`, `published` and `migration`: JSON containing repository,
SHA tag, revision, checksum and published flag. Only a successful publishing call
reports `published: true`. A registry is required for that path.

After successful publishers, call `artifact-record.yml` with an `images` JSON map
of named `{image, digest, revision}` entries and `migration` from the Atlas output.
Pass the same `revision` and `migration-directory` (default `migrations`). It checks
all revisions, image digests and the migration checksum, then uploads `artifacts.json`.
The name includes SHA/run/attempt; `retention-days` defaults to 7. Outputs are
`revision` and `artifact-id`. Use explicitly named image jobs when gathering digests;
[reusable matrix outputs](https://docs.github.com/en/actions/how-tos/reuse-automations/reuse-workflows#using-outputs-from-a-reusable-workflow)
do not aggregate every image result.

The caller owns events, concurrency, dependencies and initial freshness checks.
Set record input `check-current: true` for a final open-PR/main-head check before
upload; select the PR head explicitly. Consumers must still require a successful
current-SHA run before deployment: checking head and uploading cannot be atomic.
Partial publications must not select a deployment revision. Neither this record
nor SHA tags configure image/Atlas retention or server-side tag locking.

The record workflow fetches its script using native `job.workflow_repository` and
`job.workflow_sha`, keeping it at the caller-selected workflow revision without
another helper pin. These contexts require GitHub.com/Enterprise Cloud; Server
support is not implemented. See [GitHub context documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/contexts#job-context).
Run the focused checks with `python3 -B -m unittest discover -s tests -p 'test_publication.py'`.

Publishing uploads migration SQL to hosted Atlas Registry. A fintech deployment
requiring no vendor transfer needs an assessed Enterprise offline licence,
internal artifacts and verified egress controls; this workflow does not implement
that alternative. See [Atlas privacy](https://atlasgo.io/cli/data-privacy) and
[offline licensing](https://atlasgo.io/faq/atlas-without-cloud).
