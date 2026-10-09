import datetime
import hashlib
import html
import mimetypes
import re
import secrets
from urllib.parse import quote, urlencode
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Count
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.html import strip_tags
from django.utils.text import slugify
from django.utils.http import content_disposition_header, url_has_allowed_host_and_scheme
from django.utils.formats import date_format, time_format
from django.utils.translation import gettext as _
from django.utils.translation import ngettext
from django.views.decorators.clickjacking import xframe_options_sameorigin

from .forms import (
    AliasCourrielForm,
    suffixe_des_alias,
    DossierAccessForm,
    DossierNomForm,
    EventForm,
    InvitationForm,
    ShareLinkForm,
)
from .models import (
    CourrielImporte, Document, Dossier, DossierAccess, Event, Expediteur, Invitation, Journal,
    ShareLink,
)
from .couleurs import valider_couleur
from .export import construire_export
from .journal import consigner, consigner_pieces, differences, instantane
from . import calendrier
from . import exchange
from .etiquettes import appliquer as poser_etiquettes
from .etiquettes import contexte_du_formulaire as contexte_des_etiquettes
from . import doublons as dedoublonnage
from .graph import ErreurGraph
from .import_courriels import import_configure, relever_la_boite, verrou_de_releve
from . import filtres as filtrage
from .sanitize import nettoyer
from .permissions import (
    get_document_for_read,
    get_document_for_write,
    get_dossier_for_owner,
    get_dossier_for_read,
    get_dossier_for_write,
    get_event_for_read,
    get_event_for_write,
)

#: Extensions acceptées à l'envoi et servies avec leur vrai type MIME.
#:
#: Le formulaire la reçoit (`contexte_des_pieces`) pour filtrer le sélecteur
#: et prévenir avant l'envoi, mais c'est une vérification *client* : un POST
#: direct la contourne. Celle-ci est la seule qui fasse autorité.
ALLOWED_DOCUMENT_EXTENSIONS = frozenset(
    # `eml` et `md` : le courriel d'origine et sa version texte, joints par
    # l'import de courriels. Servis en téléchargement, jamais affichés.
    {"pdf", "jpg", "jpeg", "png", "doc", "docx", "xls", "xlsx", "eml", "md"}
)

#: Extensions que le navigateur peut afficher dans un onglet sans risque.
#:
#: Un PDF est rendu par une visionneuse isolée, sans accès au DOM, aux
#: cookies ni au stockage de l'origine ; les images sont inertes. Tout le
#: reste est téléchargé plutôt qu'affiché. `svg` en est volontairement
#: absent : c'est du XML qui peut porter du script et s'exécuterait sur
#: l'origine du site — il est déjà refusé au téléversement, cette liste
#: est la seconde barrière.
INLINE_DOCUMENT_EXTENSIONS = frozenset({"pdf", "jpg", "jpeg", "png"})


def _extension_of(filename):
    """Extension en minuscules, sans le point ; chaîne vide si absente."""
    head, separator, extension = filename.rpartition(".")
    return extension.lower() if separator else ""



#: Les représentations d'un même dossier, une par onglet.
#:
#: La chronologie (vis-timeline) ; quatre vues FullCalendar, toutes servies par
#: `calendrier.html` ; trois vues calculées côté serveur, qui s'affichent sans
#: JavaScript. La liste sans JavaScript est le repli quand tout le reste échoue.
VUES = {
    "chronologie": "events/timeline.html",
    "calendrier": "events/calendrier.html",
    "calendrier-mois": "events/calendrier.html",
    "calendrier-semaine": "events/calendrier.html",
    "calendrier-liste": "events/calendrier.html",
    "calendrier-timeline": "events/calendrier.html",
    "mois": "events/vue_mois.html",
    "semaine": "events/vue_semaine.html",
    "liste": "events/vue_liste.html",
    "documents": "events/vue_documents.html",
}


#: Onglet → vue FullCalendar (`frise` et `liste` sont définies dans
#: `calendrier.html` pour couvrir tout le dossier d'un seul tenant).
VUES_FULLCALENDAR = {
    "calendrier": "dayGridMonth",
    "calendrier-mois": "dayGridMonth",
    "calendrier-semaine": "timeGridWeek",
    "calendrier-liste": "liste",
    "calendrier-timeline": "frise",
}


#: L'onglet allumé pour chaque vue : les cinq vues de calendrier en partagent un.
ONGLET_DE_LA_VUE = {
    "calendrier-mois": "calendrier",
    "calendrier-semaine": "calendrier",
    "calendrier-liste": "calendrier",
    "calendrier-timeline": "calendrier",
    "mois": "calendrier",
    "semaine": "calendrier",
}

#: Vue FullCalendar → clé d'URL, pour que l'adresse suive le bouton choisi
#: dans la barre du calendrier (un rechargement ou un favori y revient).
CLE_DE_LA_VUE_FULLCALENDAR = {
    "dayGridMonth": "calendrier-mois",
    "timeGridWeek": "calendrier-semaine",
    "liste": "calendrier-liste",
    "frise": "calendrier-timeline",
}


