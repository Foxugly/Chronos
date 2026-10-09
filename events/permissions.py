"""Point de passage unique pour toute décision d'autorisation.

Un dossier auquel l'utilisateur n'a pas droit lève `Http404` et non
`PermissionDenied` : un 403 confirmerait son existence (spec D5).

Toutes les requêtes passent par `Dossier.objects.accessible_by` /
`writable_by`, qui lisent « propriétaire ou accès ». Le propriétaire n'a
pas de ligne dans `DossierAccess` (spec D2) : aucune vue ne doit donc
interroger `DossierAccess` directement.

Chaque helper fait **une** requête déjà filtrée par les droits, et non une
lecture suivie d'un contrôle. La différence n'est pas cosmétique : elle
ferme un oracle d'existence. Charger l'objet puis vérifier les droits fait
répondre « No Document matches… » pour un identifiant inexistant et
« No Event matches… » pour un identifiant interdit — deux messages
distinguables sous `DEBUG`, donc deux réponses différentes à la question
« ce document existe-t-il ? ». Une seule requête filtrée rend les deux cas
rigoureusement identiques.
"""

from django.shortcuts import get_object_or_404

from .models import Document, Dossier, Event


def get_dossier_for_read(user, dossier_id):
    return get_object_or_404(Dossier.objects.accessible_by(user), pk=dossier_id)


def get_dossier_for_write(user, dossier_id):
    return get_object_or_404(Dossier.objects.writable_by(user), pk=dossier_id)


def get_event_for_read(user, event_id):
    return get_object_or_404(
        Event.objects.select_related("dossier").filter(
            dossier__in=Dossier.objects.accessible_by(user)
        ),
        pk=event_id,
    )


def get_event_for_write(user, event_id):
    return get_object_or_404(
        Event.objects.select_related("dossier").filter(
            dossier__in=Dossier.objects.writable_by(user)
        ),
        pk=event_id,
    )


def get_document_for_read(user, document_id):
    """Une pièce porte son dossier en propre.

    Elle peut illustrer plusieurs évènements : « le » dossier de son
    évènement n'existe plus, et le déduire d'un évènement au hasard
    ouvrirait une fuite dès qu'une pièce serait liée à deux affaires.
    C'est `Document.dossier`, et lui seul, qui fait autorité.
    """
    return get_object_or_404(
        Document.objects.filter(dossier__in=Dossier.objects.accessible_by(user)),
        pk=document_id,
    )


def get_document_for_write(user, document_id):
    return get_object_or_404(
        Document.objects.filter(dossier__in=Dossier.objects.writable_by(user)),
        pk=document_id,
    )


def get_dossier_for_owner(user, dossier_id):
    """Dossier dont `user` est le propriétaire.

    Le partage est réservé au propriétaire. Un collaborateur en
    écriture peut tout modifier dans le dossier, mais pas décider qui
    y entre : sinon un seul compte partagé suffirait à élargir l'accès
    de proche en proche, sans que le propriétaire en sache rien.

    `Dossier.owner` fait foi, et non `DossierAccess` : le propriétaire
    n'y a pas de ligne (spec D2).
    """
    return get_object_or_404(Dossier.objects.owned_by(user), pk=dossier_id)
