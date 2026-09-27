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
        with patch.dict(os.environ, TF_ROOT='infra/prod', TF_VARS_FILE='prod.tfvars'):
            self.assertEqual(command(), ['tofu', '-chdir=infra/prod'])
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

    def test_approval_controls_accept_a_team_reviewer_by_org_and_slug(self):
        team = {'type': 'Team', 'reviewer': {
            'slug': 'platform', 'html_url': 'https://github.com/orgs/example/teams/platform'}}
        environment = {'can_admins_bypass': False,
                       'deployment_branch_policy': {'custom_branch_policies': True},
                       'protection_rules': [{'type': 'required_reviewers', 'reviewers': [team]}]}
        branches = {'branch_policies': [{'name': 'main', 'type': 'branch'}]}
        deploy.approval_controls(environment, branches, 'example/platform')
        for approver in ['platform', 'other/platform', 'example/other']:
            with self.assertRaises(ValueError):
                deploy.approval_controls(environment, branches, approver)
        blank = {'type': 'Team', 'reviewer': {'slug': 'platform'}}
        with self.assertRaises(ValueError):
            deploy.approval_controls({**environment, 'protection_rules': [
                {'type': 'required_reviewers', 'reviewers': [blank]}]}, branches, 'example/platform')

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
                (Path(directory) / 'review.tfplan').write_text('planned')
                with patch.dict(os.environ, env), patch('deploy.api'), patch('deploy.approval_controls'), \
                     patch('deploy.verify_federation'), patch('deploy.init_args', return_value=['init']), \
                     patch('deploy.run', side_effect=['', 'redacted plan', json.dumps(plan)]):
                    deploy.main()
                self.assertIn(f'changes={str(expected).lower()}', Path(env['GITHUB_OUTPUT']).read_text())
                # Retained for upload; the workflow removes it after publishing.
                self.assertTrue((Path(directory) / 'review.tfplan').exists())

    def test_non_main_deployment_fails_before_api_or_commands(self):
        with patch.dict(os.environ, TF_PHASE='apply', GITHUB_REF='refs/heads/feature'), \
             patch('deploy.api') as api, self.assertRaises(ValueError):
            deploy.main()
        api.assert_not_called()

    def apply_env(self, directory):
        return {'TF_PHASE': 'apply', 'GITHUB_REF': 'refs/heads/main',
                'GITHUB_REPOSITORY': 'example/workload', 'GITHUB_SHA': 'abc',
                'TF_APPLY_ENVIRONMENT': 'apply', 'TF_APPROVER': 'owner',
                'RUNNER_TEMP': directory, 'GITHUB_STEP_SUMMARY': directory + '/summary'}

    def test_apply_uses_the_reviewed_plan_and_never_replans(self):
        with tempfile.TemporaryDirectory() as directory:
            saved = Path(directory) / 'review.tfplan'
            saved.write_text('reviewed plan')
            with patch.dict(os.environ, self.apply_env(directory)), \
                 patch('deploy.api', return_value={'sha': 'abc'}), \
                 patch('deploy.approval_controls'), patch('deploy.verify_federation'), \
                 patch('deploy.init_args', return_value=['init']), \
                 patch('deploy.run', side_effect=['', '']) as run:
                deploy.main()
            commands = [call.args[0][0] for call in run.call_args_list]
            self.assertEqual(commands, ['init', 'apply'])
            self.assertIn(str(saved), run.call_args_list[-1].args[0])
            self.assertFalse(saved.exists())

    def test_a_rejected_stale_plan_never_leaves_the_plan_behind(self):
        with tempfile.TemporaryDirectory() as directory:
            saved = Path(directory) / 'review.tfplan'
            saved.write_text('stale plan')
            with patch.dict(os.environ, self.apply_env(directory)), \
                 patch('deploy.api', return_value={'sha': 'abc'}), \
                 patch('deploy.approval_controls'), patch('deploy.verify_federation'), \
                 patch('deploy.init_args', return_value=['init']), \
                 patch('deploy.run', side_effect=['', RuntimeError('saved plan is stale')]):
                with self.assertRaises(RuntimeError):
                    deploy.main()
            self.assertFalse(saved.exists())
