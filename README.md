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

## Preview artifact records

The composite action `.github/actions/publish-preview` commits a caller-owned JSON
record to `previews/pr-<number>.json` on the calling repository's `preview-records`
branch. It initializes that branch from the PR base automatically. No deployment,
Kubernetes configuration or application component layout is imposed by the action.
It uses the existing runner tools Bash, Git, GitHub CLI and jq with native GitHub
[Contents](https://docs.github.com/en/rest/repos/contents#create-or-update-file-contents)
and [Refs](https://docs.github.com/en/rest/git/refs#create-a-reference) APIs.

Call it only after every required publisher succeeds and the caller validates its
complete, matching artifact set. The record must be one JSON object containing
`source.repository` (`owner/repository`) and `source.revision` (full source SHA).
All other fields belong to the caller: any number of images, migration artifacts
or generated values can be included. Never include credentials or private data.

```yaml
permissions:
  contents: write
  pull-requests: read

steps:
  # Previous caller steps produce the complete artifacts.json record.
  - uses: ai-platform-portfolio/ops-shared/.github/actions/publish-preview@<merged-commit-sha>
    with:
      record: artifacts.json
```

`record` defaults to `artifacts.json`; `branch` defaults to `preview-records`.
`pull-request` and `revision` default to the PR event's number/head SHA; supply them
explicitly for other events. `token` defaults to `github.token` and needs contents
write and pull requests read. The outputs are `commit` and `path`. No checkout
credentials need to be persisted. The record branch must be distinct from the PR
head/base and the default branch, and repository rules must permit the write.

The publisher rejects stale, closed and fork PRs and mismatched source metadata.
It checks freshness before writes, on conflict retries and before success outputs.
Identical records are a no-op. Each PR has its own file; concurrent branch/file
conflicts get at most three attempts without replacing other PR records. Serialize
publishers for each PR using the caller's existing workflow concurrency group.
Transport and permission errors fail rather than being treated as missing files.

PR state and a Git write are separate API operations, so a head can advance after
the last check. A post-write check then fails, but the old record may already exist.
Consumers must use the record's source revision for deployment/test status and
revalidate the current PR head before reporting success. Argo can read the committed
JSON through its native Git file generator. ApplicationSet lifecycle integration
and deployed checks remain separate building blocks.

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
      id-token: write

    uses: ai-platform-portfolio/ops-shared/.github/workflows/docker.yml@<merged-commit-sha>
    with:
      image: portfolio/api
      context: .
      file: services/api/Dockerfile

    secrets:
      AZURE_CLIENT_ID: ${{ secrets.AZURE_CLIENT_ID }}
      AZURE_TENANT_ID: ${{ secrets.AZURE_TENANT_ID }}
      AZURE_SUBSCRIPTION_ID: ${{ secrets.AZURE_SUBSCRIPTION_ID }}
```

For several images, replace the `jobs` block with a matrix:

```yaml
jobs:
  images:
    permissions:
      contents: read
      id-token: write

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
      AZURE_CLIENT_ID: ${{ secrets.AZURE_CLIENT_ID }}
      AZURE_TENANT_ID: ${{ secrets.AZURE_TENANT_ID }}
      AZURE_SUBSCRIPTION_ID: ${{ secrets.AZURE_SUBSCRIPTION_ID }}
```

The existing organization secrets `AZURE_CLIENT_ID`, `AZURE_TENANT_ID` and
`AZURE_SUBSCRIPTION_ID` must be available to the caller. Native Azure OIDC login
uses those IDs with `id-token: write`; no client secret or JSON credential is
needed. The identity must trust the caller repository's GitHub OIDC subject and
have push access to `localacrk8s`. PR subjects end in `:pull_request`; main subjects
end in `:ref:refs/heads/main` when no GitHub environment is selected. Reusing a
workflow does not change that caller subject. These secrets are required only for
publishing; fork/build-only runs do not authenticate. Manual dispatch belongs in
the caller. See [Azure OIDC setup](https://github.com/Azure/login#login-with-openid-connect-oidc-recommended).

For a PR preview, opt in explicitly and tag the PR head commit. The caller must
trust same-repository PR authors with access to its publishing identity:

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


## Atlas validation action

Applications compose their own jobs: call `docker.yml` per image, use the official
Atlas setup/test/push actions for Atlas operations, and use this small action for
migration checksum validation and replay. It requires an Atlas CLI already on
`PATH`; it does not install tools, authenticate, check out source, publish or apply
to an application database. The caller owns config, migration paths, dev database,
revision selection, credentials, triggers, dependencies and artifact records.

```yaml
steps:
  # After checking out the application's selected revision:
  - uses: ariga/setup-atlas@2f3c785c89a15e1c0d07bcae3900fb5feb969eea
    with:
      version: v1.3.6

  - uses: ai-platform-portfolio/ops-shared/.github/actions/atlas-validate@<merged-commit-sha>
    with:
      environment: local
      dev-url: docker://postgres/18.6/dev?search_path=public
```

`environment` is required; `config` defaults to `file://atlas.hcl`. Migration paths
and dev settings come from that environment. Optional `dev-url` overrides only the
disposable dev connection. Never supply an application database as the dev target.
The action succeeds or fails without imposing a job graph or publication policy.

For Pro tests/publication, configure a bot in the caller's trusted job and use
pinned [`ariga/atlas-action/migrate/test` and `migrate/push`](https://github.com/ariga/atlas-action)
steps directly. Git-based bootstrap lint can remain a short CLI step; the official
lint action compares against a Registry baseline. This avoids duplicate wrappers
and lets applications combine building blocks in their own order.

Registry publication uploads migration source to Atlas. The fintech alternative
remains Enterprise offline licensing, internal artifacts and verified egress;
this action does not claim that deployment is implemented. See
[Atlas privacy](https://atlasgo.io/cli/data-privacy) and
[offline licensing](https://atlasgo.io/faq/atlas-without-cloud).