def _contexte_de_vue(request, dossier, evenements):
    """Le gabarit à rendre et son contexte, selon la vue demandée.

    Une vue inconnue retombe sur la chronologie plutôt que de lever : un
    paramètre d'URL tordu ne doit pas produire une page d'erreur.
    """
    vue = request.GET.get("vue", "chronologie")
    if vue not in VUES:
        vue = "chronologie"

    contexte = {
        "dossier": dossier,
        "vue": vue,
        # Quatre onglets. Le libellé vit ici et non dans le gabarit : les
        # onglets sont énumérés au même endroit que les vues. Le calendrier
        # choisit mois / semaine / liste / frise dans sa propre barre ; les
        # vues mois et semaine rendues par le serveur restent servies (repli
        # sans JavaScript, anciens liens) et allument l'onglet Calendrier.
        "onglets": [
            ("chronologie", _("Chronologie")),
            ("calendrier", _("Calendrier")),
            ("liste", _("Liste")),
            ("documents", _("Documents")),
        ],
        "onglet_actif": ONGLET_DE_LA_VUE.get(vue, vue),
    }

    if vue == "mois":
        premier = calendrier.mois_demande(request.GET.get("periode"))
        precedent, suivant = calendrier.mois_voisins(premier)
        contexte.update(
            {
                "semaines": calendrier.grille_du_mois(premier, evenements),
                "periode": premier,
                "titre_periode": calendrier.titre_du_mois(premier),
                "precedent": precedent.strftime("%Y-%m"),
                "suivant": suivant.strftime("%Y-%m"),
                "noms_de_jours": calendrier.noms_de_jours(),
            }
        )
    elif vue == "semaine":
        lundi = calendrier.semaine_demandee(request.GET.get("periode"))
        precedente, suivante = calendrier.semaines_voisines(lundi)
        contexte.update(
            {
                "jours": calendrier.jours_de_la_semaine(lundi, evenements),
                "periode": lundi,
                "titre_periode": _("Semaine du %(date)s")
                % {"date": date_format(lundi, "DATE_FORMAT")},
                "precedent": precedente.strftime("%Y-%m-%d"),
                "suivant": suivante.strftime("%Y-%m-%d"),
            }
        )
    elif vue == "liste":
        contexte["groupes"] = calendrier.liste_par_mois(evenements)
    elif vue in VUES_FULLCALENDAR:
        contexte["vue_fullcalendar"] = VUES_FULLCALENDAR[vue]
        contexte["cles_fullcalendar"] = CLE_DE_LA_VUE_FULLCALENDAR
        contexte["calendrier_items"] = [e.pour_calendrier() for e in evenements]
        contexte["calendrier_bornes"] = _bornes_du_dossier(evenements)
    else:
        contexte["timeline_items"] = [e.pour_chronologie() for e in evenements]

    return VUES[vue], contexte

def _bornes_du_dossier(evenements):
    """La période que couvrent les évènements, pour les vues timeline et liste.

    Une semaine de marge de chaque côté : un fait posé sur le bord de la
    frise se lit mal. `initiale` est le dernier fait — c'est là que le
    dossier en est, et c'est le mois que l'on veut voir en arrivant.
    Sans évènement, aucune borne : le calendrier s'ouvre sur aujourd'hui.
    """
    if not evenements:
        return None
    debuts = [e.date for e in evenements]
    fins = [e.date_fin or e.date for e in evenements]
    marge = timedelta(days=7)
    return {
        "debut": (min(debuts) - marge).isoformat(),
        "fin": (max(fins) + marge).isoformat(),
        "initiale": max(debuts).isoformat(),
    }


def vue_du_dossier(request, dossier, base_vue):
    """Le gabarit et le contexte d'une vue du dossier, filtres appliqués.

    Partagé par le chemin des comptes (`timeline`) et celui des liens de
    partage : les deux montrent les mêmes vues, avec les mêmes filtres.
    """
    tous = list(
        dossier.events.select_related("expediteur").prefetch_related("documents", "etiquettes")
    )
    # Les liens de transfert (vue liste), tirés de cette même liste : aucune
    # requête de plus, et rien qui puisse sortir du dossier.
    par_pk = {evenement.pk: evenement for evenement in tous}
    for evenement in tous:
        evenement.lien_transfert_de = par_pk.get(evenement.transfert_de_id)
        evenement.lien_reponse_a = par_pk.get(evenement.reponse_a_id)
        evenement.liens_transferts = []
        evenement.liens_reponses = []
    for evenement in tous:
        if evenement.lien_transfert_de is not None:
            evenement.lien_transfert_de.liens_transferts.append(evenement)
        if evenement.lien_reponse_a is not None:
            evenement.lien_reponse_a.liens_reponses.append(evenement)
    filtres = filtrage.lire_filtres(request.GET, dossier)
    retenus, correspondances = filtrage.appliquer(tous, filtres)
    for evenement in retenus:
        # Lu par la vue liste : où la recherche a trouvé chaque évènement.
        evenement.trouve_dans = correspondances.get(evenement.pk, [])
    gabarit, contexte = _contexte_de_vue(request, dossier, retenus)
    parametres = filtres.parametres()
    if contexte["vue"] == "documents":
        # La recherche porte ici sur les pièces elles-mêmes ; le compteur aussi.
        type_piece = request.GET.get("type", "")
        if type_piece not in filtrage.TYPES_DE_PIECE:
            type_piece = ""
        lignes, total = filtrage.pieces_filtrees(dossier, tous, filtres, type_piece)
        for ligne in lignes:
            try:
                ligne["taille"] = ligne["document"].file.size
            except OSError:
                # Fichier absent du disque : la ligne reste, sans taille ; le
                # contrôle d'intégrité de l'export le signalera.
                ligne["taille"] = None
            ligne["icone"] = ICONES_DE_PIECE.get(ligne["extension"], "fa-regular fa-file")
            if ligne["extension"] in IMAGE_EXTENSIONS:
                ligne["icone"] = "fa-regular fa-file-image text-info"
            elif ligne["extension"] in ("eml", "md"):
                ligne["icone"] = "fa-regular fa-envelope text-secondary"
        contexte.update({
            "pieces": lignes,
            "type_piece": type_piece,
            "types_de_piece": filtrage.libelles_des_types(),
            "nb_pieces": len(lignes),
            "nb_pieces_total": total,
            "filtres_pieces_actifs": filtres.actifs or bool(type_piece),
        })
    contexte.update({
        "base_vue": base_vue,
        "filtres": filtres,
        "parametres_filtres": parametres,
        # À accoler aux liens d'onglets et de navigation (`?vue=…`), déjà
        # encodé : passer d'une vue à l'autre garde la sélection.
        "suffixe_filtres": ("&" + urlencode(parametres)) if parametres else "",
        "correspondances": correspondances,
        "nb_total": len(tous),
        "nb_retenus": len(retenus),
        "options_expediteurs": list(dossier.expediteurs.order_by("nom", "adresse")),
        "options_etiquettes": list(dossier.etiquettes.all()),
        "valeur_sans_expediteur": filtrage.SANS_EXPEDITEUR,
        "options_mois": filtrage.options_de_mois(tous),
        "tous_les_evenements": tous,
    })
    return gabarit, contexte


