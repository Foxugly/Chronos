"""Enregistrer un courriel analysé (`events.courriels.Analyse`) comme évènement.

Tout se fait dans une transaction : l'évènement, ses pièces, la mémoire du
courriel importé et le journal existent ensemble ou pas du tout.

Les pièces passent par les mêmes contrôles que le formulaire
(`_verifier_fichier` : extension, taille) et le même rangement
(`_attach_document` : un contenu déjà présent dans le dossier est rattaché,
pas stocké deux fois). Une pièce refusée n'empêche pas l'import du reste :
elle est nommée dans le journal et dans la version texte du courriel.
"""

import datetime
import hashlib
import re
from collections import Counter
from contextlib import contextmanager
from email.utils import getaddresses

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import connection, transaction
from django.utils import timezone

from .couleurs import prochaine_couleur
from .courriels import _bloc_de_transfert, adresses, analyser_en_couches, en_markdown, lire, texte_en_html
from .graph import ClientGraph
from .courriels import BRUXELLES
from .liens import (
    adresse_de, citation, citations, empreinte, empreinte_d_evenement, empreinte_de_couche,
)
from .journal import consigner, consigner_pieces, instantane
from .models import CourrielImporte, Dossier, Event, Expediteur, Journal
from .sanitize import nettoyer


def dossier_destinataire(adresses):
    """Le dossier dont l'alias figure parmi les destinataires, ou `None`."""
    adresses = {a.lower() for a in adresses}
    for dossier in Dossier.objects.exclude(alias_courriel=None).exclude(alias_courriel=""):
        if dossier.alias_courriel.lower() in adresses:
            return dossier
    return None


def expediteur_du_dossier(dossier, texte):
    """L'expéditeur du dossier pour « Nom <adresse> », créé à son premier
    courriel avec la première couleur libre de la palette. `None` si le texte
    ne contient aucune adresse lisible."""
    adresses = [(nom, adresse.lower()) for nom, adresse in getaddresses([texte or ""]) if "@" in adresse]
    if not adresses:
        # `x@y <x@y>` et `Nom [mailto:x@y]` (Outlook) échappent à `getaddresses`.
        adresse = adresse_de(texte)
        if not adresse:
            return None
        paires = getaddresses([texte or ""])
        nom = paires[0][0] if paires else ""
        adresses = [("" if "@" in nom else nom, adresse)]
    nom, adresse = adresses[0]
    expediteur = Expediteur.objects.filter(dossier=dossier, adresse=adresse).first()
    if expediteur is None:
        prises = dossier.expediteurs.values_list("couleur", flat=True)
        expediteur = Expediteur.objects.create(
            dossier=dossier, adresse=adresse, nom=nom[:255], couleur=prochaine_couleur(prises),
        )
    elif nom and not expediteur.nom:
        expediteur.nom = nom[:255]
        expediteur.save(update_fields=["nom"])
    return expediteur


def cle_du_courriel(analyse):
    """La clé d'une couche : sa clé lue dans le texte, sinon le Message-ID du
    courriel, sinon l'empreinte de ses octets."""
    return (analyse.cle or analyse.message_id
            or "sha256:" + hashlib.sha256(analyse.eml).hexdigest())[:255]


def _cles(analyse):
    """Toutes les clés sous lesquelles cette couche a pu être importée."""
    return [c for c in (cle_du_courriel(analyse), analyse.cle_ancienne) if c]


def deja_importe(dossier, analyse):
    return CourrielImporte.objects.filter(dossier=dossier, message_id__in=_cles(analyse)).exists()


def evenement_importe(dossier, analyse):
    """L'évènement déjà créé pour cette couche dans ce dossier, ou `None`."""
    trace = (CourrielImporte.objects.filter(dossier=dossier, message_id__in=_cles(analyse))
             .exclude(evenement=None).select_related("evenement").first())
    return trace.evenement if trace else None


def _traces(dossier):
    return list(CourrielImporte.objects.filter(dossier=dossier).exclude(evenement=None)
                .select_related("evenement"))


def empreinte_de_trace(trace):
    return empreinte_d_evenement(trace.evenement, trace.expediteur)


def citation_de_trace(trace):
    return empreinte(trace.cite_de, trace.cite_le, trace.cite_objet) if trace.cite_le else None


def _richesse(evenement):
    """Les pièces autres que le courriel lui-même (.eml) et sa version texte (.md)."""
    return sum(1 for d in evenement.documents.all() if not d.title.lower().endswith((".eml", ".md")))


