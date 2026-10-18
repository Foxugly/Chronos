"""Ce que les journaux du serveur (journald) gardent d'une requête.

Django journalise le chemin des requêtes refusées ou en erreur (« Not Found:
/partage/<jeton>/ », « Internal Server Error: … »). Un jeton de partage ouvre
tout un dossier et `q=` est une recherche dans un dossier de litige : ni l'un
ni l'autre n'a à rester dans un journal (audit du 2026-10-18, I2). Le filtre
réécrit le message avec le même masque que Sentry (`config/sentry_filtres.py`).
"""

import logging

from config.sentry_filtres import masquer_jetons


class MasquerJetons(logging.Filter):
    def filter(self, record):
        message = record.getMessage()
        masque = masquer_jetons(message)
        if masque != message:
            record.msg, record.args = masque, None
        return True