def dossier_list(request):
    """L'accueil : un tableau de bord par dossier accessible.

    Le rôle se lit par `writable_by` et le propriétaire, jamais en
    interrogeant `DossierAccess` (invariant 2). Les doublons ne sont comptés
    que pour qui peut les traiter, l'alias montré qu'au propriétaire.
    """
    # `Count(..., distinct=True)` : `accessible_by` joint déjà `accesses`,
    # et sans `distinct` chaque accès multiplierait les comptes.
    dossiers = Dossier.objects.accessible_by(request.user).annotate(
        event_count=Count("events", distinct=True),
        piece_count=Count("documents", distinct=True),
    )
    modifiables = set(Dossier.objects.writable_by(request.user).values_list("pk", flat=True))
    cartes = []
    for dossier in dossiers:
        proprietaire = dossier.owner_id == request.user.id
        peut_ecrire = dossier.pk in modifiables
        evenements = list(dossier.events.select_related("expediteur").prefetch_related("documents"))
        cartes.append({
            "dossier": dossier,
            "role": _("Propriétaire") if proprietaire else (
                _("Écriture") if peut_ecrire else _("Lecture")),
            "proprietaire": proprietaire,
            "derniers_faits": list(reversed(evenements[-3:])),
            "derniers_imports": list(
                CourrielImporte.objects.filter(dossier=dossier, evenement__isnull=False)
                .select_related("evenement").order_by("-importe_le")[:3]
            ),
            "nb_doublons": (
                len(dedoublonnage.doublons_possibles(dossier, evenements)) if peut_ecrire else 0
            ),
        })
    return render(request, "events/dossier_list.html", {"cartes": cartes})


def timeline(request, dossier_id):
    """Le dossier, dans la représentation demandée.

    Quatre vues du même contenu : chronologie, mois, semaine, liste. Le
    paramètre `vue` choisit, `periode` navigue.
    """
    dossier = get_dossier_for_read(request.user, dossier_id)
    # Base des liens d'onglets. Calculée ici et non par `{% url ... as %}`
    # dans l'inclusion : une variable posée dans un `include` ne remonte
    # pas au gabarit qui l'inclut, et les liens seraient vides.
    gabarit, contexte = vue_du_dossier(request, dossier, reverse("timeline", args=[dossier.pk]))
    contexte["peut_ecrire"] = (
        Dossier.objects.writable_by(request.user).filter(pk=dossier.pk).exists()
    )
    if contexte["peut_ecrire"]:
        # Le bouton « Doublons » n'apparaît que s'il y a quelque chose à voir.
        contexte["nb_doublons"] = len(
            dedoublonnage.doublons_possibles(dossier, contexte["tous_les_evenements"])
        )
        contexte["peut_relever"] = bool(dossier.alias_courriel) and import_configure()
    return render(request, gabarit, contexte)


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
    try:
        ids = {int(request.POST.get("a", "")), int(request.POST.get("b", ""))}
    except ValueError:
        raise Http404
    if len(ids) != 2:
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
    try:
        garde = evenements[int(request.POST.get("garder", ""))]
    except (KeyError, ValueError):
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


def _save_documents(event, files):
    """Attache les fichiers à l'évènement, extension et taille contrôlées côté serveur.

    Lève `ValidationError` sur le premier fichier refusé plutôt que de
    l'ignorer : un envoi silencieusement abandonné, sur un dossier de
    preuves, est une perte de pièce que personne ne remarque.

    **Tout est contrôlé avant que rien ne soit écrit.** La transaction des
    vues annule les lignes, pas les fichiers : contrôler et stocker dans la
    même boucle laissait sur le disque les pièces acceptées avant celle qui
    était refusée, sans aucune ligne pour les retrouver.

    Rend `[(document, nouvelle)]` pour le journal : `nouvelle` est faux quand
    le contenu existait déjà dans le dossier et n'a été que rattaché.
    """
    for file in files:
        _verifier_fichier(file)
    return [_attach_document(event, file) for file in files]


def _verifier_fichier(file):
    extension = _extension_of(file.name)
    if extension not in ALLOWED_DOCUMENT_EXTENSIONS:
        raise ValidationError(
            _(
                "Le fichier « %(name)s » n'est pas d'un type accepté. "
                "Types acceptés : %(allowed)s."
            ),
            code="extension_refusee",
            params={
                "name": file.name,
                "allowed": ", ".join(sorted(ALLOWED_DOCUMENT_EXTENSIONS)),
            },
        )
    if file.size > settings.DOCUMENT_TAILLE_MAX:
        raise ValidationError(
            _(
                "Le fichier « %(name)s » est trop lourd (%(taille)s Mo). "
                "Taille maximale : %(max)s Mo."
            ),
            code="fichier_trop_lourd",
            params={
                "name": file.name,
                "taille": _en_mo(file.size),
                "max": _en_mo(settings.DOCUMENT_TAILLE_MAX),
            },
        )


def _en_mo(octets):
    """Taille lisible, au dixième : « 16,7 » plutôt que « 17 » ou 17486788."""
    return f"{octets / (1024 * 1024):.1f}".replace(".", ",")


