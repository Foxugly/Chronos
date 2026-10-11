"""Un évènement : détail, formulaire (ajout, modification), suppression."""

from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _

from ..etiquettes import (
    contexte_du_formulaire as contexte_des_etiquettes,
    appliquer as poser_etiquettes,
)
from ..forms import EventForm
from ..journal import consigner, consigner_pieces, differences, instantane
from ..models import CourrielImporte, Dossier, Event, Journal
from ..permissions import get_dossier_for_write, get_event_for_read, get_event_for_write
from ..sanitize import nettoyer
from .pieces import _extension_of, _save_documents, contexte_des_pieces


#: Icône Font Awesome par extension, pour les pièces qui ne sont pas des images.
ICONES_DE_PIECE = {
    "pdf": "fa-regular fa-file-pdf text-danger",
    "doc": "fa-regular fa-file-word text-primary",
    "docx": "fa-regular fa-file-word text-primary",
    "xls": "fa-regular fa-file-excel text-success",
    "xlsx": "fa-regular fa-file-excel text-success",
}


#: Ce que la visionneuse d'images sait afficher, et ce qui s'affiche en aperçu
#: dans la page. Le PDF seulement : `document_download` ne sert en `inline` que
#: ce que le navigateur n'exécute pas sur l'origine du site.
IMAGE_EXTENSIONS = frozenset({"jpg", "jpeg", "png"})


APERCU_EXTENSIONS = frozenset({"pdf"})


def rendre_detail(request, event, *, peut_ecrire, lien=None):
    """Le détail d'un évènement : le fragment en AJAX, la page entière sinon.

    Partagé par le chemin authentifié et par celui des liens de partage :
    `lien` fait basculer toutes les URL vers ce dernier, qui ne connaît que
    le dossier du lien.

    Tout est rendu par Django, qui échappe titres et noms de pièces. La
    description est la seule valeur marquée `safe`, et elle est réassainie
    ici, à la sortie, et pas seulement à l'enregistrement : l'admin permet
    d'écrire dans ce champ sans passer par `EventForm`.
    """
    if lien is None:
        telecharger = lambda doc: reverse("document_download", args=[doc.pk])  # noqa: E731
        detail_de = lambda pk: reverse("event_detail", args=[pk])  # noqa: E731
        url_modifier = reverse("event_edit", args=[event.pk])
        url_supprimer = reverse("event_delete", args=[event.pk])
        url_retour = adresse_de_retour(
            request, reverse("timeline", args=[event.dossier_id])
        )
    else:
        telecharger = lambda doc: reverse(  # noqa: E731
            "share_document_download", args=[lien.token, doc.pk]
        )
        detail_de = lambda pk: reverse("share_event_detail", args=[lien.token, pk])  # noqa: E731
        url_modifier = reverse("share_event_edit", args=[lien.token, event.pk])
        url_supprimer = reverse("share_event_delete", args=[lien.token, event.pk])
        base = reverse("share_timeline", args=[lien.token])
        url_retour = adresse_de_retour(request, base, prefixe=base)

    pieces = []
    for document in event.documents.all():
        extension = _extension_of(document.title)
        if extension in IMAGE_EXTENSIONS:
            genre = "image"
        elif extension in APERCU_EXTENSIONS:
            genre = "apercu"
        else:
            genre = "fichier"
        pieces.append(
            {
                "titre": document.title,
                "numero": document.numero,
                "url": telecharger(document),
                "genre": genre,
                "icone": ICONES_DE_PIECE.get(extension, "fa-regular fa-file"),
            }
        )

    contexte = {
        "event": event,
        "dossier": event.dossier,
        "description": nettoyer(event.description),
        "pieces": pieces,
        "url_modifier": (
            "%s?%s" % (url_modifier, urlencode({"retour": url_retour}))
            if peut_ecrire
            else None
        ),
        "url_supprimer": (
            "%s?%s" % (url_supprimer, urlencode({"retour": url_retour}))
            if peut_ecrire
            else None
        ),
        "url_retour": url_retour,
        # L'œil de la popup : le même évènement en pleine page, avec la même vue
        # de retour. Absent de la page elle-même.
        "url_pleine_page": (
            "%s?%s" % (detail_de(event.pk), urlencode({"retour": url_retour}))
            if _est_ajax(request) else None
        ),
        "lien_partage": lien,
        **_voisins(event, detail_de, url_retour),
        **_liens_de_transfert(event, detail_de, url_retour),
    }
    gabarit = (
        "events/_detail_evenement.html"
        if _est_ajax(request)
        else "events/detail_evenement.html"
    )
    return render(request, gabarit, contexte)


