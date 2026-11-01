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


## Atlas migration workflow

Call `atlas.yml` once per migration directory. The application owns its SQL,
`atlas.sum`, tests, Atlas config, organization pin and dev-database definition.
The shared workflow owns the execution steps and pinned native Atlas integrations;
it never applies migrations to an application database. No Terraform Atlas
provider, schema library or pipeline dispatcher is introduced.

| Input | Default / meaning |
| --- | --- |
| `environment` | Required environment name from caller's Atlas config |
| `config`, `directory` | `file://atlas.hcl`, `migrations`; caller-relative source paths |
| `registry` | Empty; required only for effective Registry publication |
| `revision` | Empty uses event `github.sha`, preserving PR merge-commit behavior |
| `atlas-version` | `v1.3.6` |
| `dev-url` | Empty uses config; optional disposable dev database override |
| `lint-base` | Empty uses PR base, push-before SHA, or event SHA |
| `lint`, `test` | Both `true`; need an Atlas bot for Pro operations |
| `publish`, `publish-preview` | Both `false`; publication must be explicitly enabled |
| `check-current` | `false`; optionally reject a stale open PR/main revision |

Checksum validation and replay always run. Lint compares Git revisions so a new
Registry repository does not need an existing main baseline. Native tests come
from the selected caller config. All CLI steps use the selected environment and
directory; optional `dev-url` must describe a disposable database, not a real
environment's database. No target-database credentials belong in these PR jobs.

Publication requires `publish: true` plus either a main push/main manual run or
an explicitly enabled `publish-preview: true` same-repository PR. Forks cannot
publish through this workflow. PR pushes use the full source SHA tag and never
advance Atlas `latest`; main publication updates `latest` plus the SHA tag.
Deployment consumers must select the pinned tag. `ATLAS_CLOUD_TOKEN` is an optional
workflow secret because replay-only calls do not need it, but lint, test or
publication fail explicitly without the bot. Caller config should pin its own
Atlas organization. Same-repository PR authors must be trusted with this bot.

For a checksum/replay-only call, including untrusted forks, use a config that does
not require a Cloud login, disable Pro steps, and leave publication disabled:

```yaml
jobs:
  migrations:
    uses: ai-platform-portfolio/ops-shared/.github/workflows/atlas.yml@<merged-commit-sha>
    with:
      environment: local
      dev-url: docker://postgres/18.6/dev?search_path=public
      lint: false
      test: false
```

For a trusted publishing caller, pass its CI config and secret, enable publication,
and explicitly select the reviewed PR head if required:

```yaml
jobs:
  migrations:
    if: github.event_name != 'pull_request' || github.event.pull_request.head.repo.full_name == github.repository
    uses: ai-platform-portfolio/ops-shared/.github/workflows/atlas.yml@<merged-commit-sha>
    with:
      config: file://atlas.ci.hcl
      environment: ci
      directory: db/migrations
      registry: ledger-schema
      revision: ${{ github.event.pull_request.head.sha || github.sha }}
      publish: true
      publish-preview: true
      check-current: true
    secrets:
      ATLAS_CLOUD_TOKEN: ${{ secrets.ATLAS_CLOUD_TOKEN }}
```

This call returns `revision`, `checksum` (SHA-256 of `atlas.sum`), `published` and
`migration` (JSON repository, tag, revision, checksum, boolean published). A
successful replay-only call reports `published: false`; metadata alone does not
mean it was published. These outputs do not need caller opt-in.

## Completed artifact records

`artifact-record.yml` handles one completed set of one or more caller-named images
and one Atlas migration directory. The caller owns its job dependencies and calls
this only after successful publishers. The workflow verifies every source SHA,
image digest and the migration checksum against its checkout, then uploads
`artifacts.json`. It does not build images, choose a deployment revision, or deploy.

