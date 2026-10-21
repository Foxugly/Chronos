"""Durée de conservation des invitations et des liens de partage morts (RGPD).

Audit du 2026-10-18, M8. Une invitation garde l'adresse de la personne invitée,
un lien de partage le nom de son destinataire : une fois qu'ils ne servent plus
(acceptée, annulée, expirée ; révoqué, expiré), rien ne justifie de les garder
indéfiniment. `manage.py purger_partages` (minuterie `chronos-purge.timer`) les
efface `RETENTION_PARTAGES_JOURS` jours après leur fin.

Ce qui reste, et pourquoi :

- **le journal** du dossier, en ajout seul : il garde l'invitation et le lien
  par leur libellé (`objet`), c'est la preuve de qui a eu accès ;
- **un lien qui a agi** dans le dossier (export, ajout, modification, retrait
  d'une pièce) : son entrée de journal dit « par le lien de partage X » en
  lisant le lien lui-même. L'effacer rendrait l'auteur de ces actes anonyme
  après coup — sur un dossier de preuves, ce n'est pas une purge, c'est une
  perte. Il reste, révoqué ou expiré, donc sans effet.
"""

from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import Invitation, Journal, ShareLink


def limite(maintenant=None):
    """Ce qui a pris fin avant ce moment est purgé."""
    return (maintenant or timezone.now()) - timedelta(days=settings.RETENTION_PARTAGES_JOURS)


def invitations_a_purger(maintenant=None):
    avant = limite(maintenant)
    return Invitation.objects.filter(
        Q(accepted_at__lt=avant)
        | Q(revoked_at__lt=avant)
        | Q(accepted_at__isnull=True, revoked_at__isnull=True, expires_at__lt=avant)
    )


def liens_a_purger(maintenant=None):
    avant = limite(maintenant)
    # Les liens au nom desquels le journal a noté un acte (auteur anonyme).
    ont_agi = Journal.objects.filter(lien__isnull=False, auteur__isnull=True).values("lien_id")
    return ShareLink.objects.filter(
        Q(revoked_at__lt=avant) | Q(revoked_at__isnull=True, expires_at__lt=avant)
    ).exclude(pk__in=ont_agi)


def purger(maintenant=None, essai=False):
    """Efface ce qui a dépassé la durée de conservation. Rend (invitations, liens)."""
    with transaction.atomic():
        invitations = invitations_a_purger(maintenant)
        liens = liens_a_purger(maintenant)
        compte = (invitations.count(), liens.count())
        if not essai:
            invitations.delete()
            liens.delete()
    return compte
