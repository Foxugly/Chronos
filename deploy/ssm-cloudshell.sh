#!/usr/bin/env bash
# Paramètres SSM minimaux de Chronos (/chronos/prod/*), à coller dans AWS CloudShell (eu-west-1).
#
# Le minimum pour que le site démarre (deploy/README.md §4). Graph, Exchange et Sentry
# viennent plus tard, avec le même `aws ssm put-parameter`.
#
# - SECRET_KEY est générée ici et n'est JAMAIS remplacée si elle existe déjà : la changer
#   déconnecterait tout le monde et invaliderait les liens signés.
# - DB_PASSWORD est demandé sans écho : sur le serveur, `sudo cat /root/chronos-db-password`.
# - Aucune valeur secrète n'est affichée ; la fin liste les noms et les types.
#
# Ré-exécutable : les paramètres non secrets sont réécrits à l'identique.
set -euo pipefail

REGION=eu-west-1
P=/chronos/prod

put() {  # put <nom> <type> <valeur>
  aws ssm put-parameter --region "$REGION" --name "$P/$1" --type "$2" --value "$3" \
    --overwrite --no-cli-pager >/dev/null
  echo "  $P/$1 ($2)"
}

read -r -p "GRAPH_SENDER (boîte expéditrice des invitations, ex. chronos@foxugly.com) : " GRAPH_SENDER
[[ "$GRAPH_SENDER" == *@* ]] || { echo "Adresse invalide." >&2; exit 1; }

read -r -s -p "DB_PASSWORD (sortie de sudo cat /root/chronos-db-password) : " DB_PASSWORD; echo
[ "${#DB_PASSWORD}" -ge 20 ] || { echo "Mot de passe trop court : mauvais copier-coller ?" >&2; exit 1; }

echo "Écriture :"
if aws ssm get-parameter --region "$REGION" --name "$P/SECRET_KEY" >/dev/null 2>&1; then
  echo "  $P/SECRET_KEY existe déjà : conservée"
else
  SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(64))")
  aws ssm put-parameter --region "$REGION" --name "$P/SECRET_KEY" --type SecureString \
    --value "$SECRET_KEY" --no-cli-pager >/dev/null
  unset SECRET_KEY
  echo "  $P/SECRET_KEY (SecureString, générée)"
fi
put DB_PASSWORD           SecureString "$DB_PASSWORD"; unset DB_PASSWORD
put ALLOWED_HOSTS         String "chronos.foxugly.com"
put CSRF_TRUSTED_ORIGINS  String "https://chronos.foxugly.com"
put DB_ENGINE             String "postgresql"
put DB_NAME               String "chronos"
put DB_USER               String "chronos"
put DB_HOST               String "127.0.0.1"
put DB_PORT               String "5432"
put MEDIA_ROOT            String "/var/www/django_websites/Chronos/media"
put PIECES_X_ACCEL_PREFIX String "/_pieces_internes/"
put GRAPH_SENDER          String "$GRAPH_SENDER"

echo
echo "Contenu de $P (noms et types seulement) :"
aws ssm get-parameters-by-path --region "$REGION" --path "$P" --recursive \
  --query 'Parameters[].[Name,Type]' --output table --no-cli-pager