| Input | Default / meaning |
| --- | --- |
| `images` | Required JSON map: each named entry has `image` (tagged reference), `digest`, `revision` |
| `migration` | Required JSON output from `atlas.yml`; must report `published: true` |
| `migration-directory` | `migrations`; local checksum verification path |
| `revision` | Empty uses event `github.sha`; select PR head explicitly for preview records |
| `check-current` | `false`; caller can opt into final PR/main freshness check |
| `artifact-name` | Empty generates a name containing source SHA, run ID and attempt |
| `retention-days` | `7`; Actions record retention, not image/Atlas retention |

For example, after the caller's separately named `worker`, `reports` Docker jobs
and its `migrations` Atlas job succeed:

```yaml
jobs:
  record:
    needs: [worker, reports, migrations]
    if: needs.migrations.outputs.published == 'true'
    uses: ai-platform-portfolio/ops-shared/.github/workflows/artifact-record.yml@<merged-commit-sha>
    with:
      revision: ${{ needs.migrations.outputs.revision }}
      migration-directory: db/migrations
      check-current: true
      images: >-
        {"worker":{"image":"${{ needs.worker.outputs.image }}","digest":"${{ needs.worker.outputs.digest }}","revision":"${{ needs.worker.outputs.revision }}"},
         "reports":{"image":"${{ needs.reports.outputs.image }}","digest":"${{ needs.reports.outputs.digest }}","revision":"${{ needs.reports.outputs.revision }}"}}
      migration: ${{ needs.migrations.outputs.migration }}
```

There are no special frontend/backend names or application Registry names in the
shared record format. Use explicit image jobs when aggregating their outputs:
[GitHub reusable-workflow matrix outputs](https://docs.github.com/en/actions/how-tos/reuse-automations/reuse-workflows#using-outputs-from-a-reusable-workflow)
report the last successful output, not all matrix results. The workflow returns
the verified `revision` and uploaded `artifact-id`.

Record upload and a change to the current PR head cannot be atomic. Consumers must
require a successful run and recheck the current source SHA before deployment or
reporting a merge gate. Partial image/migration publication is possible if another
publisher fails; it must not produce a completed record. Commit-tag naming and
checksums do not configure server-side Registry tag locking or retention.

## Shared source verification and maintenance

The native composite action `.github/actions/check-source` checks an existing
caller checkout. Inputs are optional `revision` (default event SHA) and
`check-current` (default `false`); output `revision` identifies the verified commit.
For current-PR checks, select `pull_request.head.sha` explicitly. Use `contents:
read` and `pull-requests: read`; the action uses the caller's GitHub token for
current-head queries. It does not check out source or modify it.

```yaml
- uses: ai-platform-portfolio/ops-shared/.github/actions/check-source@<merged-commit-sha>
  with:
    revision: ${{ github.event.pull_request.head.sha || github.sha }}
    check-current: true
```

Like the existing Linear workflow, shared script fetches use a fixed commit in
this repository. The source action also uses an immutable commit pin, so neither
assumes the caller checkout contains ops-shared code. When changing the helpers,
commit them first and update these references to that helper commit. Public
callers should pin workflows/actions to their reviewed merged commit.

Run `python3 -m unittest discover -s tests -p 'test_publication.py' -v` locally.
`publication-tests.yml` runs those checks and a secret-free, non-publishing Atlas
replay from a different directory/config as a real reusable-workflow consumer.
Native lint/tests/publication still need separate authenticated acceptance;
secret-free replay does not establish bot entitlements or Registry behavior.

For strict fintech boundaries, the offline Enterprise licence/internal-artifact
recommendation remains a separate deployment choice. These workflows use hosted
Atlas Registry when publishing; disabling anonymous telemetry does not disable
identified Pro reporting. An offline licence alone does not redirect Registry
uploads. Verify licensing, supported operator/CLI versions and egress before
claiming zero vendor transfer. See [Atlas privacy](https://atlasgo.io/cli/data-privacy)
and [offline licensing](https://atlasgo.io/faq/atlas-without-cloud).
