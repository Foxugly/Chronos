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

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from events.courriels import adresses, analyser, lire
from events.graph import ClientGraph, ErreurGraph
from events.import_courriels import deja_importe, dossier_destinataire, importer
from events.models import Dossier

#: Les en-têtes où peut figurer l'alias d'un courriel transféré.
ENTETES_DESTINATAIRES = ("To", "Cc", "Delivered-To", "X-Original-To", "Resent-To")


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
        analyse = analyser(chemin.read_bytes())
        self._decrire(analyse, dossier)
        if essai:
            return
        evenement = importer(dossier, analyse)
        if evenement is None:
            self.stdout.write("  déjà importé dans ce dossier : rien de créé.")
        else:
            self.stdout.write(self.style.SUCCESS("  évènement créé (n° %d)." % evenement.pk))

    def _depuis_la_boite(self, essai):
        client = ClientGraph(
            settings.GRAPH_TENANT_ID, settings.GRAPH_CLIENT_ID,
            settings.GRAPH_CLIENT_SECRET, settings.COURRIEL_BOITE,
        )
        dossiers_outlook = {}  # nom du dossier Chronos → identifiant du dossier Outlook
        importes = deja = ignores = erreurs = 0

        for message in client.messages_recus(timezone.now() - settings.COURRIEL_FENETRE):
            octets = client.mime(message["id"])
            dossier = dossier_destinataire(adresses(lire(octets), *ENTETES_DESTINATAIRES))
            if dossier is None:
                ignores += 1  # ni déplacé ni cité : ce courriel ne regarde pas Chronos
                continue
            try:
                analyse = analyser(octets)
                self._decrire(analyse, dossier)
                if essai:
                    continue
                if deja_importe(dossier, analyse):
                    deja += 1
                    self.stdout.write("  déjà importé : rangé sans rien créer.")
                else:
                    importer(dossier, analyse)
                    importes += 1
                if dossier.name not in dossiers_outlook:
                    dossiers_outlook[dossier.name] = client.dossier(dossier.name)
                client.deplacer(message["id"], dossiers_outlook[dossier.name])
            except Exception as erreur:  # un courriel illisible ne bloque pas les suivants
                erreurs += 1
                self.stderr.write("  ÉCHEC : %s" % erreur)

        self.stdout.write(self.style.SUCCESS(
            "%d importé(s), %d déjà importé(s), %d sans alias, %d en échec%s."
            % (importes, deja, ignores, erreurs, " (essai : rien n'a été modifié)" if essai else "")
        ))
