"""Contrôle d'intégrité du dossier : décompte des données et empreinte de chaque pièce.

Sert à vérifier un transfert (le poste local → le serveur, SQLite →
PostgreSQL) : lancée des deux côtés, elle doit afficher **exactement** la même
sortie. Elle sert aussi de contrôle périodique : chaque pièce est relue sur le
disque et son SHA-256 comparé à celui enregistré au dépôt.

Code de sortie non nul si une pièce manque ou ne correspond plus.
"""

import hashlib

from django.core.management.base import BaseCommand, CommandError

from events.models import (
    CourrielImporte, Document, Dossier, DossierAccess, Etiquette, Event, Expediteur,
    Invitation, Journal, PaireIgnoree, ShareLink,
)


class Command(BaseCommand):
    help = "Décompte des données et contrôle SHA-256 de chaque pièce."

    def handle(self, *args, **options):
        for modele in (Dossier, DossierAccess, Event, Document, Expediteur, Etiquette,
                       CourrielImporte, Invitation, ShareLink, PaireIgnoree, Journal):
            self.stdout.write(f"{modele.__name__:<16} {modele.objects.count()}")
        liens = sum(d.events.count() for d in Document.objects.all())
        self.stdout.write(f"{'liens pièce-fait':<16} {liens}")

        manquantes, differentes = [], []
        for document in Document.objects.order_by("dossier_id", "numero", "pk"):
            try:
                digest = hashlib.sha256()
                with document.file.open("rb") as fichier:
                    for bloc in iter(lambda: fichier.read(1024 * 1024), b""):
                        digest.update(bloc)
            except (FileNotFoundError, OSError):
                manquantes.append(document)
                continue
            if digest.hexdigest() != document.content_hash:
                differentes.append(document)
        self.stdout.write(f"{'pièces conformes':<16} {Document.objects.count() - len(manquantes) - len(differentes)}")
        for document in manquantes:
            self.stdout.write(self.style.ERROR(f"ABSENTE   n°{document.numero} {document.title}"))
        for document in differentes:
            self.stdout.write(self.style.ERROR(f"DIFFÉRENTE n°{document.numero} {document.title}"))
        if manquantes or differentes:
            raise CommandError(f"{len(manquantes)} pièce(s) absente(s), {len(differentes)} différente(s).")
