"""Rien du contenu d'un courriel ou d'un dossier ne part vers Sentry (`config/sentry_filtres.py`).

Règle du propriétaire : aucune donnée personnelle, aucun contenu de courriel
chez Sentry. Les tests unitaires éprouvent chaque filtre sur un évènement
fabriqué ; `SondeDjangoTests` fait passer une vraie erreur par sentry-sdk et
l'intégration Django, avec les réglages mêmes de la production, et lit ce qui
serait parti.
"""

import json
from pathlib import Path

import sentry_sdk
from django.conf import settings
from django.core.exceptions import DisallowedHost
from django.core.wsgi import get_wsgi_application
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import URLPattern, URLResolver, get_resolver
from sentry_sdk.integrations.django import DjangoIntegration
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.transport import Transport

from config.sentry_filtres import (
    JETON,
    JOURNAL_IMPORT,
    avant_envoi,
    avant_fil_d_ariane,
    avant_transaction,
    masquer_url,
    reglages_sentry,
)

ADRESSE = "a.dubois@avocat.example"
JETON_SECRET = "Zx9-secretJetonDePartage_42"


def evenement(logger, valeur="Objet : Mise en demeure <%s>" % ADRESSE):
    return {"logger": logger, "exception": {"values": [{"type": "TypeError", "value": valeur}]}}


def evenement_complet(logger="django.request", module="django.core.handlers.base"):
    return {
        "logger": logger,
        "logentry": {"message": "Erreur sur %s", "params": [ADRESSE],
                     "formatted": "Erreur sur %s" % ADRESSE},
        "exception": {"values": [{
            "type": "ValueError",
            "value": "invalid literal for int() with base 10 de %s" % ADRESSE,
            "stacktrace": {"frames": [{"module": module, "function": "f",
                                       "vars": {"objet": "Mise en demeure"}}]},
        }]},
        "request": {
            "method": "POST",
            "url": "https://chronos.foxugly.com/partage/%s/events/add/?q=avocat" % JETON_SECRET,
            "query_string": "q=avocat",
            "data": {"title": "Mise en demeure", "description": "Le carrelage…"},
            "cookies": {"sessionid": "abc", "csrftoken": "def"},
            "headers": {"User-Agent": "Firefox", "Cookie": "sessionid=abc",
                        "Referer": "https://chronos.foxugly.com/invitation/%s/" % JETON_SECRET,
                        "Authorization": "Bearer x", "X-Forwarded-For": "81.2.3.4",
                        "Content-Type": "multipart/form-data"},
            "env": {"REMOTE_ADDR": "81.2.3.4", "SERVER_NAME": "chronos"},
        },
        "user": {"id": 7, "email": ADRESSE, "username": "jean", "ip_address": "81.2.3.4"},
        "extra": {"alias": "acmebat-a1b2.chronos@foxugly.com", "stderr": "Set-Mailbox …"},
        "breadcrumbs": {"values": [
            {"category": "django.request", "message": "Not Found: /invitation/%s/?q=x" % JETON_SECRET},
            {"category": "events.import_courriels", "message": "Objet : Mise en demeure",
             "data": {"objet": "x"}},
            {"category": "httplib", "type": "http",
             "data": {"url": "https://graph.microsoft.com/v1.0/users/hello@foxugly.com/messages",
                      "http.query": "$filter=from eq '%s'" % ADRESSE, "method": "GET",
                      "status_code": 200}},
        ]},
        "transaction": "/partage/{token}/events/add/",
    }


def contient(evenement, *secrets):
    texte = json.dumps(evenement, ensure_ascii=False, default=str)
    return [s for s in secrets if s in texte]


SECRETS = (ADRESSE, JETON_SECRET, "Mise en demeure", "carrelage", "sessionid", "81.2.3.4",
           "jean", "q=avocat", "acmebat-a1b2", "hello@foxugly.com", "Bearer")