def meme_courriel(couche, traces):
    """L'évènement qui est déjà ce courriel, arrivé par un autre chemin — ou `None`.

    Même empreinte (expéditeur, objet, heure à 20 min près), mais jamais un courriel
    et sa réponse : même fil, même expéditeur, quelques minutes d'écart, et pourtant
    deux courriels (n° 4 et 5 du dossier réel)."""
    soi, cite = empreinte_de_couche(couche), citation(couche)
    candidats = [
        t.evenement for t in traces
        if soi.correspond(empreinte_de_trace(t))
        and not (cite and cite.correspond(empreinte_de_trace(t)))
        and not soi.correspond(citation_de_trace(t))
    ]
    return max(candidats, key=lambda e: (_richesse(e), -e.pk)) if candidats else None


def relier(evenement, couche, traces):
    """Relie `evenement` au courriel qu'il cite (en amont) et ceux qui le citent à lui
    (en aval). Rend la liste des `(réponse, courriel cité)` reliés.

    En amont, les citations sont essayées dans l'ordre : si le courriel auquel il répond
    n'est pas dans le dossier, on le relie au plus proche ancêtre cité qui y est (n° 18
    du dossier réel). En aval, un courriel relié ainsi à un ancêtre est **recentré** sur
    celui qui arrive s'il est le courriel qu'il cite directement."""
    relies = []
    if not couche.transfert and evenement.reponse_a_id is None:
        for cite in citations(couche):
            amont = [t.evenement for t in traces
                     if t.evenement_id != evenement.pk and cite.correspond(empreinte_de_trace(t))]
            if amont:
                cible = max(amont, key=lambda e: (_richesse(e), -e.pk))
                if cible.reponse_a_id != evenement.pk:
                    Event.objects.filter(pk=evenement.pk).update(reponse_a=cible)
                    evenement.reponse_a = cible
                    relies.append((evenement, cible))
                break
    soi = empreinte_de_couche(couche)
    for t in traces:
        aval = t.evenement
        if aval.pk == evenement.pk or aval.reponse_a_id == evenement.pk or evenement.reponse_a_id == aval.pk:
            continue
        if not soi.correspond(citation_de_trace(t)):
            continue
        actuel = aval.reponse_a
        if actuel is None or _moment(actuel) < soi.moment:
            Event.objects.filter(pk=aval.pk).update(reponse_a=evenement)
            aval.reponse_a = evenement
            relies.append((aval, evenement))
    return relies


#: Mots du début d'un courriel qu'un transfert doit contenir pour qu'on l'en dise la suite.
MOTS_DE_PREUVE = 25


def _mots(texte):
    import html as _html

    from django.utils.html import strip_tags

    return re.sub(r"[^\w]+", " ", _html.unescape(strip_tags(texte or "")).lower()).split()


def transfere_par_contenu(evenement, texte):
    """Le courriel du dossier qu'un transfert contient, quand l'en-tête de ce courriel
    a disparu du texte transféré (Outlook, converti depuis le HTML : n° 12 du dossier
    réel, qui transfère la réponse de l'architecte sans son « De : … »).

    Preuve exigée : les `MOTS_DE_PREUVE` premiers mots de son texte, à l'identique et
    dans l'ordre. Le plus récent avant le transfert, s'il y en a plusieurs."""
    contenu = " ".join(_mots(texte))
    moment = _moment(evenement)
    candidats = []
    for autre in Event.objects.filter(dossier_id=evenement.dossier_id).exclude(pk=evenement.pk):
        mots = _mots(autre.description)
        if len(mots) < MOTS_DE_PREUVE or _moment(autre) > moment:
            continue
        if " ".join(mots[:MOTS_DE_PREUVE]) in contenu:
            candidats.append(autre)
    return max(candidats, key=_moment) if candidats else None


def _moment(evenement):
    heure = evenement.heure or datetime.time(0, 0)
    return datetime.datetime.combine(evenement.date, heure).replace(tzinfo=BRUXELLES)


def champs_cites(analyse):
    """`(de, le, objet)` du courriel que cette couche cite, à stocker ; vides sinon."""
    cite = citation(analyse)
    if cite is None:
        return "", None, ""
    champs = _bloc_de_transfert(analyse.texte or "")[1]
    return champs.get("de", "")[:255], cite.moment, champs.get("objet", "")[:255]


def _memoriser(dossier, analyse, evenement):
    cite_de, cite_le, cite_objet = champs_cites(analyse)
    CourrielImporte.objects.create(
        dossier=dossier, message_id=cle_du_courriel(analyse), evenement=evenement,
        expediteur=analyse.expediteur[:255], cite_de=cite_de, cite_le=cite_le, cite_objet=cite_objet,
    )


