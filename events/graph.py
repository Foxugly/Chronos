"""Accès à la boîte d'import par Microsoft Graph (Exchange Online).

Exchange Online n'accepte plus l'IMAP par mot de passe : l'application est
déclarée dans Entra ID (permission d'application `Mail.ReadWrite`, limitée à
cette seule boîte par une `ApplicationAccessPolicy`) et s'authentifie par
« client credentials ». Aucune dépendance : `urllib` suffit pour ces quatre
appels.

Réglages (variables d'environnement, lues dans `config/settings.py`) :
`GRAPH_TENANT_ID`, `GRAPH_CLIENT_ID`, `GRAPH_CLIENT_SECRET`, `COURRIEL_BOITE`.
"""

import json
import urllib.error
import urllib.parse
import urllib.request

GRAPH = "https://graph.microsoft.com/v1.0"
DELAI = 60  # secondes, par appel


class ErreurGraph(Exception):
    pass


class ClientGraph:
    def __init__(self, tenant, client_id, secret, boite):
        if not all((tenant, client_id, secret, boite)):
            raise ErreurGraph(
                "Configuration incomplète : GRAPH_TENANT_ID, GRAPH_CLIENT_ID, "
                "GRAPH_CLIENT_SECRET et COURRIEL_BOITE sont requis."
            )
        self.tenant, self.client_id, self.secret = tenant, client_id, secret
        self.boite = urllib.parse.quote(boite)
        self._jeton = None

    # --- transport -----------------------------------------------------------

    def _requete(self, methode, url, corps=None, entetes=None, brut=False):
        entetes = dict(entetes or {})
        donnees = None
        if corps is not None:
            donnees = json.dumps(corps).encode("utf-8")
            entetes["Content-Type"] = "application/json"
        requete = urllib.request.Request(url, data=donnees, method=methode, headers=entetes)
        try:
            with urllib.request.urlopen(requete, timeout=DELAI) as reponse:
                contenu = reponse.read()
        except urllib.error.HTTPError as erreur:
            detail = erreur.read().decode("utf-8", "replace")[:500]
            raise ErreurGraph("%s %s → HTTP %s : %s" % (methode, url, erreur.code, detail)) from erreur
        except urllib.error.URLError as erreur:
            raise ErreurGraph("%s %s → %s" % (methode, url, erreur.reason)) from erreur
        return contenu if brut else (json.loads(contenu) if contenu else {})

    def _jeton_d_acces(self):
        if self._jeton is None:
            donnees = urllib.parse.urlencode({
                "client_id": self.client_id,
                "client_secret": self.secret,
                "scope": "https://graph.microsoft.com/.default",
                "grant_type": "client_credentials",
            }).encode("utf-8")
            requete = urllib.request.Request(
                "https://login.microsoftonline.com/%s/oauth2/v2.0/token" % urllib.parse.quote(self.tenant),
                data=donnees, method="POST",
            )
            try:
                with urllib.request.urlopen(requete, timeout=DELAI) as reponse:
                    self._jeton = json.loads(reponse.read())["access_token"]
            except urllib.error.HTTPError as erreur:
                detail = erreur.read().decode("utf-8", "replace")[:500]
                raise ErreurGraph("Authentification refusée : %s" % detail) from erreur
        return self._jeton

    def _appel(self, methode, chemin, corps=None, brut=False):
        url = chemin if chemin.startswith("https://") else "%s/users/%s%s" % (GRAPH, self.boite, chemin)
        return self._requete(methode, url, corps,
                             {"Authorization": "Bearer " + self._jeton_d_acces()}, brut=brut)

    def envoyer(self, destinataires, sujet, corps, html=False):
        """Envoie un courriel depuis la boîte du client (`/sendMail`).

        Demande la permission d'application `Mail.Send`, restreinte par une
        stratégie d'accès d'application Exchange à la seule boîte expéditrice
        (OPERATIONS.md §3.17). Rien n'est gardé dans « Éléments envoyés ».
        """
        self._appel("POST", "/sendMail", {
            "message": {
                "subject": sujet,
                "body": {"contentType": "HTML" if html else "Text", "content": corps},
                "toRecipients": [{"emailAddress": {"address": a}} for a in destinataires],
            },
            "saveToSentItems": False,
        })

    # --- la boîte ------------------------------------------------------------

    def messages_recus(self):
        """Les courriels de la boîte de réception, du plus ancien au plus récent."""
        url = "/mailFolders/inbox/messages?%s" % urllib.parse.urlencode({
            "$select": "id,internetMessageId,receivedDateTime,subject",
            "$orderby": "receivedDateTime asc",
            "$top": "50",
        })
        while url:
            page = self._appel("GET", url)
            yield from page.get("value", [])
            url = page.get("@odata.nextLink")

    def mime(self, identifiant):
        """Le courriel complet, tel qu'il est arrivé (format MIME)."""
        return self._appel("GET", "/messages/%s/$value" % identifiant, brut=True)

    def dossier(self, nom):
        """L'identifiant du dossier `nom` sous la racine de la boîte, créé au besoin."""
        filtre = urllib.parse.urlencode({"$filter": "displayName eq '%s'" % nom.replace("'", "''")})
        trouves = self._appel("GET", "/mailFolders?%s" % filtre).get("value", [])
        if trouves:
            return trouves[0]["id"]
        return self._appel("POST", "/mailFolders", {"displayName": nom})["id"]

    def deplacer(self, identifiant, dossier_id):
        self._appel("POST", "/messages/%s/move" % identifiant, {"destinationId": dossier_id})
