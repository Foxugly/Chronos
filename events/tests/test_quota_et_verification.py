"""Le quota de dossiers et la vérification d'adresse obligatoire (2026-10-21)."""

import importlib
import re

from allauth.account.models import EmailAddress
from django.apps import apps as global_apps
from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase
from django.urls import reverse

from events.models import Dossier, DossierAccess, Journal, Profil
from events.quotas import QuotaAtteint, creer_un_dossier, quota_de
from events.tests.factories import grant, make_dossier, make_user, verifier_adresse

migration_0025 = importlib.import_module("events.migrations.0025_quota_du_proprietaire")
migration_0026 = importlib.import_module(
    "events.migrations.0026_adresse_verifiee_des_superutilisateurs"
)


def _quota(user, maximum):
    Profil.objects.update_or_create(user=user, defaults={"max_dossiers": maximum})


class QuotaDeDossiersTests(TestCase):
    def setUp(self):
        self.user = make_user("createur")
        self.client.force_login(self.user)

    def _creer(self, nom="Nouvelle affaire"):
        return self.client.post(reverse("dossier_creer"), {"name": nom})

    def test_sans_profil_le_quota_est_zero_et_bloque(self):
        reponse = self._creer()
        self.assertEqual(reponse.status_code, 403)
        self.assertContains(reponse, "La création de dossiers n&#x27;est pas activée",
                            status_code=403)
        self.assertFalse(Dossier.objects.exists())

    def test_quota_zero_bloque(self):
        _quota(self.user, 0)
        self.assertEqual(self._creer().status_code, 403)
        self.assertFalse(Dossier.objects.exists())

    def test_sous_la_limite_la_creation_passe(self):
        _quota(self.user, 2)
        make_dossier(self.user, name="Premier")
        reponse = self._creer("Second")
        dossier = Dossier.objects.get(name="Second")
        self.assertRedirects(reponse, reverse("timeline", args=[dossier.pk]),
                             fetch_redirect_response=False)
        self.assertEqual(dossier.owner, self.user)
        self.assertFalse(DossierAccess.objects.filter(dossier=dossier).exists())  # invariant 1
        entree = Journal.objects.get(dossier=dossier)
        self.assertEqual(entree.action, Journal.Action.DOSSIER_CREE)
        self.assertEqual(entree.auteur, self.user)

    def test_a_la_limite_la_creation_est_refusee(self):
        _quota(self.user, 2)
        make_dossier(self.user, name="Un")
        make_dossier(self.user, name="Deux")
        reponse = self._creer("Trois")
        self.assertEqual(reponse.status_code, 403)
        self.assertContains(reponse, "Vous avez atteint le nombre maximum de dossiers (2).",
                            status_code=403)
        self.assertFalse(Dossier.objects.filter(name="Trois").exists())

    def test_la_page_de_creation_refuse_a_la_limite(self):
        _quota(self.user, 1)
        self.assertEqual(self.client.get(reverse("dossier_creer")).status_code, 200)
        make_dossier(self.user)
        self.assertEqual(self.client.get(reverse("dossier_creer")).status_code, 403)

    def test_les_dossiers_partages_ne_comptent_pas(self):
        _quota(self.user, 1)
        autre = make_user("autre")
        grant(make_dossier(autre, name="Chez l'autre"), self.user, DossierAccess.Role.WRITE)
        grant(make_dossier(autre, name="Chez l'autre 2"), self.user, DossierAccess.Role.READ)
        self.assertEqual(quota_de(self.user).possedes, 0)
        self.assertEqual(self._creer("Le mien").status_code, 302)
        self.assertTrue(Dossier.objects.filter(name="Le mien", owner=self.user).exists())

    def test_le_superutilisateur_suit_la_meme_regle(self):
        chef = make_user("chef", is_staff=True, is_superuser=True)
        self.client.force_login(chef)
        self.assertEqual(self._creer().status_code, 403)
        self.assertFalse(Dossier.objects.exists())

    def test_un_nom_vide_est_refuse_sans_rien_creer(self):
        _quota(self.user, 1)
        reponse = self._creer("   ")
        self.assertEqual(reponse.status_code, 400)
        self.assertFalse(Dossier.objects.exists())

    def test_la_fonction_de_creation_garde_le_quota(self):
        """Tout chemin de création passe par `creer_un_dossier` (hors administration)."""
        with self.assertRaises(QuotaAtteint):
            creer_un_dossier(self.user, "Interdit")
        self.assertFalse(Dossier.objects.exists())
        _quota(self.user, 1)
        creer_un_dossier(self.user, "Permis")
        with self.assertRaises(QuotaAtteint):
            creer_un_dossier(self.user, "De trop")
        self.assertEqual(Dossier.objects.filter(owner=self.user).count(), 1)


