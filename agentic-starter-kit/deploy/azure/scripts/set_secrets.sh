#!/usr/bin/env bash
# Put third-party keys in Key Vault. Azure services use managed identity and
# need no secret at all; this is only for things outside Azure (Langfuse).
set -euo pipefail

RG="${RG:?set RG}"
VAULT=$(az keyvault list -g "$RG" --query "[0].name" -o tsv)

read -r -s -p "LANGFUSE_PUBLIC_KEY: " PUB; echo
read -r -s -p "LANGFUSE_SECRET_KEY: " SEC; echo

az keyvault secret set --vault-name "$VAULT" -n langfuse-public-key --value "$PUB" -o none
az keyvault secret set --vault-name "$VAULT" -n langfuse-secret-key --value "$SEC" -o none
echo "stored in $VAULT"
