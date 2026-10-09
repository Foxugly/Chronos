# Mise en service de Chronos sur la flotte (chronos.foxugly.com)

Référence : `OPERATIONS.md` (dépôt `Foxugly/foxugly-ops`), §3.12 « nouveau site », et
QuizOnline, l'implémentation de référence. Port **8010**, base PostgreSQL 16 locale,
application dans `/var/www/django_websites/Chronos` (la racine du dépôt est la racine Django).

Ce qui est versionné ici et installé **par le déploiement**, depuis le blob git validé :
unités `chronos-*.service|timer`, `/usr/local/sbin/chronos-env-fetch.sh`, vhost nginx.
Ce qui s'installe **une fois, à la main, par root** : sudoers, base, dossiers, premier clone.

> **Sécurité des preuves.** Le dossier ne contient que des pièces de litige. Aucune
> n'est servie en public : `/media/` répond 404, Django contrôle les droits puis
> délègue l'envoi à nginx par `X-Accel-Redirect` vers un `location internal;`.

---

## 1. GitHub

1. Dépôt **privé** `Foxugly/Chronos`, branche par défaut `main`.
2. Environnement `production` ; secrets : `AWS_DEPLOY_ROLE_ARN`, `EC2_INSTANCE_ID`.
3. Identifiant numérique du dépôt (pour la confiance OIDC) : `gh api repos/Foxugly/Chronos --jq .id`.
4. **Clé de déploiement en lecture seule** (étape 5). Le dépôt est privé, le seul de la flotte :
   les autres sont publics et récupérés en HTTPS anonyme, ce qui ne marche pas ici.

## 1 bis. DNS

Route 53, zone `foxugly.com` : enregistrement `A` `chronos` → `54.229.220.110` (comme `tm.foxugly.com`).
Le certificat `foxugly.com` est un joker (`*.foxugly.com`) : rien à émettre.

## 2. IAM (hors machine — jamais avec les clés root de la machine, §3.5)

1. Rôle **`chronos-deploy`**, confiance OIDC GitHub, en épinglant **les deux** formes de `sub` :
   ```json
   "token.actions.githubusercontent.com:sub": [
     "repo:Foxugly@3275928/Chronos@<repoId>:environment:production",
     "repo:Foxugly/Chronos:environment:production"
   ]
   ```
   Permissions : `ssm:SendCommand` sur l'instance et sur le document `AWS-RunShellScript`,
   `ssm:GetCommandInvocation`.
2. Rôle d'instance `foxugly-fleet-ec2` : **étendre** l'instruction `SsmReadFleetAppConfig` de la
   politique en ligne `foxugly-fleet-app-config` (jamais une nouvelle politique, §3.14) avec
   `…:parameter/chronos/prod` et `…:parameter/chronos/prod/*`, plus `kms:Decrypt`.

## 3. PostgreSQL (root, une fois)

```bash
sudo -u postgres psql <<'SQL'
CREATE ROLE chronos LOGIN PASSWORD '<mot de passe fort>';
CREATE DATABASE chronos OWNER chronos;
\c chronos
ALTER SCHEMA public OWNER TO chronos;
SQL
```

## 4. Paramètres SSM `/chronos/prod/*`

Noms nus (convention de la flotte). `SecureString` pour tout ce qui est secret.