class AvantEnvoiTests(SimpleTestCase):
    def test_l_echec_d_import_perd_le_message_de_son_exception(self):
        sortant = avant_envoi(evenement(JOURNAL_IMPORT), {})
        valeur = sortant["exception"]["values"][0]["value"]
        self.assertNotIn("avocat.example", valeur)
        self.assertIn("TypeError", valeur)

    def test_tout_journal_events_perd_le_message_de_son_exception(self):
        for logger in ("events.alertes", "events.exchange", "events.graph"):
            sortant = avant_envoi(evenement(logger), {})
            self.assertNotIn("avocat.example", sortant["exception"]["values"][0]["value"], logger)

    def test_une_pile_qui_traverse_l_import_perd_son_message(self):
        """Une exception non interceptée (la commande, la vue « Relever ») n'a
        pas de journal : c'est la pile qui dit d'où elle vient."""
        sortant = avant_envoi(evenement_complet(logger=None, module="events.import_courriels"), {})
        exception = sortant["exception"]["values"][0]
        self.assertEqual(exception["value"], "(contenu retiré : ValueError)")
        self.assertEqual(exception["type"], "ValueError")
        self.assertEqual(exception["stacktrace"]["frames"][0]["function"], "f")
        self.assertNotIn("params", sortant["logentry"])
        self.assertNotIn("formatted", sortant["logentry"])

    def test_un_evenement_fabrique_ne_garde_aucun_secret(self):
        sortant = avant_envoi(evenement_complet(), {})
        self.assertEqual(contient(sortant, *SECRETS), [])

    def test_ce_qui_reste_suffit_a_trouver_le_bug(self):
        sortant = avant_envoi(evenement_complet(), {})
        requete = sortant["request"]
        self.assertEqual(requete["method"], "POST")
        self.assertEqual(requete["url"], "https://chronos.foxugly.com/partage/%s/events/add/" % JETON)
        self.assertEqual(requete["headers"], {"User-Agent": "Firefox", "Content-Type": "multipart/form-data"})
        for cle in ("data", "cookies", "query_string", "env"):
            self.assertNotIn(cle, requete)
        self.assertEqual(sortant["user"], {"id": "7"})
        self.assertNotIn("extra", sortant)
        self.assertNotIn("vars", sortant["exception"]["values"][0]["stacktrace"]["frames"][0])
        self.assertEqual(sortant["transaction"], "/partage/%s/events/add/" % JETON)

    def test_les_autres_erreurs_gardent_leur_message(self):
        sortant = avant_envoi(evenement("django.request", "boom"), {})
        self.assertEqual(sortant["exception"]["values"][0]["value"], "boom")

    def test_un_hote_refuse_n_est_pas_envoye(self):
        self.assertIsNone(avant_envoi({}, {"exc_info": (DisallowedHost, DisallowedHost("x"), None)}))

    def test_le_nom_du_journal_est_celui_de_l_import(self):
        from events import import_courriels
        self.assertEqual(import_courriels.logger.name, JOURNAL_IMPORT)


class AvantTransactionTests(SimpleTestCase):
    def test_une_transaction_fabriquee_ne_garde_aucun_secret(self):
        transaction = evenement_complet()
        del transaction["exception"], transaction["logentry"]
        transaction["type"] = "transaction"
        transaction["spans"] = [{
            "op": "http.client",
            "description": "GET https://graph.microsoft.com/v1.0/users/hello@foxugly.com/messages?$top=5",
            "data": {"url": "https://graph.microsoft.com/v1.0/users/hello@foxugly.com/messages",
                     "http.query": "$top=5"},
        }]
        sortant = avant_transaction(transaction, {})
        self.assertEqual(contient(sortant, *SECRETS, "$top"), [])


class AvantFilDArianeTests(SimpleTestCase):
    def test_le_journal_de_l_import_ne_garde_que_sa_categorie(self):
        miette = avant_fil_d_ariane({"category": JOURNAL_IMPORT, "level": "error",
                                     "message": "Objet : x <%s>" % ADRESSE, "data": {"a": ADRESSE}}, {})
        self.assertEqual(miette, {"category": JOURNAL_IMPORT, "level": "error"})

    def test_les_autres_messages_sont_masques(self):
        miette = avant_fil_d_ariane({"category": "django.request",
                                     "message": "Not Found: /partage/%s/?q=avocat" % JETON_SECRET}, {})
        self.assertEqual(miette["message"], "Not Found: /partage/%s/" % JETON)

    def test_un_appel_http_perd_sa_query_et_l_adresse_de_la_boite(self):
        miette = avant_fil_d_ariane({"category": "httplib", "type": "http", "data": {
            "url": "https://graph.microsoft.com/v1.0/users/hello@foxugly.com/messages",
            "http.query": "x=%s" % ADRESSE, "method": "GET", "status_code": 200}}, {})
        self.assertEqual(miette["data"], {
            "url": "https://graph.microsoft.com/v1.0/users/[email]/messages",
            "method": "GET", "status_code": 200})