def _liens_de_transfert(event, detail_de, url_retour):
    """Le courriel que cet évènement transfère, et ses transferts ultérieurs.

    Bornés au dossier de l'évènement, en plus de `Event.clean` : ce fragment
    est aussi servi par les liens de partage, qui ne connaissent qu'un dossier.
    """
    def lien(autre):
        return {
            "titre": autre.title,
            "periode": autre.periode_affichee,
            "url": "%s?%s" % (detail_de(autre.pk), urlencode({"retour": url_retour})),
        }

    meme_dossier = Event.objects.filter(dossier_id=event.dossier_id)
    origine = meme_dossier.filter(pk=event.transfert_de_id).first() if event.transfert_de_id else None
    repondu = meme_dossier.filter(pk=event.reponse_a_id).first() if event.reponse_a_id else None
    return {
        "transfert_de": lien(origine) if origine else None,
        "transferts": [lien(t) for t in meme_dossier.filter(transfert_de=event)],
        "reponse_a": lien(repondu) if repondu else None,
        "reponses": [lien(r) for r in meme_dossier.filter(reponse_a=event)],
    }


def _voisins(event, detail_de, url_retour):
    """Les faits précédent et suivant dans la chronologie du dossier, pour
    lire le dossier d'une traite sans refermer le détail. Bornés au dossier
    de l'évènement : on ne navigue jamais vers une autre affaire."""
    ordre = list(event.dossier.events.values_list("pk", "title"))
    rang = next(i for i, (pk, _titre) in enumerate(ordre) if pk == event.pk)
    voisins = {}
    for cle, autre in (("precedent", rang - 1), ("suivant", rang + 1)):
        if 0 <= autre < len(ordre):
            pk, titre = ordre[autre]
            voisins[cle] = {
                "url": "%s?%s" % (detail_de(pk), urlencode({"retour": url_retour})),
                "titre": titre,
            }
    voisins["rang"] = rang + 1
    voisins["nombre"] = len(ordre)
    return voisins


def event_detail(request, event_id):
    event = get_event_for_read(request.user, event_id)
    peut_ecrire = (
        Dossier.objects.writable_by(request.user).filter(pk=event.dossier_id).exists()
    )
    return rendre_detail(request, event, peut_ecrire=peut_ecrire)


def adresse_de_retour(request, defaut, prefixe="/"):
    """La vue d'où l'on vient, si elle est sûre ; sinon `defaut`.

    Les liens vers le formulaire et le détail portent `?retour=<vue>` : on
    revient au mois, à la semaine ou à la liste qu'on regardait, et non à la
    chronologie. Le paramètre vient du navigateur, donc on le vérifie : une
    adresse du site (`url_has_allowed_host_and_scheme` écarte `//ailleurs`,
    `https://ailleurs`, `javascript:`), et sous `prefixe` — un lien de
    partage ne renvoie que vers ses propres pages.
    """
    candidat = request.POST.get("retour") or request.GET.get("retour") or ""
    if candidat.startswith(prefixe) and url_has_allowed_host_and_scheme(
        candidat,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return candidat
    return defaut


def _est_ajax(request):
    """Vrai quand le formulaire est demandé pour le modal de la chronologie.

    La page complète reste servie telle quelle : elle est le repli quand
    JavaScript échoue, et on ne veut pas que deux chemins de rendu divergent
    au point que l'un se casse sans que l'autre le dise.
    """
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


def _rendre_formulaire(request, form, event, dossier, status=200):
    """Le fragment seul en AJAX, la page entière sinon."""
    contexte = {
        "form": form,
        "event": event,
        "dossier": dossier,
        # Deux identifiants distincts : la page et le modal peuvent coexister
        # dans un même document, et deux `id` identiques feraient pointer le
        # label du second vers le champ du premier.
        "champ_documents": "documents-modal" if _est_ajax(request) else "documents",
        "retour": adresse_de_retour(request, reverse("timeline", args=[dossier.pk])),
        **contexte_des_pieces(),
        **contexte_des_etiquettes(dossier, event, request.POST if request.method == "POST" else None),
    }
    gabarit = (
        "events/_event_form.html" if _est_ajax(request) else "events/event_form.html"
    )
    return render(request, gabarit, contexte, status=status)


def _evenement_en_json(event):
    """Ce qu'il faut à la chronologie pour se mettre à jour sans recharger."""
    return {
        "id": event.pk,
        "content": event.title,
        "start": event.date.strftime("%Y-%m-%d"),
    }


def event_add(request, dossier_id):
    dossier = get_dossier_for_write(request.user, dossier_id)

    if request.method == "POST":
        form = EventForm(request.POST)
        if form.is_valid():
            try:
                # L'évènement et ses pièces s'enregistrent ensemble : un
                # fichier refusé ne doit pas laisser derrière lui un
                # évènement vide que personne n'a demandé.
                with transaction.atomic():
                    event = form.save(commit=False)
                    event.dossier = dossier
                    event.save()
                    pieces = _save_documents(event, request.FILES.getlist("documents"))
                    poser_etiquettes(event, request.POST)
                    consigner(
                        dossier, Journal.Action.EVENEMENT_CREE, request=request,
                        objet=event.title, evenement=event,
                        details={"valeurs": instantane(event)},
                    )
                    consigner_pieces(event, pieces, request=request)
            except ValidationError as error:
                form.add_error(None, error)
            else:
                if _est_ajax(request):
                    return JsonResponse(
                        {"success": True, "event": _evenement_en_json(event)}
                    )
                return redirect(
                    adresse_de_retour(request, reverse("timeline", args=[dossier.pk]))
                )
        # Formulaire invalide : on renvoie le fragment avec ses erreurs et un
        # 400, pour que le modal sache qu'il n'a pas enregistré.
        if _est_ajax(request):
            return _rendre_formulaire(request, form, None, dossier, status=400)
    else:
        form = EventForm()

    return _rendre_formulaire(request, form, None, dossier)


def event_edit(request, event_id):
    event = get_event_for_write(request.user, event_id)
    # Avant le formulaire : `is_valid()` recopie déjà les valeurs envoyées
    # sur l'instance, et l'ancien état serait perdu pour le journal.
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
                            event.dossier, Journal.Action.EVENEMENT_MODIFIE,
                            request=request, objet=event.title, evenement=event,
                            details={"changements": changements},
                        )
                    consigner_pieces(event, pieces, request=request)
            except ValidationError as error:
                form.add_error(None, error)
                event.refresh_from_db()
            else:
                if _est_ajax(request):
                    return JsonResponse(
                        {"success": True, "event": _evenement_en_json(event)}
                    )
                return redirect(
                    adresse_de_retour(
                        request, reverse("timeline", args=[event.dossier_id])
                    )
                )
        if _est_ajax(request):
            return _rendre_formulaire(
                request, form, event, event.dossier, status=400
            )
    else:
        form = EventForm(instance=event)

    return _rendre_formulaire(request, form, event, event.dossier)


