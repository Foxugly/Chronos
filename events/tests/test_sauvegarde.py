import json
import sqlite3
import tempfile
from io import StringIO
from pathlib import Path
from unittest import mock, skipUnless

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TransactionTestCase, override_settings

from events.models import Event
from events.tests.factories import make_dossier, make_user


class SauvegardeTests(TransactionTestCase):
    """`manage.py sauvegarder` : la seule copie des preuves doit en avoir une autre.

    `TransactionTestCase` et non `TestCase` : ce dernier enveloppe chaque test
    dans une transaction ouverte, et l'API de sauvegarde de SQLite attend
    indéfiniment qu'une écriture non validée se termine. La commande, elle,
    tourne hors transaction — c'est ce cas réel qu'on teste.
    """

    def setUp(self):
        dossier_temporaire = tempfile.TemporaryDirectory()
        self.addCleanup(dossier_temporaire.cleanup)
        racine = Path(dossier_temporaire.name)

        self.media = racine / "media"
        (self.media / "documents").mkdir(parents=True)
        (self.media / "documents" / "devis.pdf").write_bytes(b"%PDF devis")
        (self.media / "documents" / "photo.jpg").write_bytes(b"\xff\xd8 photo")
        self.destination = racine / "sauvegardes"

        reglages = override_settings(MEDIA_ROOT=self.media)
        reglages.enable()
        self.addCleanup(reglages.disable)

        owner = make_user("proprietaire")
        Event.objects.create(
            dossier=make_dossier(owner), title="Devis signé", date="2026-06-02"
        )

    def sauvegarder(self, **options):
        options.setdefault("destination", str(self.destination))
        call_command("sauvegarder", stdout=StringIO(), **options)

    def sauvegardes(self):
        return sorted(p.name for p in self.destination.iterdir())

    @skipUnless(connection.vendor == "sqlite", "relit db.sqlite3 ; PostgreSQL : SauvegardePostgresqlTests")
    def test_copie_la_base_et_les_pieces(self):
        self.sauvegarder()

        (nom,) = self.sauvegardes()
        self.assertRegex(nom, r"^\d{8}-\d{6}$")
        sauvegarde = self.destination / nom
        self.assertEqual(
            (sauvegarde / "media" / "documents" / "devis.pdf").read_bytes(),
            b"%PDF devis",
        )
        copie = sqlite3.connect(sauvegarde / "db.sqlite3")
        try:
            titres = copie.execute("SELECT title FROM events_event").fetchall()
        finally:
            copie.close()
        self.assertEqual(titres, [("Devis signé",)])

    def test_manifeste_porte_l_empreinte_de_chaque_piece(self):
        self.sauvegarder()

        (nom,) = self.sauvegardes()
        manifeste = json.loads(
            (self.destination / nom / "manifeste.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            sorted(manifeste["media"]), ["documents/devis.pdf", "documents/photo.jpg"]
        )
        self.assertEqual(len(manifeste["media"]["documents/devis.pdf"]), 64)

    def test_rotation_ne_garde_que_les_plus_recentes(self):
        self.destination.mkdir()
        for nom in ("20260101-000000", "20260102-000000", "20260103-000000"):
            (self.destination / nom).mkdir()

        self.sauvegarder(garder=2)

        restantes = self.sauvegardes()
        self.assertEqual(len(restantes), 2)
        self.assertEqual(restantes[0], "20260103-000000")

    def test_rotation_ne_touche_a_rien_d_autre(self):
        """Seul un dossier au nom exact d'une sauvegarde peut être élagué."""
        self.destination.mkdir()
        for nom in ("19990101-000000.partiel", "copie-manuelle", "20200101-000000-bis"):
            (self.destination / nom).mkdir()
        (self.destination / "20200101-000000").write_text("un fichier, pas un dossier")

        self.sauvegarder(garder=1)

        restantes = self.sauvegardes()
        self.assertEqual(len(restantes), 5)
        self.assertEqual(
            [n for n in restantes if not n.startswith("2026")],
            [
                "19990101-000000.partiel",
                "20200101-000000",
                "20200101-000000-bis",
                "copie-manuelle",
            ],
        )

    def test_un_echec_ne_laisse_aucune_sauvegarde_et_n_elague_rien(self):
        self.destination.mkdir()
        (self.destination / "20260101-000000").mkdir()

        with mock.patch(
            "events.management.commands.sauvegarder.shutil.copytree",
            side_effect=OSError("disque plein"),
        ):
            with self.assertRaises(OSError):
                self.sauvegarder(garder=1)

        self.assertEqual(self.sauvegardes(), ["20260101-000000"])

    def test_une_piece_mal_copiee_fait_echouer_la_sauvegarde(self):
        # `copytree` s'appelle lui-même pour chaque sous-dossier : on ne peut
        # pas le remplacer sans casser sa récursion. On altère donc la copie
        # juste avant que la commande la relise.
        from events.management.commands import sauvegarder

        empreintes_reelles = sauvegarder._empreintes

        def relecture_apres_alteration(racine):
            if racine.parent.name.endswith(".partiel"):
                (racine / "documents" / "devis.pdf").write_bytes(b"autre chose")
            return empreintes_reelles(racine)

        with mock.patch.object(
            sauvegarder, "_empreintes", side_effect=relecture_apres_alteration
        ):
            with self.assertRaisesMessage(CommandError, "documents/devis.pdf"):
                self.sauvegarder()

        self.assertEqual(self.sauvegardes(), [])

    def test_refuse_de_sauvegarder_sans_les_pieces(self):
        with override_settings(MEDIA_ROOT=self.media / "absent"):
            with self.assertRaisesMessage(CommandError, "MEDIA_ROOT"):
                self.sauvegarder()

    def test_refuse_de_sauvegarder_dans_une_transaction(self):
        """Elle copierait des écritures non validées — ou attendrait sans fin."""
        from django.db import transaction

        with transaction.atomic():
            with self.assertRaisesMessage(CommandError, "transaction"):
                self.sauvegarder()

    def test_refuse_garder_zero(self):
        with self.assertRaisesMessage(CommandError, "--garder"):
            self.sauvegarder(garder=0)


class SauvegardePostgresqlTests(TransactionTestCase):
    """La branche PostgreSQL (production), sans serveur PostgreSQL : `pg_dump`
    et `pg_restore` sont simulés. Ce qui compte ici : le mot de passe ne passe
    jamais sur la ligne de commande, un échec ne laisse aucune sauvegarde, et
    une copie illisible ou incomplète est refusée."""

    LISTE_OK = "; Archive\n215; 1259 16400 TABLE public events_event chronos\n3301; 0 16400 TABLE DATA public events_event chronos\n"

    def setUp(self):
        dossier_temporaire = tempfile.TemporaryDirectory()
        self.addCleanup(dossier_temporaire.cleanup)
        racine = Path(dossier_temporaire.name)
        self.media = racine / "media"
        (self.media / "documents").mkdir(parents=True)
        (self.media / "documents" / "devis.pdf").write_bytes(b"%PDF devis")
        self.destination = racine / "sauvegardes"
        reglages = override_settings(MEDIA_ROOT=self.media)
        reglages.enable()
        self.addCleanup(reglages.disable)

        faux = mock.MagicMock(vendor="postgresql", in_atomic_block=False, settings_dict={
            "NAME": "chronos", "USER": "chronos", "PASSWORD": "s3cret!", "HOST": "127.0.0.1", "PORT": "5432"})
        for cible, valeur in (("connection", faux),):
            rustine = mock.patch(f"events.management.commands.sauvegarder.{cible}", valeur)
            rustine.start()
            self.addCleanup(rustine.stop)
        rustine = mock.patch("events.management.commands.sauvegarder._outils.which", lambda nom: "/usr/bin/" + nom)
        rustine.start()
        self.addCleanup(rustine.stop)
        self.appels = []

    def simuler(self, dump_ok=True, liste=None):
        def run(argv, **kwargs):
            self.appels.append((argv, kwargs.get("env", {})))
            if argv[0].endswith("pg_dump"):
                if dump_ok:
                    Path(argv[argv.index("--file") + 1]).write_bytes(b"PGDMP fausse archive")
                return mock.Mock(returncode=0 if dump_ok else 1, stderr="" if dump_ok else "connexion refusée", stdout="")
            return mock.Mock(returncode=0, stdout=self.LISTE_OK if liste is None else liste, stderr="")
        return mock.patch("events.management.commands.sauvegarder.subprocess.run", side_effect=run)

    def sauvegarder(self):
        call_command("sauvegarder", destination=str(self.destination), stdout=StringIO())

    def test_produit_un_dump_verifie_et_les_pieces(self):
        with self.simuler():
            self.sauvegarder()
        (sauvegarde,) = list(self.destination.iterdir())
        self.assertTrue((sauvegarde / "base.dump").exists())
        self.assertTrue((sauvegarde / "media" / "documents" / "devis.pdf").exists())
        manifeste = json.loads((sauvegarde / "manifeste.json").read_text(encoding="utf-8"))
        self.assertEqual(manifeste["tables"], 1)

    def test_le_mot_de_passe_passe_par_l_environnement_jamais_par_la_ligne_de_commande(self):
        with self.simuler():
            self.sauvegarder()
        for argv, env in self.appels:
            self.assertNotIn("s3cret!", " ".join(argv))
            self.assertEqual(env["PGPASSWORD"], "s3cret!")
            self.assertEqual(env["PGDATABASE"], "chronos")

    def test_un_echec_de_pg_dump_ne_laisse_rien(self):
        with self.simuler(dump_ok=False), self.assertRaises(CommandError):
            self.sauvegarder()
        self.assertEqual(list(self.destination.iterdir()), [])

    def test_une_copie_sans_la_table_des_evenements_est_refusee(self):
        with self.simuler(liste="; Archive vide\n"), self.assertRaises(CommandError):
            self.sauvegarder()
        self.assertEqual(list(self.destination.iterdir()), [])
