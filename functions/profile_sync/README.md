# Organisation profile sync — AI-5

Signed GitHub repository webhooks enqueue a reconciliation; the worker reads only
GitHub's anonymous public repository listing and replaces the marked catalogue in
`.github/profile/README.md`. An hourly reconciliation recovers missed events.

Private repository webhook payloads are never queued or rendered. The app requests
a short-lived installation token scoped to `.github` with Contents write permission.
Public descriptions are escaped as Markdown text. Existing catalogue markers and
the current file SHA are mandatory; malformed files fail without writes.

## Code publication

This repository owns `.github/workflows/functions.yml`. Relevant changes merged
to main automatically queue deployment of `profile`; there is no manual trigger.
Apply the required infrastructure and federation from terraform-modules before
approving code deployment. Configure the protected environment before merging
the deployment workflow: referencing a missing environment does not establish
an approval requirement.
The `central-apply` environment requires the owner and main-only deployment with
administrator bypass disabled. The workflow checks those controls and its real
OIDC claims against the central Terraform declaration, then authenticates to Azure.
Only the existing three Azure identity Secrets are needed here; backend and vault
resource references remain restricted to terraform-modules.

The checked-out workflow SHA identifies the source. CI builds hash-locked Linux
dependencies, runs the actual HTTP-handler tests, publishes ZIP to Flex Consumption
and requires a real unsigned-request rejection. A reviewed revert and redeployment
provide rollback. Each deployment requires the owner's immediate approval.

## Optional repository onboarding

`AUTO_ENROLL_REPOSITORIES` defaults to false. This org opts in: authenticated
repository events enqueue onboarding, which uses a separate installation token
restricted to terraform-modules to request a `repository-onboarding` plan. The
README writer still uses a token restricted to `.github`; no private event data
is passed to CI. The app needs Contents write on those two repositories.

`terraform-modules/ci/onboarding.json` separately enables public repository discovery
for this org. Every discovered repo gets a desired apply credential on the existing
CI identity, irrespective of immediate Azure use. CI plans but does not apply for
webhook dispatches. Review and approve the normal infrastructure deployment to
create those credentials; publishing from another repo still requires protected
environments and explicitly granted identity Secrets. These settings do not carry
into downstream consumers. Duplicate events can repeat a read-only plan, not an apply.

Queue retries recover transient API failures and optimistic-write conflicts. A
duplicate event does not create a commit when the catalogue is unchanged. Failed
messages move to `profile-sync-poison` after five attempts; inspect poison messages
and Function failures when updates stop. Anonymous API rate limits can delay bursts.

## Configuration

| Setting | Source |
| --- | --- |
| `GITHUB_ORGANIZATION` | `ai-platform-portfolio` |
| `GITHUB_APP_ID` | Registered ops app ID |
| `GITHUB_INSTALLATION_ID` | Organisation installation ID |
| `GITHUB_APP_PRIVATE_KEY_SECRET` | Existing app secret name in Key Vault |
| `WEBHOOK_SECRET_NAME` | Separate webhook signing secret name in Key Vault |
| `KEY_VAULT_URL` | Existing vault URI, supplied through a deployment secret |
| `AzureWebJobsStorage__*` | Existing storage, using the runtime managed identity |

The runtime reads secret values with its managed identity. Terraform receives only
their names and vault reference; private keys and webhook secrets never enter plans.

GitHub webhook URL is `https://<function-host>/api/github`, with SSL verification
enabled and the Repository event selected. Do not activate until the signed-event
and unsigned-request tests pass against the deployed endpoint. Direct README writes
also require the owner's decision on the `.github` app bypass exception.

## Verification

`make test` includes deliberate invalid signatures, wrong organisations/installations,
private repositories, Markdown injection, malformed markers, pagination, preservation
of handwritten text and duplicate delivery. Dependency installation plus importing
`function_app` checks Azure's function indexing. These do not prove deployment.

Live acceptance requires an unsigned request to be rejected, a valid GitHub delivery
to be accepted, a public repository metadata change to update the README, a duplicate
to make no extra commit, and private repositories to remain absent. AI-5 stays open
until that evidence is recorded.
