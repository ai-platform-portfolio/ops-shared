import os

import azure.functions as func

from sync import GitHub, accept_event, installation_client, reconcile

app = func.FunctionApp()


def secret(setting):
    from azure.identity import ManagedIdentityCredential
    from azure.keyvault.secrets import SecretClient

    with ManagedIdentityCredential(client_id=os.environ["AZURE_CLIENT_ID"]) as credential:
        with SecretClient(vault_url=os.environ["KEY_VAULT_URL"], credential=credential) as client:
            return client.get_secret(os.environ[setting]).value


@app.route(route="github", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
@app.queue_output(arg_name="work", queue_name="profile-sync", connection="AzureWebJobsStorage")
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
    work.set("reconcile")
    return func.HttpResponse(status_code=202)


@app.queue_trigger(arg_name="work", queue_name="profile-sync", connection="AzureWebJobsStorage")
def sync_catalogue(work: func.QueueMessage):
    github = installation_client(
        os.environ["GITHUB_APP_ID"], os.environ["GITHUB_INSTALLATION_ID"],
        secret("GITHUB_APP_PRIVATE_KEY_SECRET"),
    )
    # Anonymous reads cannot see private repositories, even if app scope changes.
    public = GitHub("")
    reconcile(os.environ["GITHUB_ORGANIZATION"], github, public.request)


@app.timer_trigger(arg_name="timer", schedule="0 0 * * * *", use_monitor=True)
@app.queue_output(arg_name="work", queue_name="profile-sync", connection="AzureWebJobsStorage")
def recover_missed_events(timer: func.TimerRequest, work: func.Out[str]):
    work.set("reconcile")