class BoutonAjouterUnDossierTests(TestCase):
    def setUp(self):
        self.user = make_user("lecteur")
        self.client.force_login(self.user)

    def _accueil(self):
        return self.client.get(reverse("dossier_list")).content.decode()

    def test_quota_zero_bouton_desactive_et_explique(self):
        page = self._accueil()
        self.assertRegex(page, r"<button[^>]*disabled[^>]*data-ajouter-dossier")
        self.assertNotIn(reverse("dossier_creer"), page)
        self.assertIn("La création de dossiers n&#x27;est pas activée", page)

    def test_sous_la_limite_le_bouton_mene_au_formulaire(self):
        _quota(self.user, 3)
        make_dossier(self.user)
        page = self._accueil()
        self.assertRegex(page, r'<a[^>]*href="%s"[^>]*data-ajouter-dossier' % reverse("dossier_creer"))
        self.assertIn("1 / 3 dossiers", page)

    def test_a_la_limite_le_bouton_est_desactive(self):
        _quota(self.user, 1)
        make_dossier(self.user)
        page = self._accueil()
        self.assertRegex(page, r"<button[^>]*disabled[^>]*data-ajouter-dossier")
        self.assertNotIn(reverse("dossier_creer"), page)
        self.assertIn("1 / 1 dossiers", page)
        self.assertIn("Vous avez atteint le nombre maximum de dossiers (1).", page)


class QuotaDansLAdministrationTests(TestCase):
    def test_le_quota_se_regle_sur_la_fiche_de_l_utilisateur(self):
        admin = make_user("admin", is_staff=True, is_superuser=True)
        self.client.force_login(admin)
        cible = make_user("client")
        url = reverse("admin:auth_user_change", args=[cible.pk])
        page = self.client.get(url)
        self.assertContains(page, "max_dossiers")
        self.assertContains(page, "Nombre maximum de dossiers")


class MigrationDuQuotaTests(TestCase):
    def test_le_proprietaire_recoit_cinq_casse_ignoree(self):
        proprietaire = make_user("jean")
        proprietaire.email = "RVilain@Foxugly.com"
        proprietaire.save()
        autre = make_user("autre")
        make_dossier(autre)
        migration_0025.donner_le_quota(global_apps, None)
        self.assertEqual(Profil.objects.get(user=proprietaire).max_dossiers, 5)
        self.assertEqual(quota_de(autre).maximum, 0)  # garde ses dossiers, n'en crée plus
        self.assertFalse(Profil.objects.filter(user=autre).exists())

    def test_un_profil_existant_est_mis_a_jour(self):
        proprietaire = make_user("jean")
        proprietaire.email = "rvilain@foxugly.com"
        proprietaire.save()
        _quota(proprietaire, 0)
        migration_0025.donner_le_quota(global_apps, None)
        self.assertEqual(Profil.objects.get(user=proprietaire).max_dossiers, 5)

    def test_sans_ce_compte_rien_ne_change(self):
        make_user("quelqu-un")
        migration_0025.donner_le_quota(global_apps, None)
        self.assertFalse(Profil.objects.exists())


