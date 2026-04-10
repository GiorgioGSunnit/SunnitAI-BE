# Azurite in pod – Fattibilità workaround Storage

**Implementato:** sidecar in `aks/deployment.yaml`, bootstrap in `src/core/bootstrap.py`, client in `src/utils/blob_storage_client.py`, config dev in `aks/configs/configmap-dev.yaml`.

## Contesto

- **Problema**: Nessun permesso sul container Blob Storage (`sacdpdev001`) per il runtime Azure Functions e per l’app (locks, conf, upload).
- **Idea**: Usare una “struttura simile in locale” nel pod (emulatore) al posto dello Storage reale.

## Cosa è Azurite

- Emulatore **open source** (Node.js) che implementa le API Azure Storage in locale.
- Supporta **Blob** (porta 10000), **Queue** (10001), **Table** (10002).
- Non supporta: Azure Files, Data Lake Gen2.
- Può persistere su disco (`-l /path`) o in memoria (`--inMemoryPersistence`).
- Connection string tipica:
  ```
  DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;QueueEndpoint=http://127.0.0.1:10001/devstoreaccount1;TableEndpoint=http://127.0.0.1:10002/devstoreaccount1;
  ```

## Uso nel nostro stack

| Componente              | Cosa usa                         | Azurite |
|-------------------------|-----------------------------------|---------|
| Azure Functions host   | AzureWebJobsStorage (queue + blob) | ✅ Blob + Queue |
| App (bsc, locks, conf) | CONNECTION_STRING / stesso storage | ✅ Blob (+ Queue se serve) |

**Nota**: In questo progetto **non** è usato Durable Task (nessun `durableTask` in `host.json`, nessun trigger durable nel codice). Quindi **non servono le Table**; le Table in Azurite sono comunque in PREVIEW. Per il nostro caso (solo host + blob/queue app) **Blob + Queue sono sufficienti**.

## Fattibilità

### Pro

1. **Compatibile con il nostro uso**: Solo Blob + Queue; niente Table.
2. **Stesso protocollo**: SDK Azure Storage (Python) parla ad Azurite come a un account reale.
3. **Già usato in locale**: `local.settings.json` ha già una connection string in stile Azurite per `CONNECTION_STRING`; va solo allineato `AzureWebJobsStorage`.
4. **Nessun permesso Azure**: Tutto resta nel pod (o su volume locale).

### Contro / attenzioni

1. **Node nel pod**: Azurite è Node.js. Opzioni:
   - **Sidecar**: secondo container nell’pod che esegue solo Azurite (immagine ufficiale `mcr.microsoft.com/azure-storage/azurite`).
   - **Stesso container**: installare Node + Azurite e avviarlo come secondo processo (entrypoint script). Più invasivo.
2. **Persistence**: Senza volume, a ogni restart del pod i dati in Azurite si perdono. Con volume (es. `emptyDir` o PVC) i dati sopravvivono al restart del pod (non al ricreo del PVC se non persistente).
3. **Solo workaround**: I dati non vanno su Azure; non sostituisce la soluzione “permessi + Storage reale” per integrazione con altri sistemi o backup.

## Architettura proposta (sidecar)

```
Pod
├── Container 1: app (Python / Azure Functions)
│   ENV: AzureWebJobsStorage = connection string → 127.0.0.1 (o localhost) Azurite
│   ENV: CONNECTION_STRING = stessa connection string
│   (Bootstrap non inietta più da KeyVault per storage se rileva “modalità Azurite”)
│
└── Container 2: azurite (sidecar)
    Image: mcr.microsoft.com/azure-storage/azurite
    Porte: 10000 (blob), 10001 (queue), 10002 (table)
    Volume (opzionale): /data per -l /data
```

- Nel pod i container condividono `localhost`, quindi l’app punta a `http://127.0.0.1:10000/...` (o al nome del service del sidecar, a seconda di come esponi Azurite).
- Connection string con `BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1` (e analoghi per Queue/Table) funziona se Azurite è sullo stesso pod.

## Passi concreti

1. **Deploy**: Aggiungere il sidecar Azurite nel deployment del pod (es. in `aks/` o Helm) e opzionale volume per `-l /data`.
2. **Config**: In ConfigMap o env del container app, impostare in modalità “emulator”:
   - `AzureWebJobsStorage` = connection string Azurite (Blob + Queue).
   - `CONNECTION_STRING` = stessa connection string (così `blob_storage_client` e locks usano Azurite).
3. **Bootstrap**: In `bootstrap.py`, se è presente una variabile tipo `USE_AZURITE=true` (o assenza di secrets storage), non caricare storage da KeyVault e lasciare che le variabili siano già impostate dal deployment (ConfigMap/env).
4. **Container name**: Azurite crea container on-demand; l’app deve usare lo stesso nome container che usa oggi (es. `ai-audit-poc-sa` o quello configurato in `CONTAINER_NAME`). Creare il container all’avvio di Azurite (script init o prima chiamata dall’app) se necessario.

## Verdict

**Fattibile** come workaround temporaneo: Azurite copre Blob + Queue richiesti da Azure Functions host e dall’app, senza usare Durable/Table. La soluzione più pulita è **sidecar Azurite + env/ConfigMap** che punta tutto (AzureWebJobsStorage + CONNECTION_STRING) all’emulatore, e bootstrap che in modalità “no KeyVault storage” non sovrascrive queste variabili.

**Riferimenti**
- [Use Azurite for local Azure Storage development](https://learn.microsoft.com/en-us/azure/storage/common/storage-use-azurite)
- [Install and run Azurite](https://learn.microsoft.com/en-us/azure/storage/common/storage-install-azurite)
- Immagine Docker: `mcr.microsoft.com/azure-storage/azurite`
