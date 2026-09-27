"""Fail Function code deployment unless the live webhook rejects unsigned requests."""

import os
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


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


if __name__ == "__main__":
    verify(os.environ["WEBHOOK_URL"])
