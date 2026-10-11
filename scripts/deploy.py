"""Execute deployment phases with captured, redacted diagnostics and local plans."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from deployment_plan import fingerprint, verify as verify_plan
from federation_contract import verify as verify_federation
from plan_preview import failure_diagnostics, redact
from settings import command, init_args, plan_args


def api(path):
    return json.loads(subprocess.check_output(['gh', 'api', path], text=True))


def approval_controls(environment, branches, owner):
    reviewers = [rule for rule in environment['protection_rules'] if rule['type'] == 'required_reviewers']
    policies = branches['branch_policies']
    if (environment.get('can_admins_bypass') is not False
            or len(reviewers) != 1
            or [item['reviewer'].get('login') for item in reviewers[0]['reviewers']] != [owner]
            or environment.get('deployment_branch_policy', {}).get('custom_branch_policies') is not True
            or len(policies) != 1
            or policies[0]['name'] != 'main'
            or policies[0]['type'] != 'branch'):
        raise ValueError('Apply environment must require the named owner, forbid bypass and allow main only')


def run(args):
    result = subprocess.run([*command(), *args], capture_output=True, text=True)
    if result.returncode:
        codes = sorted(set(re.findall(r"AADSTS\d+", result.stdout + result.stderr)))
        detail = ", ".join(codes) or f"exit {result.returncode}"
        # Report a stage and standard error headings, never raw command output.
        headings = re.findall(r"^Error: ([^\n]+)", result.stdout + result.stderr, re.MULTILINE)
        safe_headings = {
            "Inconsistent dependency lock file", "Failed to install provider",
            "Failed to query available provider packages", "Unsupported Terraform Core version",
            "Backend configuration changed", "Error acquiring the state lock",
            "Failed to get existing workspaces", "Invalid provider configuration",
            "Insufficient features blocks", "Unsupported argument", "Reference to undeclared resource",
        }
        labels = [heading for heading in headings if heading in safe_headings]
        print(f"::error::Infrastructure {args[0]} failed ({detail})" +
              (": " + "; ".join(labels) if labels else ""), file=sys.stderr)
        print(failure_diagnostics(result), file=sys.stderr)
        raise RuntimeError('Infrastructure command failed; see redacted diagnostics')
    return result.stdout


def main():
    phase = os.environ.get('TF_PHASE', 'plan')
    if phase not in {'plan', 'apply'} or os.environ['GITHUB_REF'] != 'refs/heads/main':
        raise ValueError('Deployment requires main and a supported phase')
    repository = os.environ['GITHUB_REPOSITORY']
    environment = os.environ['TF_APPLY_ENVIRONMENT']
    prefix = f'repos/{repository}/environments/{environment}'
    print('Checking protected Apply environment.', flush=True)
    approval_controls(api(prefix), api(prefix + '/deployment-branch-policies'), os.environ['TF_APPROVER'])
    if phase == 'apply' and api(f'repos/{repository}/commits/main')['sha'] != os.environ['GITHUB_SHA']:
        raise ValueError('Deployment superseded by a newer main revision')
    print('Checking federation contract.', flush=True)
    verify_federation(lambda name: api(f'repos/{name}'))
    saved = Path(os.environ['RUNNER_TEMP']) / f'{phase}.tfplan'
    try:
        print('Initializing infrastructure backend and providers.', flush=True)
        run(init_args())
        print('Planning infrastructure.', flush=True)
        output = run(plan_args(saved))
        plan = json.loads(run(['show', '-json', str(saved)]))
        secrets = [value for key, value in os.environ.items()
                   if key.startswith(('ARM_', 'TF_STATE_', 'TF_VAR_')) and value not in {'true', 'false'}]
        summary = redact(output, secrets, plan)
        with Path(os.environ['GITHUB_STEP_SUMMARY']).open('a') as stream:
            stream.write(f"## Infrastructure plan\n\nCommit: `{os.environ['GITHUB_SHA']}`\n\n```hcl\n{summary}\n```\n")
        if phase == 'apply':
            verify_plan(plan, os.environ['EXPECTED_FINGERPRINT'])
            run(['apply', '-input=false', '-lock-timeout=5m', str(saved)])
            print('Applied the approved plan; local plan removed on exit.')
        else:
            changes = any(item['change']['actions'] != ['no-op'] for item in plan.get('resource_changes', []))
            changes |= any(item['actions'] != ['no-op'] for item in plan.get('output_changes', {}).values())
            with Path(os.environ['GITHUB_OUTPUT']).open('a') as stream:
                stream.write(f'fingerprint={fingerprint(plan)}\nchanges={str(changes).lower()}\n')
            print('Changes require deployment approval.' if changes else 'No changes; apply will be skipped.')
    finally:
        saved.unlink(missing_ok=True)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, RuntimeError, OSError, KeyError, subprocess.CalledProcessError):
        print('::error::Deployment contract or execution failed; no unverified plan will be applied.', file=sys.stderr)
        raise SystemExit(1)
