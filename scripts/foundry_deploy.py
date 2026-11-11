"""Deploy an immutable hosted-agent version using the official Foundry SDK."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time

import httpx
from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import HostedAgentDefinition, VersionRefIndicator
from azure.core.exceptions import ResourceNotFoundError
from azure.identity import AzureCliCredential


def require(condition, message):
    if not condition:
        raise ValueError(message)


def matches(actual, expected):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(k in actual and matches(actual[k], v) for k, v in expected.items())
    return actual == expected


def plain(value):
    return value.as_dict() if hasattr(value, "as_dict") else value


def load_inputs(agent, repository, revision, configuration, smoke_file):
    require(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", agent), "Invalid agent directory name")
    require(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository), "Expected owner/repository")
    require(re.fullmatch(r"[0-9a-f]{40}", revision), "Expected full source revision")
    config = json.loads(Path(configuration).read_text())
    require(isinstance(config, dict) and set(config) <= {
        "model_deployment", "protocol", "cpu", "memory", "environment_variables"
    }, "Unknown deployment configuration field")
    require(isinstance(config.get("model_deployment"), str) and
            re.fullmatch(r"[A-Za-z0-9_.-]+", config["model_deployment"]), "model_deployment is required")
    protocol = config.get("protocol", "invocations")
    require(protocol in {"invocations", "responses"}, "Unsupported agent protocol")
    environment = config.get("environment_variables", {})
    require(isinstance(environment, dict) and all(
        re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k) and isinstance(v, str)
        for k, v in environment.items()), "Environment variables must be string-valued identifiers")
    require(not {"MODEL_DEPLOYMENT_NAME", "FOUNDRY_PROJECT_ENDPOINT"} & environment.keys(),
            "Model and project environment variables are deployment-owned")
    for key, default in (("cpu", "1"), ("memory", "2Gi")):
        require(isinstance(config.get(key, default), str) and config.get(key, default).strip(),
                f"{key} must be a nonempty string")
    smoke = json.loads(Path(smoke_file).read_text())
    require(isinstance(smoke, dict) and set(smoke) == {"request", "expect"} and
            isinstance(smoke["request"], dict) and isinstance(smoke["expect"], dict) and smoke["expect"],
            "Smoke requires a request object and nonempty expected response fields")
    require("agent_session_id" not in smoke["request"], "Smoke session is deployment-owned")
    identity = f"{repository.lower()}:{agent}"
    name = f"{agent[:45]}-{hashlib.sha256(identity.encode()).hexdigest()[:12]}"
    return name, config, smoke


def pinned_image(image, digest):
    require(re.fullmatch(r"sha256:[0-9a-f]{64}", digest), "Published sha256 digest is required")
    require(re.fullmatch(r"[a-z0-9.-]+(?::[0-9]+)?/[a-z0-9._/-]+:[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", image),
            "Expected the registry-qualified tagged image from the Docker workflow")
    return image.rsplit(":", 1)[0] + "@" + digest


def version_request(config, image, repository, revision, endpoint):
    require(re.fullmatch(r"https://[a-z0-9-]+\.services\.ai\.azure\.com/api/projects/[A-Za-z0-9_-]+", endpoint),
            "Expected a public-Azure Foundry project HTTPS endpoint")
    definition = {
        "kind": "hosted",
        "container_configuration": {"image": image},
        "cpu": config.get("cpu", "1"),
        "memory": config.get("memory", "2Gi"),
        "protocol_versions": [{"protocol": config.get("protocol", "invocations"), "version": "2.0.0"}],
        "environment_variables": {
            "PYTHONNOUSERSITE": "1", **config.get("environment_variables", {}),
            "MODEL_DEPLOYMENT_NAME": config["model_deployment"], "FOUNDRY_PROJECT_ENDPOINT": endpoint,
        },
    }
    return {
        "definition": definition,
        "metadata": {
            "source_repository": repository.lower(), "source_revision": revision, "image_digest": image,
            "configuration_sha256": hashlib.sha256(json.dumps(definition, sort_keys=True).encode()).hexdigest(),
        },
    }


def route(details):
    endpoint = details.get("agent_endpoint") or {}
    return {key: endpoint.get(key) for key in (
        "version_selector", "protocol_configuration", "authorization_schemes"
    )}


def fixed_rules(version):
    return [{"agent_version": version, "traffic_percentage": 100, "type": "FixedRatio"}]


def is_pinned(snapshot):
    rules = (snapshot.get("version_selector") or {}).get("version_selection_rules", [])
    return (len(rules) == 1 and str(rules[0].get("agent_version", "")).isdigit() and
            rules == fixed_rules(rules[0]["agent_version"]))


def wait_active(fetch, deadline):
    while time.monotonic() < deadline:
        value = plain(fetch())
        status = value.get("status")
        if status == "active":
            return value
        require(status in {"creating", "updating"}, f"Foundry readiness failed: {status}")
        time.sleep(min(5, max(0, deadline - time.monotonic())))
    raise TimeoutError("Foundry readiness deadline exceeded")


def deploy(agents, name, request, smoke, endpoint, credential, http, timeout=600):
    deadline = time.monotonic() + timeout
    try:
        before = route(plain(agents.get(name)))
    except ResourceNotFoundError:
        before = None
    versions = [plain(v) for v in agents.list_versions(name, include_drafts=True)] if before is not None else []

    require(all((v.get("metadata") or {}).get("source_repository") == request["metadata"]["source_repository"]
                for v in versions), "Existing agent versions have different or missing repository ownership")
    same_revision = [v for v in versions if (v.get("metadata") or {}).get("source_revision") ==
                     request["metadata"]["source_revision"]]
    require(len(same_revision) <= 1, "Multiple versions claim this source revision")
    existing = same_revision[0] if same_revision else None
    if existing:
        require(matches(existing, request), "Source revision already exists with a different image or configuration")
    # Recover an interrupted first deployment only for its exact sole version.
    initial = before is None or (len(versions) == 1 and existing is not None and not is_pinned(before))
    require(initial or is_pinned(before), "Pin the existing serving version before deploying a candidate")
    if existing is None:
        existing = plain(agents.create_version(
            name, definition=HostedAgentDefinition(**request["definition"]), metadata=request["metadata"],
            draft=False, retry_total=0,
        ))
    version = str(existing["version"])
    require(version.isdigit(), "Expected a concrete hosted-agent version")
    active = wait_active(lambda: agents.get_version(name, version), deadline)
    require(matches(active, request), "Active version differs from the requested artifact")
    if not initial:
        require(route(plain(agents.get(name))) == before, "Serving configuration changed during deployment")

    session = plain(agents.create_session(name, version_indicator=VersionRefIndicator(agent_version=version)))
    session_id = session["agent_session_id"]
    try:
        ready = wait_active(lambda: agents.get_session(name, session_id), deadline)
        require(matches(ready.get("version_indicator"), {"type": "version_ref", "agent_version": version}),
                "Smoke session is not pinned to the candidate")
        protocol = request["definition"]["protocol_versions"][0]["protocol"]
        params = {"api-version": "v1"}
        payload = dict(smoke["request"])
        if protocol == "invocations":
            params["agent_session_id"] = session_id
            url = f"{endpoint}/agents/{name}/endpoint/protocols/invocations"
        else:
            payload.update(agent_session_id=session_id, stream=False)
            url = f"{endpoint}/agents/{name}/endpoint/protocols/openai/responses"
        response = http.post(url, params=params, json=payload, headers={
            "Authorization": "Bearer " + credential.get_token("https://ai.azure.com/.default").token,
        })
        response.raise_for_status()
        result = response.json()
        require(matches(result, smoke["expect"]), "Smoke response did not match expected fields")
        if protocol == "responses":
            require(result.get("status") == "completed", "Responses smoke did not complete")
    finally:
        agents.stop_session(name, session_id)

    current = route(plain(agents.get(name)))
    if initial:
        observed = [plain(v) for v in agents.list_versions(name, include_drafts=True)]
        require(len(observed) == 1 and str(observed[0]["version"]) == version,
                "Initial route pin requires the exact sole agent version")
        require(before is None or current == before, "Initial serving configuration changed")
        agents.update_details(name, body={"agent_endpoint": {
            "version_selector": {"version_selection_rules": fixed_rules(version)},
        }})
        expected = {**current, "version_selector": {"version_selection_rules": fixed_rules(version)}}
        require(route(plain(agents.get(name))) == expected, "Initial route pin readback failed")
    else:
        require(current == before, "Serving configuration changed during smoke verification")
    return {"agent-name": name, "agent-version": version, "image": request["metadata"]["image_digest"],
            "revision": request["metadata"]["source_revision"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("agent", "repository", "revision", "configuration", "smoke-file"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--image", default="")
    parser.add_argument("--digest", default="")
    parser.add_argument("--project-endpoint", default="")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    name, config, smoke = load_inputs(args.agent, args.repository, args.revision, args.configuration, args.smoke_file)
    image = pinned_image(args.image, args.digest) if args.digest else None
    if args.validate_only:
        if args.project_endpoint:
            version_request(config, image, args.repository, args.revision, args.project_endpoint.rstrip("/"))
        print("Agent deployment configuration and smoke contract are valid")
        return
    require(image, "A published image digest is required for deployment")
    endpoint = args.project_endpoint.rstrip("/")
    request = version_request(config, image, args.repository, args.revision, endpoint)
    with AzureCliCredential() as credential, AIProjectClient(
        endpoint, credential, connection_timeout=10, read_timeout=30,
    ) as project, httpx.Client(timeout=120, follow_redirects=False) as http:
        result = deploy(project.agents, name, request, smoke, endpoint, credential, http)
    print(json.dumps(result))
    if os.getenv("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            output.write("".join(f"{key}={value}\n" for key, value in result.items()))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, TimeoutError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
