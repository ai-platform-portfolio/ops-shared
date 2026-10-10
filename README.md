# Shared operations

Reusable GitHub Actions workflows for `ai-platform-portfolio`. Workload repositories
own their infrastructure, state, policy and deployment environments. This repository
owns shared workflow orchestration and its tests.

Terraform helpers originate in `terraform-modules`; lint and review helpers
originate in `engineering-standards`. Existing caller approval requirements remain
in force. Workflows are reusable `workflow_call` implementations, not files to copy
into every workload. All consumers pin full commit SHAs.

Run `make test` for helper tests. Live caller runs are required before declaring the
migration validated; local tests alone cannot prove OIDC, permissions or environment
approval behaviour.

## Workflow contracts

| Workflow | Caller responsibility | Shared behaviour |
|---|---|---|
| `terraform.yml` | Root, backend, lock file, federation contract, secrets and protected environments | PR preview; main plan; approved re-plan and fingerprint-checked apply |
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

Replace `FULL_COMMIT_SHA` with a reviewed 40-character ops-shared revision and
`YOUR_REVIEWER_LOGIN` with the caller's required deployment reviewer:

```yaml
name: Infrastructure preview
on: pull_request
permissions:
  contents: read
jobs:
  plan:
    uses: ai-platform-portfolio/ops-shared/.github/workflows/terraform.yml@FULL_COMMIT_SHA
    permissions:
      contents: read
      id-token: write
      pull-requests: write
    secrets: inherit
    with:
      operation: preview
      approver: YOUR_REVIEWER_LOGIN
      engine: tofu
      version: 1.12.3
      root: ci
      lock-file: ci/opentofu.lock.hcl
  infrastructure-plan-required:
    if: always()
    needs: plan
    runs-on: ubuntu-latest
    steps:
      - env:
          RESULT: ${{ needs.plan.result }}
        run: test "$RESULT" = success
```

Require the caller's stable `infrastructure-plan-required` check in its active
ruleset. The shared result job fails when a requested preview is skipped,
including fork PRs. Trusted same-repository branches are required for live plans.

`operation: deploy` is main-only. It plans in `plan-environment`, defaults to
`central-plan`, and applies only when resource or output changes exist. The
`apply-environment` defaults to `central-apply`; it must require the named
`approver`, forbid administrator bypass and permit only main. The reviewer is a
required caller input with no personal-account default. After approval,
the workflow rejects superseded commits, re-plans and compares the entire plan
apart from its timestamp. Changed plans require a fresh run and approval.
`operation: plan-only` exercises the main planning path without enabling apply.
Manual dispatch in the module caller defaults to plan-only.

Terraform defaults to 1.12.2; preview callers explicitly select OpenTofu 1.12.3.
`root` and `federation-file` are relative to checkout; `backend-file` and optional
`vars-file` are relative to the root. `lock-file` is an optional alternate lock
file relative to checkout. Absolute paths and parent traversal are rejected. Configuration
auto tfvars remain supported. There are no arbitrary pre-step shell inputs.

Callers provide `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`, `AZURE_CLIENT_ID`,
`TF_BACKEND_RESOURCE_GROUP`, `TF_BACKEND_RESOURCE_NAME` and
`TF_BACKEND_CONTAINER` as secrets. `TF_BACKEND_CONTAINER_SCOPE` optionally maps
to the existing root's `state_container_scope` input. Environment secrets belong
to the caller; reuse does not move its OIDC identity to ops-shared. The federation
JSON uses the existing `github_repositories` map and verifies repository IDs and
phase-specific token claims before authentication.

Use the same `state-lock` value for callers sharing one backend within a
repository. GitHub concurrency groups are repository-scoped; the backend lock
remains the cross-repository safeguard. Keep one deployment owner per state.
State and saved plans are never uploaded. Plans remain runner-local and are
removed on success or failure. PR comments redact identifiers and secret inputs;
authentication failures publish safe failure text.

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