def importer_couches(dossier, couches):
    """Une couche, un évènement : de l'origine au plus récent, chacun relié à
    celui qu'il transfère (`Event.transfert_de`) et à celui qu'il cite
    (`Event.reponse_a`, `relier`). Une couche déjà présente n'est pas recréée —
    reconnue par sa clé, ou par son empreinte quand elle arrive par un autre
    chemin (`meme_courriel`) — mais le courriel suivant s'y relie : la mise en
    demeure importée hier, transférée à l'avocate aujourd'hui. Rend les
    évènements créés ; la chaîne entière ou rien.
    """
    crees = []
    with transaction.atomic():
        traces = _traces(dossier)
        precedent = None
        for couche in couches:
            evenement = evenement_importe(dossier, couche)
            if evenement is None:
                evenement = meme_courriel(couche, traces)
                if evenement is not None:
                    # Le même courriel, arrivé par un autre chemin : on retient sa clé.
                    _memoriser(dossier, couche, evenement)
            if evenement is None:
                evenement = importer(dossier, couche, transfert_de=precedent)
                crees.append(evenement)
            elif precedent is not None and couche.transfert and evenement.transfert_de_id is None:
                Event.objects.filter(pk=evenement.pk).update(transfert_de=precedent)
                evenement.transfert_de = precedent
            if couche.transfert:
                # L'en-tête du courriel transféré a pu disparaître : le contenu tranche,
                # s'il désigne un courriel plus récent que celui que les blocs ont trouvé.
                par_contenu = transfere_par_contenu(evenement, couche.texte)
                actuel = evenement.transfert_de if evenement.transfert_de_id else None
                if par_contenu is not None and par_contenu != actuel and (
                        actuel is None or _moment(par_contenu) > _moment(actuel)):
                    Event.objects.filter(pk=evenement.pk).update(transfert_de=par_contenu)
                    evenement.transfert_de = par_contenu
            traces = _traces(dossier)
            for reponse, cite in relier(evenement, couche, traces):
                consigner(dossier, Journal.Action.EVENEMENT_MODIFIE, objet=reponse.title, evenement=reponse,
                          details={"changements": {"reponse_a": ["", cite.title]}, "source": "courriel"})
            precedent = evenement
    return crees


def importer(dossier, analyse, transfert_de=None):
    """Crée l'évènement et ses pièces. Rend l'évènement, ou `None` s'il existait déjà."""
    # Import local : `views` importe ce module indirectement via les modèles.
    from .views.pieces import _attach_document, _verifier_fichier

    if deja_importe(dossier, analyse):
        return None

    moment = analyse.moment
    base = "courriel-%s" % moment.strftime("%Y%m%d-%H%M")
    description = texte_en_html(analyse.texte)
    if analyse.date_approchee:
        description = "<p><em>%s</em></p>%s" % (
            "Date d'origine illisible : date de réception du transfert.", description
        )

    with transaction.atomic():
        evenement = Event.objects.create(
            dossier=dossier,
            expediteur=expediteur_du_dossier(dossier, analyse.expediteur),
            title=analyse.titre,
            date=moment.date(),
            heure=moment.time().replace(microsecond=0),
            description=nettoyer(description),
            transfert_de=transfert_de if transfert_de and transfert_de.dossier_id == dossier.pk else None,
            destinataires=analyse.destinataires or "",
        )

        acceptees, refusees = [], []
        for piece in analyse.pieces:
            fichier = ContentFile(piece.contenu, name=piece.nom)
            try:
                _verifier_fichier(fichier)
            except ValidationError as erreur:
                refusees.append((piece.nom, " ".join(erreur.messages)))
                continue
            acceptees.append(fichier)

        fichiers = [
            ContentFile(analyse.eml, name=base + ".eml"),
            ContentFile(en_markdown(analyse, refusees).encode("utf-8"), name=base + ".md"),
        ] + acceptees
        pieces = [_attach_document(evenement, f) for f in fichiers]

        _memoriser(dossier, analyse, evenement)
        consigner(
            dossier, Journal.Action.COURRIEL_IMPORTE, objet=evenement.title, evenement=evenement,
            details={
                "expediteur": analyse.expediteur,
                "forme": analyse.forme,
                "message_id": cle_du_courriel(analyse),
                "date_approchee": analyse.date_approchee,
                "refusees": refusees,
                "valeurs": instantane(evenement),
                "source": "courriel",
            },
        )
        consigner_pieces(evenement, pieces, source="courriel")
    return evenement


# --- Relever la boîte d'import ----------------------------------------------
# Partagé par l'import planifié (`manage.py importer_courriels`) et le bouton
# « Relever les courriels » d'un dossier (`views.dossier_relever`).

