#!/usr/bin/env bash
# IAM de Chronos, à coller dans AWS CloudShell (eu-west-1) — jamais depuis la machine (§3.5).
#
# 1. Crée le rôle chronos-deploy (OIDC GitHub, confiance épinglée sur Foxugly/Chronos,
#    environnement production, les DEUX formes de `sub` — OPERATIONS.md §3.11 GOTCHA).
# 2. Étend l'instruction SsmReadFleetAppConfig de la politique en ligne
#    foxugly-fleet-app-config du rôle d'instance foxugly-fleet-ec2 (§3.14 : jamais une
#    nouvelle politique) avec /chronos/prod. Copie de l'ancienne politique gardée.
#
# Ré-exécutable : un rôle existant est mis à jour, une ressource déjà présente n'est pas doublée.
set -euo pipefail

REPO=Chronos
REPO_ID=1411693515          # gh api repos/Foxugly/Chronos --jq .id
OWNER_ID=3275928            # Foxugly
INSTANCE_ID=i-0fe664678563bae5f
REGION=eu-west-1
ROLE=chronos-deploy
FLEET_ROLE=foxugly-fleet-ec2
FLEET_POLICY=foxugly-fleet-app-config

ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
OIDC="arn:aws:iam::${ACCOUNT}:oidc-provider/token.actions.githubusercontent.com"
aws iam get-open-id-connect-provider --open-id-connect-provider-arn "$OIDC" >/dev/null

cat > /tmp/chronos-trust.json <<JSON
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {"Federated": "${OIDC}"},
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {
      "StringEquals": {
        "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
        "token.actions.githubusercontent.com:sub": [
          "repo:Foxugly@${OWNER_ID}/${REPO}@${REPO_ID}:environment:production",
          "repo:Foxugly/${REPO}:environment:production"
        ]
      }
    }
  }]
}
JSON

cat > /tmp/chronos-deploy-ssm.json <<JSON
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "SendDeployCommand",
      "Effect": "Allow",
      "Action": "ssm:SendCommand",
      "Resource": [
        "arn:aws:ec2:${REGION}:${ACCOUNT}:instance/${INSTANCE_ID}",
        "arn:aws:ssm:${REGION}::document/AWS-RunShellScript"
      ]
    },
    {
      "Sid": "ReadDeployResult",
      "Effect": "Allow",
      "Action": "ssm:GetCommandInvocation",
      "Resource": "*"
    }
  ]
}
JSON

if aws iam get-role --role-name "$ROLE" >/dev/null 2>&1; then
  aws iam update-assume-role-policy --role-name "$ROLE" --policy-document file:///tmp/chronos-trust.json
else
  aws iam create-role --role-name "$ROLE" \
    --description "GitHub Actions deploy for Foxugly/Chronos (SSM only)" \
    --assume-role-policy-document file:///tmp/chronos-trust.json >/dev/null
fi
aws iam put-role-policy --role-name "$ROLE" --policy-name chronos-deploy-ssm \
  --policy-document file:///tmp/chronos-deploy-ssm.json

# --- Rôle d'instance : lecture de /chronos/prod ---
BACKUP=~/foxugly-fleet-app-config.$(date +%Y%m%d-%H%M%S).json
aws iam get-role-policy --role-name "$FLEET_ROLE" --policy-name "$FLEET_POLICY" \
  --query PolicyDocument --output json > "$BACKUP"

python3 - "$BACKUP" "$ACCOUNT" "$REGION" > /tmp/fleet-policy.json <<'PY'
import json, sys
chemin, compte, region = sys.argv[1:]
doc = json.load(open(chemin))
cible = [s for s in doc["Statement"] if s.get("Sid") == "SsmReadFleetAppConfig"]
if len(cible) != 1:
    sys.exit("Instruction SsmReadFleetAppConfig introuvable ou en double : rien n'est modifié.")
s = cible[0]
res = s["Resource"] if isinstance(s["Resource"], list) else [s["Resource"]]
for r in (f"arn:aws:ssm:{region}:{compte}:parameter/chronos/prod",
          f"arn:aws:ssm:{region}:{compte}:parameter/chronos/prod/*"):
    if r not in res:
        res.append(r)
s["Resource"] = res
print(json.dumps(doc, indent=2))
PY

echo "--- Différence sur ${FLEET_POLICY} (ancienne copie : ${BACKUP}) ---"
diff <(python3 -m json.tool "$BACKUP") /tmp/fleet-policy.json || true
aws iam put-role-policy --role-name "$FLEET_ROLE" --policy-name "$FLEET_POLICY" \
  --policy-document file:///tmp/fleet-policy.json

# kms:Decrypt : les SecureString utilisent la clé aws/ssm ; vérifier qu'une instruction
# de la politique la couvre déjà (les autres sites lisent leurs SecureString).
echo "--- Instructions KMS de ${FLEET_POLICY} ---"
python3 -c "import json,sys; d=json.load(open(sys.argv[1])); [print(json.dumps(s)) for s in d['Statement'] if any('kms' in a for a in ([s['Action']] if isinstance(s['Action'],str) else s['Action']))]" /tmp/fleet-policy.json

echo
echo "AWS_DEPLOY_ROLE_ARN = $(aws iam get-role --role-name "$ROLE" --query Role.Arn --output text)"
