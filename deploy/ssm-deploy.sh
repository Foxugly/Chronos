#!/usr/bin/env bash
#
# Chronos — étape applicative du déploiement, lancée par SSM en tant que
# django (voir .github/workflows/deploy.yml), après le `git reset` sur
# origin/main et l'installation, par root, des unités et de nginx.
#
# Dépendances, migrations, fichiers statiques, redémarrage. Les catalogues de
# traduction (.mo) sont versionnés : pas de compilemessages ici.
set -euo pipefail
umask 027

APP=/var/www/django_websites/Chronos
ENV_FILE=/run/chronos/.env
cd "$APP"

if [ ! -x .venv/bin/python ]; then
  echo "ERROR: $APP/.venv absent — faire d'abord l'amorçage (deploy/README.md)." >&2
  exit 1
fi

.venv/bin/pip install --quiet -r requirements.txt

# Lecture littérale (clé=valeur), jamais `source` : une valeur contenant $( )
# ou des guillemets ne doit pas être interprétée par le shell (§3.11).
while IFS='=' read -r cle valeur || [ -n "$cle" ]; do
  case "$cle" in ''|\#*) continue ;; esac
  export "$cle=$valeur"
done < "$ENV_FILE"

.venv/bin/python manage.py migrate --noinput
.venv/bin/python manage.py collectstatic --noinput --verbosity 0
.venv/bin/python manage.py check --deploy --fail-level ERROR

sudo /bin/systemctl restart chronos-gunicorn
echo "[ssm-deploy] Chronos $(git rev-parse --short HEAD) déployé."
