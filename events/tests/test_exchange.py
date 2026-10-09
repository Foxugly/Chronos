"""Alias Exchange gérés par l'application (`events/exchange.py`).

PowerShell est simulé : on vérifie ce que Chronos lui demande, dans quel ordre,
ce qu'il fait d'un échec, et qu'aucun secret ne passe sur la ligne de commande.
"""

import json
from unittest import mock

from django.contrib.messages import get_messages
from django.test import TestCase, override_settings
from django.urls import reverse

from events import exchange
from events.models import Journal
from events.tests.factories import make_dossier, make_user

CONFIG = dict(
    EXCHANGE_APP_ID="app-id", EXCHANGE_TENANT="foxugly.onmicrosoft.com",
    EXCHANGE_CERT_FILE_PATH="/etc/chronos/exchange.pfx", EXCHANGE_CERT_PASSWORD="pfx-secret",
    EXCHANGE_CERT_THUMBPRINT="", COURRIEL_BOITE="import@foxugly.com",
)


def reponse(succes=True, code=None, data=None):
    charge = {"success": True, "data": data} if succes else {"success": False, "error_code": code, "error": "non"}
    return mock.Mock(stdout="WARNING bruit\n" + json.dumps(charge), stderr="", returncode=0 if succes else 6)


@override_settings(**CONFIG)
class ServiceTests(TestCase):
    def test_non_configure_sans_certificat(self):
        with override_settings(EXCHANGE_CERT_FILE_PATH=""):
            self.assertFalse(exchange.configure())
        self.assertTrue(exchange.configure())

    def test_ajouter_appelle_le_script_sans_secret_en_argument(self):
        with mock.patch("events.exchange.subprocess.run", return_value=reponse()) as run:
            exchange.ajouter("sdb-1.chronos@foxugly.com")
        argv, kwargs = run.call_args.args[0], run.call_args.kwargs
        self.assertEqual(argv[argv.index("-Action") + 1], "add")
        self.assertEqual(argv[argv.index("-Mailbox") + 1], "import@foxugly.com")
        self.assertEqual(argv[argv.index("-Alias") + 1], "sdb-1.chronos@foxugly.com")
        self.assertNotIn("pfx-secret", " ".join(argv))
        self.assertEqual(kwargs["env"]["EXCHANGE_CERT_PASSWORD"], "pfx-secret")
        self.assertFalse(kwargs["shell"])

    def test_deja_present_ou_deja_absent_n_est_pas_une_erreur(self):
        with mock.patch("events.exchange.subprocess.run", return_value=reponse(False, "alias_already_exists")):
            exchange.ajouter("sdb-1.chronos@foxugly.com")
        with mock.patch("events.exchange.subprocess.run", return_value=reponse(False, "alias_not_found")):
            exchange.retirer("sdb-1.chronos@foxugly.com")

    def test_les_autres_echecs_levent(self):
        with mock.patch("events.exchange.subprocess.run", return_value=reponse(False, "auth_failed")):
            with self.assertRaises(exchange.ErreurExchange) as contexte:
                exchange.ajouter("sdb-1.chronos@foxugly.com")
        self.assertEqual(contexte.exception.code, "auth_failed")

    def test_adresse_suspecte_refusee_avant_powershell(self):
        with mock.patch("events.exchange.subprocess.run") as run:
            for adresse in ("a@b.be; Remove-Mailbox x", "pas-une-adresse", "a$b@c.be"):
                with self.assertRaises(exchange.ErreurExchange):
                    exchange.ajouter(adresse)
        run.assert_not_called()

    def test_ne_retire_jamais_l_adresse_principale(self):
        with mock.patch("events.exchange.subprocess.run") as run, self.assertRaises(exchange.ErreurExchange):
            exchange.retirer("Import@foxugly.com")
        run.assert_not_called()

    def test_est_actif(self):
        donnees = ["SMTP:import@foxugly.com", "smtp:sdb-1.chronos@foxugly.com"]
        with mock.patch("events.exchange.subprocess.run", return_value=reponse(data=donnees)):
            self.assertTrue(exchange.est_actif("SDB-1.chronos@foxugly.com"))
            self.assertFalse(exchange.est_actif("autre@foxugly.com"))

    def test_powershell_absent(self):
        with mock.patch("events.exchange.subprocess.run", side_effect=FileNotFoundError):
            with self.assertRaises(exchange.ErreurExchange):
                exchange.ajouter("sdb-1.chronos@foxugly.com")


