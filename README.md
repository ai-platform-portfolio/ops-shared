# Shared operations workflows

Callable GitHub Actions workflows for repositories in `ai-platform-portfolio`.
This repository owns CI behavior; callers select their events and inputs.
Neither workflow provisions infrastructure or needs Azure credentials.

| Workflow | Purpose |
| --- | --- |
| `.github/workflows/linear-issue-check.yml` | Resolve every issue ID in a PR branch through Linear read-only OAuth |
| `.github/workflows/validate.yml` | Terraform formatting, module/root validation and optional Helm rendering |

## Linear issue validation

Call from a `pull_request` event. The validator uses the original caller's PR
metadata and checks every distinct issue identifier, failing when any issue is
missing/inaccessible, OAuth credentials are absent or a request fails. The
validator is embedded in the shared workflow, so its code follows the caller's
pinned workflow revision and requires no checkout of caller scripts.

```yaml
name: Linear issue check
on:
  pull_request:
    types: [opened, synchronize, reopened, edited, ready_for_review]
permissions:
  contents: read
jobs:
  linear:
    uses: ai-platform-portfolio/ops-shared/.github/workflows/linear-issue-check.yml@<merged-full-commit-sha>
    secrets:
      LINEAR_CLIENT_ID: ${{ secrets.LINEAR_CLIENT_ID }}
      LINEAR_CLIENT_SECRET: ${{ secrets.LINEAR_CLIENT_SECRET }}
```

Both secrets are required and must be accessible from the calling repository.
Authentication uses short-lived client-credentials tokens with `scope=read`.
The caller must not switch this secret-bearing check to `pull_request_target`.
Fork PRs without credentials fail rather than bypass validation.

## Terraform validation

`terraform-directories` is a required, nonempty JSON array of paths relative to
the caller checkout. `terraform-version` defaults to `1.5.7`. Formatting checks
all Terraform files in the checkout once; init and validate run as separate matrix
jobs for each supplied directory. Backend initialization is disabled. Provider
and module downloads still require network access.

```yaml
name: Validate Terraform
on:
  pull_request:
  push:
    branches: [main]
permissions:
  contents: read
jobs:
  validate:
    uses: ai-platform-portfolio/ops-shared/.github/workflows/validate.yml@<merged-full-commit-sha>
    with:
      terraform-directories: '["modules/azure/network", "modules/azure/aks-cluster", "modules/azure/managed-identity", "modules/azure/argocd-bootstrap"]'
      terraform-version: '1.5.7'
```

The shared checkout step reads the calling repository at its event revision.
Local module sources must therefore be resolvable from that checkout, or use
immutable Git source references.

## Optional Helm validation

Omitting `helm-chart` skips the Helm job. Supply a local chart directory, a chart
name plus `helm-repository`, or an OCI chart reference. Remote charts require an
explicit `helm-chart-version`. These inputs add rendering to the validation call:

```yaml
    with:
      terraform-directories: '["management/infrastructure", "management/argocd"]'
      helm-chart: argo-cd
      helm-repository: https://argoproj.github.io/argo-helm
      helm-chart-version: '10.10.5'
      helm-release: argocd
      helm-namespace: argocd
      helm-values-file: management/argocd/values.yaml
```

| Input | Default |
| --- | --- |
| `helm-chart` | empty; skips rendering |
| `helm-repository` | empty; optional for local/OCI charts |
| `helm-chart-version` | empty; required for remote charts |
| `helm-version` | `v3.17.3` |
| `helm-release` | `validation` |
| `helm-namespace` | `default` |
| `helm-values-file` | empty; uses chart defaults |

Inputs are passed through quoted shell arguments. Rendered YAML is temporary and
is not published as an artifact. Rendering checks templates, not cluster readiness
or application health; Terraform validation checks configuration/provider schemas,
not a live Azure plan.

## Checks and rollout

`ci.yml` calls the local reusable workflows from the same PR commit. It exercises
Terraform-only validation, a two-directory matrix, optional Helm rendering with
value overrides, and the actual OAuth Linear issue check. Unit tests cover the
embedded validator and Helm argument construction, including error cases.
Fixtures contain no cloud resources.

```sh
python3 -m unittest discover -s tests -v
terraform fmt -check -recursive .
helm template smoke-test tests/fixtures/chart --namespace workflow-tests --values tests/fixtures/chart/override.yaml
```

Merge this repository's first PR before changing the Terraform/bootstrap callers.
Then pin their `uses:` references to the full merged commit SHA and remove their
vendored validator scripts and duplicated workflow implementations. Those caller
changes are deliberately deferred from this PR.
