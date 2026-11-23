"""Rien du contenu d'un courriel ou d'un dossier ne part vers Sentry (`config/sentry_filtres.py`).

Règle du propriétaire : aucune donnée personnelle, aucun contenu de courriel
chez Sentry. Les tests unitaires éprouvent chaque filtre sur un évènement
fabriqué ; `SondeDjangoTests` fait passer une vraie erreur par sentry-sdk et
l'intégration Django, avec les réglages mêmes de la production, et lit ce qui
serait parti.
"""

import json
import logging
import warnings
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
    avant_span,
    masquer_url,
    reglages_sentry,
)
from config.sentry_scrub import FILTERED, scrub_event, scrub_span

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


class AvantSpanTests(SimpleTestCase):
    """sentry-sdk 3 : les spans (en flux) remplacent les transactions."""

    def span(self, nom, **attributs):
        return {"trace_id": "t", "span_id": "s", "name": nom, "attributes": attributs}

    def test_un_span_fabrique_ne_garde_aucun_secret(self):
        sortant = avant_span(self.span(
            "GET https://graph.microsoft.com/v1.0/users/hello@foxugly.com/messages?$top=5",
            **{"url.full": "https://chronos.foxugly.com/partage/%s/?q=avocat" % JETON_SECRET,
               "http.query": "$top=5", "url.query": "q=avocat",
               "http.request.header.cookie": ["sessionid=abc"],
               "http.request.header.referer": ["https://h/invitation/%s/" % JETON_SECRET],
               "http.request.header.user-agent": ["Firefox"],
               "user.email": ADRESSE, "user.id": "7", "client.address": "81.2.3.4",
               "sentry.op": "http.client", "http.response.status_code": 200}), {})
        self.assertEqual(contient(sortant, *SECRETS, "$top"), [])
        attributs = sortant["attributes"]
        self.assertEqual(attributs["url.full"], "https://chronos.foxugly.com/partage/%s/" % JETON)
        self.assertEqual(attributs["http.request.header.user-agent"], ["Firefox"])
        self.assertEqual(attributs["user.id"], "7")
        self.assertEqual(attributs["sentry.op"], "http.client")
        self.assertEqual(attributs["http.response.status_code"], 200)
        self.assertNotIn("http.request.header.cookie", attributs)

    def test_un_jeton_dans_le_nom_ou_un_attribut_est_masque(self):
        sortant = avant_span(self.span("POST /api/login token=abc123 password=hunter2",
                                       **{"db.query.text": "SELECT 1 -- api_key=k3y",
                                          "password": "hunter2"}), {})
        self.assertEqual(contient(sortant, "abc123", "hunter2", "k3y"), [])

    def test_un_span_illisible_part_vide_plutot_que_brut(self):
        class Piege(dict):
            def items(self):
                raise RuntimeError("boom")

        sortant = avant_span({"name": "x", "attributes": Piege(password="hunter2")}, {})
        self.assertEqual(sortant, {"name": FILTERED, "attributes": {}})


