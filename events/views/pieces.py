"""Les pièces : contrôle à l'envoi, stockage, téléchargement, retrait d'un évènement."""

import hashlib
import mimetypes
from urllib.parse import quote

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.utils.http import content_disposition_header
from django.utils.translation import gettext as _
from django.views.decorators.clickjacking import xframe_options_sameorigin

from ..journal import consigner
from ..models import Document, Journal
from ..permissions import get_document_for_read, get_document_for_write, get_event_for_write


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
