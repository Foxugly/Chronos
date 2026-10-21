from django.urls import path

from .share_views import (
    share_document_detach,
    share_dossier_export,
    share_document_download,
    share_event_add,
    share_event_delete,
    share_event_edit,
    share_event_detail,
    share_timeline,
)
from .views import (
    compte_supprimer,
    document_detach,
    document_download,
    dossier_access_revoke,
    dossier_creer,
    dossier_list,
    dossier_invite,
    dossier_alias,
    dossier_alias_verifier,
    dossier_relever,
    dossier_parametres,
    dossier_etiquette,
    dossier_doublons,
    dossier_manquants,
    doublons_ecarter,
    doublons_fusionner,
    dossier_export,
    dossier_expediteurs,
    dossier_journal,
    dossier_share,
    event_add,
    event_detail,
    invitation_accept,
    invitation_revoke,
    share_link_create,
    share_link_revoke,
    event_delete,
    event_edit,
    timeline,
)

urlpatterns = [
    path("partage/<str:token>/export/", share_dossier_export, name="share_dossier_export"),
    path("", dossier_list, name="dossier_list"),
    path("dossiers/nouveau/", dossier_creer, name="dossier_creer"),
    path("compte/supprimer/", compte_supprimer, name="compte_supprimer"),
    path("dossiers/<int:dossier_id>/", timeline, name="timeline"),
    path("dossiers/<int:dossier_id>/events/add/", event_add, name="event_add"),
    path("dossiers/<int:dossier_id>/journal/", dossier_journal, name="dossier_journal"),
    path("dossiers/<int:dossier_id>/expediteurs/", dossier_expediteurs, name="dossier_expediteurs"),
    path("dossiers/<int:dossier_id>/export/", dossier_export, name="dossier_export"),
    path("dossiers/<int:dossier_id>/alias/", dossier_alias, name="dossier_alias"),
    path("dossiers/<int:dossier_id>/alias/verifier/", dossier_alias_verifier, name="dossier_alias_verifier"),
    path("dossiers/<int:dossier_id>/relever/", dossier_relever, name="dossier_relever"),
    path("dossiers/<int:dossier_id>/parametres/", dossier_parametres, name="dossier_parametres"),
    path(
        "dossiers/<int:dossier_id>/etiquettes/<int:etiquette_id>/",
        dossier_etiquette,
        name="dossier_etiquette",
    ),
    path("dossiers/<int:dossier_id>/doublons/", dossier_doublons, name="dossier_doublons"),
    path("dossiers/<int:dossier_id>/manquants/", dossier_manquants, name="dossier_manquants"),
    path("dossiers/<int:dossier_id>/doublons/fusionner/", doublons_fusionner, name="doublons_fusionner"),
    path("dossiers/<int:dossier_id>/doublons/ecarter/", doublons_ecarter, name="doublons_ecarter"),
    path(
        "dossiers/<int:dossier_id>/partage/", dossier_share, name="dossier_share"
    ),
    path(
        "dossiers/<int:dossier_id>/partage/inviter/",
        dossier_invite,
        name="dossier_invite",
    ),
    path(
        "dossiers/<int:dossier_id>/partage/invitations/<int:invitation_id>/annuler/",
        invitation_revoke,
        name="invitation_revoke",
    ),
    path(
        "dossiers/<int:dossier_id>/partage/liens/",
        share_link_create,
        name="share_link_create",
    ),
    path(
        "dossiers/<int:dossier_id>/partage/liens/<int:link_id>/revoquer/",
        share_link_revoke,
        name="share_link_revoke",
    ),
    path("invitation/<str:token>/", invitation_accept, name="invitation_accept"),
    path(
        "dossiers/<int:dossier_id>/partage/<int:access_id>/retirer/",
        dossier_access_revoke,
        name="dossier_access_revoke",
    ),
    path("events/<int:event_id>/", event_detail, name="event_detail"),
    path("events/<int:event_id>/edit/", event_edit, name="event_edit"),
    path("events/<int:event_id>/delete/", event_delete, name="event_delete"),
    path("documents/<int:document_id>/", document_download, name="document_download"),
    path(
        "events/<int:event_id>/documents/<int:document_id>/detach/",
        document_detach,
        name="document_detach",
    ),
    # ------------------------------------------------------------------
    # Consultation par lien secret, sans compte.
    #
    # Préfixe distinct et vues séparées : rien ici ne partage de code
    # avec les vues authentifiées, pour qu'un privilège ne puisse pas
    # s'y propager par inadvertance.
    # ------------------------------------------------------------------
    path("partage/<str:token>/", share_timeline, name="share_timeline"),
    path(
        "partage/<str:token>/events/<int:event_id>/",
        share_event_detail,
        name="share_event_detail",
    ),
    path(
        "partage/<str:token>/documents/<int:document_id>/",
        share_document_download,
        name="share_document_download",
    ),
    path(
        "partage/<str:token>/events/add/", share_event_add, name="share_event_add"
    ),
    path(
        "partage/<str:token>/events/<int:event_id>/edit/",
        share_event_edit,
        name="share_event_edit",
    ),
    path(
        "partage/<str:token>/events/<int:event_id>/delete/",
        share_event_delete,
        name="share_event_delete",
    ),
    path(
        "partage/<str:token>/events/<int:event_id>/documents/<int:document_id>/detach/",
        share_document_detach,
        name="share_document_detach",
    ),
]
