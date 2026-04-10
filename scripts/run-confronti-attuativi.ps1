# Procedura confronti attuativi
# Esegue le chiamate a /compare-requirements/?comparisonMode=attuativa una alla volta,
# attende il completamento di ciascuna prima di passare alla successiva.
# Al termine copia i file di output in "Documenti Confrontati".
#
# Prerequisiti:
# - kubectl port-forward svc/aiac-be-service -n aiac 2025:2025 (avviato in un altro terminale)
# - PDF e analisi già presenti (Blob/cache) come da procedura analisi
#
# Uso: .\run-confronti-attuativi.ps1

$ErrorActionPreference = "Stop"
$BasePath = "C:\Users\quantis\aiac-be\Documenti Da Analizzare"
$DestConfrontati = "C:\Users\quantis\aiac-be\Documenti Confrontati"
$CompareUrl = "http://localhost:2025/compare-requirements/?comparisonMode=attuativa"
$RequestTimeoutSec = 3600
$PodNamespace = "aiac"
# Nome pod backend (se il deploy cambia, si può usare: kubectl get pods -n aiac -l app.kubernetes.io/name=aiac-be-service -o jsonpath='{.items[0].metadata.name}')
$PodName = "aiac-be-service-894d48fcd-4p8j6"
$ContainerName = "aiac-be-container"

