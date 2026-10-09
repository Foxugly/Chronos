from django.http import Http404
from django.test import TestCase

from events.models import DossierAccess, Event
from events.permissions import (
    get_dossier_for_read,
    get_dossier_for_write,
    get_event_for_read,
    get_event_for_write,
)
from events.tests.factories import grant, make_dossier, make_user


class PermissionHelperTests(TestCase):
    def setUp(self):
        self.owner = make_user("owner")
        self.reader = make_user("reader")
        self.writer = make_user("writer")
        self.stranger = make_user("stranger")
        self.dossier = make_dossier(self.owner)
        grant(self.dossier, self.reader, DossierAccess.Role.READ)
        grant(self.dossier, self.writer, DossierAccess.Role.WRITE)
        self.event = Event.objects.create(
            dossier=self.dossier, title="Devis signé", date="2026-06-02"
        )

    def test_read_allowed_for_owner_reader_and_writer(self):
        for user in (self.owner, self.reader, self.writer):
            with self.subTest(user=user.username):
                self.assertEqual(
                    get_dossier_for_read(user, self.dossier.pk), self.dossier
                )

    def test_read_refused_for_stranger(self):
        with self.assertRaises(Http404):
            get_dossier_for_read(self.stranger, self.dossier.pk)

    def test_write_allowed_for_owner_and_writer(self):
        for user in (self.owner, self.writer):
            with self.subTest(user=user.username):
                self.assertEqual(
                    get_dossier_for_write(user, self.dossier.pk), self.dossier
                )

    def test_write_refused_for_reader(self):
        with self.assertRaises(Http404):
            get_dossier_for_write(self.reader, self.dossier.pk)

    def test_write_refused_for_stranger(self):
        with self.assertRaises(Http404):
            get_dossier_for_write(self.stranger, self.dossier.pk)

    def test_event_read_follows_dossier_rights(self):
        self.assertEqual(get_event_for_read(self.reader, self.event.pk), self.event)
        with self.assertRaises(Http404):
            get_event_for_read(self.stranger, self.event.pk)

    def test_event_write_follows_dossier_rights(self):
        self.assertEqual(get_event_for_write(self.writer, self.event.pk), self.event)
        with self.assertRaises(Http404):
            get_event_for_write(self.reader, self.event.pk)

    def test_unknown_ids_raise_404(self):
        with self.assertRaises(Http404):
            get_dossier_for_read(self.owner, 999999)
        with self.assertRaises(Http404):
            get_event_for_read(self.owner, 999999)
