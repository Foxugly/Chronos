"""Quatre points de robustesse du backlog, fermés ensemble.

- deux modifications concurrentes d'un évènement ne s'écrasent plus ;
- une pièce trop lourde est refusée côté serveur ;
- `ALLOWED_HOSTS` supporte les espaces autour des virgules ;
- un visiteur anonyme qui atteindrait les helpers de droits reçoit un 404.
"""

import shutil
import tempfile
from pathlib import Path

from django.contrib.admin.sites import AdminSite
from django.contrib.auth.models import AnonymousUser
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import Http404
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from config.settings import hotes_autorises
from events import permissions
from events.admin import EventAdmin
from events.models import Document, Dossier, DossierAccess, Event, ShareLink
from events.tests.factories import make_dossier, make_user


class MediaTemporaireMixin:
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.media_root = tempfile.mkdtemp()
        cls.enterClassContext(override_settings(MEDIA_ROOT=cls.media_root))

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(cls.media_root, ignore_errors=True)


class ModificationsConcurrentesTests(MediaTemporaireMixin, TestCase):
    """Verrouillage optimiste : on enregistre une version, pas un état final.

    Deux personnes ouvrent le même évènement. La première enregistre. Sans
    contrôle, la seconde écrasait ce travail avec l'état qu'elle avait chargé
    avant — sans qu'aucune des deux le sache.
    """

    def setUp(self):
        self.owner = make_user("owner")
        self.dossier = make_dossier(self.owner)
        self.event = Event.objects.create(
            dossier=self.dossier, title="Devis signé", date="2026-06-02"
        )
        self.client.force_login(self.owner)
        self.url = reverse("event_edit", args=[self.event.pk])

    def envoyer(self, titre, version, url=None, client=None, **extra):
        donnees = {"title": titre, "date": "2026-06-02", "description": ""}
        if version is not None:
            donnees["version"] = version
        return (client or self.client).post(url or self.url, donnees, **extra)

    def test_un_nouvel_evenement_commence_en_version_1(self):
        self.assertEqual(self.event.version, 1)

    def test_le_formulaire_porte_la_version_chargee(self):
        reponse = self.client.get(self.url)
        self.assertContains(reponse, 'name="version"')
        self.assertContains(reponse, 'value="1"')

    def test_un_enregistrement_a_jour_passe_et_incremente_la_version(self):
        self.envoyer("Devis signé (corrigé)", version=1)

        self.event.refresh_from_db()
        self.assertEqual(self.event.title, "Devis signé (corrigé)")
        self.assertEqual(self.event.version, 2)

    def test_le_second_enregistrement_concurrent_est_refuse(self):
        self.envoyer("Version d'Alice", version=1)
        reponse = self.envoyer("Version de Bob", version=1)

        self.assertEqual(reponse.status_code, 200)
        self.assertContains(reponse, "modifié entre-temps")
        self.event.refresh_from_db()
        self.assertEqual(self.event.title, "Version d'Alice")
        self.assertEqual(self.event.version, 2)

    def test_le_refus_en_ajax_est_un_400(self):
        self.envoyer("Version d'Alice", version=1)
        reponse = self.envoyer(
            "Version de Bob", version=1, HTTP_X_REQUESTED_WITH="XMLHttpRequest"
        )
        self.assertEqual(reponse.status_code, 400)
        self.assertContains(reponse, "modifié entre-temps", status_code=400)

    def test_un_refus_n_enregistre_pas_les_pieces_jointes(self):
        """L'évènement et ses pièces vont ensemble : refuser l'un sans l'autre
        laisserait une pièce rattachée à une version que personne n'a vue."""
        self.envoyer("Version d'Alice", version=1)
        self.client.post(
            self.url,
            {
                "title": "Version de Bob",
                "date": "2026-06-02",
                "description": "",
                "version": 1,
                "documents": SimpleUploadedFile("devis.pdf", b"%PDF bob"),
            },
        )
        self.assertFalse(Document.objects.exists())

    def test_sans_version_l_enregistrement_reste_possible(self):
        """Un POST direct sans le champ n'est pas refusé : comme `precision`,
        le champ protège les personnes qui utilisent le formulaire, il ne
        conditionne pas l'enregistrement pour un client qui ne l'envoie pas."""
        self.envoyer("Sans version", version=None)
        self.event.refresh_from_db()
        self.assertEqual(self.event.title, "Sans version")
        self.assertEqual(self.event.version, 2)

    def test_une_version_illisible_est_une_erreur_de_formulaire(self):
        reponse = self.envoyer("Pirate", version="abc")
        self.assertEqual(reponse.status_code, 200)
        self.event.refresh_from_db()
        self.assertEqual(self.event.title, "Devis signé")

    def test_le_lien_de_partage_en_ecriture_est_protege_aussi(self):
        lien = ShareLink.objects.create(
            dossier=self.dossier, created_by=self.owner, role=DossierAccess.Role.WRITE
        )
        url = reverse("share_event_edit", args=[lien.token, self.event.pk])
        self.envoyer("Version d'Alice", version=1)

        reponse = self.envoyer("Version anonyme", version=1, url=url, client=self.client_class())

        self.assertContains(reponse, "modifié entre-temps")
        self.event.refresh_from_db()
        self.assertEqual(self.event.title, "Version d'Alice")

    def test_une_modification_par_l_admin_invalide_les_formulaires_ouverts(self):
        modele_admin = EventAdmin(Event, AdminSite())
        requete = RequestFactory().post("/admin/")
        requete.user = self.owner
        self.event.title = "Corrigé dans l'admin"
        modele_admin.save_model(requete, self.event, form=None, change=True)

        self.envoyer("Version d'Alice", version=1)

        self.event.refresh_from_db()
        self.assertEqual(self.event.title, "Corrigé dans l'admin")
        self.assertEqual(self.event.version, 2)


