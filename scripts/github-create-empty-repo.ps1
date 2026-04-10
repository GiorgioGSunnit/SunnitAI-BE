# Crea un repository vuoto sul tuo account GitHub.
#
# Metodo 1 (consigliato): una tantum
#   & "$env:ProgramFiles\GitHub CLI\gh.exe" auth login
#   .\scripts\github-create-empty-repo.ps1 -Name SunnitAI-BE
#
# Metodo 2: PAT con scope "repo"
#   $env:GITHUB_TOKEN = "ghp_..."
#   .\scripts\github-create-empty-repo.ps1 -Name SunnitAI-BE

param(
    [Parameter(Mandatory = $false)]
    [string] $Name = "SunnitAI-BE",

    [Parameter(Mandatory = $false)]
    [string] $Description = "Backend SunnitAI / AIAC (nuovo mirror, senza history GitLab)"
)

$ErrorActionPreference = "Stop"

$gh = Join-Path $env:ProgramFiles "GitHub CLI\gh.exe"
if (-not (Test-Path $gh)) {
    $gh = "gh"
}

if ($env:GITHUB_TOKEN) {
    $headers = @{
        Authorization          = "Bearer $($env:GITHUB_TOKEN)"
        Accept                   = "application/vnd.github+json"
        "X-GitHub-Api-Version"   = "2022-11-28"
    }
    $payload = @{
        name        = $Name
        description = $Description
        private     = $false
    } | ConvertTo-Json

    $null = Invoke-RestMethod `
        -Uri "https://api.github.com/user/repos" `
        -Method Post `
        -Headers $headers `
        -Body $payload `
        -ContentType "application/json; charset=utf-8"

    $me = (Invoke-RestMethod -Uri "https://api.github.com/user" -Headers $headers).login
    Write-Host "Repository creato: https://github.com/$me/$Name"
    exit 0
}

$prevEap = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$null = & $gh auth status 2>&1
$ghOk = ($LASTEXITCODE -eq 0)
$ErrorActionPreference = $prevEap
if (-not $ghOk) {
    throw "Nessuna sessione GitHub CLI. Esegui: `"$gh`" auth login`nOppure imposta la variabile d'ambiente GITHUB_TOKEN (PAT con scope repo) e rilancia."
}

& $gh repo create $Name --public --description $Description
$login = & $gh api user -q .login
Write-Host "Repository creato: https://github.com/$login/$Name"
