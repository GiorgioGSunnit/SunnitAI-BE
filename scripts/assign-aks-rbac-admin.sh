#!/bin/bash
# Assegna Azure Kubernetes Service RBAC Admin a sunggabrielli (kubectl exec)
# Eseguire da utente con Owner o User Access Administrator sul cluster
# Usage: ./assign-aks-rbac-admin.sh

set -e

AKS_ID=$(az aks show -g rg-aks-dev -n aks-dev --query id -o tsv)
USER_ID=$(az ad user show --id ext.sunggabrielli@cdp.it --query id -o tsv)

echo "Assegnazione Azure Kubernetes Service RBAC Admin a ext.sunggabrielli@cdp.it..."
az role assignment create \
  --assignee "$USER_ID" \
  --role "Azure Kubernetes Service RBAC Admin" \
  --scope "$AKS_ID"

echo "Done. Dopo 1-2 minuti di propagazione: az aks get-credentials -g rg-aks-dev -n aks-dev --overwrite-existing && kubectl exec <pod> -n aiac -- sh"
