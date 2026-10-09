"""Enregistrer un courriel analysé (`events.courriels.Analyse`) comme évènement.

Tout se fait dans une transaction : l'évènement, ses pièces, la mémoire du
courriel importé et le journal existent ensemble ou pas du tout.

Les pièces passent par les mêmes contrôles que le formulaire
(`_verifier_fichier` : extension, taille) et le même rangement
(`_attach_document` : un contenu déjà présent dans le dossier est rattaché,
pas stocké deux fois). Une pièce refusée n'empêche pas l'import du reste :
elle est nommée dans le journal et dans la version texte du courriel.
"""

import hashlib
from email.utils import getaddresses

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import transaction

from .couleurs import prochaine_couleur
from .courriels import en_markdown, texte_en_html
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
    adresses = [(nom, adresse.lower()) for nom, adresse in getaddresses([texte or ""]) if adresse]
    if not adresses:
        return None
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
    """L'identifiant du courriel d'origine ; à défaut, l'empreinte de ses octets."""
    return (analyse.message_id or "sha256:" + hashlib.sha256(analyse.eml).hexdigest())[:255]


def deja_importe(dossier, analyse):
    return CourrielImporte.objects.filter(dossier=dossier, message_id=cle_du_courriel(analyse)).exists()


def importer(dossier, analyse):
    """Crée l'évènement et ses pièces. Rend l'évènement, ou `None` s'il existait déjà."""
    # Import local : `views` importe ce module indirectement via les modèles.
    from .views import _attach_document, _verifier_fichier

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

        CourrielImporte.objects.create(
            dossier=dossier,
            message_id=cle_du_courriel(analyse),
            evenement=evenement,
            expediteur=analyse.expediteur[:255],
        )
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
