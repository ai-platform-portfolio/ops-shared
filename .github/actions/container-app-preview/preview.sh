#!/usr/bin/env bash
set -euo pipefail

# Caller jobs serialize deploy and close events with the same concurrency group.
# Re-read state after the build, so a late old build cannot resurrect a closed PR.
number=$(jq -r '.pull_request.number' "$GITHUB_EVENT_PATH")
head=$(jq -r '.pull_request.head.sha' "$GITHUB_EVENT_PATH")
[[ "$GITHUB_REPOSITORY_ID" =~ ^[1-9][0-9]{0,9}$ && "$number" =~ ^[1-9][0-9]{0,9}$ ]]
preview="pv-${GITHUB_REPOSITORY_ID}-pr-${number}"
pr=$(gh api "repos/$GITHUB_REPOSITORY/pulls/$number")
[[ $(jq -r '.head.repo.full_name' <<< "$pr") == "$GITHUB_REPOSITORY" ]] || { echo 'Fork previews are not supported.' >&2; exit 1; }

operation=delete
if [[ $(jq -r .state <<< "$pr") == open ]]; then
  [[ "$GITHUB_EVENT_NAME" == pull_request && $(jq -r '.head.sha' <<< "$pr") == "$head" ]] || exit 0
  operation=deploy
fi

report() {
  pr=$(gh api "repos/$GITHUB_REPOSITORY/pulls/$number")
  if [[ "$operation" == deploy && ($(jq -r .state <<< "$pr") != open || $(jq -r '.head.sha' <<< "$pr") != "$head") ]]; then return; fi
  local comment body endpoint="repos/$GITHUB_REPOSITORY/issues/$number/comments" method=POST
  comment=$(gh api "$endpoint" --paginate --jq '.[] | select(.user.login == "github-actions[bot]" and (.body | contains("<!-- container-app-preview -->"))) | .id')
  if [[ -n "$comment" ]]; then
    endpoint="repos/$GITHUB_REPOSITORY/issues/comments/${comment%%$'\n'*}"
    method=PATCH
  fi
  printf -v body '<!-- container-app-preview -->\n%s' "$1"
  gh api "$endpoint" --method "$method" -f "body=$body" --silent
  printf '%s\n' "$1" >> "$GITHUB_STEP_SUMMARY"
}

# Listing distinguishes absence from a failed lookup; authentication/network
# errors fail the action rather than being mistaken for a missing resource.
app=$(az containerapp list --resource-group "$RESOURCE_GROUP" --query "[?name=='$preview'] | [0]" --output json)
identity=$(az identity list --resource-group "$RESOURCE_GROUP" --query "[?name=='$preview'] | [0]" --output json)
app=${app:-null}
identity=${identity:-null}
for resource in "$app" "$identity"; do
  [[ "$resource" == null ]] && continue
  jq -e --arg repo "$GITHUB_REPOSITORY_ID" --arg pr "$number" \
    '.tags.previewRepositoryId == $repo and .tags.previewPullRequest == $pr' <<< "$resource" > /dev/null \
    || { echo 'Refusing to modify a resource without this preview ownership.' >&2; exit 1; }
done

if [[ "$operation" == delete ]]; then
  if [[ "$app" != null ]]; then
    az containerapp delete --name "$preview" --resource-group "$RESOURCE_GROUP" --yes --output none
  fi
  if [[ "$identity" != null ]]; then
    principal=$(jq -r .principalId <<< "$identity")
    assignments=$(az role assignment list --assignee-object-id "$principal" --all --query '[].id' --output tsv)
    if [[ -n "$assignments" ]]; then
      printf '%s\n' "$assignments" | xargs az role assignment delete --output none --ids
    fi
    az identity delete --name "$preview" --resource-group "$RESOURCE_GROUP"
  fi
  echo 'operation=delete' >> "$GITHUB_OUTPUT"
  report 'Preview removed.'
  exit 0
fi

[[ "$IMAGE" =~ ^[a-z0-9.-]+/[a-z0-9/._-]+@sha256:[a-f0-9]{64}$ ]] || { echo 'A scanned image digest is required.' >&2; exit 1; }
[[ "$TARGET_PORT" =~ ^[0-9]+$ ]]
jq -e 'type == "object" and (has("AZURE_CLIENT_ID") | not) and all(to_entries[]; (.key | test("^[A-Za-z_][A-Za-z0-9_]*$")) and (.value | type == "string") and (.value | contains("\n") | not))' <<< "$ENV_VARS" > /dev/null
registry=$(az acr show --name "$REGISTRY" --query '{id:id,server:loginServer}' --output json)
server=$(jq -r .server <<< "$registry")
[[ "$IMAGE" == "$server/"* ]] || { echo 'Image must belong to the selected registry.' >&2; exit 1; }

if [[ "$identity" == null ]]; then
  location=$(az containerapp env show --name "$ENVIRONMENT" --resource-group "$RESOURCE_GROUP" --query location --output tsv)
  identity=$(az identity create --name "$preview" --resource-group "$RESOURCE_GROUP" --location "$location" \
    --tags "previewRepositoryId=$GITHUB_REPOSITORY_ID" "previewPullRequest=$number" --output json)
fi
principal=$(jq -r .principalId <<< "$identity")
identity_id=$(jq -r .id <<< "$identity")

grant() {
  # Azure CLI handles an already-existing assignment natively.
  az role assignment create --assignee-object-id "$principal" --assignee-principal-type ServicePrincipal \
    --scope "$1" --role "$2" --output none
}
grant "$(jq -r .id <<< "$registry")" AcrPull
if [[ -n "$READER_SCOPE" ]]; then grant "$READER_SCOPE" Reader; fi

env_vars=("AZURE_CLIENT_ID=$(jq -r .clientId <<< "$identity")")
while IFS= read -r entry; do
  if [[ -n "$entry" ]]; then env_vars+=("$entry"); fi
done <<< "$(jq -r 'to_entries[] | "\(.key)=\(.value)"' <<< "$ENV_VARS")"

if [[ "$app" == null ]]; then
  az containerapp create --name "$preview" --resource-group "$RESOURCE_GROUP" --environment "$ENVIRONMENT" \
    --image "$IMAGE" --user-assigned "$identity_id" --registry-server "$server" --registry-identity "$identity_id" \
    --ingress external --target-port "$TARGET_PORT" --transport http --allow-insecure false \
    --cpu 0.25 --memory 0.5Gi --min-replicas 0 --max-replicas 1 --env-vars "${env_vars[@]}" \
    --tags "previewRepositoryId=$GITHUB_REPOSITORY_ID" "previewPullRequest=$number" --output none
else
  az containerapp update --name "$preview" --resource-group "$RESOURCE_GROUP" \
    --image "$IMAGE" --set-env-vars "${env_vars[@]}" --output none
fi

fqdn=$(az containerapp show --name "$preview" --resource-group "$RESOURCE_GROUP" --query properties.configuration.ingress.fqdn --output tsv)
[[ "$fqdn" =~ ^[a-z0-9.-]+\.azurecontainerapps\.io$ ]]
curl --fail --silent --show-error --retry 12 --retry-all-errors --retry-delay 5 \
  --connect-timeout 10 --max-time 20 "https://${fqdn}${HEALTH_PATH}" > /dev/null
echo 'operation=deploy' >> "$GITHUB_OUTPUT"
echo "url=https://$fqdn" >> "$GITHUB_OUTPUT"
report "[Open preview](https://$fqdn) — built revision \`$GITHUB_SHA\`."
