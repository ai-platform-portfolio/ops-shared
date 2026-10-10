"""Validate branch issue identifiers with Linear's read-only client credentials flow."""
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request


class ValidationError(Exception):
    pass


def issue_ids(branch):
    identifiers = sorted(set(re.findall(
        r'(?<![A-Za-z0-9])([A-Za-z][A-Za-z0-9]*-[1-9][0-9]*)(?![A-Za-z0-9])', branch
    )))
    if not identifiers:
        raise ValidationError('Branch name must contain a Linear issue ID, for example ai-27-add-validation.')
    return sorted(set(identifier.upper() for identifier in identifiers))


def request_json(url, data, headers, opener=urllib.request.urlopen):
    request = urllib.request.Request(url, data=data, headers=headers, method='POST')
    try:
        with opener(request, timeout=30) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as error:
        raise ValidationError(f'Linear request failed (HTTP {error.code}); check credentials and app access.') from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        raise ValidationError('Linear request failed or returned invalid JSON; validation cannot pass.') from None
    if not isinstance(payload, dict):
        raise ValidationError('Linear returned an unexpected response.')
    return payload


def validate(branch, client_id, client_secret, request=request_json):
    identifiers = issue_ids(branch)
    if not client_id or not client_secret:
        raise ValidationError('Configure LINEAR_CLIENT_ID and LINEAR_CLIENT_SECRET for the caller repository.')
    token_response = request(
        'https://api.linear.app/oauth/token',
        urllib.parse.urlencode({'grant_type': 'client_credentials', 'scope': 'read',
                                'client_id': client_id, 'client_secret': client_secret}).encode(),
        {'Content-Type': 'application/x-www-form-urlencoded'},
    )
    token = token_response.get('access_token')
    if token_response.get('error') or not isinstance(token, str) or not token:
        raise ValidationError('Linear did not issue an OAuth access token; enable client credentials on the app.')
    # Mask the freshly minted token before any later step can accidentally log it.
    if os.environ.get('GITHUB_ACTIONS') == 'true':
        print('::add-mask::' + token)
    for identifier in identifiers:
        response = request(
            'https://api.linear.app/graphql',
            json.dumps({'query': 'query Issue($id: String!) { issue(id: $id) { id identifier } }',
                        'variables': {'id': identifier}}).encode(),
            {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token},
        )
        if response.get('errors'):
            raise ValidationError(f'Linear could not validate {identifier}; check the issue and app team access.')
        data = response.get('data')
        issue = data.get('issue') if isinstance(data, dict) else None
        if not isinstance(issue, dict) or not issue.get('id') or issue.get('identifier') != identifier:
            raise ValidationError(f'{identifier} does not resolve to a matching accessible Linear issue.')
    return identifiers


def main():
    try:
        event = json.loads(open(os.environ['GITHUB_EVENT_PATH'], encoding='utf-8').read())
        if os.environ.get('GITHUB_EVENT_NAME') != 'pull_request':
            raise ValidationError('Call this workflow from a pull_request event.')
        branch = event['pull_request']['head']['ref']
        identifiers = validate(branch, os.environ.get('LINEAR_CLIENT_ID'),
                               os.environ.get('LINEAR_CLIENT_SECRET'))
    except (ValidationError, KeyError, ValueError, OSError) as error:
        # Do not print API response bodies, credentials, issue titles, or PR-controlled input.
        if isinstance(error, ValidationError):
            print(str(error), file=sys.stderr)
        else:
            print('Cannot read pull request metadata; validation cannot pass.', file=sys.stderr)
        return 1
    print('Validated Linear issue IDs: ' + ', '.join(identifiers))
    return 0


if __name__ == '__main__':
    sys.exit(main())
