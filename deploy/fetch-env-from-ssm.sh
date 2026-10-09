#!/usr/bin/env bash
#
# Chronos — récupère la configuration depuis SSM Parameter Store vers /run.
#
# Repris de QuizOnline (deploy/fetch-env-from-ssm.sh), même contrat :
# lancé au démarrage par chronos-env-fetch.service (root, oneshot), il écrit
# /run/chronos/.env (tmpfs : rien sur disque, rien dans un instantané EBS),
# atomiquement (fichier .tmp puis mv), et refuse de remplacer un .env valide
# par un fichier vide.
#
# Une différence : le certificat Exchange. Le paramètre EXCHANGE_CERT_PFX_B64
# (le PFX encodé en base64, SecureString) n'est pas recopié dans .env : il est
# décodé en /run/chronos/exchange.pfx (640 django:www-data), et la ligne
# EXCHANGE_CERT_FILE_PATH pointe dessus. Le certificat ne vit donc, comme les
# autres secrets, que dans /run — jamais dans l'arborescence de l'application.
#
# Installé par le déploiement en /usr/local/sbin/chronos-env-fetch.sh
# (root:root 0755), depuis le blob git validé — jamais exécuté depuis
# l'arborescence modifiable par django (OPERATIONS.md §3.10).
set -euo pipefail

PREFIX="${CHRONOS_SSM_PREFIX:-/chronos/prod}"
REGION="${CHRONOS_SSM_REGION:-eu-west-1}"
OUT_DIR="${CHRONOS_ENV_DIR:-/run/chronos}"
OUT_FILE="${CHRONOS_ENV_FILE:-$OUT_DIR/.env}"
PFX_FILE="$OUT_DIR/exchange.pfx"
OWNER="${CHRONOS_ENV_OWNER:-django:www-data}"

echo "[fetch-env] prefix=$PREFIX region=$REGION out=$OUT_FILE"

mkdir -p "$OUT_DIR"
chmod 750 "$OUT_DIR"
chown root:www-data "$OUT_DIR"

TMP_FILE="$OUT_FILE.tmp"
TMP_PFX="$PFX_FILE.tmp"
: > "$TMP_FILE"
chmod 640 "$TMP_FILE"
rm -f "$TMP_PFX"

OUT=$(aws ssm get-parameters-by-path \
  --region "$REGION" \
  --path "$PREFIX" \
  --recursive \
  --with-decryption \
  --output json)

python3 -c '
import base64, json, os, sys
prefix, out_path, pfx_tmp, pfx_final, payload = sys.argv[1:6]
data = json.loads(payload)
n = 0
with open(out_path, "a", encoding="utf-8") as f:
    for p in data.get("Parameters", []):
        name = p["Name"]
        key = name[len(prefix) + 1:] if name.startswith(prefix + "/") else name.rsplit("/", 1)[-1]
        value = p["Value"]
        if key == "EXCHANGE_CERT_PFX_B64":
            fd = os.open(pfx_tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
            with os.fdopen(fd, "wb") as pfx:
                pfx.write(base64.b64decode(value, validate=True))
            f.write(f"EXCHANGE_CERT_FILE_PATH={pfx_final}\n")
            n += 1
            continue
        if "\n" in value or "\r" in value:
            print(f"ERROR: value of {key} contains a newline character", file=sys.stderr)
            sys.exit(2)
        f.write(f"{key}={value}\n")
        n += 1
print(f"[fetch-env] wrote {n} entries to staging file", file=sys.stderr)
' "$PREFIX" "$TMP_FILE" "$TMP_PFX" "$PFX_FILE" "$OUT"

COUNT=$(grep -c '=' "$TMP_FILE" || true)
if [ "$COUNT" -eq 0 ]; then
  echo "ERROR: no parameters found under $PREFIX — refusing to overwrite live .env" >&2
  rm -f "$TMP_FILE" "$TMP_PFX"
  exit 3
fi

if [ -f "$TMP_PFX" ]; then
  chown "$OWNER" "$TMP_PFX"
  chmod 640 "$TMP_PFX"
  mv -f "$TMP_PFX" "$PFX_FILE"
fi
chown "$OWNER" "$TMP_FILE"
chmod 640 "$TMP_FILE"
mv -f "$TMP_FILE" "$OUT_FILE"
echo "[fetch-env] $OUT_FILE ready ($COUNT entries, owner=$OWNER, mode=0640)"
