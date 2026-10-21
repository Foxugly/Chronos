"""Les vues des comptes, un module par sujet. Les liens de partage ont les leurs (`share_views`).

Ce paquet n'expose que les vues que `urls.py` route ; le reste s'importe depuis
son module.
"""

from .compte import compte_supprimer
from .courriels import dossier_manquants
from .dossiers import dossier_list, timeline
from .doublons import dossier_doublons, doublons_ecarter, doublons_fusionner
from .evenements import event_add, event_delete, event_detail, event_edit
from .journal import dossier_export, dossier_journal
from .parametres import (
    dossier_alias,
    dossier_alias_verifier,
    dossier_etiquette,
    dossier_expediteurs,
    dossier_parametres,
    dossier_relever,
)
from .partage import (
    dossier_access_revoke,
    dossier_invite,
    dossier_share,
    invitation_accept,
    invitation_revoke,
    share_link_create,
    share_link_revoke,
)
from .pieces import document_detach, document_download

__all__ = [
    "compte_supprimer",
    "dossier_manquants",
    "document_detach",
    "document_download",
    "dossier_access_revoke",
    "dossier_alias",
    "dossier_alias_verifier",
    "dossier_doublons",
    "dossier_etiquette",
    "dossier_expediteurs",
    "dossier_export",
    "dossier_invite",
    "dossier_journal",
    "dossier_list",
    "dossier_parametres",
    "dossier_relever",
    "dossier_share",
    "doublons_ecarter",
    "doublons_fusionner",
    "event_add",
    "event_delete",
    "event_detail",
    "event_edit",
    "invitation_accept",
    "invitation_revoke",
    "share_link_create",
    "share_link_revoke",
    "timeline",
]
