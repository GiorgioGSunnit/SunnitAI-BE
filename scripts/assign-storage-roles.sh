#!/bin/bash
# Assegna Storage Blob Data Contributor su sacdpdev001
# Eseguire da utente con Owner o User Access Administrator sullo scope
# Usage: ./assign-storage-roles.sh

set -e

# Storage account (dev)
STORAGE_ACCOUNT="sacdpdev001"
SUB_DEV="c73369b0-5374-460a-9f32-58c27c777879"

# Utente sunggabrielli
USER_UPN="ext.sunggabrielli@cdp.it"
USER_ID=$(az ad user show --id "$USER_UPN" --query id -o tsv)

# Managed Identity del pod BE (Workload Identity)
MI_CLIENT_ID="966176be-711b-4f2f-8b80-dce08b14a8e2"
# Principal ID: cercare con az identity show -n id-aks-aiac-dev -g rg-aiac-svil
MI_PRINCIPAL_ID=$(az identity show -n id-aks-aiac-dev -g rg-aiac-svil --query principalId -o tsv 2>/dev/null || echo "")

ROLE="Storage Blob Data Contributor"

echo "=== Assegnazione ruoli Storagesu $STORAGE_ACCOUNT ==="

# Trova storage (specificare -g se necessario)
STORAGE_ID=$(az storage account show -n "$STORAGE_ACCOUNT" --subscription "$SUB_DEV" --query id -o tsv 2>/dev/null)
if [ -z "$STORAGE_ID" ]; then
  echo "ERRORE: Storage $STORAGE_ACCOUNT non trovato. Provare con: az storage account list -o table"
  exit 1
fi
echo "Storage ID: $STORAGE_ID"

# 1. Assegna a sunggabrielli (accesso manuale/debug)
echo ""
echo "1. Assegnazione a $USER_UPN..."
az role assignment create \
  --assignee "$USER_ID" \
  --role "$ROLE" \
  --scope "$STORAGE_ID" 2>/dev/null || echo "  (già assegnato)"

# 2. Assegna a Managed Identity del pod (accesso runtime)
if [ -n "$MI_PRINCIPAL_ID" ]; then
  echo ""
  echo "2. Assegnazione a Managed Identity (pod BE)..."
  az role assignment create \
    --assignee-object-id "$MI_PRINCIPAL_ID" \
    --role "$ROLE" \
    --scope "$STORAGE_ID" 2>/dev/null || echo "  (già assegnato)"
else
  echo ""
  echo "2. Managed Identity: eseguire manualmente:"
  echo "   az identity show -n id-aks-aiac-dev -g rg-aiac-svil --query principalId -o tsv"
  echo "   az role assignment create --assignee-object-id <PRINCIPAL_ID> --role \"$ROLE\" --scope \"$STORAGE_ID\""
fi

echo ""
echo "Done."
