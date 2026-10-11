"""La liste des dossiers et les vues d'un dossier (onglets, filtres, calendriers)."""

from datetime import timedelta
from urllib.parse import urlencode

from django.db.models import Count
from django.shortcuts import render
from django.urls import reverse
from django.utils.formats import date_format
from django.utils.translation import gettext as _

from .. import calendrier, doublons as dedoublonnage, filtres as filtrage
from ..fils import courriels_manquants
from ..import_courriels import import_configure
from ..models import CourrielImporte, Dossier
from ..permissions import get_dossier_for_read
from .evenements import ICONES_DE_PIECE, IMAGE_EXTENSIONS


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
    # Comme « Doublons » : le bouton n'apparaît que s'il manque quelque chose.
    contexte["nb_manquants"] = len(courriels_manquants(dossier))
    if contexte["peut_ecrire"]:
        # Le bouton « Doublons » n'apparaît que s'il y a quelque chose à voir.
        contexte["nb_doublons"] = len(
            dedoublonnage.doublons_possibles(dossier, contexte["tous_les_evenements"])
        )
        contexte["peut_relever"] = bool(dossier.alias_courriel) and import_configure()
    return render(request, gabarit, contexte)
