"""Sauvegarde horodatée de la base et des pièces, vérifiée, avec rotation.

`db.sqlite3` et `media/` sont l'unique copie des pièces du litige. Les
copies faites à la main avant chaque migration (`*.backup-*`) ne sont pas
une politique : rien ne les déclenche, rien ne vérifie qu'elles sont
lisibles, rien ne les élague.

Chaque sauvegarde est un dossier `AAAAMMJJ-HHMMSS/` contenant :

- la base : `db.sqlite3`, copiée par l'API de sauvegarde de SQLite et non
  par une copie de fichier — une copie brute prise pendant une écriture peut
  être incohérente, l'API produit un instantané valide même serveur lancé —,
  ou, sous PostgreSQL (production), `base.dump`, produit par `pg_dump` au
  format personnalisé : un instantané cohérent, restaurable par `pg_restore` ;
- `media/`, copie intégrale des pièces ;
- `manifeste.json`, l'empreinte SHA-256 de chaque pièce copiée.

La sauvegarde est construite sous un nom `.partiel` puis renommée une fois
vérifiée. Un dossier au nom définitif est donc toujours une sauvegarde
complète, et la rotation ne compte et ne supprime **que** ceux-là : une
sauvegarde ratée ne fait jamais tomber une bonne, et rien d'autre dans le
dossier de destination n'est touché.
"""

import hashlib
import json
import re
import shutil
import os
import shutil as _outils
import sqlite3
import subprocess
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.utils import timezone

#: Seuls les dossiers à ce nom exact sont des sauvegardes complètes.
NOM_SAUVEGARDE = re.compile(r"^\d{8}-\d{6}$")


def _empreinte(chemin):
    digest = hashlib.sha256()
    with open(chemin, "rb") as fichier:
        for bloc in iter(lambda: fichier.read(1024 * 1024), b""):
            digest.update(bloc)
    return digest.hexdigest()


def _empreintes(racine):
    """{chemin relatif POSIX: sha256} pour chaque fichier sous `racine`."""
    return {
        chemin.relative_to(racine).as_posix(): _empreinte(chemin)
        for chemin in sorted(racine.rglob("*"))
        if chemin.is_file()
    }