# Coppie: F1 = esterno (EXT / Documento 285*), F2 = interno (INT) — path relativi sotto BasePath
$Pairs = @(
  @{ F1 = "AML\EXT_1_1_Provvedimento UIF su indicatori di anomalia_12_05_2023.pdf"; F2 = "AML\INT_1_1_REG_Indicatori_di_anomalia_AR_7.0__2024.12.09.pdf" },
  @{ F1 = "AML\EXT_1_2_Disposizioni AVC_30_07_2019 (Banca Italia).pdf"; F2 = "AML\INT_1_2_PRC_Gest._Ademp._AR_v.10.4_23.12.2025.pdf" },
  @{ F1 = "AML\EXT_1_D.Lgs. 231_2007_gazzetta ufficiale_pdf.pdf"; F2 = "AML\INT_1_POL_GRP_Antiriciclaggio_v5.0_27.11.2023_Clean.pdf" },
  @{ F1 = "GESTIONE OUTSOURCING\EXT_2_Regolamento (UE) 2022_2554 - (DORA).pdf"; F2 = "GESTIONE OUTSOURCING\INT_2_REG_Gestione_rischi_info_servizi_terze_parti_20251203.pdf" },
  @{ F1 = "285\Documento 285 - Parziale - 001-206.pdf"; F2 = "GESTIONE OUTSOURCING\INT_2_REG_Gestione_rischi_info_servizi_terze_parti_20251203.pdf" },
  @{ F1 = "285\Documento 285 - Parziale - 207-399.pdf"; F2 = "GESTIONE OUTSOURCING\INT_2_REG_Gestione_rischi_info_servizi_terze_parti_20251203.pdf" },
  @{ F1 = "285\Documento 285 - Parziale - 400-561.pdf"; F2 = "GESTIONE OUTSOURCING\INT_2_REG_Gestione_rischi_info_servizi_terze_parti_20251203.pdf" },
  @{ F1 = "285\Documento 285 - Parziale - 562-702.pdf"; F2 = "GESTIONE OUTSOURCING\INT_2_REG_Gestione_rischi_info_servizi_terze_parti_20251203.pdf" },
  @{ F1 = "285\Documento 285 - Parziale - 703-815.pdf"; F2 = "GESTIONE OUTSOURCING\INT_2_REG_Gestione_rischi_info_servizi_terze_parti_20251203.pdf" },
  @{ F1 = "285\Documento 285 - Parziale - 001-206.pdf"; F2 = "ICAAP_ILAAP\INT_3_1_Collegato_Sistema_degli_obiettivi_patrimoniali_19.12.2025.pdf" },
  @{ F1 = "285\Documento 285 - Parziale - 207-399.pdf"; F2 = "ICAAP_ILAAP\INT_3_1_Collegato_Sistema_degli_obiettivi_patrimoniali_19.12.2025.pdf" },
  @{ F1 = "285\Documento 285 - Parziale - 400-561.pdf"; F2 = "ICAAP_ILAAP\INT_3_1_Collegato_Sistema_degli_obiettivi_patrimoniali_19.12.2025.pdf" },
  @{ F1 = "285\Documento 285 - Parziale - 562-702.pdf"; F2 = "ICAAP_ILAAP\INT_3_1_Collegato_Sistema_degli_obiettivi_patrimoniali_19.12.2025.pdf" },
  @{ F1 = "285\Documento 285 - Parziale - 703-815.pdf"; F2 = "ICAAP_ILAAP\INT_3_1_Collegato_Sistema_degli_obiettivi_patrimoniali_19.12.2025.pdf" },
  @{ F1 = "285\Documento 285 - Parziale - 001-206.pdf"; F2 = "ICAAP_ILAAP\INT_3_PG_Rischi_19.12.2025.pdf" },
  @{ F1 = "285\Documento 285 - Parziale - 207-399.pdf"; F2 = "ICAAP_ILAAP\INT_3_PG_Rischi_19.12.2025.pdf" },
  @{ F1 = "285\Documento 285 - Parziale - 400-561.pdf"; F2 = "ICAAP_ILAAP\INT_3_PG_Rischi_19.12.2025.pdf" },
  @{ F1 = "285\Documento 285 - Parziale - 562-702.pdf"; F2 = "ICAAP_ILAAP\INT_3_PG_Rischi_19.12.2025.pdf" },
  @{ F1 = "285\Documento 285 - Parziale - 703-815.pdf"; F2 = "ICAAP_ILAAP\INT_3_PG_Rischi_19.12.2025.pdf" },
  @{ F1 = "285\Documento 285 - Parziale - 001-206.pdf"; F2 = "ICAAP_ILAAP\INT_4_PRC_Det adeg sist gov e gestione rischio liquidit__v2.0_17.07.2024.pdf" },
  @{ F1 = "285\Documento 285 - Parziale - 207-399.pdf"; F2 = "ICAAP_ILAAP\INT_4_PRC_Det adeg sist gov e gestione rischio liquidit__v2.0_17.07.2024.pdf" },
  @{ F1 = "285\Documento 285 - Parziale - 400-561.pdf"; F2 = "ICAAP_ILAAP\INT_4_PRC_Det adeg sist gov e gestione rischio liquidit__v2.0_17.07.2024.pdf" },
  @{ F1 = "285\Documento 285 - Parziale - 562-702.pdf"; F2 = "ICAAP_ILAAP\INT_4_PRC_Det adeg sist gov e gestione rischio liquidit__v2.0_17.07.2024.pdf" },
  @{ F1 = "285\Documento 285 - Parziale - 703-815.pdf"; F2 = "ICAAP_ILAAP\INT_4_PRC_Det adeg sist gov e gestione rischio liquidit__v2.0_17.07.2024.pdf" },
  @{ F1 = "MONITORAGGIO FONDI PROPRI\EXT_5_EBA 2020 GL Loan origination and monitoring_IT.pdf"; F2 = "MONITORAGGIO FONDI PROPRI\INT_5_Regolamento_del_Credito_13.05.2025.pdf" },
  @{ F1 = "MONITORAGGIO FONDI PROPRI\EXT_5_1_EBA 2018 GL NPE_FBE_IT.pdf"; F2 = "MONITORAGGIO FONDI PROPRI\INT_5_Regolamento_del_Credito_13.05.2025.pdf" },
  @{ F1 = "SICUREZZA LOGICA\EXT_6_1_Final draft Guidelines on ICT and security risk management_COR_IT.pdf"; F2 = "SICUREZZA LOGICA\INT_6_REG_Gestione_del_Rischio_ICT_20250325_clean.pdf" },
  @{ F1 = "285\Documento 285 - Parziale - 001-206.pdf"; F2 = "SICUREZZA LOGICA\INT_6_REG_Gestione_del_Rischio_ICT_20250325_clean.pdf" },
  @{ F1 = "285\Documento 285 - Parziale - 207-399.pdf"; F2 = "SICUREZZA LOGICA\INT_6_REG_Gestione_del_Rischio_ICT_20250325_clean.pdf" },
  @{ F1 = "285\Documento 285 - Parziale - 400-561.pdf"; F2 = "SICUREZZA LOGICA\INT_6_REG_Gestione_del_Rischio_ICT_20250325_clean.pdf" },
  @{ F1 = "285\Documento 285 - Parziale - 562-702.pdf"; F2 = "SICUREZZA LOGICA\INT_6_REG_Gestione_del_Rischio_ICT_20250325_clean.pdf" },
  @{ F1 = "285\Documento 285 - Parziale - 703-815.pdf"; F2 = "SICUREZZA LOGICA\INT_6_REG_Gestione_del_Rischio_ICT_20250325_clean.pdf" }
)

