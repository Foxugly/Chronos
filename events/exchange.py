"""Alias Exchange Online de la boîte d'import : les créer, les retirer, les vérifier.

Repris de PushIT (`exchange/services.py`, `scripts/exchange/manage_alias.ps1`),
éprouvé en production sur la même organisation Microsoft 365. Graph ne sait
pas modifier les adresses d'une boîte : seul `Set-Mailbox -EmailAddresses`
le fait, d'où un script PowerShell (`pwsh` + module ExchangeOnlineManagement)
authentifié par certificat (application Entra, `Exchange.ManageAsApp`).

Le script est **copié à l'identique** de PushIT : `scripts/exchange/manage_alias.ps1`.

Défense en profondeur, comme PushIT : adresses validées en Python avant
d'appeler `pwsh` ; `shell=False` et une liste d'arguments ; secrets passés par
l'environnement du sous-processus, jamais sur la ligne de commande ; délai
maximal ; codes d'erreur du script traduits en exceptions.

**Différence avec PushIT :** l'échec n'est pas avalé. Un alias que Chronos
afficherait comme actif sans qu'il existe dans Exchange ferait refuser les
courriels transférés — en silence pour le propriétaire. `activer` ajoute donc
d'abord dans Exchange et ne rend la main qu'en cas de succès ; c'est l'appelant
(la vue) qui n'enregistre qu'ensuite.

Sans configuration (`EXCHANGE_APP_ID`, `EXCHANGE_TENANT`, certificat,
`COURRIEL_BOITE`), rien n'est appelé : l'alias se crée à la main, comme avant.
"""

import json
import logging
import os
import re
import subprocess

from django.conf import settings

logger = logging.getLogger(__name__)

_ADRESSE = re.compile(r"^[A-Za-z0-9._+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
_INTERDITS = frozenset("\"'`$;|&<>\n\r\t\\")
_TRANSMISES = ("EXCHANGE_APP_ID", "EXCHANGE_TENANT", "EXCHANGE_CERT_THUMBPRINT",
               "EXCHANGE_CERT_FILE_PATH", "EXCHANGE_CERT_PASSWORD")


class ErreurExchange(Exception):
    """Toute erreur Exchange. `code` reprend le code d'erreur du script."""

    code = "exchange_error"

    def __init__(self, message="", code=None):
        super().__init__(message)
        if code:
            self.code = code


class AliasDejaPresent(ErreurExchange):
    code = "alias_already_exists"


class AliasAbsent(ErreurExchange):
    code = "alias_not_found"


_EXCEPTIONS = {"alias_already_exists": AliasDejaPresent, "alias_not_found": AliasAbsent}


def configure():
    """Vrai si l'application peut gérer les alias elle-même."""
    return bool(
        settings.EXCHANGE_APP_ID and settings.EXCHANGE_TENANT and settings.COURRIEL_BOITE
        and (settings.EXCHANGE_CERT_THUMBPRINT or settings.EXCHANGE_CERT_FILE_PATH)
    )


def _valider(adresse):
    if not isinstance(adresse, str) or not adresse or len(adresse) > 320:
        raise ErreurExchange("Adresse vide ou trop longue.", "invalid_input")
    if any(c in _INTERDITS for c in adresse) or not _ADRESSE.match(adresse):
        raise ErreurExchange(f"Adresse invalide : {adresse!r}", "invalid_input")


def _environnement():
    garder = ("PATH", "HOME", "USER", "USERNAME", "USERPROFILE", "TEMP", "TMP", "TMPDIR",
              "PSModulePath", "SystemRoot")
    env = {k: os.environ[k] for k in garder if k in os.environ}
    for cle in _TRANSMISES:
        valeur = getattr(settings, cle, "") or os.environ.get(cle, "")
        if valeur:
            env[cle] = valeur
    return env


def _executer(action, alias=None):
    boite = settings.COURRIEL_BOITE
    _valider(boite)
    commande = [settings.EXCHANGE_PWSH, "-NoProfile", "-NonInteractive", "-File",
                str(settings.EXCHANGE_PS_SCRIPT_PATH), "-Action", action, "-Mailbox", boite]
    if alias is not None:
        _valider(alias)
        commande += ["-Alias", alias]
    try:
        fini = subprocess.run(commande, capture_output=True, text=True, check=False, shell=False,
                              timeout=settings.EXCHANGE_PS_TIMEOUT, env=_environnement())
    except subprocess.TimeoutExpired as exc:
        raise ErreurExchange(f"Exchange n'a pas répondu en {settings.EXCHANGE_PS_TIMEOUT} s.", "timeout") from exc
    except FileNotFoundError as exc:
        raise ErreurExchange("PowerShell (pwsh) est introuvable sur ce serveur.", "missing_config") from exc
    sortie = (fini.stdout or "").strip()
    derniere = next((l for l in reversed(sortie.splitlines()) if l.strip()), "")
    try:
        resultat = json.loads(derniere)
    except json.JSONDecodeError as exc:
        logger.error("exchange_sortie_illisible", extra={"stderr": (fini.stderr or "")[:500]})
        raise ErreurExchange("Réponse illisible de PowerShell.") from exc
    if isinstance(resultat, dict) and resultat.get("success") is True:
        return resultat.get("data")
    code = str((resultat or {}).get("error_code") or "exchange_error")
    message = str((resultat or {}).get("error") or "Erreur Exchange inconnue.")
    logger.error("exchange_echec", extra={"action": action, "alias": alias, "code": code})
    raise _EXCEPTIONS.get(code, ErreurExchange)(message, code)


def ajouter(alias):
    """Ajoute l'alias à la boîte d'import. Déjà présent : rien à faire."""
    try:
        _executer("add", alias)
    except AliasDejaPresent:
        pass


def retirer(alias):
    """Retire l'alias de la boîte d'import. Déjà absent : rien à faire."""
    if alias.strip().lower() == (settings.COURRIEL_BOITE or "").strip().lower():
        # Jamais l'adresse principale de la boîte elle-même.
        raise ErreurExchange("Refus de retirer l'adresse principale de la boîte.", "invalid_input")
    try:
        _executer("remove", alias)
    except AliasAbsent:
        pass


def est_actif(alias):
    """Vrai si l'alias figure parmi les adresses de la boîte d'import."""
    adresses = _executer("list") or []
    cible = alias.strip().lower()
    return any(str(a).strip().lower() in (cible, "smtp:" + cible) for a in adresses)
