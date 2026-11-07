"""Les correctifs des mineurs de l'audit du 2026-10-18 (M4, M5, M6, M8, M9, M10, M11).

M2 est gardé par `test_releve` ; chaque classe ici renvoie à son mineur.
"""

import base64
import datetime
import hashlib
import io
import re
import shutil
import tempfile
from email.message import EmailMessage
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from events.courriels import analyser_en_couches, en_markdown, verdict_d_authentification
from events.import_courriels import importer
from events.models import DossierAccess, Event, Invitation, Journal, ShareLink
from events.retention import purger
from events.tests.factories import grant, make_dossier, make_user

RACINE = Path(__file__).resolve().parents[2]
ALIAS = "litige.chronos@foxugly.com"


class MediaMixin:
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.media_root = tempfile.mkdtemp()
        cls.enterClassContext(override_settings(MEDIA_ROOT=cls.media_root))

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(cls.media_root, ignore_errors=True)


def courriel(*, resultats=()):
    m = EmailMessage()
    # Le serveur de réception écrit son en-tête EN TÊTE ; celui du dessous vient
    # de l'expéditeur et ne prouve rien.
    for valeur in resultats:
        m["Authentication-Results"] = valeur
    m["Received"] = "from mx.exemple.be by boite.exemple.be; Wed, 9 Sep 2026 10:00:00 +0000"
    m["From"], m["To"], m["Subject"] = "Partie adverse <adverse@exemple.be>", ALIAS, "Mise en demeure"
    m["Message-ID"] = "<md@exemple.be>"
    m["Date"] = "Wed, 9 Sep 2026 10:00:00 +0000"
    m.set_content("Nous contestons.")
    return m.as_bytes()


EXCHANGE = ("spf=pass (sender IP is 1.2.3.4) smtp.mailfrom=exemple.be; dkim=pass (signature was verified) "
            "header.d=exemple.be;dmarc=pass action=none header.from=exemple.be;compauth=pass reason=100")


class AuthentificationDuCourrielTests(MediaMixin, TestCase):
    """M4 : le verdict SPF/DKIM/DMARC du courriel reçu est gardé à l'import."""

    def setUp(self):
        self.dossier = make_dossier(make_user("owner"))
        self.dossier.alias_courriel = ALIAS
        self.dossier.save()

    def test_le_verdict_se_resume(self):
        self.assertEqual(verdict_d_authentification(EXCHANGE), "spf=pass dkim=pass dmarc=pass")
        self.assertEqual(verdict_d_authentification(""), "")

    def test_seul_l_en_tete_du_serveur_de_reception_compte(self):
        couches = analyser_en_couches(courriel(resultats=["mx.foxugly.com; dmarc=fail", "faux; dmarc=pass"]))
        self.assertEqual(verdict_d_authentification(couches[0].authentification), "dmarc=fail")

    def test_le_md_et_le_journal_le_disent(self):
        (couche,) = analyser_en_couches(courriel(resultats=[EXCHANGE]))
        evenement = importer(self.dossier, couche)
        md = evenement.documents.get(title__endswith=".md").file.read().decode()
        self.assertIn("spf=pass dkim=pass dmarc=pass", md)
        self.assertIn("compauth=pass", md)
        entree = Journal.objects.get(action=Journal.Action.COURRIEL_IMPORTE)
        self.assertEqual(entree.details["authentification"], "spf=pass dkim=pass dmarc=pass")

    def test_sans_en_tete_le_courriel_est_dit_non_authentifie(self):
        (couche,) = analyser_en_couches(courriel())
        self.assertIn("aucune (non authentifié)", en_markdown(couche))
        importer(self.dossier, couche)
        self.assertEqual(Journal.objects.get(action=Journal.Action.COURRIEL_IMPORTE).details["authentification"], "")


