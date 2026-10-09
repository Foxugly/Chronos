"""Consultation d'un dossier par lien secret, **sans compte**.

Chemin d'accès volontairement séparé du reste de l'application.

Il ne passe par aucun helper de `events.permissions`, ne lit jamais
`request.user`, et borne chaque requête au dossier porté par le lien. Greffer
l'accès anonyme sur le modèle de droits des comptes aurait fini par lui faire
hériter d'un privilège que personne n'a voulu lui donner : ici, il n'y a rien
dont hériter.

Le lien porte un droit, choisi par le propriétaire : lecture, ou lecture et
écriture. Les vues d'écriture refusent donc un lien en lecture seule, et
aucune vue ici ne permet de partager le dossier ni d'en voir un autre —
cela reste l'affaire du propriétaire, authentifié.
"""

import json

from django.contrib.auth.decorators import login_not_required
from django.http import FileResponse, Http404, JsonResponse
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import gettext as _
from django.views.decorators.clickjacking import xframe_options_sameorigin

from .etiquettes import appliquer as poser_etiquettes
from .etiquettes import contexte_du_formulaire as contexte_des_etiquettes
from .forms import EventForm
from .models import Document, DossierAccess, Journal, ShareLink
from .journal import consigner, consigner_pieces, differences, instantane
from .views import (
    rendre_confirmation_de_suppression,
    supprimer_evenement,
    retirer_piece,
    adresse_de_retour,
    rendre_detail,
    contexte_des_pieces,
    preparer_export,
    reponse_de_piece,
    vue_du_dossier,
    ALLOWED_DOCUMENT_EXTENSIONS,
    INLINE_DOCUMENT_EXTENSIONS,
    _est_ajax,
    _evenement_en_json,
    _extension_of,
    _save_documents,
)


def _lien_actif(token):
    """Le lien, s'il existe et vaut encore quelque chose.

    Révoqué ou expiré répond 404, comme un jeton inconnu : distinguer les
    deux dirait à qui tâtonne qu'il a trouvé un jeton réel.
    """
    lien = get_object_or_404(
        ShareLink.objects.select_related("dossier"), token=token
    )
    if not lien.is_active:
        raise Http404("Lien de partage inactif")
    return lien


def _proteger(reponse):
    """En-têtes propres à ce chemin.

    Le jeton voyage dans l'URL : sans `no-referrer`, il partirait dans
    l'en-tête `Referer` vers tout site tiers atteint depuis ces pages, et un
    index public finirait par le connaître.
    """
    reponse["Referrer-Policy"] = "no-referrer"
    reponse["X-Robots-Tag"] = "noindex, nofollow"
    return reponse


@login_not_required
def share_timeline(request, token):
    lien = _lien_actif(token)
    lien.enregistrer_consultation()

    # La chronologie sert les deux publics : ce drapeau retire tout ce qui
    # modifie, et bascule les URL vers ce chemin-ci.
    gabarit, contexte = vue_du_dossier(request, lien.dossier, reverse("share_timeline", args=[token]))
    contexte["lien_partage"] = lien
    contexte["peut_ecrire"] = lien.role == DossierAccess.Role.WRITE

    return _proteger(render(request, gabarit, contexte))


@login_not_required
def share_event_detail(request, token, event_id):
    lien = _lien_actif(token)

    # Borné au dossier du lien : un identifiant d'évènement d'une autre
    # affaire ne doit rien révéler, pas même son existence.
    event = get_object_or_404(lien.dossier.events, pk=event_id)

    return _proteger(
        rendre_detail(
            request,
            event,
            peut_ecrire=lien.role == DossierAccess.Role.WRITE,
            lien=lien,
        )
    )


@login_not_required
@xframe_options_sameorigin
def share_document_download(request, token, document_id):
    lien = _lien_actif(token)

    # Même borne que pour les évènements : la pièce doit appartenir au
    # dossier du lien, et non à un dossier quelconque.
    document = get_object_or_404(
        Document.objects.filter(dossier=lien.dossier), pk=document_id
    )

    return _proteger(reponse_de_piece(document))


def _lien_en_ecriture(token):
    """Le lien, s'il autorise l'écriture.

    Un lien en lecture seule répond 404 et non 403 : il ne doit pas
    apprendre à qui le détient qu'une porte existe ailleurs.
    """
    lien = _lien_actif(token)
    if lien.role != DossierAccess.Role.WRITE:
        raise Http404("Lien en lecture seule")
    return lien


def _rendre_formulaire_partage(request, lien, form, event, status=200):
    contexte = {
        "form": form,
        "event": event,
        "dossier": lien.dossier,
        "lien_partage": lien,
        "champ_documents": "documents-modal" if _est_ajax(request) else "documents",
        "retour": _retour_partage(request, lien),
        **contexte_des_pieces(),
        **contexte_des_etiquettes(lien.dossier, event, request.POST if request.method == "POST" else None),
    }
    gabarit = (
        "events/_event_form.html" if _est_ajax(request) else "events/event_form.html"
    )
    return _proteger(render(request, gabarit, contexte, status=status))


def _retour_partage(request, lien):
    """La vue d'où l'on vient, bornée aux pages de ce lien."""
    base = reverse("share_timeline", args=[lien.token])
    return adresse_de_retour(request, base, prefixe=base)


