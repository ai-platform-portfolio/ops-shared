"""Refresh the configured original PR policy job after a review changes."""

import json
import os
import subprocess
import time
from pathlib import Path


def refresh(api, number, head, pause=time.sleep):
    workflow = os.environ.get("REVIEW_WORKFLOW", "quality.yml")
    job_name = os.environ.get("REVIEW_JOB", "structure")
    for _ in range(60):
        pull = api(f"pulls/{number}")
        if pull["state"] != "open" or pull["head"]["sha"] != head:
            return "PR closed or superseded; no rerun needed"
        runs = api(
            f"actions/workflows/{workflow}/runs?event=pull_request&head_sha={head}&per_page=100"
        )["workflow_runs"]
        matching = [
            run
            for run in runs
            if run["event"] == "pull_request"
            and run["head_sha"] == head
            and any(pr["number"] == number for pr in run["pull_requests"])
        ]
        run = max(matching, key=lambda item: item["id"], default=None)
        if run and run["status"] == "completed":
            result = api(f"actions/runs/{run['id']}/jobs?filter=latest&per_page=100")
            jobs = [job for job in result["jobs"] if job["name"] == job_name]
            if result["total_count"] > 100 or len(jobs) != 1:
                raise RuntimeError(f"Expected exactly one {job_name} job in the latest attempt")
            current = api(f"pulls/{number}")
            if current["state"] != "open" or current["head"]["sha"] != head:
                return "PR closed or superseded; no rerun needed"
            api(f"actions/jobs/{jobs[0]['id']}/rerun", method="POST")
            return f"Requested {job_name} rerun in original run {run['id']}"
        pause(5)
    raise RuntimeError("PR validation did not finish within five minutes; rerun this review job")


def main():
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    repository = os.environ["GITHUB_REPOSITORY"]

    def api(path, method="GET"):
        output = subprocess.check_output(
            ["gh", "api", "--method", method, f"repos/{repository}/{path}"], text=True
        )
        return json.loads(output) if output.strip() else None

    print(refresh(api, event["pull_request"]["number"], event["pull_request"]["head"]["sha"]))


if __name__ == "__main__":
    main()
