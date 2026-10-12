"""All HTTP, queue and timer handlers deploy together in this Function App."""

import json
import os

import azure.functions as func

from sync import GitHub, accept_event, installation_client, reconcile

from onboarding import onboard, remove_repository, azure_request

app = func.FunctionApp()


def secret(setting):
    from azure.identity import ManagedIdentityCredential
    from azure.keyvault.secrets import SecretClient

    with ManagedIdentityCredential(client_id=os.environ["AZURE_CLIENT_ID"]) as credential:
        with SecretClient(vault_url=os.environ["KEY_VAULT_URL"], credential=credential) as client:
            return client.get_secret(os.environ[setting]).value


@app.route(route="github", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
@app.queue_output(arg_name="work", queue_name="profile-sync", connection="WorkQueue")
def webhook(req: func.HttpRequest, work: func.Out[str]) -> func.HttpResponse:
    try:
        accepted = accept_event(
            req.get_body(), req.headers.get("X-Hub-Signature-256", ""),
            req.headers.get("X-GitHub-Event", ""), secret("WEBHOOK_SECRET_NAME"),
            os.environ["GITHUB_ORGANIZATION"], int(os.environ["GITHUB_INSTALLATION_ID"]),
        )
    except (ValueError, TypeError, AttributeError):
        accepted = False
    if not accepted:
        return func.HttpResponse("Invalid webhook", status_code=403)
    onboarding = os.environ.get("AUTO_ENROLL_REPOSITORIES", "false") == "true"
    payload = json.loads(req.get_body())
    if onboarding and req.headers.get("X-GitHub-Event") == "repository" and payload.get("action") in {"created", "deleted"}:
        repository = payload["repository"]
        if payload["action"] == "deleted":
            owner = repository.get("owner", {})
            if (owner.get("login") != "ai-platform-portfolio" or str(owner.get("id")) != "334196300"
                    or type(repository.get("id")) is not int or repository["id"] <= 0):
                return func.HttpResponse("Invalid deleted repository", status_code=403)
        work.set(json.dumps({"repository": repository["name"], "id": repository["id"],
                             "action": payload["action"]}))
    else:
        work.set("reconcile")
    return func.HttpResponse(status_code=202)


@app.queue_trigger(arg_name="work", queue_name="profile-sync", connection="WorkQueue")
def sync_catalogue(work: func.QueueMessage):
    private_key = secret("GITHUB_APP_PRIVATE_KEY_SECRET")
    github = installation_client(
        os.environ["GITHUB_APP_ID"], os.environ["GITHUB_INSTALLATION_ID"],
        private_key,
    )
    # Anonymous reads cannot see private repositories, even if app scope changes.
    public = GitHub("")
    reconcile(os.environ["GITHUB_ORGANIZATION"], github, public.request)
    message = work.get_body().decode()
    if message != "reconcile":
        if os.environ.get("AUTO_ENROLL_REPOSITORIES", "false") != "true":
            raise ValueError("Repository onboarding is disabled")
        repository = json.loads(message)
        if repository.get("action", "created") == "deleted":
            remove_repository(repository["id"], azure_request)
        elif repository.get("action", "created") == "created":
            onboard(repository["repository"], repository["id"], private_key, azure_request)
        else:
            raise ValueError("Unsupported repository action")


@app.timer_trigger(arg_name="timer", schedule="0 0 * * * *", use_monitor=True)
@app.queue_output(arg_name="work", queue_name="profile-sync", connection="WorkQueue")
def recover_missed_events(timer: func.TimerRequest, work: func.Out[str]):
    work.set("reconcile")
