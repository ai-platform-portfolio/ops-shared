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

## Helm release packages

The composite action `.github/actions/publish-release` packages a selected chart
with caller-generated release values, then publishes it using native `helm push`.
The package contains the chart and artifact references, not container image layers.
The chart remains maintained centrally; application repositories need no chart copy.

Callers install Helm, check out a pinned chart, authenticate to their registry and
produce the complete matching release values before calling the action:

```yaml
- uses: ai-platform-portfolio/ops-shared/.github/actions/publish-release@<merged-commit-sha>
  with:
    chart: platform-chart
    values: release-values.json
    destination: oci://registry.example/releases/my-app
    version: ${{ env.RELEASE_VERSION }}
    revision: ${{ env.SOURCE_SHA }}
```

`destination` excludes the chart name/version; Helm derives those from the package.
The action copies the chart to a temporary directory, adds `release-values.yaml`,
lints with that overlay, and packages using the selected semantic `version` and
full source `revision` as `appVersion`. Central defaults/templates and the original
checkout stay unchanged. Existing dependencies must already be present. The overlay
filename is reserved, and publication fails if chart ignore rules exclude it.
Argo's Helm source uses `valueFiles: [release-values.yaml]` to load the overlay.

The action requires Bash, tar and Helm. Registry authentication is caller-owned:
use `helm registry login` before calling it; the optional `registry-config` input
selects an existing Helm registry credential file. Azure callers can use their
existing OIDC login and pipe `az acr login --expose-token`'s access token into
`helm registry login --password-stdin`. Do not print or store that token in Git.
There are no GitHub repository writes or credentials in this action.

Outputs `reference` and `digest` come from Helm's successful push response. Unique
versions are not automatically immutable: callers own registry write locks and
retention, version ordering, promotion and rollback policy. Keep packages available
until dependent Applications and their deletion hooks have finished using them.
See [Helm OCI](https://docs.helm.sh/docs/v3/topics/registries/) and
[ACR Helm support](https://learn.microsoft.com/en-us/azure/container-registry/container-registry-helm-repos).

Callers also own image/component layout, matching migration identity, complete-build
gates, trusted-source checks and per-preview workflow concurrency. Recheck current
PR/head immediately before publication and after it; report deployed tests against
the selected release's source SHA. A source check and registry write cannot be one
transaction. This action packages supplied values; it does not validate that their
images and migrations were built from that SHA. Never put secrets in release values.
ApplicationSet lifecycle and deployed test templates are separate building blocks.

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