#: Les en-têtes où peut figurer l'alias d'un courriel transféré.
ENTETES_DESTINATAIRES = ("To", "Cc", "Delivered-To", "X-Original-To", "Resent-To")

#: Clé du verrou consultatif PostgreSQL commun aux deux chemins (« Chron » en hexadécimal).
CLE_VERROU = 0x4368726F6E


def adresses_de_la_boite():
    """Les adresses qui ne font pas d'une enveloppe une couche : les alias des
    dossiers et la boîte d'import elle-même."""
    alias = Dossier.objects.exclude(alias_courriel=None).exclude(alias_courriel="")
    return {a.lower() for a in alias.values_list("alias_courriel", flat=True)} | {
        (settings.COURRIEL_BOITE or "").lower()} - {""}


def import_configure():
    """Vrai quand la boîte d'import est joignable : sans cela, pas de relève."""
    return all((settings.GRAPH_TENANT_ID, settings.GRAPH_CLIENT_ID,
                settings.GRAPH_CLIENT_SECRET, settings.COURRIEL_BOITE))


@contextmanager
def verrou_de_releve():
    """Un seul import à la fois : rend `True` si le verrou est pris, `False` si
    un autre import tourne déjà.

    Verrou consultatif PostgreSQL, tenu par la session : la minuterie et un
    clic sur le bouton ne relèvent jamais la boîte en même temps (deux
    passages simultanés importeraient le même courriel deux fois et l'un
    échouerait sur la contrainte d'unicité). SQLite (développement, tests) n'a
    qu'un processus : pas de verrou.
    """
    if connection.vendor != "postgresql":
        yield True
        return
    with connection.cursor() as curseur:
        curseur.execute("SELECT pg_try_advisory_lock(%s)", [CLE_VERROU])
        pris = curseur.fetchone()[0]
    try:
        yield pris
    finally:
        if pris:
            with connection.cursor() as curseur:
                curseur.execute("SELECT pg_advisory_unlock(%s)", [CLE_VERROU])


class Releve:
    """Le bilan d'une relève, compté par dossier : le bouton n'annonce que ce
    qui concerne le dossier d'où l'on clique."""

    def __init__(self):
        self.importes = Counter()   # identifiant de dossier → courriels importés
        self.deja = Counter()       # identifiant de dossier → déjà importés, rangés
        self.ignores = 0            # sans alias : ni déplacés ni cités
        self.erreurs = 0


def relever_la_boite(*, essai=False, decrire=None, signaler=None):
    """Parcourt les courriels reçus pendant `COURRIEL_FENETRE` : ceux qui visent
    l'alias d'un dossier sont importés puis rangés dans un dossier Outlook du
    nom de ce dossier ; les autres ne sont ni touchés ni cités. Un courriel en
    échec reste dans la boîte de réception (retenté au passage suivant).

    `decrire(analyse, dossier)` et `signaler(texte)` reçoivent ce qui mérite
    d'être affiché (la commande les écrit ; la vue n'en a pas besoin). Lève
    `ErreurGraph` si la boîte est injoignable.
    """
    decrire = decrire or (lambda analyse, dossier: None)
    signaler = signaler or (lambda texte: None)
    client = ClientGraph(settings.GRAPH_TENANT_ID, settings.GRAPH_CLIENT_ID,
                         settings.GRAPH_CLIENT_SECRET, settings.COURRIEL_BOITE)
    bilan = Releve()
    dossiers_outlook = {}  # nom du dossier Chronos → identifiant du dossier Outlook

    ignorer = adresses_de_la_boite()
    for message in client.messages_recus(timezone.now() - settings.COURRIEL_FENETRE):
        octets = client.mime(message["id"])
        dossier = dossier_destinataire(adresses(lire(octets), *ENTETES_DESTINATAIRES))
        if dossier is None:
            bilan.ignores += 1  # ce courriel ne regarde pas Chronos
            continue
        try:
            couches = analyser_en_couches(octets, ignorer=ignorer)
            for couche in couches:
                decrire(couche, dossier)
            if essai:
                continue
            if importer_couches(dossier, couches):
                bilan.importes[dossier.pk] += 1
            else:
                bilan.deja[dossier.pk] += 1
                signaler("  déjà importé : rangé sans rien créer.")
            if dossier.name not in dossiers_outlook:
                dossiers_outlook[dossier.name] = client.dossier(dossier.name)
            client.deplacer(message["id"], dossiers_outlook[dossier.name])
        except Exception as erreur:  # un courriel illisible ne bloque pas les suivants
            bilan.erreurs += 1
            signaler("  ÉCHEC : %s" % erreur)
    return bilan
