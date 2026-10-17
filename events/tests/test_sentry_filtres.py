"""Rien du contenu d'un courriel ou d'un dossier ne part vers Sentry (`config/sentry_filtres.py`)."""

from pathlib import Path

from django.conf import settings
from django.core.exceptions import DisallowedHost
from django.test import SimpleTestCase

from config.sentry_filtres import JOURNAL_IMPORT, avant_envoi


def evenement(logger, valeur="Objet : Mise en demeure <a.dubois@avocat.example>"):
    return {"logger": logger, "exception": {"values": [{"type": "TypeError", "value": valeur}]}}


class AvantEnvoiTests(SimpleTestCase):
    def test_l_echec_d_import_perd_le_message_de_son_exception(self):
        sortant = avant_envoi(evenement(JOURNAL_IMPORT), {})
        valeur = sortant["exception"]["values"][0]["value"]
        self.assertNotIn("avocat.example", valeur)
        self.assertIn("TypeError", valeur)

    def test_les_autres_erreurs_gardent_leur_message(self):
        sortant = avant_envoi(evenement("django.request", "boom"), {})
        self.assertEqual(sortant["exception"]["values"][0]["value"], "boom")

    def test_un_hote_refuse_n_est_pas_envoye(self):
        self.assertIsNone(avant_envoi({}, {"exc_info": (DisallowedHost, DisallowedHost("x"), None)}))

    def test_le_nom_du_journal_est_celui_de_l_import(self):
        from events import import_courriels
        self.assertEqual(import_courriels.logger.name, JOURNAL_IMPORT)

    def test_les_variables_locales_ne_partent_jamais(self):
        # Les réglages ne s'appliquent qu'avec un DSN : on vérifie la source.
        source = (Path(settings.BASE_DIR) / "config" / "settings.py").read_text(encoding="utf-8")
        self.assertIn("include_local_variables=False", source)
        self.assertIn("before_send=avant_envoi", source)
