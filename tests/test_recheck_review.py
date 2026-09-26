import unittest
from unittest.mock import Mock, patch

from scripts.recheck_review import refresh


class ReviewRerun(unittest.TestCase):
    def test_configured_nested_job_is_selected(self):
        self.jobs[0]['name'] = 'policy / check'
        with patch.dict('os.environ', REVIEW_JOB='policy / check'):
            refresh(self.api, 3, 'current')
        self.assertEqual(len(self.posts()), 1)

    def setUp(self):
        self.pull = {"state": "open", "head": {"sha": "current"}}
        self.run = {
            "id": 10,
            "event": "pull_request",
            "head_sha": "current",
            "pull_requests": [{"number": 3}],
            "status": "completed",
        }
        self.runs = [self.run]
        self.jobs = [
            {"id": 20, "name": "structure", "conclusion": "failure"},
            {"id": 21, "name": "validate", "conclusion": "success"},
        ]
        self.api = Mock(side_effect=self.response)

    def response(self, path, method="GET"):
        if method == "POST":
            self.assertEqual(path, "actions/jobs/20/rerun")
        elif path == "pulls/3":
            return self.pull
        elif path.startswith("actions/workflows/quality.yml/runs?"):
            return {"workflow_runs": self.runs}
        elif path == "actions/runs/10/jobs?filter=latest&per_page=100":
            return {"total_count": len(self.jobs), "jobs": self.jobs}
        else:
            self.fail(f"Unexpected API call: {path}")

    def posts(self):
        return [call for call in self.api.call_args_list if call.kwargs.get("method") == "POST"]

    def test_refreshes_failed_and_successful_job_in_original_run(self):
        for conclusion in ("failure", "success"):
            with self.subTest(conclusion=conclusion):
                self.api.reset_mock()
                self.jobs[0]["conclusion"] = conclusion
                refresh(self.api, 3, "current")
                self.assertEqual(len(self.posts()), 1)

    def test_ignores_other_pr_commit_and_event(self):
        self.runs.extend(
            [
                dict(self.run, id=11, pull_requests=[{"number": 4}]),
                dict(self.run, id=12, head_sha="old"),
                dict(self.run, id=13, event="push"),
            ]
        )
        refresh(self.api, 3, "current")
        self.assertEqual(len(self.posts()), 1)

    def test_waits_for_inflight_run_and_skips_superseded_head(self):
        self.run["status"] = "in_progress"
        pause = Mock(side_effect=lambda _: self.run.update(status="completed"))
        refresh(self.api, 3, "current", pause)
        pause.assert_called_once_with(5)
        self.api.reset_mock()
        self.pull["head"]["sha"] = "new"
        refresh(self.api, 3, "current")
        self.assertFalse(self.posts())

    def test_missing_or_ambiguous_job_and_timeout_fail_without_rerun(self):
        for jobs in ([], [self.jobs[0], self.jobs[0]]):
            self.jobs = jobs
            with self.assertRaises(RuntimeError):
                refresh(self.api, 3, "current")
        self.runs = []
        with self.assertRaises(RuntimeError):
            refresh(self.api, 3, "current", Mock())
        self.assertFalse(self.posts())
