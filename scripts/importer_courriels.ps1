# Import planifié des courriels transférés aux alias des dossiers.
#
# Lancé toutes les 10 minutes par la tâche Windows « Chronos - courriels »,
# une fois l'application déclarée dans Entra ID (voir
# docs/superpowers/import-courriels.md). Chaque passage ajoute son résultat à
# sauvegardes\courriels.log ; un échec rend un code de sortie non nul, visible
# dans le Planificateur de tâches.
#
# Les identifiants Graph sont lus dans les variables d'environnement de
# l'utilisateur Windows (GRAPH_TENANT_ID, GRAPH_CLIENT_ID, GRAPH_CLIENT_SECRET,
# COURRIEL_BOITE) — jamais écrits dans ce fichier.
#
# Usage manuel :  .\scripts\importer_courriels.ps1 [-Essai]

param([switch]$Essai)

$ErrorActionPreference = "Stop"
$projet = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projet ".venv\Scripts\python.exe"

# DEBUG n'expose rien ici : aucune requête HTTP n'est servie.
$env:DJANGO_DEBUG = "True"
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$arguments = @("manage.py", "importer_courriels")
if ($Essai) { $arguments += "--essai" }

$dossierLog = Join-Path $projet "sauvegardes"
New-Item -ItemType Directory -Force -Path $dossierLog | Out-Null
$log = Join-Path $dossierLog "courriels.log"

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
