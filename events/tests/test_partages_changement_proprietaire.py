"""Un changement de propriétaire, dans l'admin, annule les invitations encore
en suspens et révoque les liens de partage : les uns et les autres avaient été
émis au nom de l'ancien propriétaire."""

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from events.models import DossierAccess, Invitation, Journal, ShareLink
from admin_2fa.testing import force_admin_login
from events.tests.factories import make_dossier, make_user


class InvitationsAuChangementDeProprietaireTests(TestCase):
    def setUp(self):
        self.admin = make_user("admin", is_staff=True, is_superuser=True)
        self.ancien = make_user("ancien")
        self.nouveau = make_user("nouveau")
        self.dossier = make_dossier(self.ancien, name="Litige")
        maintenant = timezone.now()

        def invitation(email, **champs):
            return Invitation.objects.create(dossier=self.dossier, email=email, role="read",
                                             created_by=self.ancien, **champs)

        self.en_attente = invitation("attente@example.test")
        self.expiree = invitation("expiree@example.test", expires_at=maintenant - timedelta(days=1))
        self.acceptee = invitation("acceptee@example.test", accepted_at=maintenant,
                                   accepted_by=self.nouveau)
        self.annulee = invitation("annulee@example.test", revoked_at=maintenant - timedelta(days=2))
        def lien(label, **champs):
            return ShareLink.objects.create(dossier=self.dossier, created_by=self.ancien,
                                            role=DossierAccess.Role.READ, label=label, **champs)

        self.lien_actif = lien("actif")
        self.lien_expire = lien("expire", expires_at=maintenant - timedelta(days=1))
        self.lien_revoque = lien("revoque", revoked_at=maintenant - timedelta(days=2))
        force_admin_login(self.client, self.admin)  # l'admin exige la 2FA (admin_2fa/)

    def enregistrer(self, owner):
        return self.client.post(f"/admin/events/dossier/{self.dossier.pk}/change/", {
            "name": "Litige", "owner": owner.pk,
            "accesses-TOTAL_FORMS": "0", "accesses-INITIAL_FORMS": "0",
            "accesses-MIN_NUM_FORMS": "0", "accesses-MAX_NUM_FORMS": "1000",
        })

    def recharger(self):
        for objet in (self.en_attente, self.expiree, self.acceptee, self.annulee,
                      self.lien_actif, self.lien_expire, self.lien_revoque):
            objet.refresh_from_db()

    def test_les_invitations_en_suspens_sont_annulees_et_journalisees(self):
        annulee_avant = self.annulee.revoked_at
        self.assertEqual(self.enregistrer(self.nouveau).status_code, 302)
        self.recharger()
        self.assertIsNotNone(self.en_attente.revoked_at)
        self.assertIsNotNone(self.expiree.revoked_at)
        self.assertIsNone(self.acceptee.revoked_at)
        self.assertEqual(self.annulee.revoked_at, annulee_avant)

        annulations = Journal.objects.filter(action=Journal.Action.INVITATION_ANNULEE)
        self.assertEqual(sorted(annulations.values_list("objet", flat=True)),
                         ["attente@example.test", "expiree@example.test"])
        for entree in annulations:
            self.assertTrue(entree.via_admin)
            self.assertEqual(entree.details["motif"], "changement_de_proprietaire")
        self.assertTrue(Journal.objects.filter(action=Journal.Action.DOSSIER_MODIFIE).exists())

    def test_les_liens_non_revoques_sont_revoques_et_journalises(self):
        revoque_avant = self.lien_revoque.revoked_at
        self.enregistrer(self.nouveau)
        self.recharger()
        self.assertIsNotNone(self.lien_actif.revoked_at)
        self.assertIsNotNone(self.lien_expire.revoked_at)
        self.assertEqual(self.lien_revoque.revoked_at, revoque_avant)

        revocations = Journal.objects.filter(action=Journal.Action.LIEN_REVOQUE)
        self.assertEqual(sorted(revocations.values_list("objet", flat=True)), ["actif", "expire"])
        for entree in revocations:
            self.assertTrue(entree.via_admin)
            self.assertEqual(entree.details["motif"], "changement_de_proprietaire")
            self.assertIn(entree.lien_id, (self.lien_actif.pk, self.lien_expire.pk))
        reponse = self.client.get(reverse("share_timeline", args=[self.lien_actif.token]))
        self.assertEqual(reponse.status_code, 404)

    def test_sans_changement_de_proprietaire_rien_n_est_annule(self):
        self.assertEqual(self.enregistrer(self.ancien).status_code, 302)
        self.recharger()
        self.assertIsNone(self.en_attente.revoked_at)
        self.assertIsNone(self.expiree.revoked_at)
        self.assertIsNone(self.lien_actif.revoked_at)
        self.assertIsNone(self.lien_expire.revoked_at)
        self.assertFalse(Journal.objects.exists())
