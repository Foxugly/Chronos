from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from events.tests.factories import make_user


class LoginRequiredTests(TestCase):
    def test_root_redirects_anonymous_to_login(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])

    def test_login_page_is_reachable_anonymously(self):
        """Garde-fou contre la boucle de redirection : allauth >= 65 marque
        ses vues avec login_not_required."""
        response = self.client.get(reverse("account_login"))
        self.assertEqual(response.status_code, 200)

    def test_password_reset_is_reachable_anonymously(self):
        """Un mot de passe oublié doit pouvoir être réinitialisé sans session."""
        response = self.client.get(reverse("account_reset_password"))
        self.assertEqual(response.status_code, 200)


class OpenSignupTests(TestCase):
    """L'inscription a d'abord été fermée, puis rouverte sur décision du
    propriétaire, en même temps que les invitations.

    Ce que ces tests gardent n'est pas l'ouverture elle-même — c'est le fait
    qu'elle **ne donne accès à rien**. Un compte neuf doit voir une
    application vide : l'accès à un dossier vient d'un `DossierAccess`, que
    seul le propriétaire accorde.
    """

    def test_the_signup_page_offers_a_form(self):
        response = self.client.get(reverse("account_signup"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "<form", html=False)

    def test_a_valid_signup_really_creates_an_account(self):
        avant = get_user_model().objects.count()
        response = self.client.post(
            reverse("account_signup"),
            {
                "username": "nouveau",
                "email": "nouveau@example.test",
                "password1": "a-really-good-password1",
                "password2": "a-really-good-password1",
            },
        )
        self.assertEqual(get_user_model().objects.count(), avant + 1)
        self.assertEqual(response.status_code, 302)

    def test_a_brand_new_account_sees_no_dossier_at_all(self):
        """La propriété qui compte depuis l'ouverture : s'inscrire ne donne
        accès à rien. Sans cela, l'inscription libre serait une porte."""
        from events.tests.factories import make_dossier, make_user

        proprietaire = make_user("proprietaire")
        dossier = make_dossier(proprietaire, name="Dossier adverse")

        self.client.post(
            reverse("account_signup"),
            {
                "username": "curieux",
                "email": "curieux@example.test",
                "password1": "a-really-good-password1",
                "password2": "a-really-good-password1",
            },
        )

        accueil = self.client.get(reverse("dossier_list"))
        self.assertEqual(accueil.status_code, 200)
        self.assertNotContains(accueil, dossier.name)
        self.assertEqual(
            self.client.get(
                reverse("timeline", args=[dossier.pk])
            ).status_code,
            404,
        )


class ClosedEmailManagementTests(TestCase):
    """Les comptes sont créés et gérés par l'administrateur : il n'y a pas
    de gestion d'e-mails en self-service dans cette application."""

    def setUp(self):
        self.user = make_user("someone-with-an-account", password="a-good-password")

    def test_email_management_is_closed_to_authenticated_users(self):
        self.client.force_login(self.user)
        response = self.client.get("/accounts/email/")
        self.assertEqual(response.status_code, 404)


class LoginMethodTests(TestCase):
    def setUp(self):
        self.user = make_user("someone", password="a-good-password")

    def test_login_with_username(self):
        self.assertTrue(
            self.client.login(username="someone", password="a-good-password")
        )

    def test_login_with_email(self):
        """`ACCOUNT_LOGIN_METHODS` accepte les deux (spec §10), mais seul le
        nom d'utilisateur était couvert : retirer `"email"` du réglage ne
        faisait virer aucun test, alors que c'est l'identifiant que les
        comptes créés par l'administrateur retiennent le mieux."""
        response = self.client.post(
            reverse("account_login"),
            {"login": self.user.email, "password": "a-good-password"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            int(self.client.session["_auth_user_id"]), self.user.pk
        )

    def test_login_by_email_refuses_a_wrong_password(self):
        """Garde-fou : le test précédent passerait aussi si l'adresse
        ouvrait une session sans vérifier le mot de passe."""
        self.client.post(
            reverse("account_login"),
            {"login": self.user.email, "password": "ce-n-est-pas-le-bon"},
        )
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_authenticated_user_reaches_root(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get("/").status_code, 200)


class AdminLoginIsShadowedTests(TestCase):
    """I1 : `/admin/login/` était une seconde porte, sans compteur d'échecs.

    Django décore sa vue de connexion admin avec `@login_not_required` :
    elle était donc atteignable sans session et acceptait un nombre illimité
    de tentatives, là où `/accounts/login/` est limité par allauth. Les deux
    portes ouvrent sur les mêmes mots de passe.
    """

    def test_anonymous_is_sent_to_the_rate_limited_login(self):
        response = self.client.get("/admin/login/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/accounts/login/?next=/admin/")

    def test_the_admin_login_form_is_no_longer_served(self):
        """La propriété qui compte : ce n'est pas la redirection, c'est
        qu'aucun formulaire de connexion admin ne répond ici."""
        response = self.client.get("/admin/login/", follow=True)
        self.assertNotContains(response, "id_username")
        self.assertTemplateNotUsed(response, "admin/login.html")

    def test_posting_credentials_to_the_admin_login_opens_no_session(self):
        make_user("staffer", password="a-good-password", is_staff=True)
        self.client.post(
            "/admin/login/",
            {"username": "staffer", "password": "a-good-password"},
        )
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_the_shadow_is_declared_before_the_admin(self):
        """La protection tient à l'ordre de déclaration dans
        `config/urls.py`, comme pour `/accounts/email/`. Déplacer
        `admin.site.urls` au-dessus rouvre la porte, et c'est ce que ce test
        attrape : l'URL nommée `admin:login` existe toujours, mais elle ne
        répond plus."""
        self.assertEqual(reverse("admin:login"), "/admin/login/")
        self.assertEqual(self.client.get("/admin/login/").status_code, 302)

    def test_authenticated_non_staff_user_gets_404_on_admin_login(self):
        """Régression 1 : la redirection vers `/accounts/login/` ne doit jamais être
        servie à quelqu'un déjà connecté -- allauth le renverrait tout droit
        vers `next` (donc vers `/admin/`), qui le renverrait ici à son tour,
        indéfiniment."""
        make_user("lecteur", password="a-good-password")
        self.client.login(username="lecteur", password="a-good-password")
        response = self.client.get("/admin/login/")
        self.assertEqual(response.status_code, 404)

    def test_authenticated_non_staff_user_does_not_loop_on_admin_index(self):
        """La propriété qui compte : la navigation se termine. Avant le
        correctif, `follow=True` levait `RedirectCycleError`."""
        make_user("lecteur-bis", password="a-good-password")
        self.client.login(username="lecteur-bis", password="a-good-password")
        response = self.client.get("/admin/", follow=True)
        self.assertEqual(response.status_code, 404)

    def test_superuser_still_reaches_the_admin_index(self):
        """Témoin négatif : un compte `staff`/superuser ne passe jamais par
        la vue de connexion admin une fois authentifié, donc la boucle ne le
        concerne pas -- mais on le vérifie."""
        make_user(
            "admin-du-site",
            password="a-good-password",
            is_staff=True,
            is_superuser=True,
        )
        self.client.login(username="admin-du-site", password="a-good-password")
        response = self.client.get("/admin/")
        self.assertEqual(response.status_code, 200)


class ClientIpBehindNginxTests(TestCase):
    """Les limites d'allauth comptent par adresse du client vue par nginx.

    nginx est le seul proxy et ajoute l'adresse qu'il voit en fin de
    X-Forwarded-For. Sans `ALLAUTH_TRUSTED_PROXY_COUNT`, allauth prenait
    REMOTE_ADDR (127.0.0.1 pour tous) : un seul compteur partagé.
    """

    def ip(self, xff=None):
        from allauth.account.adapter import get_adapter
        from django.test import RequestFactory

        extra = {"REMOTE_ADDR": "127.0.0.1"}
        if xff is not None:
            extra["HTTP_X_FORWARDED_FOR"] = xff
        return get_adapter().get_client_ip(RequestFactory().post("/accounts/login/", **extra))

    def test_two_clients_do_not_share_a_counter(self):
        self.assertEqual(self.ip("203.0.113.7"), "203.0.113.7")
        self.assertEqual(self.ip("198.51.100.9"), "198.51.100.9")

    def test_a_forged_x_forwarded_for_does_not_change_the_address(self):
        self.assertEqual(self.ip("1.2.3.4, 203.0.113.7"), "203.0.113.7")
        self.assertEqual(self.ip("5.6.7.8, 9.9.9.9, 203.0.113.7"), "203.0.113.7")

    def test_without_header_the_socket_address_is_used(self):
        self.assertEqual(self.ip(), "127.0.0.1")
