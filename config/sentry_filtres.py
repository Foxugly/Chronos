"""Ce que Chronos laisse partir vers Sentry.

Règle du propriétaire : **aucune donnée personnelle, aucun contenu de courriel**
ne quitte le serveur vers Sentry. Un dossier de litige n'a rien à faire dans un
outil tiers. Les réglages par défaut du SDK ne suffisent pas : sentry-sdk joint
le corps des requêtes (`request.data`), l'URL complète avec sa query
string, les en-têtes (dont `Referer`), et les fils d'Ariane (messages de
journaux, URL des appels HTTP sortants, qui citent l'adresse de la boîte).

D'où une liste blanche plutôt qu'une liste noire :

- requête : méthode, URL sans query string, jeton de chemin masqué, quelques
  en-têtes sans valeur personnelle — ni corps, ni cookies, ni `env` ;
- utilisateur : un identifiant opaque, rien d'autre ;
- `extra` : jamais (les journaux y mettent un alias, la sortie de PowerShell…) ;
- erreurs du code `events.*` (import des courriels, Graph, alertes, Exchange) :
  le type de l'exception et la pile d'appels (du code, sans valeurs), jamais
  son message ni celui du journal ;
- partout ailleurs, adresses e-mail, jetons de chemin et query strings sont
  masqués dans les textes qui restent.
"""

import logging
import re
from urllib.parse import urlsplit, urlunsplit

from django.core.exceptions import DisallowedHost
from sentry_sdk.integrations.django import DjangoIntegration
from sentry_sdk.integrations.logging import LoggingIntegration

from config.sentry_scrub import FILTERED, scrub_event, scrub_span

#: Journal des échecs d'import (`events.import_courriels.relever_la_boite`).
JOURNAL_IMPORT = "events.import_courriels"

#: Code dont les erreurs peuvent citer un courriel, une adresse ou une boîte :
#: tout le paquet `events`, l'import en tête.
PREFIXE_SENSIBLE = "events"

#: Les seuls en-têtes de requête transmis.
ENTETES_PERMIS = frozenset({
    "accept",
    "accept-language",
    "content-length",
    "content-type",
    "host",
    "user-agent",
    "x-forwarded-proto",
})

JETON = "[token]"
RETIRE = "[retiré]"

#: Segments de chemin qui portent un secret (events/urls.py et allauth) :
#: lien de partage, invitation, confirmation d'e-mail, réinitialisation du mot
#: de passe (sauf la page fixe `done/`). Un test parcourt les routes pour que
#: toute nouvelle route à jeton soit couverte.
_CHEMIN_A_JETON = re.compile(
    r"(/(?:partage|invitation|confirm-email|password/reset/key)/)(?!done/)[^/?#\s\"']+"
)
_COURRIEL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_QUERY = re.compile(r"\?[^\s\"'#]*")


def masquer_jetons(texte):
    """Retire d'un texte libre ce qui ouvre un dossier ou trahit une recherche :
    jetons de chemin et query strings (aussi pour les journaux du serveur,
    `config/journaux.py`)."""
    if not isinstance(texte, str):
        return texte
    texte = _CHEMIN_A_JETON.sub(lambda m: m.group(1) + JETON, texte)
    return _QUERY.sub("", texte)


def masquer_texte(texte):
    """`masquer_jetons`, plus les adresses e-mail : ce qui part vers Sentry."""
    if not isinstance(texte, str):
        return texte
    return masquer_jetons(_COURRIEL.sub("[email]", texte))


def masquer_url(url):
    """URL sans query string ni fragment, jeton de chemin masqué."""
    if not isinstance(url, str):
        return url
    morceaux = urlsplit(url)
    chemin = _CHEMIN_A_JETON.sub(lambda m: m.group(1) + JETON, morceaux.path)
    chemin = _COURRIEL.sub("[email]", chemin)
    return urlunsplit((morceaux.scheme, morceaux.netloc, chemin, "", ""))


def _nettoyer_requete(requete):
    if not isinstance(requete, dict):
        return None
    propre = {}
    if requete.get("method"):
        propre["method"] = requete["method"]
    if requete.get("url"):
        propre["url"] = masquer_url(requete["url"])
    entetes = requete.get("headers") or {}
    if isinstance(entetes, dict):
        propre["headers"] = {
            nom: valeur for nom, valeur in entetes.items() if str(nom).lower() in ENTETES_PERMIS
        }
    return propre


def _nettoyer_utilisateur(utilisateur):
    if isinstance(utilisateur, dict) and utilisateur.get("id") is not None:
        return {"id": str(utilisateur["id"])}
    return None


