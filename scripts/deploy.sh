#!/usr/bin/env bash
# Deploy Touchline to Azure: Event Hubs (ingest), Container Registry, Container Apps (the app).
# Re-runnable: existing resources are left alone, and the app gets a new image each run.
#
#   source scripts/env.sh && scripts/deploy.sh
#
# Expects the Foundry resource and model deployments to exist already (see README).
# Everything is keyless: the app's managed identity gets data-plane roles, no secrets anywhere.
set -euo pipefail

RG=${RG:-rg-touchline}
LOC=${LOC:-swedencentral}
AI=${AI:-touchline-ai-dfbaf4}                       # Foundry resource (models + speech)
SUFFIX=${SUFFIX:-${AI##*-}}
ACR=${ACR:-touchlineacr$SUFFIX}
EH_NS=${EH_NS:-touchline-eh-$SUFFIX}
EH=${EH:-match-events}
ENV_NAME=${ENV_NAME:-touchline-env}
APP=${APP:-touchline}
TAG=${TAG:-$(git rev-parse --short HEAD)$(git diff --quiet || echo -dirty)}

: "${FOUNDRY_PROJECT_ENDPOINT:?set it (source scripts/env.sh)}"
step() { printf '\n\033[1;32m==> %s\033[0m\n' "$*"; }
exists() { "$@" --only-show-errors -o none 2>/dev/null; }

step "Container registry $ACR"
exists az acr show -n "$ACR" -g "$RG" || az acr create -n "$ACR" -g "$RG" -l "$LOC" --sku Basic --only-show-errors -o none

step "Build image touchline:$TAG in Azure (no local Docker needed)"
az acr build -r "$ACR" -t "touchline:$TAG" -t touchline:latest . --only-show-errors -o none

step "Event Hubs $EH_NS/$EH (the ingest stage)"
exists az eventhubs namespace show -n "$EH_NS" -g "$RG" ||
  az eventhubs namespace create -n "$EH_NS" -g "$RG" -l "$LOC" --sku Basic --disable-local-auth true --only-show-errors -o none
exists az eventhubs eventhub show -n "$EH" --namespace-name "$EH_NS" -g "$RG" ||
  az eventhubs eventhub create -n "$EH" --namespace-name "$EH_NS" -g "$RG" --partition-count 2 \
    --cleanup-policy Delete --retention-time-in-hours 24 --only-show-errors -o none

step "Container Apps environment $ENV_NAME"
exists az containerapp env show -n "$ENV_NAME" -g "$RG" ||
  az containerapp env create -n "$ENV_NAME" -g "$RG" -l "$LOC" --only-show-errors -o none

ENV_VARS=(
  "FOUNDRY_PROJECT_ENDPOINT=$FOUNDRY_PROJECT_ENDPOINT"
  "MODEL_FAST=${MODEL_FAST:-gpt-4.1-mini}"
  "MODEL_REASONING=${MODEL_REASONING:-gpt-5-mini}"
  "TOUCHLINE_AUTH=managed"
  "EVENTHUB_NAMESPACE=$EH_NS.servicebus.windows.net"
  "EVENTHUB_NAME=$EH"
)

step "Container app $APP"
# One replica on purpose: matches, AI results and viewers' playback live in this process's memory.
if exists az containerapp show -n "$APP" -g "$RG"; then
  az containerapp update -n "$APP" -g "$RG" --image "$ACR.azurecr.io/touchline:$TAG" \
    --set-env-vars "${ENV_VARS[@]}" --only-show-errors -o none
else
  az containerapp create -n "$APP" -g "$RG" --environment "$ENV_NAME" \
    --image "$ACR.azurecr.io/touchline:$TAG" --registry-server "$ACR.azurecr.io" --registry-identity system \
    --system-assigned --ingress external --target-port 8000 --transport auto \
    --min-replicas 1 --max-replicas 1 --cpu 1.0 --memory 2.0Gi \
    --env-vars "${ENV_VARS[@]}" --only-show-errors -o none
fi

step "Roles for the app's managed identity (keyless access to models, speech and Event Hubs)"
PRINCIPAL=$(az containerapp show -n "$APP" -g "$RG" --query identity.principalId -o tsv)
AI_ID=$(az cognitiveservices account show -n "$AI" -g "$RG" --query id -o tsv)
EH_ID=$(az eventhubs namespace show -n "$EH_NS" -g "$RG" --query id -o tsv)
grant() {
  az role assignment create --assignee-object-id "$PRINCIPAL" --assignee-principal-type ServicePrincipal \
    --role "$1" --scope "$2" --only-show-errors -o none
}
grant "Cognitive Services OpenAI User" "$AI_ID"
grant "Cognitive Services Speech User" "$AI_ID"
grant "Foundry User" "$AI_ID" || grant "Azure AI User" "$AI_ID"
grant "Azure Event Hubs Data Sender" "$EH_ID"
grant "Azure Event Hubs Data Receiver" "$EH_ID"

step "Restart so the app picks up its roles"
REVISION=$(az containerapp show -n "$APP" -g "$RG" --query properties.latestRevisionName -o tsv)
az containerapp revision restart -n "$APP" -g "$RG" --revision "$REVISION" --only-show-errors -o none

URL=https://$(az containerapp show -n "$APP" -g "$RG" --query properties.configuration.ingress.fqdn -o tsv)
step "Live at $URL"
echo "Health: $(curl -s --retry 10 --retry-delay 6 --retry-all-errors "$URL/api/health")"
