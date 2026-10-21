"""La vérification d'adresse devient obligatoire (2026-10-21) : les
superutilisateurs ne doivent pas se retrouver bloqués à la connexion.

`ACCOUNT_EMAIL_VERIFICATION = "mandatory"` refuse la connexion à un compte
sans adresse vérifiée. En production, le compte du propriétaire (superutilisateur,
créé par `createsuperuser`) n'a aucune ligne `EmailAddress` vérifiée. Pour chaque
superutilisateur :

- s'il a une adresse principale, elle est marquée vérifiée ;
- sinon, l'adresse de `user.email` devient sa principale, vérifiée — la ligne
  existante pour cette adresse si elle existe, une nouvelle sinon ;
- sans `user.email` ni adresse principale, rien n'est possible : il passe.

Une adresse déjà vérifiée par un **autre** compte n'est pas reprise (allauth
interdit deux adresses vérifiées identiques) : le superutilisateur passe.
"""

from django.conf import settings
from django.db import migrations


def verifier_les_superutilisateurs(apps, schema_editor):
    Utilisateur = apps.get_model(settings.AUTH_USER_MODEL)
    EmailAddress = apps.get_model("account", "EmailAddress")
    for utilisateur in Utilisateur.objects.filter(is_superuser=True):
        principale = EmailAddress.objects.filter(user=utilisateur, primary=True).first()
        if principale is not None:
            if not principale.verified:
                principale.verified = True
                principale.save(update_fields=["verified"])
            continue
        adresse = (utilisateur.email or "").strip().lower()
        if not adresse:
            continue
        if EmailAddress.objects.filter(email__iexact=adresse, verified=True).exclude(
            user=utilisateur
        ).exists():
            continue
        existante = EmailAddress.objects.filter(user=utilisateur, email__iexact=adresse).first()
        if existante is not None:
            existante.primary = True
            existante.verified = True
            existante.save(update_fields=["primary", "verified"])
        else:
            EmailAddress.objects.create(
                user=utilisateur, email=adresse, primary=True, verified=True,
            )


class Migration(migrations.Migration):

    dependencies = [
        ("events", "0025_quota_du_proprietaire"),
        ("account", "0009_emailaddress_unique_primary_email"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # En arrière, rien : « dé-vérifier » une adresse n'a pas de sens.
        migrations.RunPython(verifier_les_superutilisateurs, migrations.RunPython.noop),
    ]
