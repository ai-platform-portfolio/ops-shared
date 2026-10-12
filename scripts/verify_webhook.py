"""Fail Function code deployment unless the live webhook rejects unsigned requests."""

import json
import os
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import quote, urlsplit


def verify(url, attempts=12):
    for attempt in range(attempts):
        request = Request(url, data=b"{}", method="POST", headers={"Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=10):
                raise RuntimeError("Webhook accepted an unsigned request")
        except HTTPError as error:
            if error.code == 403:
                print("Live webhook rejected unsigned delivery (403). Signed-delivery verification remains required.")
                return
            if error.code < 500 and error.code != 404:
                raise RuntimeError(f"Unexpected webhook response ({error.code})") from None
        except URLError:
            pass
        if attempt + 1 < attempts:
            time.sleep(10)
    raise RuntimeError("Webhook did not become ready; deployment is unverified")


def verify_bindings(url, app, group):
    key = subprocess.check_output([
        "az", "functionapp", "keys", "list", "--name", app, "--resource-group", group,
        "--query", "masterKey", "-o", "tsv",
    ], text=True).strip()
    if not key:
        raise RuntimeError("Function host key is unavailable")
    names = json.loads(subprocess.check_output([
        "az", "functionapp", "function", "list", "--name", app, "--resource-group", group,
        "--query", "[].name", "-o", "json",
    ], text=True))
    if not names:
        raise RuntimeError("No deployed functions found")
    parsed = urlsplit(url)
    for name in names:
        endpoint = f"{parsed.scheme}://{parsed.netloc}/admin/functions/{quote(name.rsplit('/', 1)[-1], safe='')}/status"
        with urlopen(Request(endpoint, headers={"x-functions-key": key}), timeout=20) as response:
            if json.load(response).get("errors"):
                raise RuntimeError("Deployed Function has binding errors; inspect its admin status")
    print("All deployed Function bindings indexed without errors")


if __name__ == "__main__":
    verify_bindings(os.environ["WEBHOOK_URL"], os.environ["FUNCTION_NAME"], os.environ["FUNCTION_GROUP"])
    verify(os.environ["WEBHOOK_URL"])