class ScrubTests(SimpleTestCase):
    """`config/sentry_scrub` : ce que faisait l'EventScrubber de sentry-sdk 2."""

    def test_un_jeton_ou_un_mot_de_passe_est_masque_partout(self):
        sortant = scrub_event({
            "request": {"data": {"password": "hunter2", "titre": "ok"},
                        "headers": {"Authorization": "Bearer x"},
                        "cookies": {"sessionid": "abc"}},
            "extra": {"token": "abc123", "n": 1, "nested": {"api_key": "k3y"}},
            "user": {"id": "7", "ip_address": "81.2.3.4"},
            "breadcrumbs": {"values": [{"data": {"csrftoken": "def"}}]},
            "exception": {"values": [{"stacktrace": {"frames": [
                {"vars": {"password": "hunter2", "secret": "s3", "i": 2}}]}}]},
        })
        self.assertEqual(contient(sortant, "hunter2", "abc123", "k3y", "Bearer", "abc",
                                  "81.2.3.4", "def", "s3"), [])
        self.assertEqual(sortant["request"]["data"]["titre"], "ok")
        self.assertEqual(sortant["extra"]["n"], 1)
        self.assertEqual(sortant["exception"]["values"][0]["stacktrace"]["frames"][0]["vars"]["i"], 2)

    def test_avant_envoi_rejoue_la_liste_noire_sur_les_variables_locales(self):
        evt = evenement_complet()
        evt["exception"]["values"][0]["stacktrace"]["frames"][0]["vars"] = {"password": "hunter2"}
        evt["threads"] = {"values": [{"stacktrace": {"frames": [{"vars": {"token": "abc123"}}]}}]}
        self.assertEqual(contient(avant_envoi(evt, {}), "hunter2", "abc123"), [])

    def test_scrub_span_masque_cles_et_valeurs(self):
        sortant = scrub_span({"name": "GET /x?token=abc123&page=2",
                              "attributes": {"http.request.header.authorization": ["Bearer x"],
                                             "url.full": "https://h/x?password=hunter2"}})
        self.assertEqual(sortant["name"], "GET /x?token=[Filtered]&page=2")
        self.assertEqual(contient(sortant, "abc123", "hunter2", "Bearer x"), [])


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
        self.assertNotIn("send_default_pii", reglages)
        self.assertEqual(reglages["max_request_body_size"], "never")
        self.assertIs(reglages["data_collection"]["stack_frame_variables"], False)
        self.assertIs(reglages["before_send"], avant_envoi)
        self.assertIs(reglages["before_send_span"], avant_span)
        self.assertNotIn("before_send_transaction", reglages)
        self.assertIs(reglages["before_breadcrumb"], avant_fil_d_ariane)
        self.assertIs(reglages["attach_stacktrace"], False)
        django, journaux = reglages["integrations"]
        self.assertIsInstance(django, DjangoIntegration)
        self.assertIs(django.cache_spans, False)
        self.assertIsInstance(journaux, LoggingIntegration)
        self.assertIsNone(journaux._logs_handler)  # pas de « Sentry Logs »
        self.assertEqual(journaux._event_handler.level, logging.ERROR)
        source = (Path(settings.BASE_DIR) / "config" / "settings.py").read_text(encoding="utf-8")
        self.assertIn("**reglages_sentry()", source)

    def test_data_collection_ne_collecte_rien_de_plus_et_sans_avertissement(self):
        with warnings.catch_warnings(record=True) as avertissements:
            warnings.simplefilter("always")
            client = sentry_sdk.Client(dsn=None, default_integrations=False,
                                       **reglages_sentry())
        self.assertEqual([str(a.message) for a in avertissements], [])
        regles = client.options["data_collection"]
        self.assertIs(regles["user_info"], False)
        self.assertEqual(regles["cookies"], {"mode": "off"})
        self.assertEqual(regles["http_bodies"], [])
        self.assertIs(regles["stack_frame_variables"], False)
        self.assertIs(regles["database_query_data"], False)
        self.assertEqual(regles["mcp"], {"inputs": False, "outputs": False})


class TransportCapturant(Transport):
    def __init__(self, options=None):
        super().__init__(options)
        self.evenements = []

    def capture_envelope(self, envelope):
        for item in envelope.items:
            if item.type == "event":
                self.evenements.append(item.payload.json)
            elif item.type == "span":
                for span in item.payload.json["items"]:
                    self.evenements.append(dict(span, type="span"))


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
        with warnings.catch_warnings(record=True) as avertissements:
            warnings.simplefilter("always")
            sentry_sdk.init(dsn="https://cle@sentry.invalid/1", transport=self.transport,
                            default_integrations=False, traces_sample_rate=1.0,
                            **reglages_sentry())
        self.assertEqual([str(a.message) for a in avertissements
                          if issubclass(a.category, DeprecationWarning)], [])

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
        self.assertEqual(types, {"event", "span"})
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

    def test_logger_error_cree_un_evenement(self):
        """sentry-sdk 3 : sans `LoggingIntegration(event_level=ERROR)`, plus rien."""
        logging.getLogger("config.sonde").error("Sonde %s", "valeur")
        sentry_sdk.flush()
        messages = [e for e in self.transport.evenements if e.get("logentry")]
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["logentry"]["message"], "Sonde %s")
        self.assertNotIn("params", messages[0]["logentry"])
