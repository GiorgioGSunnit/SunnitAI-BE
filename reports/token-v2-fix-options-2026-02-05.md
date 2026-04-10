# Fix Token V2 - Opzioni per Team Infra

**Data:** 2026-02-05

## Problema Attuale

APIM rifiuta le chiamate dal pod BE con errore **401 Unauthorized** - token issuer mismatch.

## Test Eseguiti

### Test 1: Scope `cognitiveservices.azure.com`
```python
token = app_cred.get_token("https://cognitiveservices.azure.com/.default")
```
| Campo | Valore |
|-------|--------|
| Token Version | 1.0 |
| Issuer | `https://sts.windows.net/{tenant}/` |
| Audience | `cognitiveservices.azure.com` |
| **APIM** | **401** - issuer non accettato |

### Test 2: Scope App Registration
```python
token = app_cred.get_token("c989d43a-9e62-4c01-a67c-7eefbeef70ce/.default")
```
| Campo | Valore |
|-------|--------|
| Token Version | **2.0** |
| Issuer | `https://login.microsoftonline.com/{tenant}/v2.0` |
| Audience | `c989d43a-9e62-4c01-a67c-7eefbeef70ce` |
| **APIM** | **403** - audience non accettato |

---

## Fix Richiesto (2 opzioni alternative)

### Opzione A: Accettare issuer V1 (consigliata - minimo impatto)

Modificare policy APIM per accettare **entrambi** gli issuer:

```xml
<validate-jwt>
  <issuers>
    <issuer>https://sts.windows.net/8c4b47b5-ea35-4370-817f-95066d4f8467/</issuer>
    <issuer>https://login.microsoftonline.com/8c4b47b5-ea35-4370-817f-95066d4f8467/v2.0</issuer>
  </issuers>
</validate-jwt>
```

**Impatto:** Solo modifica APIM, nessuna modifica codice BE.

---

### Opzione B: Accettare audience App Registration

Modificare policy APIM per accettare audience della nostra App Registration:

```xml
<validate-jwt>
  <audiences>
    <audience>c989d43a-9e62-4c01-a67c-7eefbeef70ce</audience>
  </audiences>
  <issuers>
    <issuer>https://login.microsoftonline.com/8c4b47b5-ea35-4370-817f-95066d4f8467/v2.0</issuer>
  </issuers>
</validate-jwt>
```

**Impatto:** Modifica APIM + modifica codice BE (cambiare scope token request).

---

## Raccomandazione

**Opzione A** - piu' semplice, richiede solo modifica policy APIM senza toccare il codice applicativo.