def pieces_a_la_suppression(event):
    """Les pièces de l'évènement, et le sort de chacune s'il est supprimé :
    supprimée si plus aucun autre évènement ne s'y appuie, gardée sinon."""
    return [
        {"document": d, "supprimee": not d.events.exclude(pk=event.pk).exists()}
        for d in event.documents.all()
    ]


def supprimer_evenement(event, **contexte):
    """Supprime un évènement, et ses pièces que plus rien d'autre n'utilise.

    Même règle que `retirer_piece` : une pièce liée à un autre évènement reste ;
    une pièce qui n'illustrait que celui-ci part, fichier compris. Le journal
    garde l'évènement entier (titre, dates, description) et chaque pièce
    (nom, empreinte).

    La mémoire de l'import (`CourrielImporte`) part aussi : supprimer un
    évènement importé par erreur doit permettre de le réimporter.

    Les fichiers sont effacés **après** la validation de la transaction
    (`on_commit`) : une transaction annulée ne peut pas rendre des octets déjà
    effacés. `contexte` : `request=` ou `lien=`.
    """
    a_effacer = []
    with transaction.atomic():
        for piece in pieces_a_la_suppression(event):
            document = piece["document"]
            details = {"empreinte": document.content_hash, "evenement": event.title}
            if piece["supprimee"]:
                consigner(event.dossier, Journal.Action.PIECE_SUPPRIMEE, objet=document.title,
                          evenement=event, details=details, **contexte)
                a_effacer.append((document.file.storage, document.file.name))
                document.delete()
            else:
                document.events.remove(event)
                consigner(event.dossier, Journal.Action.PIECE_RETIREE, objet=document.title,
                          evenement=event, details=details, **contexte)
        CourrielImporte.objects.filter(evenement=event).delete()
        consigner(event.dossier, Journal.Action.EVENEMENT_SUPPRIME, objet=event.title,
                  details={"valeurs": instantane(event)}, **contexte)
        event.delete()

        def effacer_les_fichiers():
            for stockage, chemin in a_effacer:
                if chemin:
                    stockage.delete(chemin)

        transaction.on_commit(effacer_les_fichiers)


def rendre_confirmation_de_suppression(request, event, *, action, retour):
    """La page (ou le fragment, pour le modal) qui demande confirmation."""
    contexte = {
        "event": event,
        "dossier": event.dossier,
        "pieces": pieces_a_la_suppression(event),
        "action": action,
        "retour": retour,
    }
    gabarit = (
        "events/_confirmer_suppression.html" if _est_ajax(request)
        else "events/confirmer_suppression.html"
    )
    return render(request, gabarit, contexte)


def event_delete(request, event_id):
    """Supprimer un évènement : une confirmation (GET), puis la suppression (POST).

    Réservé à qui peut écrire dans le dossier. Jamais sur un simple GET : un
    lien préchargé ou cliqué par mégarde ne doit rien effacer.
    """
    event = get_event_for_write(request.user, event_id)
    retour = adresse_de_retour(request, reverse("timeline", args=[event.dossier_id]))
    if request.method == "POST":
        titre, pk = event.title, event.pk
        supprimer_evenement(event, request=request)
        if _est_ajax(request):
            return JsonResponse({"success": True, "id": pk})
        messages.success(request, _("Évènement « %(titre)s » supprimé.") % {"titre": titre})
        return redirect(retour)
    return rendre_confirmation_de_suppression(
        request, event, action=reverse("event_delete", args=[event.pk]), retour=retour
    )
