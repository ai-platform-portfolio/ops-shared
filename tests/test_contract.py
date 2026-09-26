import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import deploy
from settings import command, plan_args


class Contract(unittest.TestCase):
    def test_workload_root_and_vars_are_arguments_not_shell(self):
        with patch.dict(os.environ, TF_ENGINE='terraform', TF_ROOT='infra/prod', TF_VARS_FILE='prod.tfvars'):
            self.assertEqual(command(), ['terraform', '-chdir=infra/prod'])
            self.assertIn('-var-file=prod.tfvars', plan_args('/tmp/plan'))
        for root in ('../outside', '/tmp/outside', '-bad'):
            with patch.dict(os.environ, TF_ROOT=root), self.assertRaises(ValueError):
                command()

    def test_approval_controls_fail_closed(self):
        environment = {'can_admins_bypass': False,
                       'deployment_branch_policy': {'custom_branch_policies': True},
                       'protection_rules': [{'type': 'required_reviewers', 'reviewers': [{'reviewer': {'login': 'owner'}}]}]}
        branches = {'branch_policies': [{'name': 'main', 'type': 'branch'}]}
        deploy.approval_controls(environment, branches, 'owner')
        for key, value in [('can_admins_bypass', True), ('protection_rules', []), ('deployment_branch_policy', {})]:
            with self.assertRaises(ValueError):
                deploy.approval_controls({**environment, key: value}, branches, 'owner')
        with self.assertRaises(ValueError):
            deploy.approval_controls(environment, {'branch_policies': [{'name': '*', 'type': 'branch'}]}, 'owner')

    def test_plan_outputs_distinguish_no_changes_resource_changes_and_outputs(self):
        for changes, outputs, expected in [([], {}, False),
                ([{'change': {'actions': ['create']}}], {}, True),
                ([], {'value': {'actions': ['update']}}, True)]:
            with tempfile.TemporaryDirectory() as directory:
                env = {'TF_PHASE': 'plan', 'GITHUB_REF': 'refs/heads/main',
                       'GITHUB_REPOSITORY': 'example/workload', 'GITHUB_SHA': 'abc',
                       'TF_APPLY_ENVIRONMENT': 'apply', 'TF_APPROVER': 'owner',
                       'RUNNER_TEMP': directory, 'GITHUB_OUTPUT': directory + '/output',
                       'GITHUB_STEP_SUMMARY': directory + '/summary'}
                plan = {'resource_changes': changes, 'output_changes': outputs}
                with patch.dict(os.environ, env), patch('deploy.api'), patch('deploy.approval_controls'), \
                     patch('deploy.verify_federation'), patch('deploy.init_args', return_value=['init']), \
                     patch('deploy.run', side_effect=['', 'redacted plan', json.dumps(plan)]):
                    deploy.main()
                self.assertIn(f'changes={str(expected).lower()}', Path(env['GITHUB_OUTPUT']).read_text())
                self.assertFalse((Path(directory) / 'plan.tfplan').exists())

    def test_non_main_deployment_fails_before_api_or_commands(self):
        with patch.dict(os.environ, TF_PHASE='apply', GITHUB_REF='refs/heads/feature'), \
             patch('deploy.api') as api, self.assertRaises(ValueError):
            deploy.main()
        api.assert_not_called()

    def test_changed_plan_never_applies_and_removes_saved_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            saved = Path(directory) / 'apply.tfplan'
            saved.write_text('sensitive saved plan')
            env = {'TF_PHASE': 'apply', 'GITHUB_REF': 'refs/heads/main',
                   'GITHUB_REPOSITORY': 'example/workload', 'GITHUB_SHA': 'abc',
                   'TF_APPLY_ENVIRONMENT': 'apply', 'TF_APPROVER': 'owner',
                   'RUNNER_TEMP': directory, 'GITHUB_STEP_SUMMARY': directory + '/summary',
                   'EXPECTED_FINGERPRINT': 'different'}
            with patch.dict(os.environ, env), patch('deploy.api', return_value={'sha': 'abc'}), \
                 patch('deploy.approval_controls'), patch('deploy.verify_federation'), \
                 patch('deploy.init_args', return_value=['init']), \
                 patch('deploy.run', side_effect=['', 'plan', '{}']) as run:
                with self.assertRaisesRegex(ValueError, 'fresh approval'):
                    deploy.main()
                self.assertFalse(any(call.args[0][0] == 'apply' for call in run.call_args_list))
            self.assertFalse(saved.exists())
