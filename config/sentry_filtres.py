"""Ce que Chronos laisse partir vers Sentry.

Règle du propriétaire : **aucune donnée personnelle, aucun contenu de courriel**
ne quitte le serveur vers Sentry. Un dossier de litige n'a rien à faire dans un
outil tiers. `send_default_pii=False` ne suffit pas : sentry-sdk joint quand
même le corps des requêtes (`request.data`), l'URL complète avec sa query
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

import re
from urllib.parse import urlsplit, urlunsplit

from django.core.exceptions import DisallowedHost

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


def _nettoyer_commun(evenement):
    """Ce qui vaut pour une erreur comme pour une transaction."""
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


def avant_transaction(evenement, indice):
    """`before_send_transaction` : mesures de performance."""
    return _nettoyer_commun(evenement)


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
        "send_default_pii": False,
        # Le corps d'une requête (titre, description, étiquettes) n'est jamais lu.
        "max_request_body_size": "never",
        # Les variables locales d'une pile d'appels contiennent les courriels et
        # les dossiers (octets, analyses, requêtes) : jamais envoyées.
        "include_local_variables": False,
        "before_send": avant_envoi,
        "before_send_transaction": avant_transaction,
        "before_breadcrumb": avant_fil_d_ariane,
    }