def contexte_des_pieces():
    """Ce que le champ de pièces doit savoir, tiré des règles du serveur.

    Une seule source pour les deux chemins (authentifié et lien de partage) :
    la liste et la limite affichées ne peuvent pas diverger de celles que
    `_save_documents` applique.
    """
    return {
        "extensions_acceptees": sorted(ALLOWED_DOCUMENT_EXTENSIONS),
        "document_taille_max_ko": settings.DOCUMENT_TAILLE_MAX // 1024,
        "document_taille_max_mo": settings.DOCUMENT_TAILLE_MAX // (1024 * 1024),
    }


def _content_hash(file):
    """SHA-256 du contenu, lu par blocs : une pièce peut peser plusieurs Mo."""
    digest = hashlib.sha256()
    for bloc in file.chunks():
        digest.update(bloc)
    file.seek(0)
    return digest.hexdigest()


def _attach_document(event, file):
    """Lie le fichier à l'évènement, sans le stocker deux fois.

    Le même fichier déposé sur deux évènements d'un même dossier n'est
    stocké qu'une fois : on reconnaît le contenu à son empreinte et on
    rattache la pièce existante.

    La recherche est bornée au dossier. Le même fichier présent dans une
    autre affaire n'est pas un doublon mais une pièce distincte : deux
    dossiers ne partagent jamais d'octets, donc une suppression dans l'un
    ne peut rien atteindre dans l'autre.

    Si le contenu existe déjà sous un autre nom, le titre du premier dépôt
    est conservé — stocker un titre par lien alourdirait le modèle pour un
    cas rare.
    """
    empreinte = _content_hash(file)
    document = Document.objects.filter(
        dossier=event.dossier, content_hash=empreinte
    ).first()
    nouvelle = document is None
    if nouvelle:
        document = Document.objects.create(
            dossier=event.dossier,
            title=file.name,
            file=file,
            content_hash=empreinte,
        )
    document.events.add(event)
    return document, nouvelle


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



@xframe_options_sameorigin
def document_download(request, document_id):
    """Sert le fichier derrière un contrôle de droits.

    Seule porte d'accès aux fichiers depuis que `MEDIA_ROOT` n'est plus
    monté en statique.

    Le type MIME est neutralisé en `application/octet-stream` dès que
    l'extension sort de l'allow-list : un `piece.html` déposé par un
    collaborateur en écriture revenait en `text/html` *inline*, donc
    exécuté dans la session du propriétaire, sur son origine, avec son
    cookie de session et son jeton CSRF.

    `xframe_options_sameorigin` lève, pour cette vue seulement, le
    `X-Frame-Options: DENY` que Django pose sur tout le site : sans lui le
    navigateur refuse d'encadrer la pièce et l'aperçu dans la page affiche
    « connexion refusée ». `SAMEORIGIN` n'autorise que nos propres pages,
    et le reste du site garde `DENY`.

    La disposition suit `INLINE_DOCUMENT_EXTENSIONS` : les PDF et les
    images s'ouvrent dans un onglet, tout le reste est téléchargé. Servir
    *tout* en `attachment` fermait bien la faille, mais obligeait à
    télécharger chaque devis pour le lire — inacceptable sur un dossier
    de pièces. Ce qui est dangereux en `inline`, ce sont les formats que
    le navigateur exécute sur l'origine : HTML et SVG, tous deux absents
    des deux listes.
    """
    document = get_document_for_read(request.user, document_id)
    return reponse_de_piece(document)


def reponse_de_piece(document):
    """La réponse qui envoie une pièce, **après** le contrôle des droits.

    Partagée par le chemin des comptes et celui des liens de partage. Mêmes
    règles partout : type neutralisé hors de la liste autorisée, `inline`
    seulement pour PDF et images.

    En production (`PIECES_X_ACCEL_PREFIX`), Django n'envoie pas les octets :
    il répond par un en-tête `X-Accel-Redirect` vers un `location internal;`
    de nginx, qui sert le fichier. Ce `location` n'est joignable que par
    cette redirection — jamais par une URL tapée —, si bien que le contrôle
    de droits reste ici. Sans le préfixe (développement, tests), Django sert
    le fichier lui-même.
    """
    extension = _extension_of(document.title)
    content_type = (
        None if extension in ALLOWED_DOCUMENT_EXTENSIONS else "application/octet-stream"
    )
    as_attachment = extension not in INLINE_DOCUMENT_EXTENSIONS

    if settings.PIECES_X_ACCEL_PREFIX:
        if not document.file.storage.exists(document.file.name):
            raise Http404("Fichier introuvable")
        reponse = HttpResponse(
            content_type=content_type or mimetypes.guess_type(document.title)[0]
            or "application/octet-stream"
        )
        reponse["X-Accel-Redirect"] = settings.PIECES_X_ACCEL_PREFIX.rstrip("/") + "/" + quote(
            document.file.name.replace("\\", "/"))
        reponse["Content-Disposition"] = content_disposition_header(as_attachment, document.title)
        return reponse

    try:
        handle = document.file.open("rb")
    except FileNotFoundError as error:
        # La ligne existe mais le fichier a disparu du disque. Un 500 ne dit
        # rien de plus à l'utilisateur et transforme une pièce manquante en
        # incident serveur.
        raise Http404("Fichier introuvable") from error

    return FileResponse(
        handle,
        as_attachment=as_attachment,
        filename=document.title,
        content_type=content_type,
    )


def document_detach(request, event_id, document_id):
    """Retire une pièce d'un évènement ; la supprime si elle devient orpheline.

    Une pièce peut illustrer plusieurs évènements sans être stockée deux
    fois. La retirer de l'un ne doit donc pas la détruire tant qu'un autre
    s'y appuie :

    - il reste au moins un évènement lié  -> on défait le lien, rien d'autre ;
    - c'était le dernier lien             -> la pièce et son fichier partent.

    La suppression du fichier est explicite : Django ne touche pas au
    stockage quand une ligne disparaît, et ce projet a déjà quatre fichiers
    sur le disque que plus aucune ligne ne référence.

    L'évènement est pris dans l'URL et non déduit : avec plusieurs liens,
    « son » évènement n'existe plus, et en choisir un au hasard détacherait
    la pièce d'un endroit que personne n'a désigné.
    """
    if request.method != "POST":
        return JsonResponse({"success": False}, status=405)

    event = get_event_for_write(request.user, event_id)
    document = get_document_for_write(request.user, document_id)

    # Une pièce d'un autre dossier n'a rien à faire ici, et un 404 ne dit pas
    # si elle existe.
    if document.dossier_id != event.dossier_id:
        raise Http404("Pièce étrangère à ce dossier")

    resultat = retirer_piece(event, document, request=request)
    return JsonResponse({"success": True, "supprime": resultat})


