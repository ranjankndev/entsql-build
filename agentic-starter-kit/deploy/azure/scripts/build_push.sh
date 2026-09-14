#!/usr/bin/env bash
# Build the image in ACR (no local Docker needed) and roll the Container App.
set -euo pipefail

RG="${RG:?set RG}"
ENVIRONMENT="${ENVIRONMENT:-dev}"
APP_NAME="${APP_NAME:-agentkit}"
TAG="${TAG:-$(git rev-parse --short HEAD)}"

ACR=$(az acr list -g "$RG" --query "[0].name" -o tsv)
LOGIN_SERVER=$(az acr show -n "$ACR" --query loginServer -o tsv)
IMAGE="$LOGIN_SERVER/$APP_NAME:$TAG"

echo "==> building $IMAGE in ACR"
az acr build -r "$ACR" -t "$APP_NAME:$TAG" -t "$APP_NAME:latest" .

echo "==> updating container app"
az containerapp update \
  -g "$RG" -n "ca-$APP_NAME-$ENVIRONMENT" \
  --image "$IMAGE" \
  --revision-suffix "r$(date -u +%Y%m%d%H%M%S)" -o none

FQDN=$(az containerapp show -g "$RG" -n "ca-$APP_NAME-$ENVIRONMENT" \
  --query properties.configuration.ingress.fqdn -o tsv)
echo "==> smoke test https://$FQDN/healthz"
curl -fsS "https://$FQDN/healthz" && echo
curl -fsS "https://$FQDN/readyz" && echo
