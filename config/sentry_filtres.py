"""Ce que Chronos laisse partir vers Sentry (`before_send`).

Un dossier de litige n'a rien à faire dans un outil tiers : en plus de
`send_default_pii=False` et `include_local_variables=False` (settings), les
échecs d'import de courriels perdent le message de leur exception, qui peut
citer l'objet, une adresse ou un extrait du courriel. Le type et la pile
d'appels (du code, sans valeurs) suffisent à retrouver le bug.
"""

from django.core.exceptions import DisallowedHost

#: Journal des échecs d'import (`events.import_courriels.relever_la_boite`).
JOURNAL_IMPORT = "events.import_courriels"


def avant_envoi(evenement, indice):
    erreur = (indice or {}).get("exc_info", (None, None))[1]
    if isinstance(erreur, DisallowedHost):
        # Les robots qui appellent l'IP du serveur : pas une erreur de l'application.
        return None
    if evenement.get("logger") == JOURNAL_IMPORT:
        for exception in (evenement.get("exception") or {}).get("values", []):
            exception["value"] = "(contenu retiré : %s)" % exception.get("type", "exception")
    return evenement
