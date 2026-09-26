# Migration validation

Status: local implementation under validation; live migration incomplete.

| Outcome | Evidence required | Current status |
|---|---|---|
| Shared library and immutable callers | Regular PRs in ops-shared and all three existing repositories | Local branches prepared; bootstrap publication awaiting approval |
| Workflow syntax and shell failures | Actionlint/ShellCheck plus malformed-shell fixture | Local workflow validation passes |
| Terraform guard behaviour | Redaction, safe auth failure, fingerprint mismatch, cleanup, approval and no-change tests | Local tests pass |
| Repository regression checks | Standards lint/type/review tests and acceptance cases | Standards checks pass; all 29 acceptance cases pass; backend-free module suite passes |
| Actual PR plan and comment update | Successful migrated backend/auth/refresh run, commit-labelled comment, same comment ID on rerun | Pending publication and CI |
| Actual authentication failure | Failed auth posts safe failure; required caller gate fails | Pending controlled live validation |
| Main no-change behaviour | Actual migrated main plan with changes=false and apply skipped | Pending owner-approved merge and run |
| Deployment approval/re-plan | Current environment checks; changed fingerprint prevents apply | Local tests; live application not authorized |
| Policy adoption and review events | Owner current-head review reruns original nested job and dependent gate | Local approval tests; live owner review pending |
| Container build/cache/publication | Build and second-run cache hit; approved registry push/digest | Fixture prepared; registry publication choice pending |
| Governance | New repo protected; owner-verified baseline; audit green | Exact rules prepared; activation awaiting approval |

Do not mark this migration complete from YAML validation or mocked tests alone.
Never trigger an apply to obtain evidence without the owner's per-command approval.
Regular PRs remain regular while evidence and owner review are pending.

Local shared-helper result: 19 tests pass. Actionlint 1.7.7 with ShellCheck 0.11.0
passes for all four repositories' workflows. The governance contract/README test
currently fails because ops-shared is intentionally documented as unprotected;
16 other governance tests pass. Resolve this by activating and verifying the
approved controls, not by weakening the test or claiming protection prematurely.

Local commands completed on 2026-09-26:

```text
ops-shared: make test
ops-shared: tests/test_workflow_lint.sh with Actionlint and ShellCheck on PATH
terraform-modules: make test (backend=false initialization and mocked Terraform tests)
engineering-standards: make check
engineering-acceptance: make test STANDARDS=../engineering-standards-ops
engineering-standards: make governance-test (16 pass; contract/README fails pending controls)
```
