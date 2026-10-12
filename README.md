# Shared operations

Reusable GitHub Actions workflows for `ai-platform-portfolio`. Workload repositories
own their infrastructure, state, policy and deployment environments. This repository
owns shared workflow orchestration and its tests.

Terraform runs directly through the official setup actions and CLI commands.
Lint and review helpers originate in `engineering-standards`. Workflows are reusable `workflow_call` implementations, not files to copy
into every workload. All consumers pin full commit SHAs.

Run `make test` for helper tests. Live caller runs are required before declaring the
migration validated; local tests alone cannot prove OIDC, permissions or environment
approval behaviour.

## Workflow contracts

| Workflow | Caller responsibility | Shared behaviour |
|---|---|---|
| `terraform.yml` | Root, backend, tfvars, secrets and environments | OpenTofu PR plan/comment; Terraform plan; approved saved-plan apply |
| `workflow-lint.yml` | PR/main triggers | Actionlint with mandatory ShellCheck |
| `quality.yml` | Tracked policy, CODEOWNERS and PR trigger | Trusted-base policy, current-head owner adoption, JSON report |
| `review.yml` | Review trigger and exact original workflow/job names | Rerun the original policy job using trusted code |
| `governance.yml` | Portfolio contract, audit implementation and schedule in engineering-standards | Execute audit tests and read-only live audit; publish JSON |
| `docker.yml` | Dockerfile, image name, optional publication and registry credentials | BuildKit build/cache; SHA-tagged default-branch publication |

Repository-specific tests remain with their repositories. Governance policy and
the authoritative visibility/protection table remain in engineering-standards.
The governance workflow expects that repository's audit interface; it is not a
generic command runner. Review automation never checks out PR code with its
`actions: write` token.

## Terraform caller

Call one pinned workflow from the app repository:

```yaml
name: Infrastructure
on:
  pull_request:
  push:
    branches: [main]
  workflow_dispatch:
permissions:
  contents: read
jobs:
  infrastructure:
    uses: ai-platform-portfolio/ops-shared/.github/workflows/terraform.yml@FULL_COMMIT_SHA
    permissions:
      contents: read
      id-token: write
      pull-requests: write
    secrets: inherit
    with:
      root: terraform
      vars-file: environments/uk-south.tfvars
      preview-lock-file: terraform/opentofu.lock.hcl
```

There are exactly two jobs, **Plan** and **Apply**, using the official
`opentofu/setup-opentofu` and `hashicorp/setup-terraform` actions and direct CLI
commands. Plan runs every pipeline. PRs use OpenTofu and update one plan comment;
other events use Terraform. Apply is skipped outside main and waits for the
caller's protected `central-apply` environment approval. Configure the required
reviewers in that environment. The template does not inspect reviewer policies
or compare fingerprints.

Main plans are uploaded with one-day retention. Apply downloads and applies
that exact saved plan, without re-planning. Terraform rejects stale saved plans.
PRs never upload binary plans. Saved plans can contain sensitive Terraform values;
keep secret values in cloud secret managers and pass references to resources.

`root` defaults to `terraform`; `backend-file` defaults to `backend.hcl` relative
to the root. `vars-file` is optional and relative to the root. `preview-lock-file`
is an optional OpenTofu lock file relative to checkout. Terraform defaults to
1.12.2 and OpenTofu to 1.12.3; versions and plan/apply environments are configurable.
Commit provider checksums for the Linux runner. Fork PRs do not run cloud plans.

Callers supply `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`, `AZURE_CLIENT_ID`,
`TF_BACKEND_RESOURCE_GROUP`, `TF_BACKEND_RESOURCE_NAME` and
`TF_BACKEND_CONTAINER` through GitHub secrets. OIDC uses the caller repository's
plan and apply environments. No credentials are committed. Apply concurrency
serializes the same root within a repository; remote state locking remains active.

For AWS workloads, also set `aws-region` and supply `AWS_ACCOUNT_ID`,
`AWS_PLAN_ROLE_ARN` and `AWS_APPLY_ROLE_ARN` through GitHub secrets. The official
`aws-actions/configure-aws-credentials` action exchanges the job's OIDC token for
temporary credentials. Each role must trust its matching caller environment's
exact subject. Bootstrap those roles before enabling this input. Azure-only callers
leave it empty and retain their current behaviour. The Azure backend still uses
the Azure secrets above.

## Policy and review callers

Call `quality.yml` on pull requests with `contents: read` and
`pull-requests: read`. `policy-file` defaults to `engineering.yaml` and may
select another tracked path, such as `gate-example/engineering.yaml`. The base
revision supplies the policy until the base CODEOWNER approves the exact PR head.
External policy files cannot be combined with review adoption.

Call `review.yml` on submitted/dismissed `pull_request_review` events. Grant
`actions: write`, `pull-requests: read`, and `contents: read`. Set `workflow`
to the caller file and `job` to its nested policy job, for example
`policy / check`. The helper rechecks the current head and reruns that original
job, which also reruns its dependent caller gate. Native review requirements
remain mandatory; the checker does not replace merge protection.

## Docker caller