class ScriptsAutoHebergesTests(TestCase):
    """M5 : plus aucun script des CDN ; les copies locales gardent leur empreinte."""

    BALISE = re.compile(r'<script src="([^"]+)"\s+integrity="([^"]+)"')

    def gabarits(self):
        for dossier in (RACINE / "templates", RACINE / "events" / "templates"):
            yield from dossier.rglob("*.html")

    def test_la_csp_n_ouvre_plus_les_cdn_aux_scripts(self):
        self.assertNotIn("https://cdn.jsdelivr.net", settings.SECURE_CSP["script-src"])
        self.assertNotIn("https://cdnjs.cloudflare.com", settings.SECURE_CSP["script-src"])

    def test_aucun_gabarit_ne_charge_un_script_distant(self):
        for gabarit in self.gabarits():
            with self.subTest(gabarit=gabarit.name):
                self.assertNotRegex(gabarit.read_text(encoding="utf-8"), r'<script[^>]+src="https?://')

    def test_chaque_copie_correspond_a_son_empreinte(self):
        vues = 0
        for gabarit in self.gabarits():
            texte = gabarit.read_text(encoding="utf-8")
            for chemin, empreinte in re.findall(r"""<script src="\{% static '([^']+)' %\}"\s+integrity="([^"]+)\"""", texte):
                vues += 1
                with self.subTest(chemin=chemin):
                    algo, attendu = empreinte.split("-", 1)
                    octets = (RACINE / "static" / chemin).read_bytes()
                    self.assertEqual(base64.b64encode(hashlib.new(algo, octets).digest()).decode(), attendu)
        self.assertGreaterEqual(vues, 11)

    def test_les_copies_ne_sont_jamais_converties_par_git(self):
        self.assertIn("static/vendor/** -text", (RACINE / ".gitattributes").read_text(encoding="utf-8"))


class PiecesEnSandboxTests(SimpleTestCase):
    """M5 : les pièces servies par nginx portent une CSP `sandbox`, sauf les PDF."""

    def test_la_location_interne_pose_la_csp(self):
        conf = (RACINE / "deploy" / "nginx" / "chronos.foxugly.com.conf").read_text(encoding="utf-8")
        carte = re.search(r"map \$uri \$chronos_csp_piece \{(.*?)\}", conf, re.S).group(1)
        self.assertIn('default "sandbox";', carte)
        self.assertIn('"~*\\.pdf$" "";', carte)
        interne = conf[conf.index("location ^~ /_pieces_internes/"):]
        interne = interne[:interne.index("location = /health/")]
        self.assertIn("add_header Content-Security-Policy $chronos_csp_piece always;", interne)


class DescriptionNettoyeeDansLaListeTests(TestCase):
    """M6 : la liste re-nettoie la description, comme le détail."""

    def test_un_script_enregistre_hors_formulaire_ne_sort_pas(self):
        owner = make_user("owner")
        dossier = make_dossier(owner)
        # Comme l'administration : écrit en base sans passer par `nettoyer`.
        Event.objects.filter(pk=Event.objects.create(dossier=dossier, title="T", date="2026-08-05").pk).update(
            description='<p>ok</p><script>window.pwned=1</script><img src=x onerror="alert(1)">')
        self.client.force_login(owner)
        html = self.client.get(reverse("timeline", args=[dossier.pk]), {"vue": "liste"}).content.decode()
        self.assertIn("<p>ok</p>", html)
        self.assertNotIn("window.pwned", html)
        self.assertNotIn("onerror", html)


