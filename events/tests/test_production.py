"""Ce qui ne sert qu'en production : `/health/`, pièces servies par nginx,
courriels par Graph, réglages qui refusent de démarrer mal configurés."""

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.core import mail
from django.db import DatabaseError
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from events.models import DossierAccess, ShareLink
from events.tests.test_documents import DocumentFixtureMixin


class SanteTests(TestCase):
    def test_ok_sans_connexion(self):
        reponse = self.client.get("/health/")
        self.assertEqual(reponse.status_code, 200)
        self.assertEqual(reponse.json()["status"], "ok")

    def test_503_si_la_base_ne_repond_pas(self):
        with mock.patch("config.sante.connection.cursor", side_effect=DatabaseError("tombée")):
            reponse = self.client.get("/health/")
        self.assertEqual(reponse.status_code, 503)
        self.assertNotIn("tombée", reponse.content.decode())


@override_settings(PIECES_X_ACCEL_PREFIX="/_pieces_internes/")
class XAccelTests(DocumentFixtureMixin, TestCase):
    def test_django_ne_sert_pas_les_octets(self):
        self.client.force_login(self.reader)
        reponse = self.client.get(reverse("document_download", args=[self.document.pk]))
        self.assertEqual(reponse.status_code, 200)
        self.assertEqual(reponse["X-Accel-Redirect"], "/_pieces_internes/" + self.document.file.name)
        self.assertEqual(reponse.content, b"")
        self.assertIn("devis.pdf", reponse["Content-Disposition"])
        self.assertEqual(reponse["Content-Type"], "application/pdf")

    def test_les_droits_restent_controles(self):
        self.client.force_login(self.stranger)
        reponse = self.client.get(reverse("document_download", args=[self.document.pk]))
        self.assertEqual(reponse.status_code, 404)
        self.assertNotIn("X-Accel-Redirect", reponse)

    def test_type_hors_liste_neutralise_et_telecharge(self):
        from events.tests.factories import make_document
        page = make_document(self.event, title="piege.html", content=b"<script>")
        self.client.force_login(self.owner)
        reponse = self.client.get(reverse("document_download", args=[page.pk]))
        self.assertEqual(reponse["Content-Type"], "application/octet-stream")
        self.assertTrue(reponse["Content-Disposition"].startswith("attachment"))

    def test_par_lien_de_partage(self):
        lien = ShareLink.objects.create(dossier=self.dossier, created_by=self.owner, role=DossierAccess.Role.READ)
        reponse = self.client.get(reverse("share_document_download", args=[lien.token, self.document.pk]))
        self.assertIn("X-Accel-Redirect", reponse)

    def test_fichier_absent_404(self):
        os.remove(self.document.file.path)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse("document_download", args=[self.document.pk])).status_code, 404)


@override_settings(GRAPH_TENANT_ID="t", GRAPH_CLIENT_ID="c", GRAPH_CLIENT_SECRET="s",
                   GRAPH_SENDER="chronos@foxugly.com",
                   MAILERS={"default": {"BACKEND": "events.courrier_graph.EmailBackend"}})
class CourrierGraphTests(TestCase):
    def test_envoie_par_sendmail(self):
        with mock.patch("events.graph.ClientGraph.envoyer") as envoyer:
            n = mail.send_mail("Sujet", "Corps", None, ["a@exemple.be"])
        self.assertEqual(n, 1)
        envoyer.assert_called_once_with(["a@exemple.be"], "Sujet", "Corps", html=False)

    def test_un_echec_se_voit(self):
        from events.graph import ErreurGraph
        with mock.patch("events.graph.ClientGraph.envoyer", side_effect=ErreurGraph("refus")):
            with self.assertRaises(ErreurGraph):
                mail.send_mail("Sujet", "Corps", None, ["a@exemple.be"])


