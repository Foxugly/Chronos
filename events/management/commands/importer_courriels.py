"""Transformer en évènements les courriels transférés aux alias des dossiers.

Deux sources :

- **la boîte d'import** (Exchange Online, par Microsoft Graph) — parmi les
  courriels reçus ces dernières 24 heures (`COURRIEL_FENETRE`), chacun qui est
  adressé à l'alias d'un dossier devient un évènement de ce dossier, puis est
  rangé dans un dossier Outlook **du nom du dossier Chronos**. Les autres ne
  sont ni touchés ni cités : la boîte peut servir à autre chose (hello@…).
  Un courriel en échec reste dans la boîte de réception ; il est retenté tant
  qu'il est dans la fenêtre. Rien n'est supprimé de la boîte ;
- **un fichier `.eml`** (`--fichier`, avec `--dossier`) — pour importer à la
  main un courriel enregistré, ou rejouer un exemple.

`--essai` analyse et affiche sans rien créer ni déplacer.

L'opération est rejouable : un courriel déjà importé dans un dossier est
reconnu (`CourrielImporte`) et n'y crée rien de plus. Si l'import réussit
mais que le rangement échoue, le passage suivant le range sans le réimporter.

`--essai` n'affiche que les courriels adressés à un alias.
"""

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from events.courriels import analyser_en_couches
from events.graph import ErreurGraph
from events.import_courriels import (
    adresses_de_la_boite, importer_couches, relever_la_boite, verrou_de_releve,
)
from events.models import Dossier


class Command(BaseCommand):
    help = "Importe les courriels transférés aux alias des dossiers."

    def add_arguments(self, parser):
        parser.add_argument("--essai", action="store_true",
                            help="Analyse et affiche, sans rien créer ni déplacer.")
        parser.add_argument("--fichier", help="Un fichier .eml à importer, au lieu de la boîte.")
        parser.add_argument("--dossier", type=int, help="Le dossier visé par --fichier (identifiant).")

    def handle(self, *args, essai, fichier, dossier, **options):
        if fichier:
            return self._depuis_un_fichier(Path(fichier), dossier, essai)
        try:
            self._depuis_la_boite(essai)
        except ErreurGraph as erreur:
            raise CommandError(str(erreur)) from erreur

    # -------------------------------------------------------------------------

    def _decrire(self, analyse, dossier):
        self.stdout.write(
            "  « %s » — %s, %s (%s%s), %d pièce(s) jointe(s)%s"
            % (analyse.titre, analyse.expediteur or "?", analyse.moment.strftime("%d/%m/%Y %H:%M"),
               analyse.forme, ", date approchée" if analyse.date_approchee else "",
               len(analyse.pieces), " → %s" % dossier.name if dossier else "")
        )

    def _depuis_un_fichier(self, chemin, dossier_id, essai):
        if not chemin.is_file():
            raise CommandError("Fichier introuvable : %s" % chemin)
        if dossier_id is None:
            raise CommandError("--dossier est requis avec --fichier.")
        try:
            dossier = Dossier.objects.get(pk=dossier_id)
        except Dossier.DoesNotExist:
            raise CommandError("Dossier introuvable : %s" % dossier_id)
        couches = analyser_en_couches(chemin.read_bytes(), ignorer=adresses_de_la_boite())
        for couche in couches:
            self._decrire(couche, dossier)
        if essai:
            return
        crees = importer_couches(dossier, couches)
        if not crees:
            self.stdout.write("  déjà importé dans ce dossier : rien de créé.")
        for evenement in crees:
            self.stdout.write(self.style.SUCCESS("  évènement créé (n° %d) : %s" % (evenement.pk, evenement.title)))

    def _depuis_la_boite(self, essai):
        with verrou_de_releve() as libre:
            if not libre:
                # Le bouton d'un dossier relève déjà la boîte : rien à faire.
                self.stdout.write("Un import est déjà en cours : ce passage s'efface.")
                return
            bilan = relever_la_boite(essai=essai, decrire=self._decrire, signaler=self.stderr.write)
        self.stdout.write(self.style.SUCCESS(
            "%d importé(s), %d déjà importé(s), %d sans alias, %d en échec%s."
            % (sum(bilan.importes.values()), sum(bilan.deja.values()), bilan.ignores, bilan.erreurs,
               " (essai : rien n'a été modifié)" if essai else "")
        ))