class Command(BaseCommand):
    help = "Sauvegarde la base et les pièces dans un dossier horodaté, vérifié."

    def add_arguments(self, parser):
        parser.add_argument(
            "--destination",
            default=str(settings.SAUVEGARDE_DIR),
            help="Dossier qui reçoit les sauvegardes (défaut : SAUVEGARDE_DIR).",
        )
        parser.add_argument(
            "--garder",
            type=int,
            default=settings.SAUVEGARDE_GARDER,
            help="Nombre de sauvegardes complètes à conserver (défaut : %(default)s).",
        )

    def handle(self, *args, destination, garder, **options):
        if garder < 1:
            # Zéro ferait supprimer la sauvegarde qu'on vient de faire.
            raise CommandError("--garder doit valoir au moins 1.")
        if connection.vendor not in ("sqlite", "postgresql"):
            raise CommandError("Cette commande sait sauvegarder SQLite et PostgreSQL.")

        media = Path(settings.MEDIA_ROOT)
        if not media.is_dir():
            # Une sauvegarde de la base sans les fichiers qu'elle référence
            # n'en est pas une : on refuse plutôt que de réussir à moitié.
            raise CommandError(f"MEDIA_ROOT introuvable : {media}")

        racine = Path(destination)
        racine.mkdir(parents=True, exist_ok=True)
        nom = timezone.localtime().strftime("%Y%m%d-%H%M%S")
        finale = racine / nom
        partielle = racine / f"{nom}.partiel"
        if finale.exists() or partielle.exists():
            raise CommandError(f"Une sauvegarde porte déjà ce nom : {nom}")

        partielle.mkdir()
        try:
            if connection.vendor == "postgresql":
                self._copier_base_postgresql(partielle / "base.dump")
            else:
                self._copier_base(partielle / "db.sqlite3")
            shutil.copytree(media, partielle / "media")
            manifeste = self._verifier(media, partielle)
            (partielle / "manifeste.json").write_text(
                json.dumps(manifeste, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except BaseException:
            shutil.rmtree(partielle, ignore_errors=True)
            raise
        partielle.rename(finale)

        supprimees = self._elaguer(racine, garder)
        self.stdout.write(
            self.style.SUCCESS(
                f"Sauvegarde {finale} : base vérifiée, "
                f"{len(manifeste['media'])} pièce(s) copiée(s) et vérifiée(s)."
            )
        )
        for ancienne in supprimees:
            self.stdout.write(f"Rotation : {ancienne.name} supprimée.")

    def _copier_base(self, cible):
        if connection.in_atomic_block:
            # Dans une transaction ouverte, l'instantané contiendrait des
            # écritures pas encore validées — et SQLite attend sans fin
            # qu'elles le soient avant de copier.
            raise CommandError("Sauvegarde impossible dans une transaction ouverte.")
        connection.ensure_connection()
        copie = sqlite3.connect(cible)
        try:
            connection.connection.backup(copie)
        finally:
            copie.close()

    @staticmethod
    def _environnement_postgresql():
        """Les paramètres de connexion pour `pg_dump` / `pg_restore`, passés
        par l'environnement (`PGPASSWORD`…) et jamais sur la ligne de
        commande, où tout utilisateur de la machine pourrait les lire."""
        reglages = connection.settings_dict
        env = dict(os.environ)
        env.update({
            "PGHOST": str(reglages.get("HOST") or "127.0.0.1"),
            "PGPORT": str(reglages.get("PORT") or "5432"),
            "PGUSER": str(reglages.get("USER") or ""),
            "PGPASSWORD": str(reglages.get("PASSWORD") or ""),
            "PGDATABASE": str(reglages.get("NAME") or ""),
        })
        return env

    @staticmethod
    def _outil(nom):
        chemin = _outils.which(nom)
        if not chemin:
            raise CommandError(f"{nom} introuvable : installez le client PostgreSQL (postgresql-client).")
        return chemin

    def _copier_base_postgresql(self, cible):
        """`pg_dump --format=custom` : un instantané cohérent de toute la base."""
        resultat = subprocess.run(
            [self._outil("pg_dump"), "--format=custom", "--no-owner", "--file", str(cible)],
            env=self._environnement_postgresql(), capture_output=True, text=True, timeout=600,
        )
        if resultat.returncode != 0 or not cible.exists():
            raise CommandError(f"pg_dump a échoué : {resultat.stderr.strip()[:500]}")

    def _verifier_base_postgresql(self, copie):
        """Relit le fichier : `pg_restore --list` doit en lire la table des
        matières, et y trouver les tables de l'application."""
        resultat = subprocess.run(
            [self._outil("pg_restore"), "--list", str(copie)],
            env=self._environnement_postgresql(), capture_output=True, text=True, timeout=300,
        )
        if resultat.returncode != 0:
            raise CommandError(f"Copie de la base illisible : {resultat.stderr.strip()[:500]}")
        tables = sum(1 for ligne in resultat.stdout.splitlines() if " TABLE " in ligne and " TABLE DATA " not in ligne)
        if not any("events_event" in ligne for ligne in resultat.stdout.splitlines()):
            raise CommandError("Copie de la base incomplète : la table des évènements n'y est pas.")
        return tables

    def _verifier(self, media, sauvegarde):
        """Relit la copie : une sauvegarde jamais relue n'est qu'un espoir."""
        if (sauvegarde / "base.dump").exists():
            tables = self._verifier_base_postgresql(sauvegarde / "base.dump")
        else:
            copie = sqlite3.connect(sauvegarde / "db.sqlite3")
            try:
                (resultat,) = copie.execute("PRAGMA integrity_check").fetchone()
                tables = copie.execute(
                    "SELECT count(*) FROM sqlite_master WHERE type = 'table'"
                ).fetchone()[0]
            finally:
                copie.close()
            if resultat != "ok":
                raise CommandError(f"Copie de la base corrompue : {resultat}")

        source = _empreintes(media)
        copiees = _empreintes(sauvegarde / "media")
        if source != copiees:
            differences = sorted(set(source.items()) ^ set(copiees.items()))
            raise CommandError(
                "Les pièces copiées ne correspondent pas à la source : "
                + ", ".join(chemin for chemin, _ in differences[:5])
            )
        return {
            "cree_le": timezone.localtime().isoformat(),
            "tables": tables,
            "media": copiees,
        }

    def _elaguer(self, racine, garder):
        completes = sorted(
            chemin
            for chemin in racine.iterdir()
            if chemin.is_dir() and NOM_SAUVEGARDE.match(chemin.name)
        )
        a_supprimer = completes[:-garder]
        for ancienne in a_supprimer:
            shutil.rmtree(ancienne)
        return a_supprimer
