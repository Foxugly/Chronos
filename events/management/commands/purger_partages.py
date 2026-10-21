"""Efface les invitations et liens de partage morts depuis plus de RETENTION_PARTAGES_JOURS jours.

Lancée chaque nuit en production par `chronos-purge.timer` (deploy/). Règles et
exceptions : `events/retention.py`. `--essai` compte sans rien effacer.
"""

from django.conf import settings
from django.core.management.base import BaseCommand

from events.retention import purger


class Command(BaseCommand):
    help = "Efface les invitations et liens de partage morts (durée de conservation dépassée)."

    def add_arguments(self, parser):
        parser.add_argument("--essai", action="store_true", help="Compte sans rien effacer.")

    def handle(self, *args, essai=False, **options):
        invitations, liens = purger(essai=essai)
        verbe = "à effacer" if essai else "effacés"
        # Des nombres seulement : ni adresse, ni libellé dans le journal du serveur.
        self.stdout.write(
            "Purge (%d jours) : %d invitation(s), %d lien(s) %s."
            % (settings.RETENTION_PARTAGES_JOURS, invitations, liens, verbe)
        )
