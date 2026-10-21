"""Le quota de dossiers du propriétaire du site (décision du 2026-10-21).

Le compte dont l'adresse est `rvilain@foxugly.com` (casse ignorée) reçoit
`max_dossiers = 5`. Tous les autres restent à 0, y compris ceux qui possèdent
déjà des dossiers : ils les gardent, mais n'en créent plus. Aucun compte
avec cette adresse : la migration ne fait rien.
"""

from django.conf import settings
from django.db import migrations

ADRESSE_DU_PROPRIETAIRE = "rvilain@foxugly.com"
QUOTA_DU_PROPRIETAIRE = 5


def donner_le_quota(apps, schema_editor):
    Utilisateur = apps.get_model(settings.AUTH_USER_MODEL)
    Profil = apps.get_model("events", "Profil")
    for utilisateur in Utilisateur.objects.filter(email__iexact=ADRESSE_DU_PROPRIETAIRE):
        Profil.objects.update_or_create(
            user=utilisateur, defaults={"max_dossiers": QUOTA_DU_PROPRIETAIRE},
        )


class Migration(migrations.Migration):

    dependencies = [
        ("events", "0024_profil"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # En arrière, rien à défaire : `0024` supprime la table des profils.
        migrations.RunPython(donner_le_quota, migrations.RunPython.noop),
    ]
