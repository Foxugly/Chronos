"""Les journaux du serveur ne gardent ni jeton ni recherche (audit du 2026-10-18, I2).

Un jeton de partage ouvre tout un dossier, parfois en écriture et sans
expiration ; une invitation donne un accès ; `q=` est la recherche plein texte
dans un dossier de litige. Le log d'accès de gunicorn les gardait sans rotation.

- gunicorn n'écrit plus de log d'accès : nginx journalise déjà chaque requête ;
- nginx journalise le chemin sans query string, jeton masqué, sans Referer
  (il porte les mêmes jetons) ;
- les messages de Django (« Not Found: /partage/<jeton>/ ») passent par le même
  masque avant d'atteindre journald.
"""

import logging
import re
from io import StringIO
from pathlib import Path

from django.test import SimpleTestCase

from config.journaux import MasquerJetons

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"
UNITE = DEPLOY / "chronos-gunicorn.service"
NGINX = DEPLOY / "nginx" / "chronos.foxugly.com.conf"
JETON = "Zx9-secretJetonDePartage_42"


class GunicornTests(SimpleTestCase):
    def test_pas_de_log_d_acces(self):
        unite = UNITE.read_text(encoding="utf-8")
        self.assertNotIn("--access-logfile", unite)
        self.assertNotIn("--access-logformat", unite)
        self.assertNotIn("gunicorn-access.log", unite)


class NginxTests(SimpleTestCase):
    def setUp(self):
        self.conf = NGINX.read_text(encoding="utf-8")

    def format_du_journal(self):
        correspondance = re.search(r"log_format\s+chronos_sans_jeton\s+(.*?);", self.conf, re.S)
        self.assertIsNotNone(correspondance)
        return correspondance.group(1)

    def test_le_vhost_journalise_avec_le_format_sans_jeton(self):
        self.assertRegex(self.conf, r"access_log\s+/var/log/nginx/chronos\.access\.log\s+chronos_sans_jeton;")
        self.assertNotRegex(self.conf, r"access_log\s+/var/log/nginx/chronos\.access\.log;")

    def test_le_format_ne_cite_ni_query_ni_chemin_brut_ni_referer(self):
        format_ = self.format_du_journal()
        for variable in ("$request ", "$request\"", "$request_uri", "$args", "$query_string",
                         "$http_referer", "$uri", "$arg_"):
            self.assertNotIn(variable, format_)
        self.assertIn("$chronos_chemin_journal", format_)

    def masquer(self, uri):
        """Rejoue les deux `map` du vhost avec `re` (même syntaxe, à `(?P<` près)."""
        brut = re.search(r'map \$request_uri \$chronos_chemin_brut \{\s*"~(.*?)"\s+(\$\w+);', self.conf)
        jeton = re.search(r'map \$chronos_chemin_brut \$chronos_chemin_journal \{\s*"~(.*?)"\s+"(.*?)";',
                          self.conf)
        self.assertIsNotNone(brut)
        self.assertIsNotNone(jeton)
        chemin = re.match(brut.group(1).replace("(?<", "(?P<"), uri).group("chronos_chemin")
        motif = re.match(jeton.group(1).replace("(?<", "(?P<"), chemin)
        if motif is None:
            return chemin
        return re.sub(r"\$\{(\w+)\}", lambda m: motif.group(m.group(1)), jeton.group(2))

    def test_les_jetons_et_les_recherches_sont_masques(self):
        cas = {
            "/partage/%s/" % JETON: "/partage/[token]/",
            "/partage/%s/events/3/edit/?retour=x" % JETON: "/partage/[token]/events/3/edit/",
            "/invitation/%s/" % JETON: "/invitation/[token]/",
            "/accounts/confirm-email/%s/" % JETON: "/accounts/confirm-email/[token]/",
            "/accounts/password/reset/key/2-%s/" % JETON: "/accounts/password/reset/key/[token]/",
            "/accounts/password/reset/key/done/": "/accounts/password/reset/key/done/",
            "/dossiers/3/?q=mise+en+demeure&vue=liste": "/dossiers/3/",
            "/": "/",
        }
        for uri, attendu in cas.items():
            with self.subTest(uri=uri):
                self.assertEqual(self.masquer(uri), attendu)


class FiltreDeJournalTests(SimpleTestCase):
    def test_un_404_de_django_ne_garde_ni_jeton_ni_recherche(self):
        sortie = StringIO()
        gestionnaire = logging.StreamHandler(sortie)
        gestionnaire.addFilter(MasquerJetons())
        journal = logging.getLogger("test.journaux.django.request")
        journal.addHandler(gestionnaire)
        journal.propagate = False
        try:
            journal.warning("Not Found: %s", "/partage/%s/events/3/?q=avocat" % JETON)
        finally:
            journal.removeHandler(gestionnaire)
        self.assertEqual(sortie.getvalue().strip(), "Not Found: /partage/[token]/events/3/")

    def test_la_console_de_django_porte_le_filtre(self):
        from django.conf import settings
        console = settings.LOGGING["handlers"]["console"]
        self.assertIn("masquer_jetons", console["filters"])
        self.assertEqual(settings.LOGGING["filters"]["masquer_jetons"]["()"], "config.journaux.MasquerJetons")
