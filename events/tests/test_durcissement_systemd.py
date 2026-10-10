"""Durcissement systemd de chronos-gunicorn (OPERATIONS.md §3.22), vérifié sur le fichier.

Une unité ne s'éprouve qu'en production : ce garde-fou empêche le bloc de disparaître
lors d'une modification ultérieure.
"""

from pathlib import Path

from django.test import SimpleTestCase

UNITE = Path(__file__).resolve().parents[2] / "deploy" / "chronos-gunicorn.service"
DIRECTIVES = (
    "NoNewPrivileges=yes",
    "PrivateTmp=yes",
    "ProtectSystem=full",
    "ProtectKernelTunables=yes",
    "ProtectControlGroups=yes",
    "RestrictSUIDSGID=yes",
)


class DurcissementGunicornTests(SimpleTestCase):
    def setUp(self):
        self.lignes = UNITE.read_text(encoding="utf-8").splitlines()

    def test_le_bloc_de_durcissement_est_present(self):
        for directive in DIRECTIVES:
            self.assertIn(directive, self.lignes)

    def test_jamais_protectsystem_strict(self):
        """strict rendrait /var/www et /var/log/chronos en lecture seule."""
        self.assertNotIn("ProtectSystem=strict", self.lignes)

    def test_aucune_commande_n_appelle_sudo(self):
        """NoNewPrivileges=yes rend sudo inopérant dans l'unité."""
        for ligne in self.lignes:
            if ligne.startswith("Exec"):
                self.assertNotIn("sudo", ligne)
