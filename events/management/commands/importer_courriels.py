"""Transformer en évènements les courriels transférés aux alias des dossiers.

Deux sources :

- **la boîte d'import** (Exchange Online, par Microsoft Graph) — chaque
  courriel de la boîte de réception adressé à l'alias d'un dossier devient un
  évènement de ce dossier, puis est rangé dans le dossier Outlook
  « Importés » ; un courriel qui ne vise aucun alias est rangé dans « Non
  importés », sans rien créer. Rien n'est supprimé de la boîte ;
- **un fichier `.eml`** (`--fichier`, avec `--dossier`) — pour importer à la
  main un courriel enregistré, ou rejouer un exemple.

`--essai` analyse et affiche sans rien créer ni déplacer.

L'opération est rejouable : un courriel déjà importé dans un dossier est
reconnu (`CourrielImporte`) et n'y crée rien de plus. Si l'import réussit
mais que le rangement échoue, le passage suivant le range sans le réimporter.
"""

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

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
        if not essai:
            vers_importes = client.dossier(settings.COURRIEL_DOSSIER_IMPORTES)
            vers_non_importes = client.dossier(settings.COURRIEL_DOSSIER_NON_IMPORTES)
        importes = ignores = erreurs = 0

        for message in client.messages_recus():
            octets = client.mime(message["id"])
            dossier = dossier_destinataire(adresses(lire(octets), *ENTETES_DESTINATAIRES))
            if dossier is None:
                self.stdout.write("Aucun alias reconnu : « %s »" % message.get("subject", ""))
                ignores += 1
                if not essai:
                    client.deplacer(message["id"], vers_non_importes)
                continue
            try:
                analyse = analyser(octets)
                self._decrire(analyse, dossier)
                if essai:
                    continue
                if deja_importe(dossier, analyse):
                    self.stdout.write("  déjà importé : rangé sans rien créer.")
                else:
                    importer(dossier, analyse)
                    importes += 1
            except Exception as erreur:  # un courriel illisible ne bloque pas les suivants
                erreurs += 1
                self.stderr.write("  ÉCHEC : %s" % erreur)
                if not essai:
                    client.deplacer(message["id"], vers_non_importes)
                continue
            client.deplacer(message["id"], vers_importes)

        self.stdout.write(self.style.SUCCESS(
            "%d importé(s), %d sans alias, %d en échec%s."
            % (importes, ignores, erreurs, " (essai : rien n'a été modifié)" if essai else "")
        ))