def _vient_du_code_sensible(evenement):
    logger = evenement.get("logger") or ""
    if logger == PREFIXE_SENSIBLE or logger.startswith(PREFIXE_SENSIBLE + "."):
        return True
    for exception in (evenement.get("exception") or {}).get("values", []) or []:
        for cadre in ((exception.get("stacktrace") or {}).get("frames") or []):
            module = cadre.get("module") or ""
            if module == PREFIXE_SENSIBLE or module.startswith(PREFIXE_SENSIBLE + "."):
                return True
    return False


def _nettoyer_cadres(exception):
    for cadre in ((exception.get("stacktrace") or {}).get("frames") or []):
        cadre.pop("vars", None)


# Termes proxy/IP que le SDK lui-même ajoute quand il traduit
# `send_default_pii=False` ; s'ajoutent à sa liste noire de clés sensibles.
_TERMES_PII = ["forwarded", "-ip", "remote-", "via", "-user"]

#: Ce que le SDK collecte de lui-même (sentry-sdk 3 : remplace
#: `send_default_pii=False`, supprimé). Ni utilisateur, ni cookies, ni
#: corps de requête, ni variables locales (elles contiennent les courriels et
#: les dossiers), ni paramètres SQL : jamais plus qu'avant.
DONNEES_COLLECTEES = {
    "user_info": False,
    "cookies": {"mode": "off"},
    "http_headers": {
        "request": {"mode": "denylist", "terms": _TERMES_PII},
        # Ignored by sentry-sdk 3.0 (it collects no response headers and only
        # reads "request"); kept so a future SDK that adds it starts "off".
        # `contexts.response.headers` is also scrubbed in before_send.
        "response": {"mode": "off"},
    },
    "http_bodies": [],
    "url_query_params": {"mode": "denylist", "terms": _TERMES_PII},
    "graphql": {"document": False, "variables": False},
    "gen_ai": {"inputs": False, "outputs": False},
    "mcp": {"inputs": False, "outputs": False},
    "database_query_data": False,
    "queues": False,
    "stack_frame_variables": False,
    "frame_context_lines": 5,
}

def _nettoyer_commun(evenement):
    """Ce qui vaut pour toute erreur ou tout message.

    sentry-sdk 3 a supprimé son EventScrubber : `config/sentry_scrub` en
    rejoue la liste noire en tête de nos propres filtres.
    """
    scrub_event(evenement)
    if "request" in evenement:
        requete = _nettoyer_requete(evenement["request"])
        if requete is None:
            evenement.pop("request")
        else:
            evenement["request"] = requete
    utilisateur = _nettoyer_utilisateur(evenement.get("user"))
    if utilisateur is None:
        evenement.pop("user", None)
    else:
        evenement["user"] = utilisateur
    evenement.pop("extra", None)
    if evenement.get("transaction"):
        evenement["transaction"] = masquer_url(evenement["transaction"])
    fil = evenement.get("breadcrumbs")
    valeurs = fil.get("values") if isinstance(fil, dict) else fil
    if isinstance(valeurs, list):
        propres = [m for m in (avant_fil_d_ariane(dict(m), {}) for m in valeurs) if m]
        if isinstance(fil, dict):
            fil["values"] = propres
        else:
            evenement["breadcrumbs"] = propres
    for span in evenement.get("spans") or []:
        if span.get("description"):
            span["description"] = masquer_texte(span["description"])
        donnees = span.get("data")
        if isinstance(donnees, dict):
            donnees.pop("http.query", None)
            donnees.pop("http.fragment", None)
            if "url" in donnees:
                donnees["url"] = masquer_url(donnees["url"])
    return evenement


def avant_envoi(evenement, indice):
    """`before_send` : erreurs et messages."""
    erreur = (indice or {}).get("exc_info", (None, None))[1]
    if isinstance(erreur, DisallowedHost):
        # Les robots qui appellent l'IP du serveur : pas une erreur de l'application.
        return None
    sensible = _vient_du_code_sensible(evenement)
    for exception in (evenement.get("exception") or {}).get("values", []) or []:
        _nettoyer_cadres(exception)
        if sensible:
            exception["value"] = "(contenu retiré : %s)" % exception.get("type", "exception")
        else:
            exception["value"] = masquer_texte(exception.get("value"))
    entree = evenement.get("logentry")
    if isinstance(entree, dict):
        # Le gabarit du message est du code ; ses paramètres, non.
        entree.pop("params", None)
        if sensible:
            # Le texte formaté peut citer un courriel.
            entree.pop("formatted", None)
        else:
            for cle in ("message", "formatted"):
                entree[cle] = masquer_texte(entree.get(cle))
    if isinstance(evenement.get("message"), str):
        evenement["message"] = RETIRE if sensible else masquer_texte(evenement["message"])
    return _nettoyer_commun(evenement)