class PurgeDesPartagesTests(TestCase):
    """M8 : invitations et liens morts effacés après RETENTION_PARTAGES_JOURS jours."""

    def setUp(self):
        self.owner = make_user("owner")
        self.dossier = make_dossier(self.owner)
        self.vieux = timezone.now() - datetime.timedelta(days=settings.RETENTION_PARTAGES_JOURS + 1)
        self.recent = timezone.now() - datetime.timedelta(days=1)

    def invitation(self, **champs):
        return Invitation.objects.create(dossier=self.dossier, created_by=self.owner,
                                         email="invite@example.test", role=DossierAccess.Role.READ, **champs)

    def lien(self, **champs):
        return ShareLink.objects.create(dossier=self.dossier, created_by=self.owner,
                                        role=DossierAccess.Role.READ, label="Avocat", **champs)

    def test_les_invitations_mortes_depuis_longtemps_partent(self):
        garder = [self.invitation(), self.invitation(revoked_at=self.recent),
                  self.invitation(expires_at=self.recent)]
        partir = [self.invitation(revoked_at=self.vieux), self.invitation(accepted_at=self.vieux),
                  self.invitation(expires_at=self.vieux)]
        self.assertEqual(purger(), (3, 0))
        restantes = set(Invitation.objects.values_list("pk", flat=True))
        self.assertEqual(restantes, {i.pk for i in garder})
        self.assertFalse(restantes & {i.pk for i in partir})

    def test_les_liens_morts_partent_sauf_s_ils_ont_agi(self):
        actif = self.lien()
        sans_fin = self.lien(expires_at=None)
        revoque_hier = self.lien(revoked_at=self.recent)
        mort = self.lien(revoked_at=self.vieux)
        expire = self.lien(expires_at=self.vieux)
        a_agi = self.lien(revoked_at=self.vieux)
        # Créé par le propriétaire (auteur connu) : n'empêche pas la purge.
        Journal.objects.create(dossier=self.dossier, action=Journal.Action.LIEN_CREE,
                               auteur=self.owner, lien=mort, objet="Avocat")
        # Un export par le lien (auteur inconnu) : la trace doit garder son auteur.
        Journal.objects.create(dossier=self.dossier, action=Journal.Action.EXPORT, lien=a_agi, objet="x")
        self.assertEqual(purger(), (0, 2))
        restants = set(ShareLink.objects.values_list("pk", flat=True))
        self.assertEqual(restants, {actif.pk, sans_fin.pk, revoque_hier.pk, a_agi.pk})
        self.assertNotIn(expire.pk, restants)
        # Le journal reste entier, libellé compris.
        self.assertTrue(Journal.objects.filter(action=Journal.Action.LIEN_CREE, objet="Avocat",
                                               lien__isnull=True).exists())

    def test_la_commande_et_son_essai(self):
        self.invitation(revoked_at=self.vieux)
        sortie = io.StringIO()
        call_command("purger_partages", "--essai", stdout=sortie)
        self.assertIn("1 invitation(s)", sortie.getvalue())
        self.assertEqual(Invitation.objects.count(), 1)
        call_command("purger_partages", stdout=io.StringIO())
        self.assertEqual(Invitation.objects.count(), 0)


class SuppressionDuCompteTests(TestCase):
    """M8 : suppression du compte en libre-service, confirmée par le mot de passe."""

    def setUp(self):
        self.owner = make_user("owner")
        self.dossier = make_dossier(self.owner)
        self.collegue = make_user("collegue", password="le-bon-mot-de-passe")
        grant(self.dossier, self.collegue, DossierAccess.Role.WRITE)
        self.url = reverse("compte_supprimer")

    def test_il_faut_etre_connecte(self):
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_un_mauvais_mot_de_passe_ne_supprime_rien(self):
        self.client.force_login(self.collegue)
        reponse = self.client.post(self.url, {"password": "faux"})
        self.assertEqual(reponse.status_code, 200)
        self.assertTrue(get_user_model().objects.filter(pk=self.collegue.pk).exists())

    def test_un_get_ne_supprime_rien(self):
        self.client.force_login(self.collegue)
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.assertTrue(get_user_model().objects.filter(pk=self.collegue.pk).exists())

    def test_le_compte_part_et_le_journal_le_dit(self):
        self.client.force_login(self.collegue)
        reponse = self.client.post(self.url, {"password": "le-bon-mot-de-passe"})
        self.assertRedirects(reponse, reverse("account_login"), fetch_redirect_response=False)
        self.assertFalse(get_user_model().objects.filter(pk=self.collegue.pk).exists())
        self.assertFalse(self.dossier.accesses.exists())
        entree = Journal.objects.get(action=Journal.Action.ACCES_RETIRE)
        self.assertEqual((entree.objet, entree.details["motif"]), ("collegue", "compte_supprime"))
        self.assertIsNone(entree.auteur_id)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_le_proprietaire_d_un_dossier_est_refuse(self):
        self.client.force_login(self.owner)
        reponse = self.client.post(self.url, {"password": "pw-for-tests"})
        self.assertEqual(reponse.status_code, 200)
        self.assertContains(reponse, "possède un dossier")
        self.assertTrue(get_user_model().objects.filter(pk=self.owner.pk).exists())


