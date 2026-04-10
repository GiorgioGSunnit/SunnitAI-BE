#!/bin/bash
# =============================================================================
# scaffolding.sh - Setup test environment nel pod BE
# =============================================================================
# Copia i PDF e il bashrc nel pod BE per facilitare i test

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
NAMESPACE="aiac"

# Colori
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}=== AIAC BE Pod Scaffolding ===${NC}"

# -----------------------------------------------------------------------------
# 1. Trova il pod BE dinamicamente
# -----------------------------------------------------------------------------
echo -e "${YELLOW}[1/4] Finding BE pod...${NC}"

POD_NAME=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=aiac-be-service -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)

if [ -z "$POD_NAME" ]; then
    echo -e "${RED}Error: Could not find aiac-be-service pod${NC}"
    exit 1
fi

echo -e "${GREEN}Found pod: $POD_NAME${NC}"

# -----------------------------------------------------------------------------
# 2. Copia il bashrc
# -----------------------------------------------------------------------------
echo -e "${YELLOW}[2/4] Copying bashrc...${NC}"

BASHRC_SRC="$SCRIPT_DIR/pod-bashrc"
if [ -f "$BASHRC_SRC" ]; then
    kubectl cp "$BASHRC_SRC" "$POD_NAME:/root/.bashrc" -n "$NAMESPACE"
    echo -e "${GREEN}  Copied: pod-bashrc -> /root/.bashrc${NC}"
else
    echo -e "${RED}  Warning: pod-bashrc not found${NC}"
fi

# -----------------------------------------------------------------------------
# 3. Copia i PDF dalla root del progetto
# -----------------------------------------------------------------------------
echo -e "${YELLOW}[3/4] Copying PDF files...${NC}"

PDF_COUNT=0
for pdf in "$PROJECT_ROOT"/*.pdf; do
    if [ -f "$pdf" ]; then
        filename=$(basename "$pdf")
        echo -e "  Copying: $filename"
        kubectl cp "$pdf" "$POD_NAME:/tmp/$filename" -n "$NAMESPACE"
        ((PDF_COUNT++))
    fi
done

if [ $PDF_COUNT -eq 0 ]; then
    echo -e "${YELLOW}  No PDF files found in project root${NC}"
else
    echo -e "${GREEN}  Copied $PDF_COUNT PDF file(s) to /tmp/${NC}"
fi

# -----------------------------------------------------------------------------
# 4. Verifica
# -----------------------------------------------------------------------------
echo -e "${YELLOW}[4/4] Verifying...${NC}"

echo -e "${BLUE}Files in /tmp/*.pdf:${NC}"
kubectl exec "$POD_NAME" -n "$NAMESPACE" -- ls -lh /tmp/*.pdf 2>/dev/null || echo "  No PDF files"

echo ""
echo -e "${GREEN}=== Setup Complete ===${NC}"
echo ""
echo -e "To connect to the pod with the new environment:"
echo -e "  ${YELLOW}kubectl exec -it $POD_NAME -n $NAMESPACE -- bash${NC}"
echo ""
echo -e "Once inside, run:"
echo -e "  ${YELLOW}source /root/.bashrc${NC}"
echo -e "  ${YELLOW}help_aiac${NC}"
echo ""