| Paramètre | Type | Valeur |
|---|---|---|
| `SECRET_KEY` | SecureString | 50+ caractères aléatoires |
| `ALLOWED_HOSTS` | String | `chronos.foxugly.com` |
| `CSRF_TRUSTED_ORIGINS` | String | `https://chronos.foxugly.com` |
| `DB_ENGINE` | String | `postgresql` |
| `DB_NAME` / `DB_USER` | String | `chronos` |
| `DB_PASSWORD` | SecureString | celui de l'étape 3 |
| `DB_HOST` / `DB_PORT` | String | `127.0.0.1` / `5432` |
| `MEDIA_ROOT` | String | `/var/www/django_websites/Chronos/media` |
| `PIECES_X_ACCEL_PREFIX` | String | `/_pieces_internes/` |
| `GRAPH_TENANT_ID` | String | identifiant de l'organisation |
| `GRAPH_CLIENT_ID` | String | application Entra « Chronos » (§6) |
| `GRAPH_CLIENT_SECRET` | SecureString | son secret |
| `GRAPH_SENDER` | String | boîte expéditrice des invitations |
| `COURRIEL_BOITE` | String | boîte d'import (ex. `import-chronos@foxugly.com`) |
| `EXCHANGE_APP_ID` | String | application Entra « Chronos Exchange » (§6) |
| `EXCHANGE_TENANT` | String | le **domaine** (`foxugly.onmicrosoft.com`), pas le GUID |
| `EXCHANGE_CERT_PFX_B64` | SecureString | le PFX en base64 — décodé vers `/run/chronos/exchange.pfx` |
| `EXCHANGE_CERT_PASSWORD` | SecureString | mot de passe du PFX |
| `SENTRY_DSN` | String | projet `chronos-backend` (org `foxugly-srl`, de.sentry.io) |
| `SENTRY_ENVIRONMENT` | String | `production` |

Le PFX tient dans un paramètre standard (4 Ko) avec une clé **RSA 2048** ; en 4096 bits,
il faut le niveau avancé. Encodage : `base64 -w0 chronos-exchange.pfx`.

## 5. Machine (root, une fois)

```bash
# Dossiers
install -d -o django -g www-data -m 750 /var/log/chronos
install -d -o django -g www-data -m 700 /var/backups/chronos

# Clé de déploiement (dépôt privé) : paire propre à Chronos, en lecture seule sur GitHub.
sudo -u django ssh-keygen -t ed25519 -N '' -C chronos-deploy@foxugly -f ~django/.ssh/chronos_deploy
sudo -u django bash -c 'ssh-keyscan github.com >> ~/.ssh/known_hosts'
sudo -u django bash -c 'cat >> ~/.ssh/config' <<'CFG'
Host github-chronos
    HostName github.com
    User git
    IdentityFile ~/.ssh/chronos_deploy
    IdentitiesOnly yes
CFG
# Depuis le PC : gh repo deploy-key add chronos_deploy.pub -R Foxugly/Chronos -t chronos-machine
# (sans --allow-write : lecture seule)

# Premier clone et environnement, en django (§3.11 : amorçage)
sudo -u django bash -c 'umask 027; cd /var/www/django_websites && git clone github-chronos:Foxugly/Chronos.git'
sudo -u django bash -c 'cd /var/www/django_websites/Chronos && python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt'

# sudoers
install -o root -g root -m 0440 /var/www/django_websites/Chronos/deploy/sudoers/chronos-deploy /etc/sudoers.d/chronos-deploy
visudo -c

# Premier nginx (le déploiement le tient ensuite à jour)
cp /var/www/django_websites/Chronos/deploy/nginx/chronos.foxugly.com.conf /etc/nginx/sites-available/chronos.foxugly.com
ln -s /etc/nginx/sites-available/chronos.foxugly.com /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx

# Unités (le déploiement les réinstalle à chaque fois depuis git)
cd /var/www/django_websites/Chronos
for u in chronos-env-fetch.service chronos-gunicorn.service chronos-courriels.service \
         chronos-courriels.timer chronos-sauvegarde.service chronos-sauvegarde.timer; do
  git -c safe.directory=$PWD show HEAD:deploy/$u > /etc/systemd/system/$u
done
git -c safe.directory=$PWD show HEAD:deploy/fetch-env-from-ssm.sh > /usr/local/sbin/chronos-env-fetch.sh
chown root:root /usr/local/sbin/chronos-env-fetch.sh && chmod 0755 /usr/local/sbin/chronos-env-fetch.sh
systemctl daemon-reload
systemctl enable --now chronos-env-fetch.service chronos-gunicorn.service
systemctl enable --now chronos-sauvegarde.timer
# L'import des courriels seulement une fois la boîte et l'application Entra prêtes :
# systemctl enable --now chronos-courriels.timer
```

Rotation d'un secret : `systemctl restart chronos-env-fetch` **puis** `systemctl restart chronos-gunicorn`.

## 6. Microsoft 365

Deux applications Entra distinctes de celles de PushIT — Chronos ne détient jamais un secret
d'une autre application.