class VerificationObligatoireTests(TestCase):
    def test_le_reglage_est_obligatoire(self):
        from django.conf import settings

        self.assertEqual(settings.ACCOUNT_EMAIL_VERIFICATION, "mandatory")

    def test_une_adresse_non_verifiee_ne_connecte_pas(self):
        make_user("pas-verifie", password="un-bon-mot-de-passe")
        reponse = self.client.post(
            reverse("account_login"),
            {"login": "pas-verifie", "password": "un-bon-mot-de-passe"},
        )
        self.assertRedirects(reponse, reverse("account_email_verification_sent"),
                             fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["pas-verifie@example.test"])

    def test_une_adresse_verifiee_connecte(self):
        user = make_user("verifie", password="un-bon-mot-de-passe")
        verifier_adresse(user)
        self.client.post(
            reverse("account_login"),
            {"login": "verifie@example.test", "password": "un-bon-mot-de-passe"},
        )
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)

    def test_inscription_puis_confirmation_puis_connexion(self):
        reponse = self.client.post(reverse("account_signup"), {
            "username": "nouvelle",
            "email": "nouvelle@example.test",
            "password1": "un-tres-bon-mot-de-passe1",
            "password2": "un-tres-bon-mot-de-passe1",
        })
        self.assertRedirects(reponse, reverse("account_email_verification_sent"),
                             fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)

        page = self.client.get(reverse("account_email_verification_sent"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "auth-card")  # mise en page du site, pas celle d'allauth

        lien = re.search(r"/accounts/confirm-email/[^/\s]+/", mail.outbox[0].body).group(0)
        self.assertEqual(self.client.get(lien).status_code, 200)
        self.client.post(lien)
        self.assertTrue(EmailAddress.objects.get(email="nouvelle@example.test").verified)

        self.client.post(reverse("account_login"), {
            "login": "nouvelle", "password": "un-tres-bon-mot-de-passe1",
        })
        self.assertIn("_auth_user_id", self.client.session)

    def test_un_lien_expire_ne_renvoie_pas_vers_la_page_masquee(self):
        page = self.client.get("/accounts/confirm-email/cle-inconnue/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "data-lien-expire")
        self.assertNotContains(page, 'href="/accounts/email/"')
        self.assertContains(page, reverse("account_login"))


class MigrationDesSuperutilisateursTests(TestCase):
    def _migrer(self):
        migration_0026.verifier_les_superutilisateurs(global_apps, None)

    def test_le_superutilisateur_sans_adresse_recoit_une_principale_verifiee(self):
        chef = make_user("chef", is_staff=True, is_superuser=True)
        chef.email = "Chef@Example.test"
        chef.save()
        self._migrer()
        adresse = EmailAddress.objects.get(user=chef)
        self.assertEqual(adresse.email, "chef@example.test")
        self.assertTrue(adresse.primary)
        self.assertTrue(adresse.verified)

    def test_une_principale_non_verifiee_est_verifiee(self):
        chef = make_user("chef", is_staff=True, is_superuser=True)
        EmailAddress.objects.create(user=chef, email=chef.email, primary=True, verified=False)
        self._migrer()
        self.assertTrue(EmailAddress.objects.get(user=chef).verified)

    def test_une_adresse_non_principale_devient_principale(self):
        chef = make_user("chef", is_staff=True, is_superuser=True)
        EmailAddress.objects.create(user=chef, email=chef.email, primary=False, verified=False)
        self._migrer()
        adresse = EmailAddress.objects.get(user=chef)
        self.assertTrue(adresse.primary)
        self.assertTrue(adresse.verified)

    def test_les_autres_comptes_ne_sont_pas_touches(self):
        simple = make_user("simple")
        EmailAddress.objects.create(user=simple, email=simple.email, primary=True, verified=False)
        sans_ligne = make_user("sans-ligne")
        self._migrer()
        self.assertFalse(EmailAddress.objects.get(user=simple).verified)
        self.assertFalse(EmailAddress.objects.filter(user=sans_ligne).exists())

    def test_un_superutilisateur_sans_adresse_du_tout_passe(self):
        chef = get_user_model().objects.create_user(
            username="chef", email="", password="pw", is_superuser=True, is_staff=True)
        self._migrer()
        self.assertFalse(EmailAddress.objects.filter(user=chef).exists())

    def test_apres_la_migration_le_proprietaire_se_connecte(self):
        chef = make_user("chef", password="un-bon-mot-de-passe", is_staff=True, is_superuser=True)
        self._migrer()
        self.client.post(reverse("account_login"), {
            "login": chef.email, "password": "un-bon-mot-de-passe",
        })
        self.assertEqual(int(self.client.session["_auth_user_id"]), chef.pk)