class ReglagesDeProductionTests(TestCase):
    """Les réglages refusent de démarrer en production s'il manque l'essentiel."""

    def demarrer(self, **env):
        base = {k: v for k, v in os.environ.items() if not k.startswith(("DJANGO_", "DB_", "SECRET_KEY", "ALLOWED_HOSTS", "SECURE_"))}
        code = "import sys; sys.argv=['manage.py','check']; import config.settings as s; print('OK', s.DATABASES['default']['ENGINE'], s.SECURE_SSL_REDIRECT)"
        return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                              env={**base, **env}, cwd=settings.BASE_DIR)

    def test_sans_cle_secrete(self):
        self.assertIn("SECRET_KEY", self.demarrer(ALLOWED_HOSTS="chronos.foxugly.com").stderr)

    def test_sans_hotes_ou_avec_etoile(self):
        self.assertIn("ALLOWED_HOSTS", self.demarrer(SECRET_KEY="x" * 60).stderr)
        self.assertIn("ALLOWED_HOSTS", self.demarrer(SECRET_KEY="x" * 60, ALLOWED_HOSTS="*").stderr)

    def test_sans_postgresql(self):
        sortie = self.demarrer(SECRET_KEY="x" * 60, ALLOWED_HOSTS="chronos.foxugly.com")
        self.assertIn("DB_ENGINE=postgresql", sortie.stderr)

    def test_configuration_complete(self):
        sortie = self.demarrer(SECRET_KEY="x" * 60, ALLOWED_HOSTS="chronos.foxugly.com", DB_ENGINE="postgresql")
        self.assertIn("OK django.db.backends.postgresql True", sortie.stdout, sortie.stderr)


class CspTests(DocumentFixtureMixin, TestCase):
    """Seuls les scripts portant le nonce de la page s'exécutent."""

    def test_chaque_script_en_ligne_porte_le_nonce_de_l_en_tete(self):
        import re
        self.client.force_login(self.owner)
        for vue in ("chronologie", "calendrier", "liste", "documents"):
            with self.subTest(vue=vue):
                reponse = self.client.get(reverse("timeline", args=[self.dossier.pk]), {"vue": vue})
                entete = reponse["Content-Security-Policy"]
                nonce = re.search(r"'nonce-([^']+)'", entete).group(1)
                script_src = next(d for d in entete.split(";") if d.strip().startswith("script-src"))
                self.assertNotIn("'unsafe-inline'", script_src)
                html = reponse.content.decode()
                # Insensible à la casse : un <SCRIPT> sans nonce ne doit pas échapper au test.
                en_ligne = re.findall(r"<script(?![^>]*\bsrc=)(?![^>]*application/json)([^>]*)>", html,
                                      re.IGNORECASE)
                self.assertTrue(en_ligne)
                for attributs in en_ligne:
                    self.assertIn(f'nonce="{nonce}"', attributs)


class DeploiementEchoueEnClairTests(SimpleTestCase):
    """Le 2026-10-09, le premier déploiement est sorti « Success » sans avoir
    migré : ssm-deploy.sh avait échoué faute de /run/chronos/.env, mais SSM
    ne retient que le code de la dernière commande (un chown, réussi)."""

    racine = Path(settings.BASE_DIR)

    def commandes_ssm(self):
        texte = (self.racine / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")
        debut = texte.index("cat > /tmp/ssm-params.json <<EOF\n") + len("cat > /tmp/ssm-params.json <<EOF\n")
        fin = texte.index("\n          EOF", debut)
        return json.loads(textwrap.dedent(texte[debut:fin]))["commands"]

    def test_la_premiere_commande_ssm_arrete_tout_au_premier_echec(self):
        self.assertEqual(self.commandes_ssm()[0], "set -eu")

    def test_ssm_deploy_refuse_de_continuer_sans_configuration(self):
        script = (self.racine / "deploy" / "ssm-deploy.sh").read_text(encoding="utf-8")
        garde = script.index('if [ ! -s "$ENV_FILE" ]')
        self.assertLess(garde, script.index("manage.py migrate"))
