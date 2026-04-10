# Procedura confronti attuativi (auto pod).
# Esegue le chiamate a /compare-requirements/?comparisonMode=attuativa una alla volta.
# Convenzione POST: file1 = norma esterna (EXT o Documento 285*), file2 = interno (INT).
# Richiede port-forward già attivo:
#   kubectl port-forward svc/aiac-be-service -n aiac 2025:2025
#
param()

$ErrorActionPreference = "Stop"
$BasePath = "C:\Users\quantis\aiac-be\Documenti Da Analizzare"
$DestConfrontati = "C:\Users\quantis\aiac-be\Documenti Confrontati"
$CompareUrl = "http://localhost:2025/compare-requirements/?comparisonMode=attuativa"
$RequestTimeoutSec = 3600
$PairsFile = Join-Path $PSScriptRoot "confronti-attuativi-pairs.txt"

$PodNamespace = "aiac"
$ContainerName = "aiac-be-container"

function Get-AiacPodName() {
  $pod = (kubectl get pods -n $PodNamespace -l app.kubernetes.io/name=aiac-be-service -o jsonpath='{.items[0].metadata.name}' 2>$null)
  if (-not $pod) {
    $lines = kubectl get pods --namespace=$PodNamespace --output=name 2>$null
    $match = $lines | Where-Object { $_ -match "aiac-be-service" } | Select-Object -First 1
    if ($match) { return ($match -replace "^pod/", "").Trim() }
    throw "Nessun pod aiac-be-service trovato in namespace $PodNamespace"
  }
  return $pod.Trim()
}

function Load-PairsFromFile([string] $Path) {
  if (-not (Test-Path $Path)) { throw "File coppie non trovato: $Path" }
  $pairs = @()
  foreach ($line in Get-Content -Path $Path -Encoding UTF8) {
    $t = $line.Trim()
    if (-not $t) { continue }
    if ($t.StartsWith("#")) { continue }
    if ($t.StartsWith("Coppie")) { continue }
    if ($t.StartsWith("Formato")) { continue }
    # Expect TAB-separated columns
    $cols = $t -split "`t"
    if ($cols.Count -lt 2) { continue }
    $pairs += @{ F1 = $cols[0].Trim(); F2 = $cols[1].Trim() }
  }
  return $pairs
}

function Test-IsExternalLeaf([string] $LeafName) {
  if ([string]::IsNullOrWhiteSpace($LeafName)) { return $false }
  $n = $LeafName.Trim()
  if ($n.StartsWith("EXT", [StringComparison]::OrdinalIgnoreCase)) { return $true }
  if ($n.StartsWith("Documento", [StringComparison]::OrdinalIgnoreCase)) { return $true }
  return $false
}

function Test-IsInternalLeaf([string] $LeafName) {
  if ([string]::IsNullOrWhiteSpace($LeafName)) { return $false }
  return $LeafName.Trim().StartsWith("INT", [StringComparison]::OrdinalIgnoreCase)
}

function Normalize-AttuativaPair([hashtable] $p) {
  $leaf1 = Split-Path $p.F1 -Leaf
  $leaf2 = Split-Path $p.F2 -Leaf
  $ext1 = Test-IsExternalLeaf $leaf1
  $int1 = Test-IsInternalLeaf $leaf1
  $ext2 = Test-IsExternalLeaf $leaf2
  $int2 = Test-IsInternalLeaf $leaf2
  if ($int1 -and $ext2) {
    Write-Host "  [ordine] coppia corretta automaticamente: file1=esterno, file2=interno"
    return @{ F1 = $p.F2; F2 = $p.F1 }
  }
  if ($ext1 -and $int2) { return $p }
  if ($ext1 -and $ext2) { Write-Warning "Entrambi i PDF risultano esterni (EXT/Documento): $leaf1 vs $leaf2" }
  if ($int1 -and $int2) { Write-Warning "Entrambi i PDF risultano interni (INT): $leaf1 vs $leaf2" }
  return $p
}

$rawPairs = Load-PairsFromFile $PairsFile
$Pairs = @()
foreach ($rp in $rawPairs) {
  $Pairs += (Normalize-AttuativaPair $rp)
}

if (-not (Test-Path $BasePath)) { throw "Cartella sorgente non trovata: $BasePath" }
if (-not (Test-Path $DestConfrontati)) { New-Item -ItemType Directory -Path $DestConfrontati -Force | Out-Null }

Write-Host "Attendo 2 secondi per il port-forward..."
Start-Sleep -Seconds 2

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

  try {
    $fs = New-Object -ComObject Scripting.FileSystemObject
    $path1 = $fs.GetFile($path1).ShortPath
    $path2 = $fs.GetFile($path2).ShortPath
  } catch { }

  Write-Host ""
  Write-Host "[$num/$total] Confronto attuativo: $name1  vs  $name2"

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

$PodName = Get-AiacPodName
$remoteOut = "/tmp/confronti_attuativi_out"

kubectl exec $PodName -n $PodNamespace -c $ContainerName -- python -c "import sys; sys.path.insert(0, '/app/src'); from pathlib import Path; from utils import blob_storage_client as bsc; cc = bsc.get_container_client(); Path('$remoteOut').mkdir(parents=True, exist_ok=True); prefix='out/implementations/'; [Path('$remoteOut').joinpath(b.name[len(prefix):]).write_bytes(cc.get_blob_client(b.name).download_blob().readall()) for b in cc.list_blobs(name_starts_with=prefix) if len(b.name) > len(prefix)]" 2>&1 | Out-Null

Set-Location $DestConfrontati
$podFiles = (kubectl exec $PodName -n $PodNamespace -c $ContainerName -- sh -c "ls -1 $remoteOut 2>/dev/null") -split "`n"
foreach ($f in $podFiles) {
  $f = $f.Trim()
  if (-not $f) { continue }
  $ok = $false
  for ($i=1; $i -le 5; $i++) {
    try {
      kubectl cp "${PodNamespace}/${PodName}:${remoteOut}/$f" ".\\$f" -c $ContainerName 2>&1 | Out-Null
      if (Test-Path ".\\$f") { $ok = $true; break }
    } catch { }
    Start-Sleep -Seconds 2
  }
  if ($ok) { Write-Host "  Copiato: $f" } else { Write-Warning "  Copia fallita (file in uso?): $f" }
}
Set-Location $PSScriptRoot

Write-Host ""
Write-Host "Procedura completata. File in: $DestConfrontati"

