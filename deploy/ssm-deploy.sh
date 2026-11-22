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

# Le venv doit tourner sur $PYTHON (python3.14 de deadsnakes, à root, dans
# /usr/bin). Un venv sur une autre version est reconstruit À SA PLACE — jamais
# construit ailleurs puis déplacé : les scripts console (.venv/bin/gunicorn, que
# lance l'unité) ont le chemin du venv écrit en dur dans leur shebang. L'ancien
# est gardé en .venv.prev et remis en place si la reconstruction échoue.
VENV="$APP/.venv"
PYTHON=/usr/bin/python3.14
PY_WANT="$("$PYTHON" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
PY_HAVE="$("$VENV/bin/python" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo none)"
if [ "$PY_HAVE" != "$PY_WANT" ]; then
  echo ">>> Reconstruction du venv : Python $PY_HAVE -> $PY_WANT..."
  rm -rf "$VENV.prev"
  if [ -e "$VENV" ]; then mv "$VENV" "$VENV.prev"; fi
  if ! { "$PYTHON" -m venv "$VENV"          && "$VENV/bin/pip" install --quiet --require-hashes --no-deps -r requirements.txt          && "$VENV/bin/pip" check; }; then
    echo "ERROR: reconstruction du venv ratée ; retour à l'ancien venv." >&2
    rm -rf "$VENV"
    if [ -e "$VENV.prev" ]; then mv "$VENV.prev" "$VENV"; fi
    exit 1
  fi
fi

# requirements.txt est un verrou haché (généré par uv depuis requirements.in) :
# la production installe exactement les versions que la CI a testées. Sans
# verrou, `pip install -r` ne mettait jamais à jour une version déjà installée
# encore dans sa plage, et la box ratait les correctifs. --no-deps : tout est
# dans le verrou ; pip check vérifie qu'il est cohérent.
.venv/bin/pip install --quiet --require-hashes --no-deps -r requirements.txt
.venv/bin/pip check

if [ ! -s "$ENV_FILE" ]; then
  # Sans configuration, migrate et collectstatic ne peuvent pas tourner : on
  # échoue ici, en clair, plutôt que de laisser un déploiement à moitié fait.
  echo "ERROR: $ENV_FILE absent ou vide — chronos-env-fetch a-t-il tourné ? (systemctl status chronos-env-fetch)" >&2
  exit 1
fi

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
