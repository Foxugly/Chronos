"""Alertes au propriétaire d'un dossier quand quelque chose demande son attention.

Un seul canal pour l'instant : un courriel (le moteur de `MAILERS`, Graph en
production). Pushover ou PushIT viendront s'ajouter dans `alerter` — les
appelants n'ont pas à changer.

Une alerte qui ne part pas ne doit pas casser ce qui l'a déclenchée : l'échec
est journalisé (donc remonté à Sentry), pas propagé.
"""

import datetime
import logging

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext as _

logger = logging.getLogger(__name__)

#: Catégorie Outlook posée sur un courriel dont l'échec d'import a été signalé :
#: un courriel qui la porte n'est plus signalé ; la retirer demande une relance.
CATEGORIE_ECHEC = "Chronos : échec d'import"


def alerter(utilisateur, sujet, texte):
    """Prévient `utilisateur`. Rend `True` si l'alerte est partie."""
    if not utilisateur.email:
        logger.error("Alerte impossible : l'utilisateur %s n'a pas d'adresse", utilisateur.pk)
        return False
    try:
        send_mail(subject=sujet, message=texte, from_email=None, recipient_list=[utilisateur.email])
    except Exception:
        logger.exception("Alerte non envoyée à l'utilisateur %s", utilisateur.pk)
        return False
    return True


def alerter_echec_import(dossier, message, erreur):
    """Prévient le propriétaire de `dossier` qu'un courriel adressé à son alias
    n'a pas pu être importé. `message` : l'entrée Graph (objet, expéditeur, date)."""
    expediteur = (message.get("from") or {}).get("emailAddress") or {}
    nom, adresse = expediteur.get("name") or "", expediteur.get("address") or ""
    recu = message.get("receivedDateTime") or ""
    try:
        recu = date_format(timezone.localtime(datetime.datetime.fromisoformat(recu.replace("Z", "+00:00"))),
                           "SHORT_DATETIME_FORMAT")
    except ValueError:
        pass
    sujet = _("Chronos — un courriel n'a pas pu être importé (%(dossier)s)") % {"dossier": dossier.name}
    texte = _(
        "Un courriel adressé au dossier « %(dossier)s » n'a pas pu être importé.\n\n"
        "Expéditeur : %(expediteur)s\n"
        "Objet : %(objet)s\n"
        "Reçu le : %(recu)s\n"
        "Erreur : %(erreur)s\n\n"
        "Le courriel reste dans la boîte de réception de %(boite)s, marqué de la "
        "catégorie « %(categorie)s ». Il est retenté à chaque relève pendant 24 heures ; "
        "vous ne serez pas prévenu une seconde fois tant qu'il porte cette catégorie.\n"
    ) % {
        "dossier": dossier.name,
        "expediteur": ("%s <%s>" % (nom, adresse) if nom and adresse else nom or adresse) or "?",
        "objet": message.get("subject") or _("(sans objet)"),
        "recu": recu or "?",
        "erreur": type(erreur).__name__,
        "boite": settings.COURRIEL_BOITE,
        "categorie": CATEGORIE_ECHEC,
    }
    return alerter(dossier.owner, sujet, texte)
