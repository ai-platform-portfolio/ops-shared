import io
import json
import unittest
import urllib.error
import urllib.parse
from unittest.mock import Mock

from linear_issue import ValidationError, issue_ids, request_json, validate


class LinearValidation(unittest.TestCase):
    def test_linear_generated_branch_and_multiple_ids(self):
        self.assertEqual(issue_ids('michaelalinks/ai-27-add-check'), ['AI-27'])
        self.assertEqual(issue_ids('ai-27-ai-27/AI-28-check'), ['AI-27', 'AI-28'])
        for branch in ['feature/no-issue', 'ai-0-check', 'ai-27abc', '123-27-check']:
            with self.assertRaises(ValidationError):
                issue_ids(branch)

    def test_missing_credentials_fails_before_network(self):
        request = Mock()
        with self.assertRaises(ValidationError):
            validate('ai-27-check', 'client', '', request)
        request.assert_not_called()

    def test_read_only_token_and_exact_issue_lookup(self):
        request = Mock(side_effect=[{'access_token': 'temporary-token'},
                                    {'data': {'issue': {'id': 'uuid', 'identifier': 'AI-27'}}}])
        self.assertEqual(validate('ai-27-check', 'client', 'secret', request), ['AI-27'])
        token_call, issue_call = request.call_args_list
        form = urllib.parse.parse_qs(token_call.args[1].decode())
        self.assertEqual(form['scope'], ['read'])
        self.assertEqual(form['grant_type'], ['client_credentials'])
        self.assertEqual(json.loads(issue_call.args[1])['variables'], {'id': 'AI-27'})
        self.assertEqual(issue_call.args[2]['Authorization'], 'Bearer temporary-token')

    def test_token_errors_null_issue_partial_errors_and_wrong_issue_fail(self):
        for response in [{'error': 'invalid_client'}, {'access_token': ''}]:
            with self.assertRaises(ValidationError):
                validate('ai-27-check', 'client', 'secret', Mock(return_value=response))
        for response in [{'data': {'issue': None}}, {'errors': [{'message': 'private'}]},
                         {'data': {'issue': {'id': 'uuid', 'identifier': 'AI-28'}}},
                         {'data': None}]:
            request = Mock(side_effect=[{'access_token': 'token'}, response])
            with self.assertRaises(ValidationError):
                validate('ai-27-check', 'client', 'secret', request)

    def test_every_referenced_issue_must_exist(self):
        request = Mock(side_effect=[{'access_token': 'token'},
                                    {'data': {'issue': {'id': 'uuid', 'identifier': 'AI-27'}}},
                                    {'data': {'issue': None}}])
        with self.assertRaises(ValidationError):
            validate('ai-27-ai-999999-check', 'client', 'secret', request)

    def test_http_and_invalid_json_fail_without_response_disclosure(self):
        errors = [urllib.error.HTTPError('url', 401, 'secret', {}, None),
                  urllib.error.URLError('secret'), TimeoutError('secret')]
        for error in errors:
            with self.assertRaises(ValidationError) as caught:
                request_json('https://api.linear.app/graphql', b'{}', {}, Mock(side_effect=error))
            self.assertNotIn('secret', str(caught.exception))
        for body in [b'not json', b'[]']:
            stream = io.BytesIO(body)
            with self.assertRaises(ValidationError):
                request_json('https://api.linear.app/graphql', b'{}', {}, Mock(return_value=stream))