class MasquerUrlTests(SimpleTestCase):
    def test_chaque_route_a_jeton_est_masquee(self):
        """Toute route du site qui prend un jeton ou une clé dans son chemin :
        une nouvelle route oubliée par le filtre ferait échouer ce test."""
        def routes(resolveur, prefixe=""):
            for motif in resolveur.url_patterns:
                texte = prefixe + str(motif.pattern).lstrip("^").rstrip("$")
                if isinstance(motif, URLResolver):
                    yield from routes(motif, texte)
                elif isinstance(motif, URLPattern):
                    yield texte

        a_jeton = [r for r in routes(get_resolver()) if "token" in r or "key>" in r]
        self.assertGreaterEqual(len(a_jeton), 9)
        for route in a_jeton:
            chemin = (route.replace("<str:token>", JETON_SECRET).replace("<int:event_id>", "3")
                      .replace("<int:document_id>", "4")
                      .replace("(?P<uidb36>[0-9A-Za-z]+)-(?P<key>.+)", "2-" + JETON_SECRET)
                      .replace("(?P<key>[-:\\w]+)", JETON_SECRET))
            with self.subTest(route=route):
                self.assertIn(JETON_SECRET, chemin)  # la substitution a bien eu lieu
                self.assertNotIn(JETON_SECRET, masquer_url("https://h/" + chemin))

    def test_la_page_fixe_de_reinitialisation_reste_lisible(self):
        self.assertEqual(masquer_url("https://h/accounts/password/reset/key/done/"),
                         "https://h/accounts/password/reset/key/done/")

    def test_un_chemin_sans_jeton_garde_ses_identifiants_opaques(self):
        self.assertEqual(masquer_url("https://h/dossiers/3/events/add/?q=avocat#x"),
                         "https://h/dossiers/3/events/add/")


class ReglagesTests(SimpleTestCase):
    def test_les_reglages_de_production_sont_ceux_eprouves_ici(self):
        reglages = reglages_sentry()
        self.assertIs(reglages["send_default_pii"], False)
        self.assertEqual(reglages["max_request_body_size"], "never")
        self.assertIs(reglages["include_local_variables"], False)
        self.assertIs(reglages["before_send"], avant_envoi)
        self.assertIs(reglages["before_send_transaction"], avant_transaction)
        self.assertIs(reglages["before_breadcrumb"], avant_fil_d_ariane)
        source = (Path(settings.BASE_DIR) / "config" / "settings.py").read_text(encoding="utf-8")
        self.assertIn("**reglages_sentry()", source)


class TransportCapturant(Transport):
    def __init__(self, options=None):
        super().__init__(options)
        self.evenements = []

    def capture_envelope(self, envelope):
        for item in envelope.items:
            if item.type in ("event", "transaction"):
                self.evenements.append(item.payload.json)


@override_settings(ROOT_URLCONF="events.tests.urls_sonde_sentry")
class SondeDjangoTests(TestCase):
    """La sonde de l'audit du 2026-10-18 : une erreur pendant un POST sous un
    lien de partage envoyait le corps et le jeton.

    La requête passe par le vrai `WSGIHandler` (celui de gunicorn), que
    sentry-sdk instrumente : URL, query string, en-têtes, `env` et transaction
    sont produits comme en production — le client de test, lui, l'évite.
    """

    def setUp(self):
        self.transport = TransportCapturant()
        sentry_sdk.init(dsn="https://cle@sentry.invalid/1", transport=self.transport,
                        integrations=[DjangoIntegration(), LoggingIntegration()],
                        default_integrations=False, traces_sample_rate=1.0, **reglages_sentry())

    def tearDown(self):
        sentry_sdk.get_client().close()
        sentry_sdk.get_global_scope().set_client(None)

    def appeler(self, chemin, donnees):
        environ = RequestFactory().post(
            chemin, donnees,
            HTTP_REFERER="https://chronos.foxugly.com/partage/%s/" % JETON_SECRET,
            HTTP_COOKIE="sessionid=abc; csrftoken=def",
            REMOTE_ADDR="81.2.3.4",
        ).environ
        statuts = []
        # Avant `assertLogs` : `get_wsgi_application()` refait la configuration
        # des journaux, ce qui retirerait le gestionnaire du test.
        application = get_wsgi_application()
        with self.assertLogs("django.request", level="WARNING"):
            b"".join(application(environ, lambda statut, entetes, *a: statuts.append(statut)))
        sentry_sdk.flush()
        return statuts[0]

    def test_une_erreur_sous_un_lien_de_partage_n_emporte_ni_corps_ni_jeton(self):
        statut = self.appeler("/partage/%s/events/add/?q=avocat" % JETON_SECRET,
                              {"title": "Mise en demeure", "description": "Le carrelage fuit"})
        self.assertTrue(statut.startswith("500"))
        types = {e.get("type", "event") for e in self.transport.evenements}
        self.assertEqual(types, {"event", "transaction"})
        for envoye in self.transport.evenements:
            with self.subTest(type=envoye.get("type", "event"), logger=envoye.get("logger")):
                self.assertEqual(contient(envoye, *SECRETS), [])
        erreurs = [e for e in self.transport.evenements if e.get("exception")]
        self.assertTrue(erreurs)
        for erreur in erreurs:
            self.assertEqual(erreur["exception"]["values"][-1]["type"], "RuntimeError")
            self.assertEqual(erreur["request"]["method"], "POST")
            self.assertEqual(erreur["request"]["url"],
                             "http://testserver/partage/%s/events/add/" % JETON)