**« Chronos » — lire la boîte d'import, envoyer les invitations (Graph)**
- Permissions d'application `Mail.ReadWrite` et `Mail.Send`, consentement administrateur.
- Restreintes à la boîte d'import et à la boîte expéditrice : stratégie d'accès d'application
  Exchange (OPERATIONS.md §3.17) ou RBAC pour les applications
  (`docs/superpowers/import-courriels.md`).

**« Chronos Exchange » — gérer les alias (PowerShell)**, sur le modèle de PushIT
(`PushIT_server/exchange/README.md`, qui documente aussi les pièges) :
1. Certificat (RSA 2048) : `openssl req -x509 -newkey rsa:2048 -sha256 -days 730 -nodes -subj "/CN=Chronos-Exchange" -keyout c.key -out c.crt`
   puis `openssl pkcs12 -export -inkey c.key -in c.crt -out chronos-exchange.pfx`.
   Téléverser **le .crt seulement** dans l'application. Le PFX va dans SSM (§4), nulle part ailleurs.
2. Permission `Office 365 Exchange Online` → `Exchange.ManageAsApp`, consentement administrateur.
3. Dans Exchange Online (compte administrateur) :
   ```powershell
   New-ServicePrincipal -AppId <AppId> -ObjectId <Object ID de l'application d'entreprise> -DisplayName "Chronos Exchange"
   # Restreindre l'écriture à la seule boîte d'import (à valider : PushIT attribue le rôle sans restriction)
   New-ManagementScope -Name "Chronos boite import" -RecipientRestrictionFilter "PrimarySmtpAddress -eq 'import-chronos@foxugly.com'"
   New-ManagementRoleAssignment -App <AppId> -Role "Mail Recipients" -CustomRecipientWriteScope "Chronos boite import"
   Get-ManagementRoleAssignment -RoleAssignee <AppId>
   ```
   Compter 5 à 15 minutes de propagation.
4. PowerShell 7 et le module `ExchangeOnlineManagement` sont déjà installés sur la machine (PushIT).

## 7. Transfert des données (une fois, depuis le PC)

Les 30 évènements et 77 pièces — l'unique copie. Rien n'est effacé du PC.

```powershell
# Sur le PC
$env:DJANGO_DEBUG="True"; $env:PYTHONUTF8="1"
.\.venv\Scripts\python.exe manage.py sauvegarder                 # filet de sécurité
.\.venv\Scripts\python.exe manage.py verifier_integrite > avant.txt
.\.venv\Scripts\python.exe manage.py dumpdata --natural-foreign --natural-primary `
    -e contenttypes -e auth.permission -e sessions -e admin.logentry -o chronos.json
# Copier chronos.json et le dossier media\ sur la machine (scp), dans un dossier temporaire.
```

```bash
# Sur la machine, en django, avec l'environnement de /run/chronos/.env chargé
cp -a /tmp/transfert/media/. /var/www/django_websites/Chronos/media/
.venv/bin/python manage.py migrate
.venv/bin/python manage.py loaddata /tmp/transfert/chronos.json
.venv/bin/python manage.py verifier_integrite > apres.txt     # comparer avec avant.txt : identique
.venv/bin/python manage.py sauvegarder --destination /var/backups/chronos
shred -u /tmp/transfert/chronos.json && rm -rf /tmp/transfert
```

Répété le 2026-10-09 vers une base neuve : contrôle d'intégrité identique, numéros de pièces,
compteur, journal et descriptions intacts.

## 8. Après la mise en service

- UptimeRobot : moniteur HTTPS sur `https://chronos.foxugly.com/health/` (300 s, mot-clé `"ok"`).
- Sentry : projet `chronos-backend`.
- `OPERATIONS.md` : ligne d'inventaire (§2), ligne dans la table des rôles (§3.11).
- Vérifications §3.12 : `/health/` 200 ;
  `sudo find /var/www/django_websites/Chronos ! -type l \( -perm /020 -o -perm /004 \)` vide ;
  `sudo -l -U django` ne montre que les autorisations attendues ;
  `curl -I https://chronos.foxugly.com/media/documents/x.pdf` → 404.
