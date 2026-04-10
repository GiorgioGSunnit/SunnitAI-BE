# Permessi necessari per analisi PDF

## Situazione attuale

| Componente | Cosa richiede | Stato |
|------------|---------------|-------|
| **Azure Functions host** | Lock lease su Storage (sacdpdev001) per coordinamento singleton | 403 - Managed Identity senza permessi |
| **upload_to_blob** | Scrivi PDF su container cdp/cdp-ext | 403 - stessa identità |
| **extract_requirements** | Legge sum.json da conf, scrive requirements su blob | Fallisce se CONNECTION_STRING manca o Storage 403 |
| **VMAI (FastAPI :2025)** | Chiama OpenAI per estrazione | Dipende da APIM/token v2 |

## Possiamo usare Azure Functions?

**Sì**, ma il pod deve avere accesso allo Storage. L’host Azure Functions e la logica applicativa usano lo stesso storage account.

---

## Permessi da richiedere al team Infra

### 1. Storage Blob Data Contributor (o Storage Blob Data Owner)

**Risorsa:** Storage Account `sacdpdev001`  
**Principale:** Managed Identity del pod (Workload Identity)  
- **Client ID:** `966176be-711b-4f2f-8b80-dce08b14a8e2`

**Comando (esempio):**
```bash
# Trovare resource ID dello storage
STORAGE_ID=$(az storage account show --name sacdpdev001 --query id -o tsv)

# Assegnare ruolo
az role assignment create \
  --assignee 966176be-711b-4f2f-8b80-dce08b14a8e2 \
  --role "Storage Blob Data Contributor" \
  --scope $STORAGE_ID
```

### 2. CONNECTION_STRING nel KeyVault

La `CONNECTION_STRING` va nel KeyVault (`kv-aiac-cdp-dev`) e viene letta dal bootstrap. Se manca, il bootstrap può usare solo `AzureWebJobsStorage__accountName` (Managed Identity), ma il codice attuale usa `CONNECTION_STRING` per blob operativi (cdp, cdp-ext, conf).

**Opzioni:**
- **A)** Aggiungere `AZURE-STORAGE--CONNECTION-STRING` nel KeyVault e mapparlo nel bootstrap
- **B)** Riscrivere il codice per usare `DefaultAzureCredential` + account name invece della connection string

---

## Riepilogo

| Azione | Chi la fa |
|--------|-----------|
| Ruolo "Storage Blob Data Contributor" su sacdpdev001 per Managed Identity `966176be-711b-4f2f-8b80-dce08b14a8e2` | Team Infra |
| Configurare CONNECTION_STRING (o adattare il codice a Managed Identity) | Team Infra / Sviluppo |
| Fix applicativi (rstrip, load_sum_data con CONNECTION_STRING mancante) | Fatto |

---

## Dopo i permessi

Con i permessi corretti:
1. L’host Azure Functions acquisisce il lock lease
2. upload, extract_requirements, compare_requirements possono scrivere/leggere sui blob
3. Le analisi PDF possono essere eseguite end-to-end