class TailleDesPiecesTests(MediaTemporaireMixin, TestCase):
    def setUp(self):
        self.owner = make_user("owner")
        self.dossier = make_dossier(self.owner)
        self.client.force_login(self.owner)

    @override_settings(DOCUMENT_TAILLE_MAX=10)
    def test_une_piece_trop_lourde_est_refusee_avec_son_evenement(self):
        reponse = self.client.post(
            reverse("event_add", args=[self.dossier.pk]),
            {
                "title": "Photo",
                "date": "2026-06-02",
                "description": "",
                "documents": SimpleUploadedFile("photo.jpg", b"x" * 11),
            },
        )

        self.assertContains(reponse, "photo.jpg")
        self.assertContains(reponse, "trop lourd")
        self.assertFalse(Event.objects.exists())
        self.assertFalse(Document.objects.exists())

    @override_settings(DOCUMENT_TAILLE_MAX=10)
    def test_un_refus_ne_laisse_aucun_fichier_sur_le_disque(self):
        """La transaction annule les lignes, pas les fichiers : la première
        pièce, acceptée, ne doit pas avoir été écrite avant le refus de la
        seconde — elle resterait sur le disque sans ligne pour la retrouver."""
        self.client.post(
            reverse("event_add", args=[self.dossier.pk]),
            {
                "title": "Deux pièces",
                "date": "2026-06-02",
                "description": "",
                "documents": [
                    SimpleUploadedFile("petite.pdf", b"%PDF"),
                    SimpleUploadedFile("lourde.pdf", b"x" * 11),
                ],
            },
        )
        ecrits = [p for p in Path(self.media_root).rglob("*") if p.is_file()]
        self.assertEqual(ecrits, [])

    @override_settings(DOCUMENT_TAILLE_MAX=10)
    def test_une_piece_a_la_limite_passe(self):
        self.client.post(
            reverse("event_add", args=[self.dossier.pk]),
            {
                "title": "Photo",
                "date": "2026-06-02",
                "description": "",
                "documents": SimpleUploadedFile("photo.jpg", b"x" * 10),
            },
        )
        self.assertEqual(Document.objects.count(), 1)

    @override_settings(DOCUMENT_TAILLE_MAX=2 * 1024 * 1024)
    def test_le_navigateur_connait_la_limite(self):
        """Confort seulement : le serveur fait autorité, mais refuser après
        l'envoi de 200 Mo est une mauvaise façon d'apprendre la limite."""
        reponse = self.client.get(reverse("event_add", args=[self.dossier.pk]))
        self.assertContains(reponse, 'data-taille-max-ko="2048"')


class HotesAutorisesTests(TestCase):
    def test_les_espaces_autour_des_virgules_sont_retires(self):
        self.assertEqual(hotes_autorises("a.com, b.com ,c.com"), ["a.com", "b.com", "c.com"])

    def test_les_entrees_vides_sont_ecartees(self):
        self.assertEqual(hotes_autorises("a.com,, ,"), ["a.com"])

    def test_absente_ou_vide_donne_une_liste_vide(self):
        self.assertEqual(hotes_autorises(None), [])
        self.assertEqual(hotes_autorises(""), [])


class VisiteurAnonymeTests(TestCase):
    """`LoginRequiredMiddleware` arrête l'anonyme avant les vues. Ces tests
    vérifient ce qui se passerait sans lui : un 404, et non un `TypeError`
    devenu 500 — qui distinguerait déjà « il y a quelque chose ici »."""

    def setUp(self):
        self.anonyme = AnonymousUser()
        self.dossier = make_dossier(make_user("owner"))
        self.event = Event.objects.create(
            dossier=self.dossier, title="Devis", date="2026-06-02"
        )

    def test_les_querysets_de_droits_sont_vides(self):
        self.assertFalse(Dossier.objects.accessible_by(self.anonyme).exists())
        self.assertFalse(Dossier.objects.writable_by(self.anonyme).exists())
        self.assertFalse(Dossier.objects.owned_by(self.anonyme).exists())

    def test_chaque_helper_repond_404(self):
        cas = [
            (permissions.get_dossier_for_read, self.dossier.pk),
            (permissions.get_dossier_for_write, self.dossier.pk),
            (permissions.get_dossier_for_owner, self.dossier.pk),
            (permissions.get_event_for_read, self.event.pk),
            (permissions.get_event_for_write, self.event.pk),
            (permissions.get_document_for_read, 1),
            (permissions.get_document_for_write, 1),
        ]
        for helper, identifiant in cas:
            with self.subTest(helper=helper.__name__):
                with self.assertRaises(Http404):
                    helper(self.anonyme, identifiant)