@login_not_required
def share_event_add(request, token):
    lien = _lien_en_ecriture(token)

    if request.method == "POST":
        form = EventForm(request.POST)
        if form.is_valid():
            try:
                with transaction.atomic():
                    event = form.save(commit=False)
                    event.dossier = lien.dossier
                    event.save()
                    pieces = _save_documents(event, request.FILES.getlist("documents"))
                    poser_etiquettes(event, request.POST)
                    consigner(
                        lien.dossier, Journal.Action.EVENEMENT_CREE, lien=lien,
                        objet=event.title, evenement=event,
                        details={"valeurs": instantane(event)},
                    )
                    consigner_pieces(event, pieces, lien=lien)
            except ValidationError as error:
                form.add_error(None, error)
            else:
                if _est_ajax(request):
                    return _proteger(
                        JsonResponse(
                            {"success": True, "event": _evenement_en_json(event)}
                        )
                    )
                return _proteger(redirect(_retour_partage(request, lien)))
        if _est_ajax(request):
            return _rendre_formulaire_partage(request, lien, form, None, status=400)
    else:
        form = EventForm()

    return _rendre_formulaire_partage(request, lien, form, None)


@login_not_required
def share_event_edit(request, token, event_id):
    lien = _lien_en_ecriture(token)
    # Borné au dossier du lien, comme toutes les vues de ce chemin.
    event = get_object_or_404(lien.dossier.events, pk=event_id)
    avant = instantane(event)

    if request.method == "POST":
        form = EventForm(request.POST, instance=event)
        if form.is_valid():
            try:
                with transaction.atomic():
                    event = form.save()
                    pieces = _save_documents(event, request.FILES.getlist("documents"))
                    poser_etiquettes(event, request.POST)
                    changements = differences(avant, instantane(event))
                    if changements:
                        consigner(
                            lien.dossier, Journal.Action.EVENEMENT_MODIFIE, lien=lien,
                            objet=event.title, evenement=event,
                            details={"changements": changements},
                        )
                    consigner_pieces(event, pieces, lien=lien)
            except ValidationError as error:
                form.add_error(None, error)
                event.refresh_from_db()
            else:
                if _est_ajax(request):
                    return _proteger(
                        JsonResponse(
                            {"success": True, "event": _evenement_en_json(event)}
                        )
                    )
                return _proteger(redirect(_retour_partage(request, lien)))
        if _est_ajax(request):
            return _rendre_formulaire_partage(request, lien, form, event, status=400)
    else:
        form = EventForm(instance=event)

    return _rendre_formulaire_partage(request, lien, form, event)


@login_not_required
def share_document_detach(request, token, event_id, document_id):
    """Retire une pièce d'un évènement, et la supprime au dernier lien.

    Même règle que le chemin authentifié, et même irréversibilité : les
    octets partent du disque. Par un lien anonyme, personne ne saura qui a
    fait le geste — c'est la contrepartie d'un lien en écriture.
    """
    if request.method != "POST":
        return JsonResponse({"success": False}, status=405)

    lien = _lien_en_ecriture(token)
    event = get_object_or_404(lien.dossier.events, pk=event_id)
    document = get_object_or_404(
        Document.objects.filter(dossier=lien.dossier), pk=document_id
    )

    supprime = retirer_piece(event, document, lien=lien)
    return _proteger(JsonResponse({"success": True, "supprime": supprime}))


@login_not_required
def share_dossier_export(request, token):
    """L'export du dossier, par un lien de partage (lecture ou écriture).

    Le lien ouvre déjà la lecture de tout ce que contient l'archive : la
    remettre en un fichier n'élargit rien. L'export est inscrit au journal au
    nom du lien — on ne sait pas qui le tenait —, et l'archive le dit aussi.
    POST uniquement, comme sur le chemin authentifié.
    """
    if request.method != "POST":
        return _proteger(JsonResponse({"success": False}, status=405))
    lien = _lien_actif(token)
    dossier = lien.dossier
    if lien.label:
        auteur = _("le lien de partage « %(nom)s »") % {"nom": lien.label}
    else:
        auteur = _("un lien de partage")
    # Jamais le journal par un lien : il n'y est pas consultable.
    archive, details = preparer_export(dossier, request.POST, auteur)
    consigner(dossier, Journal.Action.EXPORT, lien=lien, objet=dossier.name, details=details)
    nom = "%s-%s.zip" % (slugify(dossier.name) or "dossier", timezone.localdate().isoformat())
    return _proteger(
        FileResponse(archive, as_attachment=True, filename=nom, content_type="application/zip")
    )


@login_not_required
def share_event_delete(request, token, event_id):
    """La suppression par un lien en écriture : bornée au dossier du lien,
    inscrite au journal au nom du lien."""
    lien = _lien_en_ecriture(token)
    event = get_object_or_404(lien.dossier.events, pk=event_id)
    retour = _retour_partage(request, lien)
    if request.method == "POST":
        pk = event.pk
        supprimer_evenement(event, lien=lien)
        if _est_ajax(request):
            return _proteger(JsonResponse({"success": True, "id": pk}))
        return _proteger(redirect(retour))
    return _proteger(rendre_confirmation_de_suppression(
        request, event,
        action=reverse("share_event_delete", args=[token, event.pk]), retour=retour,
    ))
