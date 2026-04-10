# Blob Storage - Comandi utili

- **Account**: `sacdpdev001`
- **Container**: `ai-audit-poc-sa`
- **Network rules**: `Deny` di default, whitelist IP `193.93.108.16` (VPN aziendale)

---

## Dove sono salvati i file (Blob vs filesystem POD)

| Tipo | Dove viene scritto | Path in Blob Storage | Note |
|------|--------------------|----------------------|------|
| **PDF originali (upload)** | Prima in `./tmp/` sul POD durante l’elaborazione, poi (se previsto) in blob | **Root del container** (nome file PDF) | Upload da frontend/job; VMAI usa `./tmp/` come staging |
| **Output analisi (JSON, Excel)** | `./output/` sul POD, poi caricati in blob da `upload_to_blob("requirements", ...)` | **out/requirements/** | File: `{hash}.json`, `{hash}_flattened.json`, `{hash}.xlsx` |
| **Output confronto versioning** | `./output/` sul POD, poi blob | **out/versionings/** | `{hash1}_vs_{hash2}.json` e `.xlsx` |
| **Output confronto attuativa** | `./output/` / `out_schema_attuativo/` sul POD, poi blob | **out/implementations/** | `{hash1}_vs_{hash2}.json` e `.xlsx` |
| **Output confronto emendativa** | `./output/` sul POD, poi blob | **out/amendments/** | `{hash1}_vs_{hash2}.json` e `.xlsx` |
| **Confronti generici** | `./output/` sul POD, poi blob | **out/comparisons/** | confronti non tipizzati |
| **Config / job / lock** | Blob (e in memoria sul POD) | **conf/** | `sum.json`, `conf/jobs/{id}.json`, `conf/locks/` |
| **Log debug** | Blob | **debug/** | log da lex_package |

Le cartelle **`./tmp/`** e **`./output/`** (e simili) sono **sul filesystem del POD** (VMAI/backend): servono come cache/lavoro temporaneo; i file persistenti sono quelli in **Blob Storage**. Il mapping nome PDF → hash (`pdf_mapping.json`) è costruito da `./tmp/` e `./output/` e salvato sul POD; non è salvato in blob, quindi dopo restart o su un altro replica il mapping può essere vuoto se non viene ripopolato.

---

## PowerShell: raggiungere e verificare Blob Storage

Prerequisito: Azure CLI installato (`az`) e login eseguito (`az login`). Da rete aziendale/VPN (IP in whitelist).

### Listare i blob (prefissi principali)

```powershell
# Account e container (valori di default del codice)
$accountName = "sacdpdev001"
$containerName = "ai-audit-poc-sa"

# Login (se non già fatto)
az login

# PDF originali (root del container)
az storage blob list --account-name $accountName --container-name $containerName --prefix "" --auth-mode login --output table

# Output analisi (JSON, Excel requisiti)
az storage blob list --account-name $accountName --container-name $containerName --prefix "out/requirements/" --auth-mode login --output table

# Output confronti versioning
az storage blob list --account-name $accountName --container-name $containerName --prefix "out/versionings/" --auth-mode login --output table

# Output confronti attuativa
az storage blob list --account-name $accountName --container-name $containerName --prefix "out/implementations/" --auth-mode login --output table

# Output confronti emendativa
az storage blob list --account-name $accountName --container-name $containerName --prefix "out/amendments/" --auth-mode login --output table

# Confronti generici
az storage blob list --account-name $accountName --container-name $containerName --prefix "out/comparisons/" --auth-mode login --output table

# Config / job / lock
az storage blob list --account-name $accountName --container-name $containerName --prefix "conf/" --auth-mode login --output table

# Debug log
az storage blob list --account-name $accountName --container-name $containerName --prefix "debug/" --auth-mode login --output table
```

### Scaricare un file da blob (es. un JSON analisi)

```powershell
$accountName = "sacdpdev001"
$containerName = "ai-audit-poc-sa"
$blobPath = "out/requirements/HASH.json"   # sostituire HASH con il nome reale
$localFile = ".\Downloads\file.json"

az storage blob download --account-name $accountName --container-name $containerName --name $blobPath --file $localFile --auth-mode login
```

### Scaricare tutti i file di una “cartella” (es. requirements)

```powershell
$accountName = "sacdpdev001"
$containerName = "ai-audit-poc-sa"
$destination = ".\Downloads\blob-requirements"

New-Item -ItemType Directory -Force -Path $destination
az storage blob download-batch --account-name $accountName --source $containerName --pattern "out/requirements/*" --destination $destination --auth-mode login
```

---

## Da VPN aziendale (accesso diretto con `az`)

Se sei connesso alla VPN, puoi usare `az` direttamente.

### Listare contenuti

```bash
# Listare container
az storage container list --account-name sacdpdev001 --auth-mode login -o table

# Listare blob (tutto il container)
az storage blob list --account-name sacdpdev001 --container-name ai-audit-poc-sa --auth-mode login -o table

# Listare blob con prefisso (navigare in una "directory")
az storage blob list --account-name sacdpdev001 --container-name ai-audit-poc-sa \
  --prefix "out/requirements/" --auth-mode login -o table

# Prefissi utili:
#   out/requirements/   → risultati analisi (json, xlsx)
#   debug/              → log di debug parser e LLM
#   conf/               → configurazione (sum.json)
```

### Scaricare file

```bash
# Singolo file
az storage blob download --account-name sacdpdev001 --container-name ai-audit-poc-sa \
  --name "out/requirements/NOME_FILE.json" \
  --file ~/Downloads/NOME_FILE.json \
  --auth-mode login

# Intera directory
az storage blob download-batch --account-name sacdpdev001 \
  --source ai-audit-poc-sa \
  --pattern "out/requirements/*" \
  --destination ~/Downloads/blob-export/ \
  --auth-mode login
```

### Cancellare un blob (invalidare cache analisi)

```bash
az storage blob delete --account-name sacdpdev001 --container-name ai-audit-poc-sa \
  --name "out/requirements/HASH.json" --auth-mode login
```

---

## Senza VPN (via pod AKS)

Se non sei in VPN, passa dal pod con `kubectl exec`.

### Setup

```bash
POD=$(kubectl get pods -n aiac -l app.kubernetes.io/name=aiac-be-service \
  -o jsonpath='{.items[0].metadata.name}')
```

### Listare contenuti

```bash
kubectl exec $POD -n aiac -- python3 -u -c "
import sys, os; sys.path.insert(0, '/app/src'); sys.path.insert(0, '/app/src/be/src')
import core.bootstrap
from azure.storage.blob import BlobServiceClient
from azure.identity import DefaultAzureCredential
account = os.getenv('AZURE_STORAGE_ACCOUNT_NAME', 'sacdpdev001')
c = BlobServiceClient(f'https://{account}.blob.core.windows.net', DefaultAzureCredential()) \
    .get_container_client('ai-audit-poc-sa')
for blob in c.list_blobs(name_starts_with='out/requirements/'):
    print(f'{blob.size:>10}  {blob.last_modified}  {blob.name}')
"
```

> Cambia `name_starts_with=` per navigare: `''`, `'out/'`, `'debug/'`, `'conf/'`

### Scaricare una directory in locale (blob → pod → locale)

```bash
# Step 1: blob → pod
kubectl exec $POD -n aiac -- python3 -u -c "
import sys, os; sys.path.insert(0, '/app/src'); sys.path.insert(0, '/app/src/be/src')
import core.bootstrap
from azure.storage.blob import BlobServiceClient
from azure.identity import DefaultAzureCredential
account = os.getenv('AZURE_STORAGE_ACCOUNT_NAME', 'sacdpdev001')
c = BlobServiceClient(f'https://{account}.blob.core.windows.net', DefaultAzureCredential()) \
    .get_container_client('ai-audit-poc-sa')
PREFIX = 'out/requirements/'
for blob in c.list_blobs(name_starts_with=PREFIX):
    local = f'/tmp/blob-export/{os.path.basename(blob.name)}'
    os.makedirs('/tmp/blob-export', exist_ok=True)
    with open(local, 'wb') as f:
        f.write(c.get_blob_client(blob.name).download_blob().readall())
    print(f'OK {blob.name} ({blob.size} bytes)')
"

# Step 2: pod → locale
mkdir -p ~/Downloads/blob-export
kubectl cp $POD:/tmp/blob-export/ ~/Downloads/blob-export/ -n aiac
```

### Scaricare un singolo file

```bash
# Step 1: blob → pod
kubectl exec $POD -n aiac -- python3 -u -c "
import sys, os; sys.path.insert(0, '/app/src'); sys.path.insert(0, '/app/src/be/src')
import core.bootstrap
from azure.storage.blob import BlobServiceClient
from azure.identity import DefaultAzureCredential
account = os.getenv('AZURE_STORAGE_ACCOUNT_NAME', 'sacdpdev001')
c = BlobServiceClient(f'https://{account}.blob.core.windows.net', DefaultAzureCredential()) \
    .get_container_client('ai-audit-poc-sa')
with open('/tmp/file.json', 'wb') as f:
    f.write(c.get_blob_client('out/requirements/NOME_FILE.json').download_blob().readall())
print('OK')
"

# Step 2: pod → locale
kubectl cp $POD:/tmp/file.json ~/Downloads/NOME_FILE.json -n aiac
```

### Cancellare un blob

```bash
kubectl exec $POD -n aiac -- python3 -u -c "
import sys, os; sys.path.insert(0, '/app/src'); sys.path.insert(0, '/app/src/be/src')
import core.bootstrap
from azure.storage.blob import BlobServiceClient
from azure.identity import DefaultAzureCredential
account = os.getenv('AZURE_STORAGE_ACCOUNT_NAME', 'sacdpdev001')
c = BlobServiceClient(f'https://{account}.blob.core.windows.net', DefaultAzureCredential()) \
    .get_container_client('ai-audit-poc-sa')
c.delete_blob('out/requirements/HASH.json')
print('Deleted')
"
```
