# Shared operations workflows

Reusable GitHub Actions workflow templates. Callers choose their events and inputs.

| Callable workflow | Purpose |
| --- | --- |
| `.github/workflows/linear-issue-check.yml` | Validate PR branch issue IDs through Linear read-only OAuth |
| `.github/workflows/validate.yml` | Terraform formatting and validation, with optional Helm rendering |

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
      terraform-directories: '["modules/azure/network", "modules/azure/aks-cluster"]'
      terraform-version: '1.5.7'
```

The Linear workflow requires a `pull_request` event and both OAuth secrets.
`terraform-directories` is a nonempty JSON array of caller-relative directories.

For Helm rendering, also supply `helm-chart`, `helm-chart-version`, and optionally
`helm-repository`, `helm-release`, `helm-namespace`, `helm-values-file`, and
`helm-version`. Omitting `helm-chart` skips rendering. Remote charts require a
pinned chart version. Input defaults are declared in the callable workflow.