#: Attributs de span jamais transmis : query string, fragment, corps.
_ATTRIBUTS_RETIRES = frozenset({
    "url.query", "url.fragment", "http.query", "http.fragment", "http.request.body.data",
})
_PREFIXE_ENTETE = "http.request.header."


def _attribut_permis(cle):
    if cle in _ATTRIBUTS_RETIRES:
        return False
    if cle.startswith(_PREFIXE_ENTETE):
        return cle[len(_PREFIXE_ENTETE):].lower().replace("_", "-") in ENTETES_PERMIS
    # L'utilisateur : l'identifiant opaque seulement.
    return not cle.startswith("user.") or cle == "user.id"


def _masquer_valeur(cle, valeur):
    if valeur == FILTERED:
        return valeur
    if cle in ("url.full", "http.url", "url"):
        return masquer_url(valeur)
    if isinstance(valeur, list):
        return [masquer_texte(v) for v in valeur]
    return masquer_texte(valeur)


def avant_span(span, indice):
    """`before_send_span` (sentry-sdk 3 : les spans remplacent les transactions).

    Un span ne peut pas être supprimé, seulement réécrit : nom et attributs
    passent par la liste noire du SDK (`scrub_span`), puis par nos masques
    (jetons, query strings, adresses e-mail) ; les en-têtes hors liste blanche,
    la query string et l'utilisateur (hors identifiant) sont retirés. Ne lève
    jamais : une exception ferait partir le span d'origine.
    """
    span = scrub_span(span)
    try:
        span["name"] = masquer_texte(span.get("name"))
        attributs = span.get("attributes")
        if isinstance(attributs, dict):
            span["attributes"] = {
                cle: _masquer_valeur(cle, valeur)
                for cle, valeur in attributs.items()
                if isinstance(cle, str) and _attribut_permis(cle)
            }
        return span
    except Exception:  # noqa: BLE001 -- sinon le SDK enverrait le span brut
        return {"name": FILTERED, "attributes": {}}


def avant_fil_d_ariane(miette, indice):
    """`before_breadcrumb` : les traces qui précèdent une erreur."""
    categorie = miette.get("category") or ""
    if categorie == PREFIXE_SENSIBLE or categorie.startswith(PREFIXE_SENSIBLE + "."):
        # Journal du code de l'import, de Graph, d'Exchange : niveau et
        # catégorie seulement.
        miette.pop("message", None)
        miette.pop("data", None)
        return miette
    if "message" in miette:
        miette["message"] = masquer_texte(miette["message"])
    donnees = miette.get("data")
    if isinstance(donnees, dict):
        propres = {}
        for cle, valeur in donnees.items():
            if cle in ("url", "from", "to"):
                propres[cle] = masquer_url(valeur)
            elif cle in ("method", "status_code", "reason", "level"):
                propres[cle] = valeur
            # tout le reste (query, fragment, paramètres…) part à la poubelle
        miette["data"] = propres
    return miette


def reglages_sentry():
    """Les options de `sentry_sdk.init` qui gardent les données chez nous.

    Partagées par `config/settings.py` et les tests, pour qu'un test éprouve
    exactement ce que la production utilise.
    """
    return {
        "integrations": [
            # Pas de spans de cache (sentry-sdk 3 les active par défaut).
            DjangoIntegration(cache_spans=False),
            # sentry-sdk 3 n'active plus l'intégration des journaux d'office, et
            # sans `event_level` un `logger.error()` ne crée plus d'évènement.
            # `level=None` : pas de « Sentry Logs » (jamais envoyés en 2.x).
            LoggingIntegration(level=None, event_level=logging.ERROR,
                               breadcrumb_level=logging.INFO),
        ],
        # Défaut passé à True en 3.0 : un simple message n'emporte pas de pile.
        "attach_stacktrace": False,
        # Ni PII, ni cookies, ni corps, ni variables locales : `DONNEES_COLLECTEES`.
        "data_collection": DONNEES_COLLECTEES,
        # Le corps d'une requête (titre, description, étiquettes) n'est jamais lu.
        "max_request_body_size": "never",
        "before_send": avant_envoi,
        "before_send_span": avant_span,
        "before_breadcrumb": avant_fil_d_ariane,
    }
