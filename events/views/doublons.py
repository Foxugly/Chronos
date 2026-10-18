"""La page Doublons : paires proposées, fusion, paires écartées."""

import html
import re

from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _

from .. import doublons as dedoublonnage
from ..filtres import identifiant
from ..permissions import get_dossier_for_write, get_event_for_write


def _texte_court(description, longueur=400):
    # Chaque balise devient une espace : sans cela, deux paragraphes se
    # collent (« ACME BAT.Cordialement »).
    texte = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", description or "")).split())
    return texte if len(texte) <= longueur else texte[:longueur].rstrip() + "…"


def dossier_doublons(request, dossier_id):
    """Les doublons possibles du dossier, à fusionner ou à écarter.

    Réservé à qui peut écrire : fusionner modifie et supprime. 404 sinon.
    """
    dossier = get_dossier_for_write(request.user, dossier_id)
    paires = dedoublonnage.doublons_possibles(dossier)
    for paire in paires:
        for cote in ("a", "b"):
            evenement = paire[cote]
            evenement.resume = _texte_court(evenement.description)
    return render(request, "events/doublons.html", {"dossier": dossier, "paires": paires})


def _paire_postee(request, dossier):
    """Les deux évènements de la paire postée, du dossier, en écriture — ou 404."""
    ids = {identifiant(request.POST.get("a")), identifiant(request.POST.get("b"))}
    if None in ids or len(ids) != 2:
        raise Http404
    evenements = {e.pk: e for e in (get_event_for_write(request.user, pk) for pk in ids)}
    if any(e.dossier_id != dossier.pk for e in evenements.values()):
        raise Http404
    return evenements


def doublons_fusionner(request, dossier_id):
    dossier = get_dossier_for_write(request.user, dossier_id)
    if request.method != "POST":
        return redirect("dossier_doublons", dossier_id=dossier.pk)
    evenements = _paire_postee(request, dossier)
    garde = evenements.get(identifiant(request.POST.get("garder")))
    if garde is None:
        raise Http404
    absorbe = next(e for pk, e in evenements.items() if pk != garde.pk)
    titre_absorbe = absorbe.title
    dedoublonnage.fusionner(garde, absorbe, request=request)
    messages.success(request, _("« %(absorbe)s » a été fusionné dans « %(garde)s ».") % {
        "absorbe": titre_absorbe, "garde": garde.title})
    return redirect("dossier_doublons", dossier_id=dossier.pk)


def doublons_ecarter(request, dossier_id):
    dossier = get_dossier_for_write(request.user, dossier_id)
    if request.method != "POST":
        return redirect("dossier_doublons", dossier_id=dossier.pk)
    premier, second = _paire_postee(request, dossier).values()
    dedoublonnage.ecarter(premier, second, request=request)
    messages.success(request, _("Ces deux évènements ne seront plus proposés comme doublons."))
    return redirect("dossier_doublons", dossier_id=dossier.pk)
