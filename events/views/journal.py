"""Le journal d'un dossier et l'export."""

import datetime
import html
import re

from django.http import FileResponse, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.utils.formats import date_format, time_format
from django.utils.html import strip_tags
from django.utils.text import slugify
from django.utils.translation import gettext as _

from .. import filtres as filtrage
from ..export import construire_export
from ..journal import consigner
from ..models import (
    CourrielImporte,
    Document,
    Dossier,
    DossierAccess,
    Event,
    Expediteur,
    Journal,
)
from ..permissions import get_dossier_for_read


def _valeur_lisible(champ, valeur):
    """Une valeur du journal telle qu'on la lit : date et heure au format de
    la langue courante, description en texte (le HTML assaini n'a pas sa
    place dans un tableau d'historique), « — » pour une valeur vide."""
    if valeur in (None, ""):
        return "—"
    if champ in ("date", "date_fin"):
        return date_format(datetime.date.fromisoformat(valeur), "SHORT_DATE_FORMAT")
    if champ in ("heure", "heure_fin"):
        return time_format(datetime.time.fromisoformat(valeur), "TIME_FORMAT")
    if champ == "description":
        # Une fin de paragraphe ou de ligne devient un saut de ligne : sans
        # cela, deux paragraphes se liraient collés une fois le HTML retiré.
        texte = re.sub(r"</(p|li|h[1-6])>|<br\s*/?>", "\n", valeur)
        # Le HTML assaini code les apostrophes en entités (`&#x27;`) :
        # décodées ici, Django les ré-échappe à l'affichage.
        return html.unescape(strip_tags(texte)).strip() or "—"
    if champ == "role" and valeur in DossierAccess.Role.values:
        return DossierAccess.Role(valeur).label
    return valeur


#: D'où viennent les noms de champs d'une entrée : un évènement par défaut,
#: un dossier, un accès ou une pièce pour ce qui a été modifié dans l'admin.
MODELES_DU_JOURNAL = {
    "expediteur": Expediteur,
    "evenement": Event,
    "dossier": Dossier,
    "acces": DossierAccess,
    "document": Document,
}


def _nom_du_champ(modele, champ):
    if champ == "etiquettes":
        return _("étiquettes")
    try:
        return modele._meta.get_field(champ).verbose_name
    except Exception:  # champ disparu depuis : on montre son nom brut
        return champ


def _ligne_de_journal(entree, evenements_importes=frozenset()):
    details = entree.details or {}
    modele = MODELES_DU_JOURNAL.get(details.get("modele"), Event)
    champs = details.get("changements")
    valeurs = details.get("valeurs")
    modifications = []
    if champs:
        for champ, (avant, apres) in champs.items():
            modifications.append({
                "champ": _nom_du_champ(modele, champ),
                "avant": _valeur_lisible(champ, avant),
                "apres": _valeur_lisible(champ, apres),
            })
    elif valeurs:
        for champ, valeur in valeurs.items():
            if valeur not in (None, ""):
                modifications.append({
                    "champ": _nom_du_champ(modele, champ),
                    "avant": None,
                    "apres": _valeur_lisible(champ, valeur),
                })
    role = details.get("role")
    return {
        "entree": entree,
        "modifications": modifications,
        # Deux colonnes, avant et après, pour une modification ; une seule
        # pour une création ou une suppression.
        "comparaison": bool(champs),
        "role": DossierAccess.Role(role).label if role in DossierAccess.Role.values else None,
        "empreinte": details.get("empreinte"),
        "deja_presente": details.get("deja_presente"),
        "evenement": details.get("evenement"),
        "par_import": _par_import(entree, details, evenements_importes),
    }


def _par_import(entree, details, evenements_importes):
    """Vrai pour une entrée écrite par l'import de courriels, qui n'a pas d'auteur.

    Les entrées récentes portent `source: "courriel"`. Les premières (avant ce
    marqueur) se reconnaissent à ce qu'elles n'ont ni auteur, ni lien, ni
    administration, et touchent un évènement importé — le journal ne se
    réécrit pas, on les lit donc ainsi.
    """
    if entree.auteur_id or entree.lien_id or entree.via_admin:
        return False
    if details.get("source") == "courriel" or entree.action == Journal.Action.COURRIEL_IMPORTE:
        return True
    return entree.evenement_id is not None and entree.evenement_id in evenements_importes


def dossier_journal(request, dossier_id):
    """Ce qui s'est passé dans le dossier, du plus récent au plus ancien.

    Lisible par quiconque lit le dossier. Les entrées sur le partage — accès,
    invitations, liens — ne sont montrées qu'au propriétaire, seul à le gérer
    (invariant 5) : un collaborateur n'a pas à apprendre qui d'autre a accès.
    """
    dossier = get_dossier_for_read(request.user, dossier_id)
    return render(
        request,
        "events/journal.html",
        {"dossier": dossier, "lignes": lignes_du_journal(dossier, request.user)},
    )


def lignes_du_journal(dossier, utilisateur):
    """Le journal tel que `utilisateur` peut le lire : les entrées de partage
    pour le seul propriétaire (invariant 5). Sert la page et l'export."""
    entrees = dossier.journal.select_related("auteur", "lien")
    if dossier.owner_id != utilisateur.id:
        entrees = entrees.exclude(action__in=Journal.ACTIONS_DE_PARTAGE)
    importes = frozenset(
        CourrielImporte.objects.filter(dossier=dossier, evenement__isnull=False)
        .values_list("evenement_id", flat=True)
    )
    return [_ligne_de_journal(e, importes) for e in entrees]


def preparer_export(dossier, parametres, auteur, journal=None):
    """L'archive, et les détails à inscrire au journal de l'export.

    `parametres` : les champs postés par le bouton d'export — les filtres de
    la vue affichée. Sans filtre, tout le dossier ; avec, la sélection, et
    l'archive dit laquelle.
    """
    filtres = filtrage.lire_filtres(parametres, dossier)
    tous = list(dossier.events.select_related("expediteur").prefetch_related("documents", "etiquettes"))
    retenus, _correspondances = filtrage.appliquer(tous, filtres)
    selection = filtres.description() if filtres.actifs else ""
    archive, nb_pieces = construire_export(
        dossier, auteur, evenements=retenus if filtres.actifs else None,
        selection=selection, total=len(tous), journal=journal,
    )
    details = {"pieces": nb_pieces, "evenements": len(retenus)}
    if selection:
        details["selection"] = selection
    if journal is not None:
        details["journal"] = True
    return archive, details


def dossier_export(request, dossier_id):
    """Le dossier complet en un zip : chronologie en PDF, pièces, empreintes.

    Ouvert à quiconque lit le dossier : l'archive ne contient rien qu'il ne
    puisse déjà consulter. POST uniquement — l'export est inscrit au journal,
    et un lien qu'un navigateur précharge en inscrirait sans que personne
    ne l'ait demandé.
    """
    if request.method != "POST":
        return JsonResponse({"success": False}, status=405)
    dossier = get_dossier_for_read(request.user, dossier_id)
    journal = lignes_du_journal(dossier, request.user) if request.POST.get("journal") else None
    archive, details = preparer_export(
        dossier, request.POST, request.user.get_username(), journal=journal
    )
    consigner(
        dossier, Journal.Action.EXPORT, request=request, objet=dossier.name, details=details,
    )
    nom = "%s-%s.zip" % (slugify(dossier.name) or "dossier", timezone.localdate().isoformat())
    return FileResponse(archive, as_attachment=True, filename=nom, content_type="application/zip")
