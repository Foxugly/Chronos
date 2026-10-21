"""Le quota de dossiers : combien un compte peut en posséder.

La règle (décision du propriétaire, 2026-10-21) : un compte peut créer un
dossier tant que le nombre de dossiers dont il est **propriétaire** est
inférieur à `Profil.max_dossiers`. Les dossiers partagés avec lui ne
comptent pas. Pas d'exception pour le superutilisateur : la même règle pour
tous. Un compte sans profil a un quota de 0.

L'administration n'est pas soumise au quota : c'est d'elle qu'il se règle.
Toute autre création de dossier doit passer par `creer_un_dossier`.
"""

from dataclasses import dataclass

from django.db import transaction
from django.utils.translation import gettext as _

from .journal import consigner
from .models import Dossier, Journal, Profil


@dataclass(frozen=True)
class Quota:
    possedes: int
    maximum: int

    @property
    def atteint(self):
        return self.possedes >= self.maximum

    def message(self):
        """Pourquoi la création est refusée, à l'écran."""
        if self.maximum == 0:
            return _("La création de dossiers n'est pas activée pour votre compte.")
        return _("Vous avez atteint le nombre maximum de dossiers (%(maximum)d).") % {
            "maximum": self.maximum,
        }


class QuotaAtteint(Exception):
    def __init__(self, quota):
        super().__init__(quota.message())
        self.quota = quota


def quota_de(user, *, verrouiller=False):
    """Le quota de `user`. `verrouiller` pose un verrou sur son profil
    (à appeler dans une transaction) : deux créations simultanées ne
    peuvent pas, ensemble, dépasser le maximum."""
    if not user.is_authenticated:
        return Quota(possedes=0, maximum=0)
    profils = Profil.objects.filter(user=user)
    if verrouiller:
        profils = profils.select_for_update()
    maximum = profils.values_list("max_dossiers", flat=True).first() or 0
    return Quota(possedes=Dossier.objects.owned_by(user).count(), maximum=maximum)


def creer_un_dossier(user, nom, *, request=None):
    """Crée un dossier dont `user` est le propriétaire, dans la limite de son quota.

    Lève `QuotaAtteint` sans rien créer quand le quota est atteint.
    """
    with transaction.atomic():
        quota = quota_de(user, verrouiller=True)
        if quota.atteint:
            raise QuotaAtteint(quota)
        dossier = Dossier.objects.create(name=nom, owner=user)
        consigner(dossier, Journal.Action.DOSSIER_CREE, request=request, objet=dossier.name,
                  details={"modele": "dossier", "valeurs": {"name": dossier.name}})
    return dossier