@override_settings(**CONFIG)
class VueAliasTests(TestCase):
    def setUp(self):
        self.owner = make_user("owner")
        self.dossier = make_dossier(self.owner)
        self.url = reverse("dossier_alias", args=[self.dossier.pk])
        self.client.force_login(self.owner)

    def poster(self, alias, *reponses):
        with mock.patch("events.exchange.subprocess.run", side_effect=list(reponses)) as run:
            reponse_http = self.client.post(self.url, {"alias_courriel": alias}, follow=True)
        actions = [c.args[0][c.args[0].index("-Action") + 1] for c in run.call_args_list]
        return reponse_http, actions

    def test_ajout_reussi_puis_enregistre(self):
        _r, actions = self.poster("sdb-1.chronos@foxugly.com", reponse())
        self.assertEqual(actions, ["add"])
        self.dossier.refresh_from_db()
        self.assertEqual(self.dossier.alias_courriel, "sdb-1.chronos@foxugly.com")
        self.assertTrue(Journal.objects.get().details["exchange"])

    def test_echec_exchange_rien_n_est_enregistre(self):
        r, actions = self.poster("sdb-1.chronos@foxugly.com", reponse(False, "auth_failed"))
        self.dossier.refresh_from_db()
        self.assertIsNone(self.dossier.alias_courriel)
        self.assertFalse(Journal.objects.exists())
        self.assertIn("rien n'a été enregistré", " ".join(str(m) for m in get_messages(r.wsgi_request)))

    def test_changement_ajoute_le_nouveau_puis_retire_l_ancien(self):
        self.dossier.alias_courriel = "sdb-1.chronos@foxugly.com"
        self.dossier.save()
        _r, actions = self.poster("sdb-2.chronos@foxugly.com", reponse(), reponse())
        self.assertEqual(actions, ["add", "remove"])
        self.dossier.refresh_from_db()
        self.assertEqual(self.dossier.alias_courriel, "sdb-2.chronos@foxugly.com")

    def test_retrait_rate_avertit_mais_garde_le_nouveau(self):
        self.dossier.alias_courriel = "sdb-1.chronos@foxugly.com"
        self.dossier.save()
        r, _actions = self.poster("sdb-2.chronos@foxugly.com", reponse(), reponse(False, "exchange_error"))
        self.dossier.refresh_from_db()
        self.assertEqual(self.dossier.alias_courriel, "sdb-2.chronos@foxugly.com")
        self.assertIn("retirez-le à la main", " ".join(str(m) for m in get_messages(r.wsgi_request)))

    def test_desactiver_retire_dans_exchange(self):
        self.dossier.alias_courriel = "sdb-1.chronos@foxugly.com"
        self.dossier.save()
        _r, actions = self.poster("", reponse())
        self.assertEqual(actions, ["remove"])
        self.dossier.refresh_from_db()
        self.assertIsNone(self.dossier.alias_courriel)

    def test_sans_configuration_aucun_appel(self):
        with override_settings(EXCHANGE_APP_ID=""):
            _r, actions = self.poster("sdb-1.chronos@foxugly.com")
        self.assertEqual(actions, [])
        self.dossier.refresh_from_db()
        self.assertEqual(self.dossier.alias_courriel, "sdb-1.chronos@foxugly.com")

    def test_verifier_reserve_au_proprietaire_et_post(self):
        self.dossier.alias_courriel = "sdb-1.chronos@foxugly.com"
        self.dossier.save()
        url = reverse("dossier_alias_verifier", args=[self.dossier.pk])
        with mock.patch("events.exchange.subprocess.run", return_value=reponse(data=["smtp:sdb-1.chronos@foxugly.com"])) as run:
            self.client.get(url)
            run.assert_not_called()
            r = self.client.post(url, follow=True)
        self.assertIn("est actif", " ".join(str(m) for m in get_messages(r.wsgi_request)))
        self.client.force_login(make_user("autre"))
        self.assertEqual(self.client.post(url).status_code, 404)

    def test_la_page_dit_que_chronos_gere_exchange(self):
        r = self.client.get(reverse("dossier_parametres", args=[self.dossier.pk]))
        self.assertTrue(r.context["exchange_gere"])
        self.assertNotContains(r, "Set-Mailbox")
