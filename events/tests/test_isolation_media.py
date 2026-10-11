"""Les tests n'écrivent jamais dans le vrai `media/`, qui contient les pièces du dossier.

Le 2026-10-11, un test qui créait des pièces sans `MediaMixin` a déposé 324
fichiers dans `media/documents/` du poste de développement, à côté des seules
copies des pièces du litige. Le garde-fou est maintenant global : sous
`manage.py test`, `MEDIA_ROOT` est un dossier temporaire, quel que soit le test.
"""

from pathlib import Path

from django.conf import settings
from django.test import TestCase

from events.models import Event
from events.tests.factories import make_document, make_dossier, make_user


class IsolationDuMediaTests(TestCase):
    def test_media_root_hors_du_projet(self):
        racine = Path(settings.MEDIA_ROOT).resolve()
        self.assertFalse(racine.is_relative_to(Path(settings.BASE_DIR).resolve()), racine)
        self.assertIn("chronos-tests-media-", racine.name)

    def test_une_piece_creee_par_un_test_atterrit_dans_le_dossier_temporaire(self):
        evenement = Event.objects.create(dossier=make_dossier(make_user("o")), title="X", date="2026-01-01")
        document = make_document(evenement, "isolation.pdf", b"%PDF-isolation")
        chemin = Path(document.file.path).resolve()
        self.assertTrue(chemin.is_relative_to(Path(settings.MEDIA_ROOT).resolve()))
        self.assertFalse(chemin.is_relative_to((Path(settings.BASE_DIR) / "media").resolve()))
