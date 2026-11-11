"""Exercise publication retries, artifact identity and serving-route isolation."""

import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock

from azure.core.exceptions import ResourceNotFoundError


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/foundry_deploy.py"
SPEC = importlib.util.spec_from_file_location("foundry_deploy", SCRIPT)
deploy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(deploy)
ENDPOINT = "https://example.services.ai.azure.com/api/projects/demo"
IMAGE = "example.azurecr.io/agents/demo@sha256:" + "a" * 64
REVISION = "a" * 40


class FoundryDeployTests(unittest.TestCase):
    def setUp(self):
        self.request = deploy.version_request({"model_deployment": "chat"}, IMAGE, "owner/catalog", REVISION, ENDPOINT)
        self.smoke = {"request": {"message": "health"}, "expect": {"status": "ok"}}
        self.versions = []
        self.snapshot = {"version_selector": {"version_selection_rules": deploy.fixed_rules("@latest")},
                         "protocol_configuration": {"invocations": {}}, "authorization_schemes": ["Entra"]}
        self.agents = Mock()

        def get(name):
            if not self.versions:
                raise ResourceNotFoundError("Agent not found")
            return {"agent_endpoint": copy.deepcopy(self.snapshot)}

        def create(name, **kwargs):
            version = {"version": str(len(self.versions) + 1), "status": "active",
                       "definition": kwargs["definition"].as_dict(), "metadata": copy.deepcopy(kwargs["metadata"])}
            self.versions.append(version)
            return version

        self.agents.get.side_effect = get
        self.agents.list_versions.side_effect = lambda *a, **k: copy.deepcopy(self.versions)
        self.agents.create_version.side_effect = create
        self.agents.get_version.side_effect = lambda n, v: copy.deepcopy(self.versions[int(v) - 1])
        self.agents.update_details.side_effect = lambda n, body: self.snapshot.update(body["agent_endpoint"])
        self.agents.create_session.side_effect = lambda n, version_indicator: {
            "agent_session_id": "smoke-session", "status": "creating", "version_indicator": version_indicator.as_dict(),
        }
        self.agents.get_session.side_effect = lambda *a: {
            "status": "active", "version_indicator": self.agents.create_session.call_args.kwargs["version_indicator"].as_dict(),
        }
        self.http = Mock()
        self.http.post.return_value.json.return_value = {"status": "ok", "extra": "allowed"}
        self.credential = Mock()
        self.credential.get_token.return_value.token = "test-token"

    def run_deploy(self):
        return deploy.deploy(self.agents, "demo", self.request, self.smoke, ENDPOINT, self.credential, self.http)

    def seed_base(self):
        base = copy.deepcopy(self.request)
        base["metadata"]["source_revision"] = "b" * 40
        self.versions.append({**base, "version": "1", "status": "active"})
        self.snapshot["version_selector"]["version_selection_rules"] = deploy.fixed_rules("1")

    def test_first_release_is_verified_and_pinned_and_retry_reuses_it(self):
        result = self.run_deploy()
        self.assertEqual(result["agent-version"], "1")
        self.assertEqual(self.snapshot["version_selector"]["version_selection_rules"], deploy.fixed_rules("1"))
        self.assertEqual(self.snapshot["authorization_schemes"], ["Entra"])
        self.agents.stop_session.assert_called_once_with("demo", "smoke-session")
        self.assertEqual(self.agents.create_version.call_args.kwargs["retry_total"], 0)
        self.run_deploy()
        self.assertEqual(self.agents.create_version.call_count, 1)

    def test_candidate_and_retry_preserve_serving_version_and_pin_smoke_session(self):
        self.seed_base()
        before = copy.deepcopy(self.snapshot)
        self.assertEqual(self.run_deploy()["agent-version"], "2")
        self.assertEqual(self.snapshot, before)
        self.assertEqual(self.agents.create_session.call_args.kwargs["version_indicator"].as_dict(),
                         {"type": "version_ref", "agent_version": "2"})
        self.assertEqual(self.http.post.call_args.kwargs["params"]["agent_session_id"], "smoke-session")
        self.run_deploy()
        self.assertEqual(self.agents.create_version.call_count, 1)
        self.agents.update_details.assert_not_called()

    def test_unpinned_existing_agent_and_foreign_ownership_fail_before_mutation(self):
        self.seed_base()
        self.snapshot["version_selector"]["version_selection_rules"] = deploy.fixed_rules("@latest")
        with self.assertRaisesRegex(ValueError, "Pin the existing"):
            self.run_deploy()
        self.versions[0]["metadata"]["source_repository"] = "another/repository"
        with self.assertRaisesRegex(ValueError, "ownership"):
            self.run_deploy()
        self.agents.create_version.assert_not_called()

    def test_same_revision_with_different_artifact_is_rejected(self):
        self.run_deploy()
        self.request["metadata"]["image_digest"] = IMAGE.replace("a" * 64, "b" * 64)
        with self.assertRaisesRegex(ValueError, "different image"):
            self.run_deploy()
        self.assertEqual(self.agents.create_version.call_count, 1)

    def test_failed_smoke_stops_session_and_does_not_pin_or_promote(self):
        self.seed_base()
        self.http.post.return_value.json.return_value = {"status": "failed"}
        with self.assertRaisesRegex(ValueError, "Smoke response"):
            self.run_deploy()
        self.agents.stop_session.assert_called_once()
        self.agents.update_details.assert_not_called()

    def test_failed_readiness_and_wrong_artifact_prevent_smoke(self):
        self.agents.get_version.side_effect = lambda *a: {"status": "failed"}
        with self.assertRaisesRegex(ValueError, "readiness failed"):
            self.run_deploy()
        self.agents.create_session.assert_not_called()
        with self.assertRaises(TimeoutError):
            deploy.wait_active(lambda: {"status": "creating"}, time.monotonic() - 1)
        self.agents.get_version.side_effect = lambda *a: {"status": "active", "definition": {}, "metadata": {}}
        with self.assertRaisesRegex(ValueError, "differs from"):
            self.run_deploy()
        self.agents.create_session.assert_not_called()

    def test_interrupted_initial_deployment_can_resume_exact_version(self):
        self.http.post.side_effect = RuntimeError("Transient smoke failure")
        with self.assertRaises(RuntimeError):
            self.run_deploy()
        self.http.post.side_effect = None
        self.assertEqual(self.run_deploy()["agent-version"], "1")
        self.assertEqual(self.agents.create_version.call_count, 1)

    def test_route_change_during_smoke_is_detected_without_overwriting_it(self):
        self.seed_base()

        def smoke(*a, **k):
            self.snapshot["authorization_schemes"] = ["changed externally"]
            response = Mock()
            response.json.return_value = {"status": "ok"}
            return response

        self.http.post.side_effect = smoke
        with self.assertRaisesRegex(ValueError, "Serving configuration changed"):
            self.run_deploy()
        self.agents.update_details.assert_not_called()

    def test_responses_smoke_uses_pinned_session_and_requires_completion(self):
        self.request = deploy.version_request({"model_deployment": "chat", "protocol": "responses"},
                                             IMAGE, "owner/catalog", REVISION, ENDPOINT)
        self.smoke = {"request": {"input": "health", "stream": True}, "expect": {"object": "response"}}
        self.http.post.return_value.json.return_value = {"object": "response", "status": "completed"}
        self.run_deploy()
        call = self.http.post.call_args
        self.assertTrue(call.args[0].endswith("/protocols/openai/responses"))
        self.assertEqual(call.kwargs["json"]["agent_session_id"], "smoke-session")
        self.assertIs(call.kwargs["json"]["stream"], False)

    def test_minimal_inputs_namespacing_and_invalid_contracts(self):
        with tempfile.TemporaryDirectory() as directory:
            config, smoke = Path(directory) / "deployment.json", Path(directory) / "smoke.json"
            config.write_text('{"model_deployment":"chat"}')
            smoke.write_text(json.dumps(self.smoke))
            first = deploy.load_inputs("demo", "owner/one", REVISION, config, smoke)[0]
            second = deploy.load_inputs("demo", "owner/two", REVISION, config, smoke)[0]
            self.assertNotEqual(first, second)
            self.assertEqual(first, deploy.load_inputs("demo", "OWNER/ONE", REVISION, config, smoke)[0])
            config.write_text('{"model_deployment":"chat","unexpected":true}')
            with self.assertRaisesRegex(ValueError, "Unknown"):
                deploy.load_inputs("demo", "owner/one", REVISION, config, smoke)
            config.write_text('{"model_deployment":"chat"}')
            smoke.write_text('{"request":{},"expect":{}}')
            with self.assertRaisesRegex(ValueError, "Smoke requires"):
                deploy.load_inputs("demo", "owner/one", REVISION, config, smoke)
        self.assertEqual(deploy.pinned_image("example.azurecr.io/demo:mutable", "sha256:" + "a" * 64),
                         "example.azurecr.io/demo@sha256:" + "a" * 64)
        with self.assertRaises(ValueError):
            deploy.pinned_image("example.azurecr.io/demo:mutable", "latest")
        with self.assertRaises(ValueError):
            deploy.version_request({"model_deployment": "chat"}, IMAGE, "owner/repo", REVISION, "https://untrusted.test")
        self.assertFalse(deploy.matches({}, {"missing": None}))


if __name__ == "__main__":
    unittest.main()