def retirer_piece(event, document, **contexte):
    """Défait le lien ; supprime la pièce et son fichier au dernier lien.

    Rend vrai si la pièce a été supprimée. Le journal garde son nom et son
    empreinte : c'est tout ce qui en reste. Partagé avec le chemin des liens
    de partage (`contexte` : `request=` ou `lien=`).
    """
    with transaction.atomic():
        document.events.remove(event)
        if document.events.exists():
            consigner(
                event.dossier, Journal.Action.PIECE_RETIREE, objet=document.title,
                evenement=event,
                details={"empreinte": document.content_hash, "evenement": event.title},
                **contexte,
            )
            return False
        consigner(
            event.dossier, Journal.Action.PIECE_SUPPRIMEE, objet=document.title,
            evenement=event,
            details={"empreinte": document.content_hash, "evenement": event.title},
            **contexte,
        )
        stockage, chemin = document.file.storage, document.file.name
        document.delete()
    # Le fichier part après la validation : une transaction annulée ne peut
    # pas rendre des octets déjà effacés.
    if chemin:
        stockage.delete(chemin)
    return True


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


def dossier_alias(request, dossier_id):
    """L'alias de courriel du dossier. Réservé au propriétaire, POST uniquement.

    Changer l'alias coupe l'ancien : s'il a fuité, c'est le moyen d'arrêter
    les évènements qu'on n'a pas demandés.

    Quand Exchange est configuré (`events/exchange.py`), l'application gère
    l'alias elle-même, dans cet ordre : **ajouter** le nouvel alias dans
    Exchange — en cas d'échec, rien n'est enregistré et la page le dit —,
    **enregistrer** dans Chronos, puis **retirer** l'ancien. Si ce retrait
    échoue, l'ancien alias reste actif dans Exchange (ses courriels resteront
    dans la boîte de réception, sans être importés) et un avertissement demande
    de le retirer à la main.
    Sans configuration, l'alias se crée à la main.
    """
    dossier = get_dossier_for_owner(request.user, dossier_id)
    if request.method != "POST":
        return redirect("dossier_parametres", dossier_id=dossier.pk)
    avant = dossier.alias_courriel or ""
    form = AliasCourrielForm(request.POST, instance=dossier)
    if not form.is_valid():
        for erreurs in form.errors.values():
            for erreur in erreurs:
                messages.error(request, erreur)
        return redirect("dossier_parametres", dossier_id=dossier.pk)
    apres = form.cleaned_data.get("alias_courriel") or ""
    gere = exchange.configure()
    if gere and apres and apres != avant:
        try:
            exchange.ajouter(apres)
        except exchange.ErreurExchange as erreur:
            messages.error(request, _(
                "Exchange a refusé l'alias %(alias)s : rien n'a été enregistré. Détail : %(detail)s"
            ) % {"alias": apres, "detail": erreur})
            return redirect("dossier_parametres", dossier_id=dossier.pk)
    with transaction.atomic():
        dossier = form.save(commit=False)
        dossier.save(update_fields=["alias_courriel"])
        if apres != avant:
            consigner(
                dossier, Journal.Action.DOSSIER_MODIFIE, request=request, objet=dossier.name,
                details={"modele": "dossier", "changements": {"alias_courriel": [avant, apres]},
                         "exchange": gere},
            )
    if gere and avant and avant != apres:
        try:
            exchange.retirer(avant)
        except exchange.ErreurExchange as erreur:
            messages.warning(request, _(
                "L'ancien alias %(alias)s n'a pas pu être retiré d'Exchange : retirez-le à la "
                "main. Ses courriels ne créent plus d'évènement. Détail : %(detail)s"
            ) % {"alias": avant, "detail": erreur})
    if apres != avant:
        if apres:
            messages.success(request, _("Les courriels transférés à %(alias)s deviendront des évènements de ce dossier.") % {"alias": apres})
        else:
            messages.success(request, _("Import par courriel désactivé pour ce dossier."))
    return redirect("dossier_parametres", dossier_id=dossier.pk)


def dossier_relever(request, dossier_id):
    """« Relever les courriels » : lance tout de suite l'import planifié.

    Réservé à qui peut écrire (l'import ajoute des évènements) ; 404 sinon.
    POST seulement : un préchargement ne doit pas relever la boîte. La relève
    porte sur toute la boîte — savoir à quel dossier un courriel est destiné
    demande de le lire — mais le message n'annonce que ce qui concerne ce
    dossier : un collaborateur n'apprend rien des autres ni du reste de la
    boîte. Le verrou est celui de la minuterie : jamais deux relèves à la fois.
    """
    dossier = get_dossier_for_write(request.user, dossier_id)
    retour = adresse_de_retour(request, reverse("timeline", args=[dossier.pk]))
    if request.method != "POST" or not dossier.alias_courriel or not import_configure():
        return redirect(retour)
    with verrou_de_releve() as libre:
        if not libre:
            messages.info(request, _("Un import est déjà en cours : réessayez dans une minute."))
            return redirect(retour)
        try:
            bilan = relever_la_boite()
        except ErreurGraph as erreur:
            messages.error(request, _("La boîte n'a pas pu être relevée : %(detail)s") % {"detail": erreur})
            return redirect(retour)
    importes, deja = bilan.importes[dossier.pk], bilan.deja[dossier.pk]
    if importes:
        messages.success(request, ngettext(
            "%(n)d courriel importé dans ce dossier.",
            "%(n)d courriels importés dans ce dossier.", importes) % {"n": importes})
    else:
        messages.info(request, _("Aucun nouveau courriel pour ce dossier."))
    if bilan.erreurs:
        messages.warning(request, _(
            "Certains courriels n'ont pas pu être importés : ils restent dans la boîte de "
            "réception et seront retentés."))
    consigner(dossier, Journal.Action.COURRIELS_RELEVES, request=request,
              objet=ngettext("%(n)d courriel importé", "%(n)d courriels importés", importes) % {"n": importes},
              details={"importes": importes, "deja": deja, "erreurs": bilan.erreurs})
    return redirect(retour)


