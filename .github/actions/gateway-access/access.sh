#!/usr/bin/env bash
set -euo pipefail

[[ "$OPERATION" == configure || "$OPERATION" == apply ]]
[[ "$SERVICE" =~ ^[a-z0-9][a-z0-9-]{0,62}$ ]]
[[ "$CHART_REVISION" =~ ^[0-9a-f]{40}$ ]]
[[ "$GITHUB_SHA" =~ ^[0-9a-f]{40}$ ]]
[[ "$GITHUB_REPOSITORY" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]]
[[ "$(git -C "$CHART_DIRECTORY" rev-parse HEAD)" == "$CHART_REVISION" ]]
for tool in az helm jq python3; do command -v "$tool" >/dev/null; done

task_dir="$RUNNER_TEMP/gateway-$SERVICE"
mkdir -p "$task_dir"
chmod 700 "$task_dir"
export GATEWAY_TASK_DIRECTORY="$task_dir"

invoke() {
  az aks command invoke --resource-group "$RESOURCE_GROUP" --name "$CLUSTER" "$@" --output json > "$task_dir/invocation.json"
  jq -e '.exitCode == 0' "$task_dir/invocation.json" >/dev/null || {
    echo 'Gateway access command failed; inspect the Azure operation with authorized access.' >&2
    return 1
  }
  jq -r '.logs' "$task_dir/invocation.json"
}

if [[ "$OPERATION" == configure ]]; then
  invoke --command 'kubectl get configmap litellm-consumer-defaults -n litellm -o json' > "$task_dir/defaults.json"
  python3 - <<'PY'
import hashlib, json, os
from pathlib import Path
import yaml

root = Path(os.environ['GATEWAY_TASK_DIRECTORY'])
agent = Path(os.environ['AGENT_DIRECTORY']).resolve()
workspace = Path(os.environ['GITHUB_WORKSPACE']).resolve()
if not agent.is_relative_to(workspace):
    raise ValueError('Agent intent must be inside the source checkout')
intent = yaml.safe_load((agent / 'config/agent.yaml').read_text())
access = intent['gatewayAccess']
if set(access) != {'policy'}:
    raise ValueError('Hosted consumer declares only gateway policy intent')
platform = json.loads(json.loads((root / 'defaults.json').read_text())['data']['values.json'])
policy = platform['modelGateway']['policies'][access['policy']]
if any(type(policy.get(k)) not in (int, float) or policy[k] <= 0 for k in ('maxBudget', 'rpm', 'tpm', 'parallel')):
    raise ValueError('Gateway admission blocked: operator must supply explicit SRE ceilings')
identity = os.environ['GITHUB_REPOSITORY'].lower() + '/' + os.environ['SERVICE']
name = 'hosted-' + hashlib.sha256(identity.encode()).hexdigest()[:24]
platform['modelGateway'].update(consumerIdentity=os.environ['GITHUB_REPOSITORY'].lower(), connectionOnly=True)
platform['argocd'] = {'enabled': True, 'repository': 'https://github.com/' + os.environ['GITHUB_REPOSITORY'] + '.git',
                      'revision': os.environ['GITHUB_SHA'], 'chartRevision': os.environ['CHART_REVISION'],
                      'valuesPath': (agent / 'config/agent.yaml').relative_to(workspace).as_posix()}
platform['lifecycle'] = {'cleanup': {'enabled': True}}
(root / 'values.json').write_text(json.dumps({'name': name, 'namespace': name, 'gatewayAccess': access, 'platform': platform}))
PY
  name=$(jq -r '.name' "$task_dir/values.json")
  helm template "$name" "$CHART_DIRECTORY" -f "$task_dir/values.json" > "$task_dir/resources.yaml"
  python3 - <<'PY'
import json,os
from pathlib import Path
import yaml
root = Path(os.environ['GATEWAY_TASK_DIRECTORY'])
resources = list(yaml.safe_load_all((root / 'resources.yaml').read_text()))
metadata = next(r for r in resources if r and r['kind'] == 'ConfigMap' and 'connection.json' in r.get('data', {}))
connection = json.loads(metadata['data']['connection.json'])
if any('\n' in str(v) or '\r' in str(v) for v in connection.values()):
    raise ValueError('Connection metadata must be single-line values')
(root / 'connection.json').write_text(json.dumps(connection))
with open(os.environ['GITHUB_ENV'], 'a') as output:
    for key, value in connection.items():
        output.write(f'{key}={value}\n')
with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
    output.write(f'connection={root / "connection.json"}\n')
PY
else
  [[ -s "$task_dir/values.json" && -s "$RELEASE_RECORD" ]]
  jq -e --arg revision "$GITHUB_SHA" --arg agent "$SERVICE" \
    '.source_revision == $revision and .agent == $agent and (.instance_principal_id | test("^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$"))' "$RELEASE_RECORD" >/dev/null
  principal=$(jq -r '.instance_principal_id' "$RELEASE_RECORD")
  jq --arg principal "$principal" '.platform.modelGateway.principalId = $principal | .platform.modelGateway.connectionOnly = false' "$task_dir/values.json" > "$task_dir/actual.json"
  name=$(jq -r '.name' "$task_dir/actual.json")
  invoke --command "kubectl get application/$name --namespace=argocd --ignore-not-found -o json" > "$task_dir/existing.json"
  if [[ -s "$task_dir/existing.json" ]] && [[ -n "$(tr -d '[:space:]' < "$task_dir/existing.json")" ]]; then
    jq -e --arg principal "$principal" '.spec.sources[0].helm.valuesObject.platform.modelGateway.principalId == $principal' "$task_dir/existing.json" >/dev/null || {
      echo 'Native principal changed; review old-reader cleanup before replacement.' >&2
      exit 1
    }
  fi
  helm template "$name" "$CHART_DIRECTORY" -f "$task_dir/actual.json" \
    --show-only templates/argocd/application.yaml > "$task_dir/registration.yaml"
  (
    cd "$task_dir"
    invoke --file registration.yaml --command "kubectl apply -f registration.yaml && kubectl wait --namespace=argocd --for=jsonpath='{.status.sync.comparedTo.sources[0].helm.valuesObject.platform.modelGateway.principalId}'=$principal application/$name --timeout=240s && kubectl wait --namespace=argocd --for=jsonpath='{.status.sync.status}'=Synced application/$name --timeout=240s && kubectl wait --namespace=argocd --for=jsonpath='{.status.health.status}'=Healthy application/$name --timeout=240s && kubectl get application/$name --namespace=argocd -o json" > application.json
  )
  # The preceding kubectl lines have text output. Get a separate clean JSON read
  # and verify compared sources so stale Healthy status cannot admit a candidate.
  invoke --command "kubectl get application/$name --namespace=argocd -o json" > "$task_dir/application.json"
  jq -e '.status.sync.status == "Synced" and .status.health.status == "Healthy" and .status.sync.comparedTo.sources == .spec.sources' "$task_dir/application.json" >/dev/null
fi
