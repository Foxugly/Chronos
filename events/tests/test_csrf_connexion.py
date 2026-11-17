"""Un formulaire de connexion envoyé une seconde fois ne doit pas finir en 403.

Se connecter renouvelle le jeton CSRF. Un second envoi du même formulaire
(double clic, gestionnaire de mots de passe) ou celui d'un autre onglet resté
sur la page de connexion porte donc un jeton périmé : Django le refuse, et
l'on voit une erreur alors qu'on est bel et bien connecté (prod, 16 et
17/11/2026).
"""

from django.test import Client, TestCase

from events.tests.factories import make_user

CONNEXION = "/accounts/login/"


class EchecCsrfALaConnexionTests(TestCase):
    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        self.utilisateur = make_user("owner")

    def poster(self, chemin=CONNEXION, **donnees):
        return self.client.post(chemin, {"login": "owner", "password": "x",
                                         "csrfmiddlewaretoken": "perime", **donnees})

    def test_deja_connecte_on_suit_next(self):
        self.client.force_login(self.utilisateur)
        reponse = self.poster(next="/dossiers/1/partage/")
        self.assertEqual(reponse.status_code, 302)
        self.assertEqual(reponse["Location"], "/dossiers/1/partage/")

    def test_deja_connecte_sans_next_on_va_a_l_accueil(self):
        self.client.force_login(self.utilisateur)
        reponse = self.poster()
        self.assertEqual((reponse.status_code, reponse["Location"]), (302, "/"))

    def test_un_next_vers_un_autre_site_est_ignore(self):
        self.client.force_login(self.utilisateur)
        for suivant in ("https://exemple.invalid/", "//exemple.invalid/", "javascript:alert(1)"):
            with self.subTest(suivant=suivant):
                reponse = self.poster(next=suivant)
                self.assertEqual((reponse.status_code, reponse["Location"]), (302, "/"))

    def test_anonyme_reste_refuse(self):
        self.assertEqual(self.poster().status_code, 403)

    def test_ailleurs_que_la_connexion_reste_refuse(self):
        """Un jeton périmé sur toute autre page reste un refus : seule la
        connexion, sans effet pour qui est déjà connecté, est rattrapée."""
        self.client.force_login(self.utilisateur)
        self.assertEqual(self.poster("/accounts/logout/").status_code, 403)
        self.assertEqual(self.poster("/dossiers/nouveau/", name="X").status_code, 403)

    def test_le_formulaire_de_connexion_ne_part_qu_une_fois(self):
        page = Client().get(CONNEXION).content.decode()
        self.assertIn("data-un-seul-envoi", page)
