#!/usr/bin/env bash
# Deploy Touchline to Azure: Event Hubs (ingest), Container Registry, App Service (the app).
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
PLAN=${PLAN:-touchline-plan}
APP=${APP:-touchline-$SUFFIX}                    # https://$APP.azurewebsites.net
TAG=${TAG:-$(git rev-parse --short HEAD)$(git diff --quiet || echo -dirty)}

: "${FOUNDRY_PROJECT_ENDPOINT:?set it (source scripts/env.sh)}"
step() { printf '\n\033[1;32m==> %s\033[0m\n' "$*"; }
exists() { "$@" --only-show-errors -o none 2>/dev/null; }

step "Container registry $ACR"
exists az acr show -n "$ACR" -g "$RG" || az acr create -n "$ACR" -g "$RG" -l "$LOC" --sku Basic --only-show-errors -o none

step "Build and push image touchline:$TAG"
IMAGE=$ACR.azurecr.io/touchline
if ! az acr build -r "$ACR" -t "touchline:$TAG" -t touchline:latest . --only-show-errors -o none 2>/dev/null; then
  # Free Trial subscriptions can't use ACR Tasks: build locally instead (Azure runs linux/amd64).
  # A project-only Docker config keeps this registry login out of any other Docker setup.
  export DOCKER_CONFIG=$HOME/.docker-touchline DOCKER_HOST=${DOCKER_HOST:-unix://$HOME/.docker/run/docker.sock}
  mkdir -p "$DOCKER_CONFIG"
  docker build --platform linux/amd64 -t "$IMAGE:$TAG" -t "$IMAGE:latest" .
  az acr login -n "$ACR" --expose-token --query accessToken -o tsv --only-show-errors |
    docker login "$ACR.azurecr.io" -u 00000000-0000-0000-0000-000000000000 --password-stdin
  docker push -q "$IMAGE:$TAG" && docker push -q "$IMAGE:latest"
fi

step "Event Hubs $EH_NS/$EH (the ingest stage)"
exists az eventhubs namespace show -n "$EH_NS" -g "$RG" ||
  az eventhubs namespace create -n "$EH_NS" -g "$RG" -l "$LOC" --sku Basic --disable-local-auth true --only-show-errors -o none
exists az eventhubs eventhub show -n "$EH" --namespace-name "$EH_NS" -g "$RG" ||
  az eventhubs eventhub create -n "$EH" --namespace-name "$EH_NS" -g "$RG" --partition-count 2 \
    --cleanup-policy Delete --retention-time-in-hours 24 --only-show-errors -o none

step "App Service plan $PLAN and web app $APP"
# App Service (Linux, B1): containers, WebSockets, managed identity, always on. One instance on
# purpose: matches, AI results and viewers' playback live in this process's memory.
exists az appservice plan show -n "$PLAN" -g "$RG" ||
  az appservice plan create -n "$PLAN" -g "$RG" -l "$LOC" --is-linux --sku B1 --only-show-errors -o none
exists az webapp show -n "$APP" -g "$RG" ||
  az webapp create -n "$APP" -g "$RG" -p "$PLAN" --container-image-name "$IMAGE:$TAG" \
    --assign-identity '[system]' --only-show-errors -o none

step "Roles for the app's managed identity (keyless: registry, models, speech, Event Hubs)"
PRINCIPAL=$(az webapp show -n "$APP" -g "$RG" --query identity.principalId -o tsv)
ACR_ID=$(az acr show -n "$ACR" -g "$RG" --query id -o tsv)
AI_ID=$(az cognitiveservices account show -n "$AI" -g "$RG" --query id -o tsv)
EH_ID=$(az eventhubs namespace show -n "$EH_NS" -g "$RG" --query id -o tsv)
grant() {
  az role assignment create --assignee-object-id "$PRINCIPAL" --assignee-principal-type ServicePrincipal \
    --role "$1" --scope "$2" --only-show-errors -o none
}
grant AcrPull "$ACR_ID"
grant "Cognitive Services OpenAI User" "$AI_ID"
grant "Cognitive Services Speech User" "$AI_ID"
grant "Foundry User" "$AI_ID" || grant "Azure AI User" "$AI_ID"
grant "Azure Event Hubs Data Sender" "$EH_ID"
grant "Azure Event Hubs Data Receiver" "$EH_ID"

step "Configure and roll out touchline:$TAG"
az webapp config set -n "$APP" -g "$RG" --generic-configurations '{"acrUseManagedIdentityCreds": true}' \
  --web-sockets-enabled true --always-on true --http20-enabled true --min-tls-version 1.2 --ftps-state Disabled \
  --only-show-errors -o none
az webapp update -n "$APP" -g "$RG" --https-only true --only-show-errors -o none
az webapp config appsettings set -n "$APP" -g "$RG" --only-show-errors -o none --settings \
  WEBSITES_PORT=8000 WEBSITES_CONTAINER_START_TIME_LIMIT=600 \
  "FOUNDRY_PROJECT_ENDPOINT=$FOUNDRY_PROJECT_ENDPOINT" "MODEL_FAST=${MODEL_FAST:-gpt-4.1-mini}" \
  "MODEL_REASONING=${MODEL_REASONING:-gpt-5-mini}" TOUCHLINE_AUTH=managed \
  "EVENTHUB_NAMESPACE=$EH_NS.servicebus.windows.net" "EVENTHUB_NAME=$EH" \
  WEBSITES_ENABLE_APP_SERVICE_STORAGE=true TOUCHLINE_CACHE_FILE=/home/data/ai-cache.json \
  TOUCHLINE_VOICE_DIR=/home/data/voice TOUCHLINE_WARM=on "TOUCHLINE_DAILY_AI_RUNS=${TOUCHLINE_DAILY_AI_RUNS:-1500}"
az webapp config container set -n "$APP" -g "$RG" --container-image-name "$IMAGE:$TAG" --only-show-errors -o none
az webapp restart -n "$APP" -g "$RG" -o none

URL=https://$APP.azurewebsites.net
step "Live at $URL (first start pulls the image: a few minutes)"
for _ in $(seq 1 40); do
  health=$(curl -s -m 10 "$URL/api/health" || true)
  [[ $health == *'"ai":true'* ]] && break
  sleep 15
done
echo "Health: $health"
