import contextlib
import copy
import importlib

from django.apps import apps as global_apps
from django.db import IntegrityError, connection, migrations, transaction
from django.test import TestCase, TransactionTestCase

from events.models import Dossier, Event
from events.tests.factories import make_dossier, make_user

# Le module de migration commence par un chiffre : impossible à importer avec
# un simple `import`.
migration_0003 = importlib.import_module("events.migrations.0003_event_dossier")


class EventDossierTests(TestCase):
    def test_an_event_cannot_be_created_without_a_dossier(self):
        """La propriété, et non sa déclaration.

        La version précédente lisait `Event._meta.get_field("dossier").null`
        et comparait `models.py` à lui-même : elle restait verte si la
        migration n'avait jamais posé la contrainte en base. On demande donc
        à la base de refuser.
        """
        with self.assertRaises(IntegrityError), transaction.atomic():
            Event.objects.create(title="Devis signé", date="2026-06-02")

    def test_events_are_reachable_from_their_dossier(self):
        dossier = make_dossier(make_user("owner"))
        event = Event.objects.create(
            dossier=dossier, title="Devis signé", date="2026-06-02"
        )
        self.assertEqual(list(dossier.events.all()), [event])


@contextlib.contextmanager
def _dossier_temporarily_nullable():
    """Relâche temporairement la contrainte NOT NULL sur `Event.dossier`.

    Une fois la migration 0003 appliquée (ce qui est toujours le cas dans la
    base de test, construite en rejouant tout l'historique), la colonne est
    NOT NULL : on ne peut donc pas fabriquer de ligne orpheline pour tester
    `attach_orphan_events` tel quel. On reproduit ici,
    via le schema editor, la fenêtre transitoire (AddField nullable, avant
    l'AlterField final) que la vraie migration traverse sur une base réelle.
    Toute ligne encore orpheline est supprimée avant de restaurer la
    contrainte, pour que la restauration ne puisse jamais échouer.
    """
    Event = global_apps.get_model("events", "Event")
    not_null_field = Event._meta.get_field("dossier")
    nullable_field = copy.deepcopy(not_null_field)
    nullable_field.null = True
    with connection.schema_editor() as editor:
        editor.alter_field(Event, not_null_field, nullable_field)
    try:
        yield Event
    finally:
        Event.objects.filter(dossier__isnull=True).delete()
        with connection.schema_editor() as editor:
            editor.alter_field(Event, nullable_field, not_null_field)


class AttachOrphanEventsTests(TransactionTestCase):
    """Teste directement les fonctions de données de la migration 0003.

    `TransactionTestCase` est nécessaire (et non `TestCase`) : relâcher la
    contrainte NOT NULL via le schema editor SQLite exige de ne pas être
    déjà dans une transaction, ce que `TestCase` impose par construction.
    """

    def test_attaches_orphans_to_a_newly_created_dossier_owned_by_the_superuser(self):
        with _dossier_temporarily_nullable() as Event:
            owner = make_user("owner", is_superuser=True, is_staff=True)
            orphan = Event.objects.create(title="Devis signé", date="2026-06-02")

            migration_0003.attach_orphan_events(global_apps, None)

            orphan.refresh_from_db()
            self.assertIsNotNone(orphan.dossier_id)
            self.assertEqual(orphan.dossier.name, migration_0003.INITIAL_DOSSIER_NAME)
            self.assertEqual(orphan.dossier.owner_id, owner.pk)

    def test_raises_when_there_are_orphans_but_no_superuser(self):
        with _dossier_temporarily_nullable() as Event:
            orphan = Event.objects.create(title="Devis signé", date="2026-06-02")

            with self.assertRaises(RuntimeError):
                migration_0003.attach_orphan_events(global_apps, None)

            orphan.refresh_from_db()
            self.assertIsNone(orphan.dossier_id)

    def test_is_idempotent_when_run_twice(self):
        with _dossier_temporarily_nullable() as Event:
            make_user("owner", is_superuser=True, is_staff=True)

            Event.objects.create(title="Devis signé", date="2026-06-02")
            migration_0003.attach_orphan_events(global_apps, None)

            Event.objects.create(title="Réception des travaux", date="2026-07-10")
            migration_0003.attach_orphan_events(global_apps, None)

            self.assertEqual(
                Dossier.objects.filter(
                    name=migration_0003.INITIAL_DOSSIER_NAME
                ).count(),
                1,
            )
            self.assertEqual(
                Event.objects.filter(
                    dossier__name=migration_0003.INITIAL_DOSSIER_NAME
                ).count(),
                2,
            )

    def test_the_migration_declares_no_reverse(self):
        """La migration est irréversible, et doit le rester.

        Son ancienne marche arrière détachait les évènements du dossier
        initial et le supprimait. Rejouée en avant, elle rattachait alors
        *tous* les évènements de la base à ce même dossier : deux affaires
        distinctes fusionnaient en une seule chronologie, sans un mot. Le
        `CASCADE` emportait au passage les `DossierAccess` accordés bien
        après le passage de la migration.
        """
        run_python = [
            operation
            for operation in migration_0003.Migration.operations
            if isinstance(operation, migrations.RunPython)
        ]
        self.assertEqual(len(run_python), 1)
        self.assertIsNone(run_python[0].reverse_code)
        self.assertFalse(run_python[0].reversible)
