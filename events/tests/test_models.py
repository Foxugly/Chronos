from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.test import TestCase

from events.models import Dossier, DossierAccess
from events.tests.factories import grant, make_dossier, make_user


class DossierQuerySetTests(TestCase):
    def setUp(self):
        self.owner = make_user("owner")
        self.reader = make_user("reader")
        self.writer = make_user("writer")
        self.stranger = make_user("stranger")
        self.dossier = make_dossier(self.owner)
        grant(self.dossier, self.reader, DossierAccess.Role.READ)
        grant(self.dossier, self.writer, DossierAccess.Role.WRITE)

    def test_owner_can_access(self):
        self.assertIn(self.dossier, Dossier.objects.accessible_by(self.owner))

    def test_reader_can_access(self):
        self.assertIn(self.dossier, Dossier.objects.accessible_by(self.reader))

    def test_writer_can_access(self):
        self.assertIn(self.dossier, Dossier.objects.accessible_by(self.writer))

    def test_stranger_cannot_access(self):
        self.assertNotIn(self.dossier, Dossier.objects.accessible_by(self.stranger))

    def test_owner_can_write(self):
        self.assertIn(self.dossier, Dossier.objects.writable_by(self.owner))

    def test_writer_can_write(self):
        self.assertIn(self.dossier, Dossier.objects.writable_by(self.writer))

    def test_reader_cannot_write(self):
        self.assertNotIn(self.dossier, Dossier.objects.writable_by(self.reader))

    def test_stranger_cannot_write(self):
        self.assertNotIn(self.dossier, Dossier.objects.writable_by(self.stranger))

    def test_accessible_by_does_not_duplicate_rows(self):
        # Un dossier ne doit apparaître qu'une fois, même avec plusieurs accès.
        grant(self.dossier, make_user("other"), DossierAccess.Role.READ)
        self.assertEqual(Dossier.objects.accessible_by(self.owner).count(), 1)


class DossierIsolationTests(TestCase):
    """Un accès à un dossier ne doit jamais fuiter vers un dossier distinct.

    `test_stranger_cannot_access` (ci-dessus) prouve seulement qu'un tiers
    sans aucun accès est exclu d'un dossier donné. Ça ne prouve pas qu'un
    utilisateur ayant un accès légitime à un dossier A n'apparaît pas aussi
    pour un dossier B sans rapport — exactement ce qu'un futur refactor
    maladroit de `accessible_by` (vers un `DossierAccess.objects.filter
    (user=user)` à plat, par exemple) pourrait introduire silencieusement.
    """

    def setUp(self):
        self.owner_a = make_user("owner_a")
        self.reader_a = make_user("reader_a")
        self.writer_a = make_user("writer_a")
        self.dossier_a = make_dossier(self.owner_a, name="Dossier A")
        grant(self.dossier_a, self.reader_a, DossierAccess.Role.READ)
        grant(self.dossier_a, self.writer_a, DossierAccess.Role.WRITE)

        self.owner_b = make_user("owner_b")
        self.reader_b = make_user("reader_b")
        self.writer_b = make_user("writer_b")
        self.dossier_b = make_dossier(self.owner_b, name="Dossier B")
        grant(self.dossier_b, self.reader_b, DossierAccess.Role.READ)
        grant(self.dossier_b, self.writer_b, DossierAccess.Role.WRITE)

    def test_accessible_by_keeps_dossiers_separate(self):
        accessible_to_owner_a = Dossier.objects.accessible_by(self.owner_a)
        self.assertIn(self.dossier_a, accessible_to_owner_a)
        self.assertNotIn(self.dossier_b, accessible_to_owner_a)

        accessible_to_reader_a = Dossier.objects.accessible_by(self.reader_a)
        self.assertIn(self.dossier_a, accessible_to_reader_a)
        self.assertNotIn(self.dossier_b, accessible_to_reader_a)

        accessible_to_owner_b = Dossier.objects.accessible_by(self.owner_b)
        self.assertIn(self.dossier_b, accessible_to_owner_b)
        self.assertNotIn(self.dossier_a, accessible_to_owner_b)

        accessible_to_reader_b = Dossier.objects.accessible_by(self.reader_b)
        self.assertIn(self.dossier_b, accessible_to_reader_b)
        self.assertNotIn(self.dossier_a, accessible_to_reader_b)

    def test_writable_by_keeps_dossiers_separate(self):
        writable_to_owner_a = Dossier.objects.writable_by(self.owner_a)
        self.assertIn(self.dossier_a, writable_to_owner_a)
        self.assertNotIn(self.dossier_b, writable_to_owner_a)

        writable_to_writer_a = Dossier.objects.writable_by(self.writer_a)
        self.assertIn(self.dossier_a, writable_to_writer_a)
        self.assertNotIn(self.dossier_b, writable_to_writer_a)

        writable_to_owner_b = Dossier.objects.writable_by(self.owner_b)
        self.assertIn(self.dossier_b, writable_to_owner_b)
        self.assertNotIn(self.dossier_a, writable_to_owner_b)

        writable_to_writer_b = Dossier.objects.writable_by(self.writer_b)
        self.assertIn(self.dossier_b, writable_to_writer_b)
        self.assertNotIn(self.dossier_a, writable_to_writer_b)


class DossierAccessConstraintTests(TestCase):
    def test_one_access_per_user_per_dossier(self):
        owner = make_user("owner")
        reader = make_user("reader")
        dossier = make_dossier(owner)
        grant(dossier, reader, DossierAccess.Role.READ)
        with self.assertRaises(IntegrityError), transaction.atomic():
            grant(dossier, reader, DossierAccess.Role.WRITE)


class DossierDeletionTests(TestCase):
    def test_deleting_owner_is_refused(self):
        owner = make_user("owner")
        make_dossier(owner)
        with self.assertRaises(ProtectedError):
            owner.delete()

    def test_deleting_granted_user_removes_only_the_access(self):
        owner = make_user("owner")
        reader = make_user("reader")
        dossier = make_dossier(owner)
        grant(dossier, reader, DossierAccess.Role.READ)
        reader.delete()
        self.assertEqual(DossierAccess.objects.count(), 0)
        self.assertTrue(Dossier.objects.filter(pk=dossier.pk).exists())
