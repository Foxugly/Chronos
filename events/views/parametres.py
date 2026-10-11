"""Les paramètres d'un dossier : alias de courriel, relève, nom, étiquettes, expéditeurs."""

import secrets

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.text import slugify
from django.utils.translation import gettext as _, ngettext

from .. import exchange
from ..couleurs import valider_couleur
from ..forms import AliasCourrielForm, DossierNomForm, suffixe_des_alias
from ..graph import ErreurGraph
from ..import_courriels import import_configure, relever_la_boite, verrou_de_releve
from ..journal import consigner
from ..models import Dossier, Journal
from ..permissions import get_dossier_for_owner, get_dossier_for_read, get_dossier_for_write
from .evenements import _est_ajax, adresse_de_retour


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

    Ouverte dans le modal des vues du dossier : en AJAX, le GET rend le
    fragment seul et le POST répond `{"modifies": n, "erreurs": [...]}` — la
    page se recharge s'il y a eu un changement. Sans JavaScript, la page
    entière et la redirection d'avant.
    """
    dossier = get_dossier_for_read(request.user, dossier_id)
    peut_ecrire = Dossier.objects.writable_by(request.user).filter(pk=dossier.pk).exists()
    if request.method == "POST":
        dossier = get_dossier_for_write(request.user, dossier_id)
        modifies, erreurs = 0, []
        for expediteur in dossier.expediteurs.all():
            nouvelle = (request.POST.get("couleur-%d" % expediteur.pk) or "").strip().lower()
            if not nouvelle or nouvelle == expediteur.couleur:
                continue
            try:
                valider_couleur(nouvelle)
            except ValidationError:
                erreurs.append(_("Couleur invalide pour %(nom)s.") % {"nom": expediteur})
                continue
            consigner(
                dossier, Journal.Action.DOSSIER_MODIFIE, request=request, objet=str(expediteur),
                details={"modele": "expediteur",
                         "changements": {"couleur": [expediteur.couleur, nouvelle]}},
            )
            expediteur.couleur = nouvelle
            expediteur.save(update_fields=["couleur"])
            modifies += 1
        if _est_ajax(request):
            return JsonResponse({"modifies": modifies, "erreurs": erreurs})
        for erreur in erreurs:
            messages.error(request, erreur)
        return redirect("dossier_expediteurs", dossier_id=dossier.pk)
    expediteurs = dossier.expediteurs.annotate(nombre=Count("evenements"))
    gabarit = "events/_expediteurs.html" if _est_ajax(request) else "events/expediteurs.html"
    return render(request, gabarit, {
        "dossier": dossier, "expediteurs": expediteurs, "peut_ecrire": peut_ecrire,
    })
