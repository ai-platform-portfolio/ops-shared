# Central infrastructure

This root deploys the portfolio's shared network, CI identity, federation and
Function infrastructure. Reusable modules come from full commit pins in
`ai-platform-portfolio/terraform-modules`; that repository does not deploy them.

## Pipelines

- PRs call the shared `terraform.yml` workflow with OpenTofu 1.12.3. Every PR
  requires a successful backend-connected plan, published as one updated,
  collapsible comment with the exact head SHA.
- Relevant main pushes call the same template with Terraform 1.12.2. A fresh
  plan runs without approval and publishes its redacted detail in the run summary.
  Changed plans wait at `central-apply`; the approved job re-plans and requires
  the same fingerprint before applying. Unchanged plans skip apply.
- After infrastructure succeeds, Function publishing waits for its own
  `central-apply` approval. A failed infrastructure job prevents code publishing.
- Authenticated `repository-onboarding` events run `plan-only`. They cannot
  start either infrastructure apply or Function publishing. No manual trigger
  is configured.

The shared sandbox identity is write-capable even during plans. That is the
owner's existing sandbox decision, not a least-privilege production pattern.
Planning takes a backend state lease; plans do not apply resources. State and
saved plans remain private and are never uploaded as GitHub artifacts.

## Local checks

Run `make preflight` after initializing this backend with authorised local
credentials. It runs helper tests, formatting, backend-free validation and
mock-provider tests, then reads GitHub/Azure planning prerequisites. It performs
no apply. A local preflight cannot prove a GitHub OIDC exchange; the PR's real
plan must succeed before merge.

Terraform uses `ci/.terraform.lock.hcl`; OpenTofu previews use the separate
`ci/opentofu.lock.hcl`. Provider changes must update Linux and macOS checksums
and pass validation with `-lockfile=readonly`.

## Repository handover

The source was terraform-modules at
`b0c5f712b8a760e4853fa4a6f417b4e1cbde2782`. The destination keeps the same Azure
backend and `central-devops.tfstate` key, module names, resource keys and outputs.
There is no state-copy, state-push, import or state-move step.

1. Review the ops-shared migration and the companion module-library cleanup.
   Disable the old repository's deployment writers before switching trust.
2. Configure ops-shared `central-plan` with no approval/delay/branch restrictions,
   and `central-apply` with reviewer `michaelalinks`, main-only access and
   administrator bypass disabled.
3. Grant ops-shared the existing Azure identity, backend, Function storage and
   vault reference Secrets. Repoint the existing primary plan/apply federated
   credentials to the ops-shared subjects below through an explicitly approved
   bootstrap. Existing Terraform credential addresses stay unchanged.
4. Require an actual ops-shared PR plan and inspect the resource actions. The
   repository move must not replace existing network or identity resources.
   Function resources not previously applied remain legitimate additions.
5. After reviewing evidence and updating required checks, retire the old deploy
   workflows and revoke their secret access. Merge the ops-shared PR only with
   explicit permission to start the protected deployment sequence.

The initial ops-shared plan cannot authenticate until its trust exists. This
bootstrap is a prerequisite, not a reason to bypass the PR plan gate. Take
read-only backups of credential settings before the approved handover. Rollback
restores the prior credentials and writer configuration with approval; keep only
one repository authorised to deploy this state. Never overwrite or copy state.

Federated subjects:

```
repo:ai-platform-portfolio@334196300/ops-shared@1389842744:environment:central-plan
repo:ai-platform-portfolio@334196300/ops-shared@1389842744:environment:central-apply
```

Issuer: `https://token.actions.githubusercontent.com`; audience:
`api://AzureADTokenExchange`. The primary credential and extra `apply` resource
keep their addresses while their subjects change. Optional org-wide apply
enrollment remains enabled through `ci/onboarding.json`; only ops-shared has
planning trust. GitHub IDs, missing fields and the 20-credential limit are checked
before each plan, including the plan after approval.

## Secrets and Function runtime

Use organisation Secrets, never Actions Variables: `AZURE_CLIENT_ID`,
`AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`, the four `TF_BACKEND_*` inputs,
`FUNCTION_STORAGE` and `FUNCTION_VAULT`. No values are committed here.
The Function uses the previously approved VNet/subnet and public HTTPS endpoint;
this repository move does not change network topology.

Runtime identity permissions remain scoped to host/deployment containers, queues
and named vault secrets. The separate webhook signing secret must exist before
code publishing. Verify a real signed delivery, unsigned/tampered rejection,
README update, duplicate safety and private-repository exclusion before closing AI-5.
See [runtime configuration](../functions/profile_sync/README.md).

The older network ownership migration is historical evidence under
[migrations](migrations/VALIDATION.md); do not rerun it for this repository move.

Configuration references: [pinned module sources](https://developer.hashicorp.com/terraform/language/modules/configuration),
[backend initialization](https://developer.hashicorp.com/terraform/language/backend),
[reusable workflows and secret boundaries](https://docs.github.com/en/actions/how-tos/reuse-automations/reuse-workflows).
