<#
.SYNOPSIS
    Réception des courriels de Chronos : Microsoft 365 + SSM + serveur, en une fois.

.DESCRIPTION
    1. Entra « Chronos »          : application Graph (client secret), AUCUNE permission Entra —
                                    les droits viennent d'Exchange (RBAC pour les applications),
                                    limités aux seules boîtes d'import et d'expédition.
    2. Entra « Chronos Exchange » : application PowerShell (certificat RSA 2048 par défaut), permission
                                    Exchange.ManageAsApp consentie ; rôle « Mail Recipients »
                                    limité à la boîte d'import (création automatique des alias).
    3. SSM /chronos/prod/*        : identifiants écrits par l'AWS CLI si elle fonctionne ; sinon
                                    un fichier JSON à téléverser dans CloudShell (commande affichée).
    4. Serveur                    : rechargement de la configuration, essai d'import à blanc
                                    (--essai : rien n'est créé ni déplacé), puis, après ta
                                    confirmation, activation de l'import toutes les 10 minutes.

    Aucun secret n'est affiché ni passé en ligne de commande. Le PFX ne reste pas sur le disque.
    Ré-exécutable : les applications existantes sont reprises (nouveau secret, nouveau certificat).

.EXAMPLE
    .\deploy\m365-setup.ps1 -BoiteImport import@foxugly.com -Expediteur chronos@foxugly.com
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)] [string] $BoiteImport,
    [Parameter(Mandatory = $true)] [string] $Expediteur,
    [string] $CleSsh = "C:\Users\Renaud\Dropbox\key\foxugly.com.pem",
    [string] $Serveur = "ubuntu@ec2-54-229-220-110.eu-west-1.compute.amazonaws.com",
    [string] $Region = "eu-west-1",
    [switch] $SansServeur
)

$ErrorActionPreference = "Stop"
$BoiteImport = $BoiteImport.Trim().ToLower()
$Expediteur = $Expediteur.Trim().ToLower()
$NomGraph = "Chronos"
$NomExchange = "Chronos Exchange"
$ExoAppId = "00000002-0000-0ff1-ce00-000000000000"   # Office 365 Exchange Online

function Etape($texte) { Write-Host "`n=== $texte ===" -ForegroundColor Cyan }
function Ok($texte) { Write-Host "  OK  $texte" -ForegroundColor Green }

function Nouveau-MotDePasse {
    $octets = New-Object byte[] 36
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($octets)
    return ([Convert]::ToBase64String($octets) -replace '[+/=]', '')
}

# --- 0. Prérequis ------------------------------------------------------------------------
Etape "Prérequis"
foreach ($module in "Microsoft.Graph.Applications", "Microsoft.Graph.Identity.DirectoryManagement", "ExchangeOnlineManagement") {
    if (-not (Get-Module -ListAvailable -Name $module)) {
        Write-Host "  Installation du module $module (utilisateur courant)…"
        Install-Module $module -Scope CurrentUser -Force -AllowClobber
    }
}
Ok "modules Microsoft.Graph et ExchangeOnlineManagement"

$awsOk = $false
if (Get-Command aws -ErrorAction SilentlyContinue) {
    # Windows PowerShell 5.1 change toute sortie d'erreur redirigée d'un programme
    # externe en erreur bloquante quand ErrorActionPreference vaut Stop.
    $ErrorActionPreference = "Continue"
    aws sts get-caller-identity --output text *> $null
    $awsOk = ($LASTEXITCODE -eq 0)
    $ErrorActionPreference = "Stop"
}
if ($awsOk) { Ok "AWS CLI utilisable : SSM sera écrit directement" }
else { Write-Host "  --  AWS CLI sans identifiants valides : un fichier pour CloudShell sera préparé" -ForegroundColor Yellow }

# --- 1. Connexions -----------------------------------------------------------------------
Etape "Connexion à Microsoft Graph (compte administrateur)"
Connect-MgGraph -Scopes "Application.ReadWrite.All", "AppRoleAssignment.ReadWrite.All", "Organization.Read.All" -NoWelcome
$contexte = Get-MgContext
$tenantId = $contexte.TenantId
$domaineInitial = (Get-MgOrganization).VerifiedDomains | Where-Object IsInitial | Select-Object -ExpandProperty Name
Ok "locataire $tenantId ($domaineInitial)"

Etape "Connexion à Exchange Online"
Connect-ExchangeOnline -ShowBanner:$false
foreach ($boite in @($BoiteImport, $Expediteur) | Select-Object -Unique) {
    $mbx = Get-EXOMailbox -Identity $boite -ErrorAction SilentlyContinue
    if (-not $mbx) { throw "La boîte $boite n'existe pas dans Exchange Online." }
    Ok "boîte $boite ($($mbx.RecipientTypeDetails))"
}

# La boîte d'import peut servir à autre chose : Chronos ne lit que les dernières 24 h,
# ne déplace que les courriels adressés à un alias (vers un dossier Outlook du nom du
# dossier Chronos) et laisse tous les autres en place.

function Application-Entra($nom) {
    $app = Get-MgApplication -Filter "displayName eq '$nom'" -All
    if (@($app).Count -gt 1) { throw "Plusieurs applications Entra s'appellent « $nom » : rien n'est modifié." }
    if (-not $app) {
        $app = New-MgApplication -DisplayName $nom -SignInAudience AzureADMyOrg
        Ok "application « $nom » créée"
    } else { Ok "application « $nom » reprise" }
    $sp = Get-MgServicePrincipal -Filter "appId eq '$($app.AppId)'"
    if (-not $sp) { $sp = New-MgServicePrincipal -AppId $app.AppId }
    return @{ App = $app; Sp = $sp }
}

# Les applets Exchange signalent leurs erreurs sans arrêter le script : -ErrorAction Stop
# partout, sinon une étape ratée s'afficherait « OK ».

function Portee-Exchange($nom, $filtre) {
    $existe = $null
    try { $existe = Get-ManagementScope -Identity $nom -ErrorAction Stop } catch { }
    if ($existe) { Set-ManagementScope -Identity $nom -RecipientRestrictionFilter $filtre -ErrorAction Stop }
    else { New-ManagementScope -Name $nom -RecipientRestrictionFilter $filtre -ErrorAction Stop | Out-Null }
}

function Principal-Exchange($appId, $objectId, $nom) {
    # Une application qui vient d'être créée dans Entra met une à trois minutes à
    # devenir visible d'Exchange (AADServicePrincipalNotFound) : on attend.
    if (Get-ServicePrincipal -ErrorAction SilentlyContinue | Where-Object AppId -eq $appId) { return }
    for ($essai = 1; $essai -le 12; $essai++) {
        try {
            New-ServicePrincipal -AppId $appId -ObjectId $objectId -DisplayName $nom -ErrorAction Stop | Out-Null
            Ok "principal Exchange « $nom » inscrit"
            return
        } catch {
            if ("$_" -notmatch "AADServicePrincipalNotFound") { throw }
            Write-Host "  …  Exchange ne voit pas encore « $nom » (propagation Entra), nouvel essai dans 15 s ($essai/12)"
            Start-Sleep -Seconds 15
        }
    }
    throw "Exchange ne voit toujours pas « $nom » après 3 minutes : relance le script dans quelques minutes."
}

function Role-Exchange($appId, $role, $portee) {
    $existe = Get-ManagementRoleAssignment -RoleAssignee $appId -Role $role -ErrorAction SilentlyContinue
    if (-not $existe) {
        New-ManagementRoleAssignment -App $appId -Role $role -CustomResourceScope $portee -ErrorAction Stop | Out-Null
    }
    $verifie = Get-ManagementRoleAssignment -RoleAssignee $appId -Role $role -ErrorAction Stop
    if (-not $verifie) { throw "Le rôle $role n'apparaît pas sur l'application $appId." }
    if ($verifie.CustomResourceScope -ne $portee) {
        throw "Le rôle $role existe sur $appId sans être limité à « $portee » : retire-le (Remove-ManagementRoleAssignment) et relance."
    }
}

# --- 2. « Chronos » : Graph, lecture de la boîte d'import et envoi ------------------------
Etape "Application « $NomGraph » (Graph)"
$g = Application-Entra $NomGraph
$secret = Add-MgApplicationPassword -ApplicationId $g.App.Id -PasswordCredential @{
    DisplayName = "chronos-prod $(Get-Date -Format yyyy-MM-dd)"
    EndDateTime = (Get-Date).AddMonths(24)
}
Ok "secret client créé (expire le $($secret.EndDateTime.ToString('yyyy-MM-dd')) — à renouveler avant)"
# Les secrets des passages précédents ne servent plus : on ne garde que le nouveau.
(Get-MgApplication -ApplicationId $g.App.Id).PasswordCredentials |
    Where-Object { $_.DisplayName -like "chronos-prod*" -and $_.KeyId -ne $secret.KeyId } |
    ForEach-Object { Remove-MgApplicationPassword -ApplicationId $g.App.Id -KeyId $_.KeyId }

Principal-Exchange $g.App.AppId $g.Sp.Id $NomGraph
$filtre = (@($BoiteImport, $Expediteur) | Select-Object -Unique | ForEach-Object { "PrimarySmtpAddress -eq '$_'" }) -join " -or "
$scopeMail = "Chronos boites"
Portee-Exchange $scopeMail $filtre
foreach ($role in "Application Mail.ReadWrite", "Application Mail.Send") {
    Role-Exchange $g.App.AppId $role $scopeMail
    Ok "$role limité à : $filtre"
}

# --- 3. « Chronos Exchange » : alias automatiques -----------------------------------------
Etape "Application « $NomExchange » (alias)"
$e = Application-Entra $NomExchange

$motDePassePfx = Nouveau-MotDePasse
$cert = New-SelfSignedCertificate -Subject "CN=Chronos-Exchange" -CertStoreLocation "Cert:\CurrentUser\My" `
    -KeySpec KeyExchange -KeyExportPolicy Exportable `
    -HashAlgorithm SHA256 -NotAfter (Get-Date).AddMonths(24)
$fichierPfx = Join-Path $env:TEMP "chronos-exchange-$([guid]::NewGuid()).pfx"
try {
    Export-PfxCertificate -Cert $cert -FilePath $fichierPfx -CryptoAlgorithmOption AES256_SHA256 `
        -Password (ConvertTo-SecureString $motDePassePfx -AsPlainText -Force) | Out-Null
    $pfxB64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes($fichierPfx))
} finally {
    Remove-Item $fichierPfx -Force -ErrorAction SilentlyContinue
    Remove-Item "Cert:\CurrentUser\My\$($cert.Thumbprint)" -DeleteKey -ErrorAction SilentlyContinue
}
Update-MgApplication -ApplicationId $e.App.Id -KeyCredentials @(@{
    Type = "AsymmetricX509Cert"; Usage = "Verify"; Key = $cert.RawData; DisplayName = "CN=Chronos-Exchange"
})
Ok "certificat $($cert.Thumbprint) déposé (clé publique seulement), expire le $($cert.NotAfter.ToString('yyyy-MM-dd'))"

$exo = Get-MgServicePrincipal -Filter "appId eq '$ExoAppId'"
$roleManage = $exo.AppRoles | Where-Object Value -eq "Exchange.ManageAsApp"
Update-MgApplication -ApplicationId $e.App.Id -RequiredResourceAccess @(@{
    ResourceAppId = $ExoAppId; ResourceAccess = @(@{ Id = $roleManage.Id; Type = "Role" })
})
$dejaConsenti = Get-MgServicePrincipalAppRoleAssignment -ServicePrincipalId $e.Sp.Id |
    Where-Object { $_.AppRoleId -eq $roleManage.Id -and $_.ResourceId -eq $exo.Id }
if (-not $dejaConsenti) {
    New-MgServicePrincipalAppRoleAssignment -ServicePrincipalId $e.Sp.Id -PrincipalId $e.Sp.Id `
        -ResourceId $exo.Id -AppRoleId $roleManage.Id | Out-Null
}
Ok "Exchange.ManageAsApp consentie"

Principal-Exchange $e.App.AppId $e.Sp.Id $NomExchange
$filtreAlias = "PrimarySmtpAddress -eq '$BoiteImport'"
if ($filtreAlias -eq $filtre) {
    $scopeAlias = $scopeMail   # même boîte : Exchange refuse deux étendues identiques
} else {
    $scopeAlias = "Chronos boite import"
    Portee-Exchange $scopeAlias $filtreAlias
}
$aliasAuto = $true
try {
    Role-Exchange $e.App.AppId "Mail Recipients" $scopeAlias
    Ok "Mail Recipients limité à $BoiteImport"
} catch {
    # Jamais de repli sur un rôle valable pour tout le tenant : les alias se feront à la main.
    $aliasAuto = $false
    Write-Host "  !!  Exchange refuse de limiter « Mail Recipients » à $BoiteImport :" -ForegroundColor Yellow
    Write-Host "      $_" -ForegroundColor Yellow
    Write-Host "      On continue SANS alias automatiques (EXCHANGE_* non écrits) : la page Paramètres" -ForegroundColor Yellow
    Write-Host "      donnera la commande Set-Mailbox à lancer à la main. L'import, lui, fonctionnera." -ForegroundColor Yellow
}

# --- 4. SSM ------------------------------------------------------------------------------
Etape "Paramètres SSM /chronos/prod"
$parametres = [ordered]@{
    GRAPH_TENANT_ID        = @("String", $tenantId)
    GRAPH_CLIENT_ID        = @("String", $g.App.AppId)
    GRAPH_CLIENT_SECRET    = @("SecureString", $secret.SecretText)
    GRAPH_SENDER           = @("String", $Expediteur)
    COURRIEL_BOITE         = @("String", $BoiteImport)
}
if ($aliasAuto) {
    $parametres.EXCHANGE_APP_ID        = @("String", $e.App.AppId)
    $parametres.EXCHANGE_TENANT        = @("String", $domaineInitial)
    $parametres.EXCHANGE_CERT_PFX_B64  = @("SecureString", $pfxB64)
    $parametres.EXCHANGE_CERT_PASSWORD = @("SecureString", $motDePassePfx)
}
if ($pfxB64.Length -gt 4096) { throw "PFX trop grand pour un paramètre standard ($($pfxB64.Length) caractères)." }

if ($awsOk) {
    foreach ($nom in $parametres.Keys) {
        $type, $valeur = $parametres[$nom]
        $tmp = Join-Path $env:TEMP "chronos-ssm-$([guid]::NewGuid())"
        try {
            [IO.File]::WriteAllText($tmp, $valeur)       # ni écho, ni ligne de commande
            aws ssm put-parameter --region $Region --name "/chronos/prod/$nom" --type $type `
                --value "file://$tmp" --overwrite --no-cli-pager | Out-Null
            if ($LASTEXITCODE -ne 0) { throw "Échec de l'écriture de /chronos/prod/$nom." }
        } finally { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
        Ok "/chronos/prod/$nom ($type)"
    }
} else {
    $fichier = Join-Path ([Environment]::GetFolderPath("Desktop")) "chronos-ssm.json"
    $parametres.GetEnumerator() | ForEach-Object {
        [ordered]@{ Name = "/chronos/prod/$($_.Key)"; Type = $_.Value[0]; Value = $_.Value[1] }
    } | ConvertTo-Json | Set-Content -Path $fichier -Encoding utf8
    icacls $fichier /inheritance:r /grant:r "$($env:USERNAME):F" | Out-Null
    Write-Host @"

  Fichier prêt : $fichier  (contient des secrets — lisible par toi seul)
  1. CloudShell → Actions → Upload file → choisis ce fichier.
  2. Colle dans CloudShell :

     python3 -c "import json,subprocess; [subprocess.run(['aws','ssm','put-parameter','--region','$Region','--name',p['Name'],'--type',p['Type'],'--value',p['Value'],'--overwrite','--no-cli-pager'],check=True,stdout=subprocess.DEVNULL) or print('OK',p['Name']) for p in json.load(open('chronos-ssm.json',encoding='utf-8-sig'))]" && shred -u chronos-ssm.json

  3. Supprime le fichier du Bureau une fois que CloudShell a affiché les $($parametres.Count) « OK ».
"@ -ForegroundColor Yellow
    Read-Host "  Appuie sur Entrée quand SSM est écrit (CloudShell a affiché $($parametres.Count) OK)"
}
Remove-Variable secret, pfxB64, motDePassePfx, parametres -ErrorAction SilentlyContinue

Disconnect-ExchangeOnline -Confirm:$false | Out-Null
Disconnect-MgGraph | Out-Null

# --- 5. Serveur --------------------------------------------------------------------------
if ($SansServeur) { Write-Host "`nServeur non touché (-SansServeur)."; return }

Etape "Serveur : rechargement de la configuration"
ssh -i $CleSsh $Serveur "sudo systemctl restart chronos-env-fetch && sudo systemctl restart chronos-gunicorn && systemctl is-active chronos-gunicorn"
if ($LASTEXITCODE -ne 0) { throw "Le redémarrage a échoué sur le serveur." }
Ok "configuration rechargée"

Etape "Serveur : essai d'import à blanc (rien n'est créé ni déplacé)"
Write-Host "  Les droits Exchange mettent 5 à 30 minutes à s'appliquer : un refus (403) juste après est normal." -ForegroundColor Yellow
$essai = "sudo systemd-run --wait --pipe --quiet --uid=django --gid=www-data -p EnvironmentFile=/run/chronos/.env " +
         "-p WorkingDirectory=/var/www/django_websites/Chronos " +
         "/var/www/django_websites/Chronos/.venv/bin/python manage.py importer_courriels --essai"
do {
    ssh -i $CleSsh $Serveur $essai
    $reussi = ($LASTEXITCODE -eq 0)
    if (-not $reussi) { $encore = Read-Host "  L'essai a échoué. Réessayer ? (o/N)" }
} while (-not $reussi -and $encore -match '^[oOyY]')
if (-not $reussi) { Write-Host "`nImport NON activé. Relance plus tard : .\deploy\m365-setup.ps1 … ou l'essai seul."; return }

$go = Read-Host "`n  L'essai est-il correct ? Activer l'import toutes les 10 minutes ? (o/N)"
if ($go -match '^[oOyY]') {
    ssh -i $CleSsh $Serveur "sudo systemctl enable --now chronos-courriels.timer && systemctl list-timers chronos-courriels.timer --no-pager"
    Ok "import activé"
} else {
    Write-Host "Import non activé. Plus tard : sudo systemctl enable --now chronos-courriels.timer"
}
Write-Host "`nReste : dans Chronos, Paramètres du dossier → Import par courriel → enregistrer l'alias (créé dans Exchange automatiquement)." -ForegroundColor Cyan