def dossier_alias_verifier(request, dossier_id):
    """« Vérifier dans Exchange » : l'alias du dossier est-il bien actif ?

    POST seulement — l'appel prend une dizaine de secondes et ne doit pas
    partir sur un préchargement. Réservé au propriétaire.
    """
    dossier = get_dossier_for_owner(request.user, dossier_id)
    if request.method == "POST" and dossier.alias_courriel and exchange.configure():
        try:
            actif = exchange.est_actif(dossier.alias_courriel)
        except exchange.ErreurExchange as erreur:
            messages.error(request, _("Exchange n'a pas pu être interrogé : %(detail)s") % {"detail": erreur})
        else:
            if actif:
                messages.success(request, _("L'alias %(alias)s est actif dans Exchange.") % {
                    "alias": dossier.alias_courriel})
            else:
                messages.warning(request, _(
                    "L'alias %(alias)s n'existe pas dans Exchange : les courriels transférés "
                    "seront refusés. Enregistrez-le à nouveau pour le recréer."
                ) % {"alias": dossier.alias_courriel})
    return redirect("dossier_parametres", dossier_id=dossier.pk)


def _contexte_du_suffixe(dossier):
    """Le suffixe à afficher dans le champ, et le début de l'alias actuel.

    Un alias d'avant le suffixe continue de recevoir : il est signalé, le
    champ reste vide, et l'enregistrer à nouveau impose le format.
    """
    suffixe = suffixe_des_alias()
    alias = dossier.alias_courriel or ""
    au_format = bool(suffixe) and alias.endswith(suffixe)
    return {
        "suffixe_alias": suffixe,
        "debut_alias": alias[:-len(suffixe)] if au_format else ("" if suffixe else alias),
        "alias_hors_format": bool(suffixe and alias and not au_format),
    }


def _alias_propose(dossier):
    """Une adresse difficile à deviner, sur le domaine de la boîte d'import.

    Rien si la boîte n'est pas configurée : on ne devine pas un domaine.
    """
    suffixe = suffixe_des_alias()
    if not suffixe:
        return ""
    debut = slugify(dossier.name)[:20].strip("-") or "dossier"
    return "%s-%s%s" % (debut, secrets.token_hex(3), suffixe)


def dossier_parametres(request, dossier_id):
    """Les réglages du dossier : son nom, son alias d'import par courriel.

    Réservé au propriétaire (404 pour tout autre, comme le partage). Le nom
    se règle ici (POST) ; l'alias passe par `dossier_alias`. Chaque
    changement est inscrit au journal.
    """
    dossier = get_dossier_for_owner(request.user, dossier_id)
    propose = "" if dossier.alias_courriel and not _contexte_du_suffixe(dossier)["alias_hors_format"] \
        else _alias_propose(dossier)
    nom_form = DossierNomForm(instance=dossier)
    if request.method == "POST":
        avant = dossier.name
        nom_form = DossierNomForm(request.POST, instance=dossier)
        if nom_form.is_valid():
            with transaction.atomic():
                dossier = nom_form.save()
                if dossier.name != avant:
                    consigner(
                        dossier, Journal.Action.DOSSIER_MODIFIE, request=request,
                        objet=dossier.name,
                        details={"modele": "dossier", "changements": {"name": [avant, dossier.name]}},
                    )
            if dossier.name != avant:
                messages.success(request, _("Dossier renommé."))
            return redirect("dossier_parametres", dossier_id=dossier.pk)
        dossier.name = avant
    return render(request, "events/parametres.html", {
        "dossier": dossier,
        "nom_form": nom_form,
        "boite": settings.COURRIEL_BOITE,
        **_contexte_du_suffixe(dossier),
        "alias_propose": propose,
        "debut_propose": propose[:-len(suffixe_des_alias())] if propose and suffixe_des_alias() else "",
        "exchange_gere": exchange.configure(),
        "etiquettes": dossier.etiquettes.annotate(nombre=Count("evenements")),
    })


def dossier_etiquette(request, dossier_id, etiquette_id):
    """Renommer (POST `nom`) ou supprimer (POST `supprimer`) une étiquette.

    Réservé au propriétaire, comme le reste des paramètres. Supprimer une
    étiquette la retire des évènements, qui restent. Inscrit au journal.
    """
    dossier = get_dossier_for_owner(request.user, dossier_id)
    etiquette = get_object_or_404(dossier.etiquettes, pk=etiquette_id)
    if request.method != "POST":
        return redirect("dossier_parametres", dossier_id=dossier.pk)
    ancien = etiquette.nom
    if request.POST.get("supprimer"):
        with transaction.atomic():
            etiquette.delete()
            consigner(dossier, Journal.Action.DOSSIER_MODIFIE, request=request, objet=dossier.name,
                      details={"modele": "dossier", "changements": {"etiquettes": [ancien, ""]}})
        messages.success(request, _("Étiquette « %(nom)s » supprimée.") % {"nom": ancien})
        return redirect("dossier_parametres", dossier_id=dossier.pk)
    nom = " ".join((request.POST.get("nom") or "").split())[:60]
    if not nom:
        messages.error(request, _("Le nom d'une étiquette ne peut pas être vide."))
    elif nom != ancien and dossier.etiquettes.filter(nom__iexact=nom).exclude(pk=etiquette.pk).exists():
        messages.error(request, _("Une étiquette « %(nom)s » existe déjà dans ce dossier.") % {"nom": nom})
    elif nom != ancien:
        with transaction.atomic():
            etiquette.nom = nom
            etiquette.save(update_fields=["nom"])
            consigner(dossier, Journal.Action.DOSSIER_MODIFIE, request=request, objet=dossier.name,
                      details={"modele": "dossier", "changements": {"etiquettes": [ancien, nom]}})
        messages.success(request, _("Étiquette renommée."))
    return redirect("dossier_parametres", dossier_id=dossier.pk)


