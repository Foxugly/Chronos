"""Étiquettes d'évènements : les poser depuis le formulaire, les proposer.

Une étiquette appartient à un dossier (`Etiquette.dossier`) : le formulaire ne
propose et n'accepte que celles du dossier de l'évènement. Un identifiant
d'étiquette d'un autre dossier, posté à la main, est ignoré.

Le formulaire envoie un marqueur `etiquettes_presentes` : sans lui (un POST
direct, un vieux formulaire en cache), les étiquettes de l'évènement ne sont
pas touchées — on ne vide pas ce qu'on n'a pas montré.
"""

from django.utils.translation import gettext as _

from .models import Etiquette

LONGUEUR_MAX = 60


def suggestions():
    """Des étiquettes à proposer tant que le dossier ne les a pas créées."""
    return [_("Devis"), _("Facture"), _("Paiement"), _("Photo du chantier"), _("Courrier"),
            _("Mise en demeure"), _("Avis d'expert")]


def appliquer(evenement, donnees):
    """Pose sur `evenement` les étiquettes cochées et celles saisies.

    `donnees` : le `request.POST`. Les nouvelles étiquettes, séparées par des
    virgules, sont créées dans le dossier ; une étiquette existante de même
    nom (sans tenir compte des majuscules) est réutilisée plutôt que doublée.
    Rend True si quelque chose a été appliqué.
    """
    if not donnees.get("etiquettes_presentes"):
        return False
    dossier = evenement.dossier
    ids = [int(v) for v in donnees.getlist("etiquettes") if str(v).isdigit()]
    choisies = list(dossier.etiquettes.filter(pk__in=ids))
    for nom in (donnees.get("nouvelles_etiquettes") or "").split(","):
        nom = " ".join(nom.split())[:LONGUEUR_MAX]
        if not nom:
            continue
        existante = dossier.etiquettes.filter(nom__iexact=nom).first()
        choisies.append(existante or Etiquette.objects.create(dossier=dossier, nom=nom))
    evenement.etiquettes.set({e.pk: e for e in choisies}.values())
    return True


def contexte_du_formulaire(dossier, evenement, donnees=None):
    """Ce que le formulaire affiche : les étiquettes du dossier, celles
    cochées (reprises du POST si le formulaire revient avec une erreur), et
    des suggestions pour celles qui n'existent pas encore."""
    existantes = list(dossier.etiquettes.all()) if dossier is not None else []
    if donnees is not None and donnees.get("etiquettes_presentes"):
        cochees = {int(v) for v in donnees.getlist("etiquettes") if str(v).isdigit()}
        nouvelles = donnees.get("nouvelles_etiquettes", "")
    else:
        cochees = set(evenement.etiquettes.values_list("pk", flat=True)) if evenement and evenement.pk else set()
        nouvelles = ""
    noms = {e.nom.casefold() for e in existantes}
    suggerees = [s for s in suggestions() if s.casefold() not in noms]
    return {
        "etiquettes_du_dossier": existantes,
        "etiquettes_cochees": cochees,
        "nouvelles_etiquettes": nouvelles,
        "suggestions_etiquettes": suggerees,
        # L'autocomplétion du champ : d'abord les étiquettes du dossier, puis les
        # suggestions qu'il n'a pas encore.
        "propositions_etiquettes": [e.nom for e in existantes] + suggerees,
    }
