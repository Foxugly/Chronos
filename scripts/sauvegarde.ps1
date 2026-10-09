# Sauvegarde planifiée de Chronos : base + pièces, vérifiée, avec rotation.
#
# Lancé par la tâche Windows « Chronos - sauvegarde » (voir CLAUDE.md).
# Chaque exécution ajoute son résultat à sauvegardes\sauvegarde.log ; un échec
# y est écrit et rend un code de sortie non nul, visible dans le
# Planificateur de tâches (« Résultat de la dernière exécution »).
#
# Usage manuel :  .\scripts\sauvegarde.ps1 [-Destination D:\ailleurs] [-Garder 14]

param(
    [string]$Destination,
    [int]$Garder = 0
)

$ErrorActionPreference = "Stop"
$projet = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projet ".venv\Scripts\python.exe"

# DEBUG n'expose rien ici : aucune requête HTTP n'est servie. Il autorise
# seulement la clé de développement, que la commande n'utilise pas.
$env:DJANGO_DEBUG = "True"

# Python écrit en UTF-8, et PowerShell doit le lire ainsi : sans ces deux
# lignes, le journal recevait « vÚrifiÚe » au lieu de « vérifiée ».
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$arguments = @("manage.py", "sauvegarder")
if ($Destination) { $arguments += @("--destination", $Destination) }
if ($Garder -gt 0) { $arguments += @("--garder", $Garder) }

$dossierLog = if ($Destination) { $Destination } else { Join-Path $projet "sauvegardes" }
New-Item -ItemType Directory -Force -Path $dossierLog | Out-Null
$log = Join-Path $dossierLog "sauvegarde.log"

Push-Location $projet
try {
    $sortie = & $python @arguments 2>&1 | Out-String
    $code = $LASTEXITCODE
} finally {
    Pop-Location
}

$horodatage = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
$etat = if ($code -eq 0) { "OK" } else { "ÉCHEC (code $code)" }
Add-Content -Path $log -Encoding utf8 -Value "[$horodatage] $etat`n$($sortie.Trim())`n"
Write-Output $sortie
exit $code