def dossier_expediteurs(request, dossier_id):
    """Les expéditeurs des courriels importés, et leur couleur.

    Lisible par quiconque lit le dossier : c'est la légende des couleurs.
    Modifiable par qui peut écrire : changer une couleur recolore d'un coup
    tous les évènements de cet expéditeur, puisqu'ils la lisent chez lui.
    Chaque changement est inscrit au journal.
    """
    dossier = get_dossier_for_read(request.user, dossier_id)
    peut_ecrire = Dossier.objects.writable_by(request.user).filter(pk=dossier.pk).exists()
    if request.method == "POST":
        dossier = get_dossier_for_write(request.user, dossier_id)
        for expediteur in dossier.expediteurs.all():
            nouvelle = (request.POST.get("couleur-%d" % expediteur.pk) or "").strip().lower()
            if not nouvelle or nouvelle == expediteur.couleur:
                continue
            try:
                valider_couleur(nouvelle)
            except ValidationError:
                messages.error(request, _("Couleur invalide pour %(nom)s.") % {"nom": expediteur})
                continue
            consigner(
                dossier, Journal.Action.DOSSIER_MODIFIE, request=request, objet=str(expediteur),
                details={"modele": "expediteur",
                         "changements": {"couleur": [expediteur.couleur, nouvelle]}},
            )
            expediteur.couleur = nouvelle
            expediteur.save(update_fields=["couleur"])
        return redirect("dossier_expediteurs", dossier_id=dossier.pk)
    expediteurs = dossier.expediteurs.annotate(nombre=Count("evenements"))
    return render(request, "events/expediteurs.html", {
        "dossier": dossier, "expediteurs": expediteurs, "peut_ecrire": peut_ecrire,
    })


def dossier_share(request, dossier_id):
    """Qui a accès au dossier, et comment le leur accorder ou le reprendre.

    Réservé au propriétaire : `get_dossier_for_owner` renvoie 404 à un
    collaborateur en écriture, qui peut tout modifier dans le dossier
    mais ne décide pas qui y entre.
    """
    dossier = get_dossier_for_owner(request.user, dossier_id)

    if request.method == "POST":
        form = DossierAccessForm(request.POST, dossier=dossier)
        if form.is_valid():
            acces = form.save(commit=False)
            acces.dossier = dossier
            acces.origin = DossierAccess.Origin.DIRECT
            acces.granted_by = request.user
            acces.save()
            consigner(
                dossier, Journal.Action.ACCES_ACCORDE, request=request,
                objet=acces.user.get_username(), details={"role": acces.role},
            )
            messages.success(
                request,
                _("Accès accordé à %(compte)s (%(droit)s).")
                % {
                    "compte": acces.user.get_username(),
                    "droit": acces.get_role_display(),
                },
            )
            return redirect("dossier_share", dossier_id=dossier.pk)
    else:
        form = DossierAccessForm(dossier=dossier)

    return render(
        request,
        "events/dossier_share.html",
        {
            "dossier": dossier,
            "form": form,
            "invitation_form": InvitationForm(dossier=dossier),
            "accesses": dossier.accesses.select_related(
                "user", "granted_by"
            ).order_by("user__username"),
            "invitations": dossier.invitations.select_related("created_by"),
            "share_link_form": ShareLinkForm(),
            "share_links": dossier.share_links.all(),
        },
    )


def dossier_access_revoke(request, dossier_id, access_id):
    """Reprend un accès. POST uniquement : un GET suffirait sinon à le
    révoquer depuis un lien, une image ou un préchargement.
    """
    if request.method != "POST":
        return JsonResponse({"success": False}, status=405)

    dossier = get_dossier_for_owner(request.user, dossier_id)
    acces = get_object_or_404(dossier.accesses, pk=access_id)
    nom = acces.user.get_username()
    consigner(
        dossier, Journal.Action.ACCES_RETIRE, request=request, objet=nom,
        details={"role": acces.role},
    )
    acces.delete()
    messages.success(request, _("Accès retiré à %(compte)s.") % {"compte": nom})
    return redirect("dossier_share", dossier_id=dossier.pk)


def _date_courte(moment):
    """Une date à l'heure de Bruxelles, au format court de la langue courante.

    `strftime` sur l'instant UTC stocké donnait la veille pour une
    expiration posée avant 2 h du matin, et figeait le format français.
    """
    return date_format(timezone.localtime(moment), "SHORT_DATE_FORMAT")


def _envoyer_invitation(request, invitation):
    """Envoie le courriel portant le lien d'acceptation.

    Le backend est la console : en développement, le message s'affiche dans
    le terminal du serveur. C'est volontairement visible — une invitation
    silencieusement perdue serait pire qu'une invitation non envoyée.
    """
    lien = request.build_absolute_uri(
        reverse("invitation_accept", args=[invitation.token])
    )
    send_mail(
        subject=_("Accès au dossier « %(dossier)s »")
        % {"dossier": invitation.dossier.name},
        message=(
            _(
                "%(auteur)s vous donne accès au dossier « %(dossier)s ».\n\n"
                "Droit accordé : %(droit)s\n\n"
                "Pour l'accepter, ouvrez ce lien :\n%(lien)s\n\n"
                "Ce lien expire le %(date)s. Il ne sert qu'une fois.\n"
            )
            % {
                "auteur": invitation.created_by.get_username(),
                "dossier": invitation.dossier.name,
                "droit": invitation.get_role_display(),
                "lien": lien,
                "date": _date_courte(invitation.expires_at),
            }
        ),
        from_email=None,
        recipient_list=[invitation.email],
    )
    return lien


