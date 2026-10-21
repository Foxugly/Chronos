from django.contrib.auth import get_user_model

from events.models import Dossier, DossierAccess


def make_user(username, password="pw-for-tests", **kwargs):
    return get_user_model().objects.create_user(
        username=username, email=f"{username}@example.test", password=password, **kwargs
    )

def verifier_adresse(user):
    """Donne au compte une adresse principale vérifiée, comme après le clic sur
    le lien de confirmation : la vérification est obligatoire (2026-10-21), et
    un compte sans elle ne se connecte pas par le formulaire d'allauth."""
    from allauth.account.models import EmailAddress

    return EmailAddress.objects.create(user=user, email=user.email, primary=True, verified=True)



def make_dossier(owner, name="Dossier de test"):
    return Dossier.objects.create(name=name, owner=owner)


def grant(dossier, user, role, granted_by=None):
    return DossierAccess.objects.create(
        dossier=dossier, user=user, role=role, granted_by=granted_by
    )


def make_document(event, title="devis.pdf", content=b"contenu-du-devis"):
    """Crée une pièce liée à un évènement, comme le ferait un téléversement.

    Reprend la règle de la vue : la pièce appartient au dossier, porte
    l'empreinte de son contenu, et le lien vers l'évènement est ajouté
    ensuite. Écrire cela à la main dans chaque test ferait diverger les
    tests du code qu'ils gardent.
    """
    import hashlib

    from django.core.files.uploadedfile import SimpleUploadedFile

    from events.models import Document

    document = Document.objects.create(
        dossier=event.dossier,
        title=title,
        file=SimpleUploadedFile(title, content),
        content_hash=hashlib.sha256(content).hexdigest(),
    )
    document.events.add(event)
    return document
