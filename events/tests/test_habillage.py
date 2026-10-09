"""L'habillage commun à la flotte (STANDARD-frontend-layout.md) : topbar, footer,
thème sombre, page de confidentialité, version."""

from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse

from events.tests.factories import make_dossier, make_user


class HabillageTests(TestCase):
    def setUp(self):
        self.owner = make_user("owner")
        self.dossier = make_dossier(self.owner, name="ACMEBAT")

    def page(self, url, connecte=True):
        if connecte:
            self.client.force_login(self.owner)
        return self.client.get(url).content.decode()

    def test_topbar_dans_l_ordre_de_la_flotte_theme_langue_utilisateur(self):
        html = self.page(reverse("dossier_list"))
        theme = html.index("data-basculer-theme")
        langue = html.index('class="dropdown selecteur-langue"')
        utilisateur = html.index('aria-label="Mon compte"')
        self.assertLess(theme, langue)
        self.assertLess(langue, utilisateur)
        self.assertIn("topbar__brand", html)

    def test_un_visiteur_anonyme_a_le_bouton_se_connecter(self):
        html = self.page(reverse("account_login"), connecte=False)
        self.assertIn("topbar__action--login", html)
        self.assertNotIn('aria-label="Mon compte"', html)

    def test_footer_complet(self):
        html = self.page(reverse("dossier_list"))
        self.assertIn("Version %s" % settings.VERSION, html)
        self.assertIn('href="https://www.foxugly.com"', html)
        self.assertIn('href="%s"' % reverse("confidentialite"), html)

    def test_le_lien_d_evitement_mene_au_contenu(self):
        html = self.page(reverse("dossier_list"))
        self.assertIn('href="#main-content"', html)
        self.assertIn('id="main-content"', html)

    def test_le_theme_est_pose_avant_le_rendu_et_avec_le_nonce(self):
        """Le script anti-flash est en ligne : sans nonce, la CSP le bloque."""
        reponse = self.client.get(reverse("account_login"))
        html = reponse.content.decode()
        tete = html[:html.index("</head>")]
        self.assertIn('localStorage.getItem("theme")', tete)
        self.assertRegex(tete, r'<script nonce="[^"]+">\s*\(function \(\) \{\s*var theme')

    def test_la_confidentialite_se_lit_sans_compte(self):
        reponse = self.client.get(reverse("confidentialite"))
        self.assertEqual(reponse.status_code, 200)
        self.assertContains(reponse, "Foxugly SRL")

    @override_settings(VERSION="9.9.9")
    def test_health_donne_la_version(self):
        self.assertEqual(self.client.get("/health/").json()["version"], "9.9.9")

    def test_l_en_tete_du_dossier_a_le_retour_et_le_titre_centre(self):
        html = self.page(reverse("timeline", args=[self.dossier.pk]) + "?vue=liste")
        self.assertIn('<h1 class="page-header__title">ACMEBAT</h1>', html)
        self.assertIn('class="page-header__left"', html)