def dossier_invite(request, dossier_id):
    """Émet une invitation. POST uniquement, depuis l'écran de partage."""
    dossier = get_dossier_for_owner(request.user, dossier_id)

    if request.method != "POST":
        return redirect("dossier_share", dossier_id=dossier.pk)

    form = InvitationForm(request.POST, dossier=dossier)
    if form.is_valid():
        invitation = form.save(commit=False)
        invitation.dossier = dossier
        invitation.created_by = request.user
        invitation.save()
        consigner(
            dossier, Journal.Action.INVITATION_EMISE, request=request,
            objet=invitation.email,
            details={"role": invitation.role, "expire": invitation.expires_at.isoformat()},
        )
        _envoyer_invitation(request, invitation)
        messages.success(
            request,
            _("Invitation envoyée à %(email)s. Elle expire le %(date)s.")
            % {
                "email": invitation.email,
                "date": _date_courte(invitation.expires_at),
            },
        )
        return redirect("dossier_share", dossier_id=dossier.pk)

    return render(
        request,
        "events/dossier_share.html",
        {
            "dossier": dossier,
            "form": DossierAccessForm(dossier=dossier),
            "invitation_form": form,
            "accesses": dossier.accesses.select_related(
                "user", "granted_by"
            ).order_by("user__username"),
            "invitations": dossier.invitations.select_related("created_by"),
        },
        status=400,
    )


def invitation_revoke(request, dossier_id, invitation_id):
    """Annule une invitation en attente. POST uniquement."""
    if request.method != "POST":
        return JsonResponse({"success": False}, status=405)

    dossier = get_dossier_for_owner(request.user, dossier_id)
    invitation = get_object_or_404(dossier.invitations, pk=invitation_id)
    if invitation.is_usable:
        invitation.revoked_at = timezone.now()
        invitation.save(update_fields=["revoked_at"])
        consigner(
            dossier, Journal.Action.INVITATION_ANNULEE, request=request,
            objet=invitation.email,
        )
        messages.success(
            request,
            _("Invitation annulée pour %(email)s.") % {"email": invitation.email},
        )
    return redirect("dossier_share", dossier_id=dossier.pk)


def invitation_accept(request, token):
    """Page d'acceptation d'une invitation.

    Le jeton vaut autorisation : qui le détient peut l'utiliser, avec le
    compte sous lequel il est connecté. C'est la nature d'un lien
    transmissible, et la raison pour laquelle l'octroi direct reste préférable
    pour les personnes que l'on connaît déjà.

    L'acceptation exige un POST : un GET suffirait sinon à consommer
    l'invitation depuis un aperçu de lien ou un préchargement.
    """
    invitation = get_object_or_404(Invitation, token=token)

    deja_autorise = (
        invitation.dossier.owner_id == request.user.id
        or invitation.dossier.accesses.filter(user=request.user).exists()
    )

    if request.method == "POST" and invitation.is_usable and not deja_autorise:
        DossierAccess.objects.create(
            dossier=invitation.dossier,
            user=request.user,
            role=invitation.role,
            origin=DossierAccess.Origin.INVITATION,
            granted_by=invitation.created_by,
        )
        invitation.accepted_at = timezone.now()
        invitation.accepted_by = request.user
        invitation.save(update_fields=["accepted_at", "accepted_by"])
        consigner(
            invitation.dossier, Journal.Action.INVITATION_ACCEPTEE, request=request,
            objet=invitation.email, details={"role": invitation.role},
        )
        messages.success(
            request,
            _("Vous avez désormais accès au dossier « %(dossier)s ».")
            % {"dossier": invitation.dossier.name},
        )
        return redirect("timeline", dossier_id=invitation.dossier_id)

    return render(
        request,
        "events/invitation_accept.html",
        {"invitation": invitation, "deja_autorise": deja_autorise},
    )


def share_link_create(request, dossier_id):
    """Émet un lien de consultation anonyme. POST uniquement."""
    dossier = get_dossier_for_owner(request.user, dossier_id)

    if request.method != "POST":
        return redirect("dossier_share", dossier_id=dossier.pk)

    form = ShareLinkForm(request.POST)
    if form.is_valid():
        lien = form.save(commit=False)
        lien.dossier = dossier
        lien.created_by = request.user
        lien.expires_at = form.expiration()
        lien.save()
        consigner(
            dossier, Journal.Action.LIEN_CREE, request=request, lien=lien,
            objet=lien.label,
            details={
                "role": lien.role,
                "expire": lien.expires_at.isoformat() if lien.expires_at else None,
            },
        )
        messages.success(
            request,
            _(
                "Lien créé. Toute personne qui le détient pourra consulter ce "
                "dossier, sans compte et sans trace nominative."
            ),
        )
    return redirect("dossier_share", dossier_id=dossier.pk)


def share_link_revoke(request, dossier_id, link_id):
    """Coupe un lien. POST uniquement, et sans délai."""
    if request.method != "POST":
        return JsonResponse({"success": False}, status=405)

    dossier = get_dossier_for_owner(request.user, dossier_id)
    lien = get_object_or_404(dossier.share_links, pk=link_id)
    if lien.is_active:
        lien.revoked_at = timezone.now()
        lien.save(update_fields=["revoked_at"])
        consigner(
            dossier, Journal.Action.LIEN_REVOQUE, request=request, lien=lien,
            objet=lien.label,
        )
        messages.success(request, _("Lien révoqué. Il ne donne plus accès à rien."))
    return redirect("dossier_share", dossier_id=dossier.pk)
