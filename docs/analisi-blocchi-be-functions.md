# Analisi blocchi BE e Azure Functions

## 1. Connection string (workaround temporaneo)

**Non abbiamo** una connection string per sacdpdev001 nel repo (corretto per sicurezza).

Opzioni:
- **Workaround:** Infra fornisce la connection string → la aggiungiamo al ConfigMap come `CONNECTION_STRING` (solo per test, NON in prod per sicurezza)
- **Corretto:** Infra aggiunge `AZURE-STORAGE--CONNECTION-STRING` nel KeyVault `kv-aiac-cdp-dev` → il bootstrap la carica (SecretProviderClass già configurato)

---

## 2. Container blob storage

| Container | Esiste? | MI ha accesso? |
|-----------|---------|----------------|
| **ai-audit-poc-sa** | Sì | Sì (4 blob) |
| **cdp** | No | - |
| **cdp-ext** | No | - |
| **conf** | No | - |
| **azure-webjobs-hosts** | Sì | **No** (403) |

**Alternativa:** Adattare il codice per usare `ai-audit-poc-sa` al posto di cdp/cdp-ext/conf (se la struttura dati è compatibile).

---

## 2.1 Permessi creazione container

**Risultato:** La Managed Identity **non può** creare container (`create_container` → 403).

**Richiesta per Team Infra:**

> Assegnare **Storage Blob Data Contributor** (o Storage Blob Data Owner) sullo **Storage Account sacdpdev001** alla Managed Identity del pod BE:
> - **Principal ID:** da ottenere con `az identity show -n id-aks-aiac-dev -g rg-aiac-svil --query principalId -o tsv`
> - **Scope:** `/subscriptions/<SUB>/resourceGroups/<RG>/providers/Microsoft.Storage/storageAccounts/sacdpdev001`
>
> E/o creare i container `cdp`, `cdp-ext`, `conf` su sacdpdev001.

---

## 3. Altri problemi (BE + Azure Functions)

| Componente | Stato | Blocco |
|------------|-------|--------|
| **Azure Functions host** | azure-webjobs-hosts: 403 | MI senza accesso al container Functions |
| **CONNECTION_STRING** | Mancante | Blob upload/extract/compare falliscono |
| **Container cdp, cdp-ext, conf** | Inesistenti | ResourceNotFoundError |
| **IPVMAI** | 127.0.0.1 (default) | OK – VMAI in-pod |
| **Azure OpenAI / APIM** | Token v1 vs v2 | Vedi azure-openai-auth.mdc |
| **KeyVault access** | Funziona | Bootstrap carica da KV |

### Riepilogo azioni Infra

1. **Storage:** Ruolo Storage Blob Data Contributor su sacdpdev001 per MI `966176be-711b-4f2f-8b80-dce08b14a8e2` (scope: storage account)
2. **KeyVault:** Aggiungere `AZURE-STORAGE--CONNECTION-STRING` con connection string di sacdpdev001
3. **Container:** Creare `cdp`, `cdp-ext`, `conf` su sacdpdev001 (o dare alla MI permessi per crearli)
4. **Azure OpenAI:** Fix token v2 (vedi azure-openai-auth.mdc)