Call `docker.yml` with `contents: read`, `packages: write`, and a full
`image` such as `ghcr.io/ai-platform-portfolio/workload`. Set `context` and
`dockerfile` when they differ from the repository root and `Dockerfile`.
`publish` defaults to false. Even when true, PRs and non-default branches cannot
publish. Publication is limited to default-branch push/manual events and tags
the image `sha-COMMIT`; the workflow returns its digest.

GHCR uses the scoped GitHub token. Other registries require `REGISTRY_USERNAME`
and `REGISTRY_PASSWORD`, or Azure callers select `authentication: azure-oidc`.
Azure callers pass `ACR_NAME`, `AZURE_CLIENT_ID`, `AZURE_TENANT_ID` and
`AZURE_SUBSCRIPTION_ID` as secrets, grant `id-token: write`, and choose the caller
environment trusted by their identity. With Azure authentication, `image` is only
the image repository path; the registry hostname is derived from `ACR_NAME` at
runtime. Keep the registry resource name out of tracked configuration.
Use `BUILD_SECRETS` for newline-separated BuildKit
secret entries; never put secrets in `build-args`. Build records are not uploaded.
Cache scopes include image, context, Dockerfile, target and platforms, and GitHub
adds branch isolation. A repeat run tests cache reuse. The bundled scratch image
is a build fixture; publishing it requires owner approval.

## Releasing changes

Commit helper/action changes first. Pin those immutable revisions in shared
workflows, then commit workflows and update caller pins. This avoids resolving
helper code from a mutable main branch or accidentally executing caller code.
Keep referenced commits reachable when merging; do not squash away revisions
already pinned by callers.

The migration checklist and live evidence are in [VALIDATION.md](VALIDATION.md).

## Linear issue validation caller

`linear-issue.yml` validates every issue ID in a PR's branch name against Linear,
using OAuth client credentials with the `read` scope. Use Linear's **Copy git
branch name**, for example `michaelalinks/ai-27-add-validation`. Missing IDs,
inaccessible/nonexistent issues, credential failures and API errors fail the check.
The check accepts issues across the connected workspace; it does not restrict the
project, issue state or assignee. It verifies issue existence, not the integration's
attachment state. Linear's GitHub integration separately links the PR from its issue
ID. Branch creation remains unrestricted.

Create a Linear OAuth application in the intended workspace and enable **Client
credentials**. Store `LINEAR_CLIENT_ID` and `LINEAR_CLIENT_SECRET` as organization
Actions secrets, granting selected-repository access to each caller. A fresh token
is obtained for each run; no access or refresh token is stored. Private Linear teams
must be accessible to the application. Never put credentials in a workflow or PR.

Add this caller workflow, replacing `FULL_COMMIT_SHA` with a reviewed immutable
ops-shared revision. Do not add path filters: every PR must run the required check.

```yaml
name: Linear issue check
on:
  pull_request:
    types: [opened, synchronize, reopened, ready_for_review]
permissions:
  contents: read
jobs:
  linear:
    uses: ai-platform-portfolio/ops-shared/.github/workflows/linear-issue.yml@FULL_COMMIT_SHA
    secrets:
      LINEAR_CLIENT_ID: ${{ secrets.LINEAR_CLIENT_ID }}
      LINEAR_CLIENT_SECRET: ${{ secrets.LINEAR_CLIENT_SECRET }}
  linear-issue-required:
    if: always()
    needs: linear
    runs-on: ubuntu-latest
    steps:
      - env:
          RESULT: ${{ needs.linear.result }}
        run: test "$RESULT" = success
```

After testing, add **linear-issue-required**, with GitHub Actions as its source, to
the active default-branch ruleset's required status checks. Keep existing checks
and review requirements. Adding the caller alone does not enforce merging.
The final gate fails if validation fails or is skipped. Fork and Dependabot PRs
normally do not receive these secrets and fail closed; use a same-repository branch
for reviewed contributions. Never switch to checking out PR code under
`pull_request_target` to expose credentials.

`ops-shared` includes its own caller in `linear-issue-check.yml`. Changes to the
validator follow the release process above: commit helper code first, update its
immutable checkout pin, then publish the workflow revision. Use merge commits to
preserve helper revisions already referenced by workflows.

## Function App

`functions/` contains only `function_app.py`, `sync.py`, `host.json` and one
`requirements.txt` with exact dependency versions. All HTTP, queue and timer
handlers deploy together. Tests stay in `tests/functions` and are excluded from
publication.

**Deploy Function App** starts automatically when Function code, its tests,
deployment helpers or the deployment workflow change on main. Manual dispatch
is also available for retries. The pipeline checks deployment controls,
OIDC and handler indexing, runs the security tests, builds one ZIP and verifies
that the deployed webhook rejects unsigned requests. The `central-apply`
environment must require the `platform` team, forbid administrator bypass and allow main
only. GitHub App keys and the webhook secret remain in Key Vault and are read
using managed identity. Configure the App's Repository events after deployment.
Existing onboarding requests a platform-iac plan; its .github migration is pending.

## Repository settings

`repository-settings.json` contains **per-repository** GitHub settings.
Organisation-level settings are not managed by this script and must be documented
separately when added. All active repositories receive squash-only merging and
automatic deletion of merged branches.

Preview with `python3 scripts/repository_settings.py`; apply with
`python3 scripts/repository_settings.py --apply` using an authenticated GitHub CLI
with repository administration permission. The script skips archived repositories,
updates only differing settings, and verifies each write.
