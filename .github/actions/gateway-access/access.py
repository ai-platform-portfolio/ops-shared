"""Connect one native hosted consumer to chart-owned gateway credentials."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time

import yaml


def command(*arguments):
    result = subprocess.run(arguments, check=True, capture_output=True, text=True, timeout=900)
    return result.stdout


def invoke(*arguments):
    result = json.loads(command('az', 'aks', 'command', 'invoke',
                                '--resource-group', os.environ['RESOURCE_GROUP'],
                                '--name', os.environ['CLUSTER'], *arguments, '--output', 'json'))
    if result.get('exitCode') != 0:
        diagnostics = (result.get('logs') or '').splitlines()
        if 'ACCOUNTING_RESTORE_REQUIRED' in diagnostics:
            raise RuntimeError('ACCOUNTING_RESTORE_REQUIRED: restore or explicitly reconcile existing accounting; no new allowance was issued')
        if 'CREDENTIAL_APPLICATION_NOT_READY' in diagnostics:
            raise RuntimeError('Credential Application did not become Synced and Healthy for its current sources')
        raise RuntimeError(f'Gateway command failed; Azure operation: {result.get("id", "unavailable")}')
    return (result['logs'] or '').strip()


def render(values):
    return command('helm', 'template', values['name'], os.environ['CHART_DIRECTORY'],
                   '-f', str(directory() / 'values.json'))


def directory():
    return Path(os.environ['RUNNER_TEMP']) / ('gateway-' + os.environ['SERVICE'])


def configure():
    agent = Path(os.environ['AGENT_DIRECTORY']).resolve()
    workspace = Path(os.environ['GITHUB_WORKSPACE']).resolve()
    if not agent.is_relative_to(workspace):
        raise ValueError('Agent intent must be inside the source checkout')
    access = yaml.safe_load((agent / 'config/agent.yaml').read_text())['gatewayAccess']
    if set(access) != {'policy'}:
        raise ValueError('Hosted consumer declares only gateway policy intent')
    metadata = json.loads(invoke('--command', 'kubectl get configmap litellm-consumer-defaults -n litellm -o json'))
    defaults = metadata['data']
    platform = json.loads(defaults['values.json'])
    policy = platform['modelGateway']['policies'][access['policy']]
    if any(type(policy.get(k)) not in (int, float) or policy[k] <= 0 for k in ('maxBudget', 'rpm', 'tpm', 'parallel')):
        raise ValueError('Gateway admission blocked: operator must supply explicit ceilings')
    identity = os.environ['GITHUB_REPOSITORY'].lower() + '/' + os.environ['SERVICE']
    name = 'hosted-' + hashlib.sha256(identity.encode()).hexdigest()[:24]
    platform['modelGateway'].update(consumerIdentity=os.environ['GITHUB_REPOSITORY'].lower(), connectionOnly=True)
    platform['lifecycle'] = {'cleanup': {'enabled': True}}
    values = {'name': name, 'namespace': name, 'gatewayAccess': access, 'platform': platform}
    (directory() / 'values.json').write_text(json.dumps(values))
    metadata = next(r for r in yaml.safe_load_all(render(values))
                    if r and r['kind'] == 'ConfigMap' and 'connection.json' in r.get('data', {}))
    connection = json.loads(metadata['data']['connection.json'])
    if any('\n' in str(value) or '\r' in str(value) for value in connection.values()):
        raise ValueError('Connection metadata must be single-line values')
    path = directory() / 'connection.json'
    path.write_text(json.dumps(connection))
    generation = defaults.get('hosted-generation', '')
    traces = defaults.get('traces-endpoint', '')
    if not re.fullmatch(r'[a-f0-9]{24}', generation):
        raise ValueError('Argo hosted delivery defaults are missing the current generation')
    if traces and not re.fullmatch(r'https://[a-z0-9.-]+/v1/traces', traces):
        raise ValueError('Invalid platform trace endpoint')
    (directory() / 'generation').write_text(generation)
    with open(os.environ['GITHUB_ENV'], 'a') as output:
        output.writelines(f'{key}={value}\n' for key, value in connection.items())
    with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
        output.write(f'connection={path}\ntraces-endpoint={traces}\n')


def release_facts(values, stage):
    record = json.loads(Path(os.environ['RELEASE_RECORD']).read_text())
    agent_directory = Path(os.environ['AGENT_DIRECTORY']).resolve()
    agent = agent_directory.name
    if agent_directory != Path(os.environ['GITHUB_WORKSPACE']).resolve() / 'agents' / agent:
        raise ValueError('Hosted agent must be under agents/ in the source checkout')
    repository = os.environ['GITHUB_REPOSITORY']
    service = f'{repository.rsplit("/", 1)[1].lower()}-{agent}'
    principal = record.get('instance_principal_id', '').lower()
    version = record['version']
    if (record.get('source_revision') != os.environ['GITHUB_SHA'] or record.get('agent') != service
            or service != os.environ['SERVICE'] or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,62}', agent)
            or not re.fullmatch(r'[1-9][0-9]*', version)
            or not re.fullmatch(r'[a-f0-9]{8}(-[a-f0-9]{4}){3}-[a-f0-9]{12}', principal)):
        raise ValueError('Release record must match source, agent, version and native principal')

    intent = yaml.safe_load((agent_directory / 'config/agent.yaml').read_text())
    target = {k: values['platform']['telemetry'][k] for k in ('namespace', 'workload')} if values['platform'].get('telemetry') else {}
    if any(not re.fullmatch(r'[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?', label) for label in target.values()):
        raise ValueError('Hosted telemetry target must contain DNS labels')
    alerts = intent.get('readinessAlerts', False)
    if type(alerts) is not bool or (alerts and not target):
        raise ValueError('Hosted readiness alerts require a scoped telemetry target')
    if stage == 'serving':
        smoke = json.loads(Path(os.environ['ROUTING_RECORD']).read_text())['smoke']['deployment']
        if any(smoke.get(k) != record.get(k) for k in ('agent', 'version', 'source_revision', 'image')):
            raise ValueError('Stable smoke must match the verified candidate')
    return {'agent': agent, 'sourceRevision': record['source_revision'], 'principalId': principal,
            'version': version, 'telemetry': target, 'alerts': alerts and stage == 'serving'}


def publish_to_git(generation, facts):
    repository = os.environ['GITHUB_REPOSITORY']
    branch = 'refs/heads/delivery'
    with tempfile.TemporaryDirectory(dir=directory()) as checkout:
        env = {**os.environ, 'GIT_TERMINAL_PROMPT': '0'}

        def git(*args):
            return subprocess.run(['git', '-C', checkout, *args], check=True, capture_output=True,
                                  text=True, env=env, timeout=60).stdout

        git('init', '--quiet')
        git('remote', 'add', 'origin', f'https://github.com/{repository}.git')
        git('config', '--add', 'credential.helper', '')
        git('config', '--add', 'credential.helper', '!gh auth git-credential')
        git('config', 'user.name', 'github-actions[bot]')
        git('config', 'user.email', '41898282+github-actions[bot]@users.noreply.github.com')

        path = Path(f'deploy/hosted/{generation}/{facts["agent"]}.json')
        record = Path(checkout) / path
        for attempt in range(3):
            source = branch if git('ls-remote', '--heads', 'origin', branch) else facts['sourceRevision']
            git('fetch', '--quiet', '--depth=1', 'origin', source)
            git('checkout', '--quiet', '--detach', 'FETCH_HEAD')
            if record.exists():
                current = json.loads(record.read_text())
                if current['principalId'] != facts['principalId']:
                    raise ValueError('Native principal changed; review old-reader cleanup before replacement')
                if int(current['version']) > int(facts['version']):
                    raise ValueError('Hosted delivery must not replace a newer version')
                if current == facts or (current['alerts'] and current == dict(facts, alerts=True)):
                    return current

            record.parent.mkdir(parents=True, exist_ok=True)
            record.write_text(json.dumps(facts, sort_keys=True) + '\n')
            git('add', '--', str(path))
            git('commit', '--quiet', '--no-gpg-sign', '-m', f'Publish hosted release for {facts["agent"]}')
            try:
                git('push', '--quiet', 'origin', f'HEAD:{branch}')
                return facts
            except subprocess.CalledProcessError:
                if attempt == 2:
                    raise RuntimeError('Could not push the hosted release to delivery') from None
                time.sleep(attempt + 1)


def publish(stage):
    values = json.loads((directory() / 'values.json').read_text())
    name = values['name']
    generation = (directory() / 'generation').read_text()
    facts = release_facts(values, stage)
    if not re.fullmatch(r'hosted-[a-f0-9]{24}', name) or not re.fullmatch(r'[a-f0-9]{24}', generation):
        raise ValueError('Invalid hosted application or generation')
    existing = invoke('--command', f'kubectl get applications.argoproj.io/{name} --namespace=argocd --ignore-not-found -o json')
    if existing:
        current = json.loads(existing)
        annotations = current['metadata'].get('annotations', {})
        previous = annotations.get('platform.ai-platform-portfolio/hosted-principal')
        if not previous:
            previous = current['spec']['sources'][0]['helm']['valuesObject']['platform']['modelGateway']['principalId']
        if previous.lower() != facts['principalId']:
            raise ValueError('Native principal changed; review old-reader cleanup before replacement')

    facts = publish_to_git(generation, facts)
    values['platform']['modelGateway'].update(principalId=facts['principalId'], connectionOnly=False)
    (directory() / 'values.json').write_text(json.dumps(values))
    job = next(r for r in yaml.safe_load_all(render(values)) if r and r['kind'] == 'Job'
               and not r['metadata'].get('annotations', {}).get('argocd.argoproj.io/hook'))
    namespace, job_name = job['metadata']['namespace'], job['metadata']['name']
    if any(not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,62}', label) for label in (namespace, job_name)):
        raise ValueError('Invalid chart-owned credential Job identity')

    alerts = str(facts['alerts']).lower()
    invoke('--command', f'''set -eu
deadline=$(($(date +%s) + 240))
while [ "$(date +%s)" -lt "$deadline" ]; do
  if application=$(kubectl get -n argocd applications.argoproj.io/{name} --request-timeout=12s -o json 2>/dev/null) &&
    printf '%s\n' "$application" | jq -e '
      any(.metadata.ownerReferences[]?; .kind == "ApplicationSet" and .name == "application-hosted" and .controller == true)
      and .metadata.annotations["platform.ai-platform-portfolio/hosted-principal"] == "{facts['principalId']}"
      and .metadata.annotations["platform.ai-platform-portfolio/hosted-source"] == "{facts['sourceRevision']}"
      and .metadata.annotations["platform.ai-platform-portfolio/hosted-version"] == "{facts['version']}"
      and .metadata.annotations["platform.ai-platform-portfolio/hosted-alerts"] == "{alerts}"
      and .status.sync.status == "Synced" and .status.health.status == "Healthy"
      and .status.sync.comparedTo.sources == .spec.sources
    ' >/dev/null; then
    if [ "{alerts}" = false ]; then exit 0; fi
    sensor=$(printf '%s\n' "$application" | jq -r '
      [.status.resources[]? | select(.kind=="Sensor" and .namespace=="cluster-onboarding" and .status=="Synced") | .name]
      | if length==1 then .[0] else "" end')
    if [ -n "$sensor" ] && kubectl -n cluster-onboarding get sensors.argoproj.io "$sensor" --request-timeout=12s -o json |
      jq -e 'any(.status.conditions[]?; .type=="Deployed" and .status=="True")' >/dev/null; then
      exit 0
    fi
  fi
  sleep 3
done
# Only the fixed diagnostic code may leave the credential Job logs.
kubectl logs -n {namespace} job/{job_name} --tail=20 2>/dev/null |
  sed -n 's/^Gateway credential operation failed: ACCOUNTING_RESTORE_REQUIRED$/ACCOUNTING_RESTORE_REQUIRED/p'
printf '%s\n' CREDENTIAL_APPLICATION_NOT_READY
exit 1
''')


def main():
    for key, pattern in [('SERVICE', r'[a-z0-9][a-z0-9-]{0,62}'), ('CHART_REVISION', r'[0-9a-f]{40}'),
                         ('GITHUB_SHA', r'[0-9a-f]{40}'), ('GITHUB_REPOSITORY', r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+')]:
        if not re.fullmatch(pattern, os.environ[key]):
            raise ValueError(f'Invalid {key}')
    if command('git', '-C', os.environ['CHART_DIRECTORY'], 'rev-parse', 'HEAD').strip() != os.environ['CHART_REVISION']:
        raise ValueError('Chart checkout must match its immutable revision')
    directory().mkdir(mode=0o700, parents=True, exist_ok=True)
    operation = os.environ['OPERATION']
    if operation == 'configure':
        configure()
    elif operation in {'candidate', 'serving'}:
        publish(operation)
    else:
        raise ValueError('Unknown hosted delivery operation')


if __name__ == '__main__':
    main()