if (-not (Test-Path $BasePath)) {
  Write-Error "Cartella sorgente non trovata: $BasePath"
}

if (-not (Test-Path $DestConfrontati)) {
  New-Item -ItemType Directory -Path $DestConfrontati -Force | Out-Null
  Write-Host "Creata cartella: $DestConfrontati"
}

Write-Host "Attendo 5 secondi per il port-forward..."
Start-Sleep -Seconds 5

$num = 0
$total = $Pairs.Count
foreach ($p in $Pairs) {
  $num++
  $path1 = Join-Path $BasePath $p.F1
  $path2 = Join-Path $BasePath $p.F2
  $name1 = Split-Path $p.F1 -Leaf
  $name2 = Split-Path $p.F2 -Leaf

  if (-not (Test-Path $path1)) { Write-Warning "Manca file1: $path1"; continue }
  if (-not (Test-Path $path2)) { Write-Warning "Manca file2: $path2"; continue }

  # Usa path brevi (8.3) per evitare che curl interpreti " - " nel path come opzione
  try {
    $fs = New-Object -ComObject Scripting.FileSystemObject
    $path1 = $fs.GetFile($path1).ShortPath
    $path2 = $fs.GetFile($path2).ShortPath
  } catch {
    # Fallback se FSO non disponibile
  }

  Write-Host ""
  Write-Host "[$num/$total] Confronto attuativo: $name1  vs  $name2"

  # Nomi per il form: evita " - " che curl interpreta come opzione
  $name1Form = $name1 -replace ' - ', '_'
  $name2Form = $name2 -replace ' - ', '_'
  $form1 = "file1=@`"$path1`";filename=$name1Form"
  $form2 = "file2=@`"$path2`";filename=$name2Form"

  $outFile = Join-Path $env:TEMP "compare_$num.json"
  $result = & curl.exe -s -X POST $CompareUrl -F "$form1" -F "$form2" -H "accept: application/json" --max-time $RequestTimeoutSec -w "HTTP_CODE:%{http_code} TIME:%{time_total}s" -o $outFile 2>&1
  Write-Host "  $result"

  if ($result -match "HTTP_CODE:200") {
    Write-Host "  OK."
  } else {
    Write-Warning "  Fallito o timeout. Controllare $outFile se necessario."
  }
}

Write-Host ""
Write-Host "Confronti eseguiti. Download file da Blob in Documenti Confrontati..."

# Scarica dal Blob (via pod) i file in out/implementations/ (confronti attuativi) e copiali in Documenti Confrontati
$remoteOut = "/tmp/confronti_attuativi_out"
# Per comparisonMode=attuativa il backend salva in out/implementations/
kubectl exec $PodName -n $PodNamespace -c $ContainerName -- python -c "import sys; sys.path.insert(0, '/app/src'); from pathlib import Path; from utils import blob_storage_client as bsc; cc = bsc.get_container_client(); Path('$remoteOut').mkdir(parents=True, exist_ok=True); prefix='out/implementations/'; [Path('$remoteOut').joinpath(b.name[len(prefix):]).write_bytes(cc.get_blob_client(b.name).download_blob().readall()) for b in cc.list_blobs(name_starts_with=prefix) if len(b.name) > len(prefix)]" 2>&1 | Out-Null

# Elenca file scaricati nel pod e copiali in locale
Set-Location $DestConfrontati
$podFiles = (kubectl exec $PodName -n $PodNamespace -c $ContainerName -- sh -c "ls -1 $remoteOut 2>/dev/null") -split "`n"
foreach ($f in $podFiles) {
  $f = $f.Trim()
  if (-not $f) { continue }
  kubectl cp "${PodNamespace}/${PodName}:${remoteOut}/$f" ".\$f" -c $ContainerName 2>&1 | Out-Null
  if (Test-Path ".\$f") { Write-Host "  Copiato: $f" }
}
# Torna alla directory iniziale (cartella dello script)
Set-Location $PSScriptRoot

Write-Host ""
Write-Host "Procedura completata. File in: $DestConfrontati"
