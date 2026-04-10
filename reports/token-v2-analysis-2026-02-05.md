# Report: Analisi Token V2 - App Registration

**Data:** 2026-02-05  
**Ambiente:** DEV (aks-dev)  
**Pod testato:** aiac-be-service-cd676dcc4-7hcjh

---

## 1. Verifica Configurazione App Registration

### Comando
```bash
az ad app show --id c989d43a-9e62-4c01-a67c-7eefbeef70ce \
  --query "{appId:appId, displayName:displayName, accessTokenAcceptedVersion:api.requestedAccessTokenVersion, signInAudience:signInAudience}" \
  -o json
```

### Output
```json
{
  "accessTokenAcceptedVersion": 2,
  "appId": "c989d43a-9e62-4c01-a67c-7eefbeef70ce",
  "displayName": "dev-ssi-aiac-ai",
  "signInAudience": "AzureADMyOrg"
}
```

### Stato
**App Registration configurata correttamente con `accessTokenAcceptedVersion: 2`**

---

## 2. Verifica Token Emesso

### Comando (eseguito dal pod)
```python
from azure.identity import ClientSecretCredential
app_cred = ClientSecretCredential(tenant_id, client_id, client_secret)
token = app_cred.get_token("https://cognitiveservices.azure.com/.default")
# decode JWT payload
```

### Output
```
=== TOKEN PAYLOAD ===
ver: 1.0
iss: https://sts.windows.net/8c4b47b5-ea35-4370-817f-95066d4f8467/
aud: https://cognitiveservices.azure.com
appid: c989d43a-9e62-4c01-a67c-7eefbeef70ce
idtyp: app
```

### Stato
**Token emesso e' V1** nonostante App Registration configurata per V2

---

## 3. Test Chiamata APIM

### Comando (eseguito dal pod)
```python
resp = requests.post(
    "https://dev-api.cdp.it/azure/openai/deployments/gpt-4.1/chat/completions?api-version=2024-12-01-preview",
    json={"messages": [{"role": "user", "content": "hi"}], "max_tokens": 5},
    headers={
        "Authorization": "Bearer " + token.token,
        "api-key": apikey,
        "Content-Type": "application/json"
    }
)
```

### Output
```
Status: 401
Response: { "statusCode": 401, "message": "Unauthorized" }
```

### Stato
**APIM rifiuta il token V1** - richiede issuer V2 (`https://login.microsoftonline.com/{tenant}/v2.0`)

---

## Diagnosi

| Componente | Configurazione | Stato |
|------------|----------------|-------|
| App Registration `accessTokenAcceptedVersion` | 2 | OK |
| Token emesso | V1 | PROBLEMA |
| APIM | Richiede V2 | OK (policy corretta) |

### Causa Root

L'impostazione `accessTokenAcceptedVersion: 2` sull'App Registration controlla i token che l'app **riceve quando agisce come API/risorsa**.

Quando l'app agisce come **client** e richiede token **per un'altra risorsa** (`cognitiveservices.azure.com`), la versione del token dipende dalla **risorsa target**, non dal client.

**Azure Cognitive Services** e' un servizio Microsoft first-party che di default emette token V1.

---

## Soluzioni Possibili

### Opzione 1: Modificare APIM Policy (Consigliata)
Aggiornare la policy JWT validation dell'APIM per accettare **entrambi** gli issuer:
- V1: `https://sts.windows.net/{tenant}/`
- V2: `https://login.microsoftonline.com/{tenant}/v2.0`

### Opzione 2: Usare Managed Identity
Invece di App Registration + Client Credentials, usare la Managed Identity del pod per autenticarsi direttamente all'APIM (se supportato).

### Opzione 3: Richiedere token con scope custom
Creare uno scope custom sull'App Registration e configurare APIM per validare token emessi per quello scope (complesso, richiede modifiche architetturali).

---

## Azione Richiesta

**Team Infra deve modificare la policy APIM** per accettare issuer V1:

```xml
<validate-jwt>
  <issuers>
    <issuer>https://sts.windows.net/8c4b47b5-ea35-4370-817f-95066d4f8467/</issuer>
    <issuer>https://login.microsoftonline.com/8c4b47b5-ea35-4370-817f-95066d4f8467/v2.0</issuer>
  </issuers>
  ...
</validate-jwt>
```

---

## Note

- L'App Registration e' configurata **correttamente**
- Il problema e' che Cognitive Services come risorsa target emette sempre token V1
- La soluzione piu' semplice e' adattare APIM, non cambiare il flusso di autenticazione