class ExportAnonymeLimiteTests(MediaMixin, TestCase):
    """M9 : un export par lien et par minute."""

    def setUp(self):
        owner = make_user("owner")
        self.dossier = make_dossier(owner)
        Event.objects.create(dossier=self.dossier, title="Devis", date="2026-08-05")
        self.lien = ShareLink.objects.create(dossier=self.dossier, created_by=owner,
                                             role=DossierAccess.Role.READ)
        self.url = reverse("share_dossier_export", args=[self.lien.token])

    def test_le_second_export_de_la_minute_est_refuse(self):
        self.assertEqual(self.client.post(self.url).status_code, 200)
        reponse = self.client.post(self.url)
        self.assertEqual(reponse.status_code, 429)
        self.assertEqual(reponse["Retry-After"], "60")
        self.assertEqual(reponse["Referrer-Policy"], "no-referrer")
        self.assertEqual(Journal.objects.filter(action=Journal.Action.EXPORT).count(), 1)

    def test_un_autre_lien_n_est_pas_freine(self):
        self.client.post(self.url)
        autre = ShareLink.objects.create(dossier=self.dossier, created_by=self.dossier.owner,
                                         role=DossierAccess.Role.READ)
        self.assertEqual(self.client.post(reverse("share_dossier_export", args=[autre.token])).status_code, 200)

    def test_apres_la_minute_l_export_repart(self):
        self.client.post(self.url)
        # Le journal est en ajout seul : on avance l'horloge plutôt que de vieillir l'entrée.
        plus_tard = timezone.now() + datetime.timedelta(minutes=2)
        with mock.patch("events.share_views.timezone.now", return_value=plus_tard):
            self.assertEqual(self.client.post(self.url).status_code, 200)


class DeploiementTests(SimpleTestCase):
    """M10 et M11 : pas de déploiement sans CI verte ; la release Sentry est le SHA."""

    def setUp(self):
        self.deploy = (RACINE / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")

    def test_plus_de_workflow_dispatch(self):
        self.assertNotIn("workflow_dispatch:", self.deploy)
        self.assertIn("if: ${{ github.event.workflow_run.conclusion == 'success' }}", self.deploy)

    def test_la_ci_passe_ruff_a_la_version_de_la_flotte(self):
        ci = (RACINE / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("ruff check .", ci)
        # Une version figée, pas une version précise : Dependabot la suit avec la flotte.
        self.assertRegex((RACINE / "requirements-dev.txt").read_text(encoding="utf-8"), r"(?m)^ruff==\d+\.\d+\.\d+$")

    def test_le_sha_deploye_devient_sentry_release(self):
        self.assertIn("echo SENTRY_RELEASE=$SHA > /var/lib/fleet-deploy/Chronos.release.env.tmp", self.deploy)
        for unite in ("chronos-gunicorn.service", "chronos-mail-import.service", "chronos-purge.service"):
            with self.subTest(unite=unite):
                lignes = (RACINE / "deploy" / unite).read_text(encoding="utf-8").splitlines()
                self.assertIn("EnvironmentFile=-/var/lib/fleet-deploy/Chronos.release.env", lignes)
                # Après .env : c'est lui qui l'emporte.
                self.assertGreater(lignes.index("EnvironmentFile=-/var/lib/fleet-deploy/Chronos.release.env"),
                                   lignes.index("EnvironmentFile=/run/chronos/.env"))

    def test_la_purge_est_installee_depuis_le_miroir_de_root(self):
        for unite in ("chronos-purge.service", "chronos-purge.timer"):
            with self.subTest(unite=unite):
                self.assertIn("$G:deploy/%s > /etc/systemd/system/%s" % (unite, unite), self.deploy)
        self.assertIn("systemctl enable --now chronos-purge.timer", self.deploy)
        lignes = (RACINE / "deploy" / "chronos-purge.service").read_text(encoding="utf-8").splitlines()
        for directive in ("User=django", "NoNewPrivileges=yes", "PrivateTmp=yes", "ProtectSystem=full"):
            self.assertIn(directive, lignes)
