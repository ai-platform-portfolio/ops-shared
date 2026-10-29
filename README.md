# Shared operations workflows

Reusable GitHub Actions workflow templates. Callers choose their events and inputs.

| Callable workflow | Purpose |
| --- | --- |
| `.github/workflows/linear-issue-check.yml` | Validate PR branch issue IDs through Linear read-only OAuth |
| `.github/workflows/validate.yml` | Terraform formatting and validation, with optional Helm rendering |
| `.github/workflows/docker.yml` | Build or publish a GHCR image with image-scoped BuildKit caching |

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

Call `docker.yml` once per image. The caller supplies a full GHCR image name
without a tag. `context` defaults to the repository root, and `file` defaults
to the Dockerfile in that context. On pull requests it builds without pushing;
set `push: true` to publish on other events. The caller must grant
`contents: read` and `packages: write` to the job when publishing.

For one image in a monorepo:

```yaml
jobs:
  api:
    permissions:
      contents: read
      packages: write

    uses: ai-platform-portfolio/ops-shared/.github/workflows/docker.yml@<merged-commit-sha>
    with:
      image: ghcr.io/ai-platform-portfolio/api
      context: .
      file: services/api/Dockerfile
      push: ${{ github.event_name == 'push' }}
```

For several images, put the same call under a matrix:

```yaml
jobs:
  images:
    permissions:
      contents: read
      packages: write

    strategy:
      fail-fast: false
      matrix:
        include:
          - image: ghcr.io/ai-platform-portfolio/api
            context: .
            file: services/api/Dockerfile

          - image: ghcr.io/ai-platform-portfolio/worker
            context: .
            file: services/worker/Dockerfile

    uses: ai-platform-portfolio/ops-shared/.github/workflows/docker.yml@<merged-commit-sha>
    with:
      image: ${{ matrix.image }}
      context: ${{ matrix.context }}
      file: ${{ matrix.file }}
      push: ${{ github.event_name == 'push' }}
```

Buildx imports and exports all build stages through the GitHub Actions cache.
Each image has its own cache scope, so matrix builds do not overwrite one
another. GitHub's cache branch access and storage limits still apply. The
workflow emits a full commit SHA tag on every build, plus a branch or PR tag;
the default branch also gets `latest`. Use a `.dockerignore` in each build
context to keep irrelevant files out of the build.
