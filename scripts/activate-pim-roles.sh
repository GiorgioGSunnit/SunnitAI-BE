#!/bin/bash
# Attiva ruoli PIM per ambiente specificato
# Usage: ./activate-pim-roles.sh [dev|test|prod]

set -e

ENV="${1:-dev}"

activate_pim_role() {
  local SCOPE="$1"
  local ROLE_ID="$2"
  local SUB="$3"
  local JUSTIFICATION="${4:-Cluster access for development}"
  
  REQUEST_ID=$(python3 -c "import uuid; print(str(uuid.uuid4()))")
  PRINCIPAL_ID=$(az ad signed-in-user show --query id -o tsv 2>/dev/null)
  
  echo "Activating role on scope: ${SCOPE##*/}..."
  
  az rest --method PUT \
    --uri "https://management.azure.com${SCOPE}/providers/Microsoft.Authorization/roleAssignmentScheduleRequests/${REQUEST_ID}?api-version=2020-10-01" \
    --body "{
      \"properties\": {
        \"principalId\": \"${PRINCIPAL_ID}\",
        \"roleDefinitionId\": \"/subscriptions/${SUB}/providers/Microsoft.Authorization/roleDefinitions/${ROLE_ID}\",
        \"requestType\": \"SelfActivate\",
        \"justification\": \"${JUSTIFICATION}\",
        \"scheduleInfo\": {
          \"expiration\": {
            \"type\": \"AfterDuration\",
            \"duration\": \"PT8H\"
          }
        }
      }
    }" --query "properties.{role:expandedProperties.roleDefinition.displayName, status:status}" -o table 2>&1 || echo "  (may already be active)"
}

# Role Definition IDs
CLUSTER_ADMIN="0ab0b1a8-8aac-4efd-b8c2-3ee1fb270be8"
RBAC_READER="7f6c6a51-bcf8-42ba-9220-52d62157d7db"
RBAC_WRITER="a7ffa36f-339b-4b5c-8bdf-e2c188b2c0eb"
CDP_READER="b62276cb-b789-46ce-ab7c-2c1c57a2ed09"

case "$ENV" in
  dev)
    echo "=== Activating DEV roles ==="
    SUB="c73369b0-5374-460a-9f32-58c27c777879"
    AKS_SCOPE="/subscriptions/$SUB/resourcegroups/rg-aks-dev/providers/Microsoft.ContainerService/managedClusters/aks-dev"
    
    activate_pim_role "$AKS_SCOPE" "$CLUSTER_ADMIN" "$SUB"
    activate_pim_role "$AKS_SCOPE" "$RBAC_READER" "$SUB"
    activate_pim_role "$AKS_SCOPE" "$RBAC_WRITER" "$SUB"
    activate_pim_role "/subscriptions/$SUB/resourceGroups/rg-aks-dev" "$CDP_READER" "$SUB"
    activate_pim_role "/subscriptions/$SUB/resourceGroups/rg-aks-mc-dev" "$CDP_READER" "$SUB"
    
    echo ""
    echo "Refreshing credentials..."
    az aks get-credentials --resource-group rg-aks-dev --name aks-dev --overwrite-existing
    ;;
    
  test)
    echo "=== Activating TEST roles ==="
    SUB="dbe0475e-03c6-4b2a-a374-cdc3b533a88b"
    AKS_SCOPE="/subscriptions/$SUB/resourcegroups/rg-aks-test/providers/Microsoft.ContainerService/managedClusters/aks-sharedaks-test"
    
    activate_pim_role "$AKS_SCOPE" "$CLUSTER_ADMIN" "$SUB"
    activate_pim_role "$AKS_SCOPE" "$RBAC_READER" "$SUB"
    activate_pim_role "$AKS_SCOPE" "$RBAC_WRITER" "$SUB"
    
    echo ""
    echo "Refreshing credentials..."
    az aks get-credentials --resource-group rg-aks-test --name aks-sharedaks-test --overwrite-existing
    ;;
    
  prod)
    echo "=== Activating PROD roles ==="
    SUB="ad238119-6378-4c23-a011-2b0f75ee91d2"
    AKS_SCOPE="/subscriptions/$SUB/resourcegroups/rg-aks-prod/providers/Microsoft.ContainerService/managedClusters/aks-sharedaks-prod"
    
    activate_pim_role "$AKS_SCOPE" "$CLUSTER_ADMIN" "$SUB"
    activate_pim_role "$AKS_SCOPE" "$RBAC_READER" "$SUB"
    activate_pim_role "$AKS_SCOPE" "$RBAC_WRITER" "$SUB"
    
    echo ""
    echo "Refreshing credentials..."
    az aks get-credentials --resource-group rg-aks-prod --name aks-sharedaks-prod --overwrite-existing
    ;;
    
  *)
    echo "Usage: $0 [dev|test|prod]"
    exit 1
    ;;
esac

echo ""
echo "Done! Roles active for 8 hours."
echo "Note: If DNS doesn't resolve, use Azure Cloud Shell: https://shell.azure.com"
