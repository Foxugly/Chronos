# Import de courriels — mise en place côté Microsoft 365

L'application lit une boîte Exchange Online par **Microsoft Graph**. Exchange Online n'accepte plus
l'IMAP par mot de passe : il faut une « application » déclarée dans Entra ID. À faire **une fois**.

> **À vérifier au moment de le faire.** Microsoft fait évoluer ces écrans et ces commandes. Les étapes
> ci-dessous sont justes à la rédaction (octobre 2026) ; si un intitulé a changé, l'intention reste la
> même : une application, un secret, et un accès limité à **une seule** boîte.

## 1. Déclarer l'application (Entra ID)

1. <https://entra.microsoft.com> → **Applications** → **Inscriptions d'applications** → **Nouvelle
   inscription**. Nom : `Chronos import`. Comptes : *ce répertoire uniquement*. Pas d'URI de
   redirection.
2. Notez l'**ID d'application (client)** et l'**ID de l'annuaire (locataire)**.
3. **Certificats et secrets** → **Nouveau secret client** → copiez la **valeur** tout de suite (elle
   ne se réaffiche pas). Notez sa date d'expiration : à son terme, l'import s'arrête.

## 2. Limiter l'application à la seule boîte d'import — indispensable

Une permission `Mail.ReadWrite` d'application ouvre, par défaut, **toutes** les boîtes du tenant. On la
restreint à la boîte d'import, avec Exchange Online PowerShell :

```powershell
Install-Module ExchangeOnlineManagement   # une fois
Connect-ExchangeOnline
```

**Méthode recommandée — RBAC pour les applications** (ne PAS accorder `Mail.ReadWrite` dans Entra) :

```powershell
# L'ID d'objet est celui de l'« Application d'entreprise » (et non de l'inscription).
New-ServicePrincipal -AppId <ID client> -ObjectId <ID d'objet de l'application d'entreprise> -DisplayName "Chronos import"
New-ManagementScope -Name "Chronos boite" -RecipientRestrictionFilter "PrimarySmtpAddress -eq 'import@votredomaine.be'"
New-ManagementRoleAssignment -App <ID client> -Role "Application Mail.ReadWrite" -CustomResourceScope "Chronos boite"

# Vérification : InScope = True pour la boîte d'import, False pour une autre.
Test-ServicePrincipalAuthorization -Identity <ID client> -Resource import@votredomaine.be
Test-ServicePrincipalAuthorization -Identity <ID client> -Resource quelquun@votredomaine.be
```

*Méthode ancienne* (si la précédente n'est pas disponible) : accorder `Mail.ReadWrite` (type
**Application**) dans Entra avec consentement administrateur, puis
`New-ApplicationAccessPolicy -AppId <ID client> -PolicyScopeGroupId <groupe ne contenant que la boîte> -AccessRight RestrictAccess`
et vérifier avec `Test-ApplicationAccessPolicy`.

## 3. Créer les alias des dossiers

Un alias par dossier, sur la boîte d'import. Choisissez-le difficile à deviner : **quiconque le connaît
peut ajouter un évènement au dossier**.

```powershell
Set-Mailbox import@votredomaine.be -EmailAddresses @{add="dossier-sdb-k7f3q9@votredomaine.be"}
```

Puis, dans l'application : page **Paramètres** du dossier (roue dentée dans l'en-tête) → **Import par courriel** → saisir l'alias. La page propose une adresse difficile à deviner et rappelle la commande ci-dessus.

## 4. Donner les identifiants à l'application

Variables d'environnement de l'utilisateur Windows qui lance la tâche (jamais dans le code) :

```powershell
setx GRAPH_TENANT_ID "<ID de l'annuaire>"
setx GRAPH_CLIENT_ID "<ID client>"
setx GRAPH_CLIENT_SECRET "<valeur du secret>"
setx COURRIEL_BOITE "import@votredomaine.be"
```

Ouvrez un nouveau terminal, puis un essai qui ne crée ni ne déplace rien :

```powershell
.\scripts\importer_courriels.ps1 -Essai
```

## 5. Lancer l'import toutes les 10 minutes

```powershell
$action = New-ScheduledTaskAction -Execute "pwsh.exe" -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"D:\Projects\PycharmProjects\Chronos\scripts\importer_courriels.ps1`"" -WorkingDirectory "D:\Projects\PycharmProjects\Chronos"
$declencheur = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 10)
$reglages = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 9)
Register-ScheduledTask -TaskName "Chronos - courriels" -Action $action -Trigger $declencheur -Settings $reglages
```

Le résultat de chaque passage s'ajoute à `sauvegardes\courriels.log`.

## Transférer un courriel

- **Le mieux : « Transférer en tant que pièce jointe »** (Outlook : *Plus d'actions* → *Transférer en
  tant que pièce jointe* ; Ctrl+Alt+F dans Outlook pour Windows). Le courriel d'origine voyage intact :
  l'évènement prend sa **date de réception** exacte, son expéditeur, son objet.
- **Un transfert normal fonctionne aussi** : l'application lit le bloc « De : … Envoyé : … » (français,
  anglais, néerlandais, Gmail). Seule la date d'**envoi** de l'original y figure, à quelques secondes
  de sa réception.
- Le texte que vous écrivez au-dessus d'un transfert est gardé, à part, dans la version `.md`.

Dans la boîte : un courriel importé est rangé dans **Importés** ; un courriel qui ne vise aucun alias
(ou illisible) dans **Non importés**. Rien n'est supprimé.
