# Function App

`function_app.py` contains the HTTP webhook, queue worker and hourly recovery
function. `sync.py` handles signed-event validation and GitHub catalogue/onboarding
requests. Add further handlers to the same app; every deployment includes them all.

`host.json` configures the runtime and queue retries. `requirements.txt` is the
single dependency file: exact versions and verified wheel hashes for Python 3.12
on Linux x86_64 (Azure/CI) and macOS arm64. Update versions and the hashes of their
published wheels together; CI installs with `--require-hashes`.

Run **Deploy Function App** in ops-shared from main. It builds one ZIP containing
only the four runtime files and installed dependencies, checks indexing and the
security tests in `tests/functions`, deploys with Azure OIDC, and verifies that
`/api/github` rejects unsigned requests. Tests and this README are not published.

Before deployment, configure `central-apply` to require the owner, forbid admin
bypass and allow only main. Configure the ops GitHub App's Repository events and
signed webhook URL after deployment. App private keys and the webhook secret stay
in Key Vault; the runtime reads them using managed identity. Existing onboarding
requests a platform-iac plan only; the .github migration is not activated yet.
