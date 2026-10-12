# Shared Function App

All functions in `ai-platform-portfolio-ops` deploy together from this directory.
A ZIP deployment replaces the Function App package, so deploying individual
handler folders would remove other deployed handlers.

`function_app.py` registers each Azure Functions Blueprint. `profile_sync/`
contains the GitHub webhook, queue worker and recovery timer. Add another folder
with a Blueprint and register it in `function_app.py`; the deployment package
includes all registered handlers. Keep shared dependencies in `requirements.in`
and regenerate the hash-locked `requirements.txt` for Python 3.12. Put tests in
`<folder>/tests/`; both CI and deployment run every folder's tests and check indexing.

Run **Deploy central Function code** in ops-shared from main with
`application=operations`. The GitHub `central-apply` environment must require the
owner, disallow administrator bypass and restrict deployments to main. The job
uses GitHub OIDC, packages all handlers, deploys once, and requires the webhook
to reject an unsigned request. Signed webhook delivery remains a separate live
acceptance check.

Infrastructure remains in platform-iac. Function code publication requires the
main PR to merge and the owner to approve the protected deployment job.
