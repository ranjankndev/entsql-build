#!/usr/bin/env bash
# Provision the whole environment from nothing. Idempotent: re-running updates.
set -euo pipefail

RG="${RG:?set RG, e.g. rg-agentkit-dev}"
LOCATION="${LOCATION:-swedencentral}"
ENVIRONMENT="${ENVIRONMENT:-dev}"
APP_NAME="${APP_NAME:-agentkit}"
PARAMS="${PARAMS:-deploy/azure/bicep/params.${ENVIRONMENT}.json}"

echo "==> resource group $RG ($LOCATION)"
az group create -n "$RG" -l "$LOCATION" -o none

echo "==> what-if (review before applying)"
az deployment group what-if \
  -g "$RG" -f deploy/azure/bicep/main.bicep -p "@$PARAMS" \
  -p environmentName="$ENVIRONMENT" appName="$APP_NAME"

read -r -p "Apply this deployment? [y/N] " reply
[[ "$reply" == "y" ]] || { echo "aborted"; exit 1; }

echo "==> deploying"
az deployment group create \
  -g "$RG" -n "$APP_NAME-$ENVIRONMENT" \
  -f deploy/azure/bicep/main.bicep -p "@$PARAMS" \
  -p environmentName="$ENVIRONMENT" appName="$APP_NAME" \
  --query properties.outputs -o json | tee deploy/azure/.outputs.${ENVIRONMENT}.json
