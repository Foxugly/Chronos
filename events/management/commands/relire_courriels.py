"""Relire le texte de courriels déjà importés, après une amélioration de la lecture.

Pour chaque évènement nommé (`--evenement`), ou chaque courriel importé d'un dossier
(`--dossier`), relit son `.eml` stocké avec le code actuel, retrouve la couche qui lui
correspond et en refait la description — comme à l'import, donc sans le courriel cité
quand celui-ci est dans le dossier. Le titre, la date, les pièces et la version texte
(`.md`, une pièce numérotée) ne changent pas.

Jamais un évènement dont quelqu'un a modifié la description à la main (le journal le
dit) : on n'écrase pas un texte retouché.

**Essai par défaut** : rien n'est écrit sans `--appliquer`, qui sauvegarde d'abord
(`events/sauvegarde.py`). Chaque description remplacée est journalisée (avant → après)
et la version de l'évènement augmente.
"""

import html
import re

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import F
from django.utils.html import strip_tags

from events import sauvegarde
from events.import_courriels import _traces, adresses_de_la_boite, cite_present, description_de
from events.journal import consigner, differences, instantane
from events.management.commands.relier_courriels import Command as Relier
from events.models import CourrielImporte, Event, Journal


class Command(BaseCommand):
    help = "Refait la description de courriels importés à partir de leur .eml (essai par défaut)."

    def add_arguments(self, parser):
        cible = parser.add_mutually_exclusive_group(required=True)
        cible.add_argument("--evenement", type=int, nargs="+", help="Les évènements à relire (identifiants).")
        cible.add_argument("--dossier", type=int, help="Tous les courriels importés de ce dossier.")
        parser.add_argument("--appliquer", action="store_true", help="Écrit (sinon : essai).")

    def handle(self, *args, evenement, dossier, appliquer, **options):
        if dossier is not None:
            evenements = Event.objects.filter(dossier_id=dossier, pk__in=CourrielImporte.objects.filter(
                dossier_id=dossier).values("evenement"))
        else:
            evenements = Event.objects.filter(pk__in=evenement)
        evenements = list(evenements.select_related("dossier").prefetch_related("documents").order_by("pk"))
        inconnus = set(evenement or ()) - {e.pk for e in evenements}
        if inconnus:
            raise CommandError("Évènement(s) inconnu(s) : %s" % ", ".join(map(str, sorted(inconnus))))
        self.detail = dossier is None
        ignorer = adresses_de_la_boite()
        if appliquer:
            sauvegarde.sauvegarder_avant_d_ecrire(self)
        with transaction.atomic():
            for ev in evenements:
                self._relire(ev, ignorer, appliquer)
        if not appliquer:
            self.stdout.write(self.style.WARNING("\nEssai : rien n'a été écrit. Relancer avec --appliquer."))

    def _relire(self, evenement, ignorer, appliquer):
        if not CourrielImporte.objects.filter(evenement=evenement).exists():
            self.stdout.write("n°%d : pas un courriel importé, ignoré." % evenement.pk)
            return
        retouches = Journal.objects.filter(evenement=evenement, action=Journal.Action.EVENEMENT_MODIFIE,
                                           auteur__isnull=False)
        if any("description" in (j.details.get("changements") or {}) for j in retouches):
            self.stdout.write("n°%d : description modifiée à la main, laissée telle quelle." % evenement.pk)
            return
        couche = Relier._couche(self, evenement, ignorer)
        if couche is None:
            self.stdout.write("n°%d : aucune couche retrouvée dans ses .eml." % evenement.pk)
            return
        nouvelle = description_de(couche, sans_citations=cite_present(couche, _traces(evenement.dossier),
                                                                      soi=evenement.pk))
        if nouvelle == evenement.description:
            self.stdout.write("n°%d : inchangée." % evenement.pk)
            return
        self.stdout.write(self.style.MIGRATE_HEADING("n°%d — %s : %d → %d caractères" % (
            evenement.pk, evenement.title, len(evenement.description or ""), len(nouvelle))))
        if self.detail:
            self.stdout.write(html.unescape(strip_tags(re.sub(r"</p>|<br>", "\n", nouvelle))))
        if not appliquer:
            return
        avant = instantane(evenement)
        evenement.description = nouvelle
        Event.objects.filter(pk=evenement.pk).update(description=nouvelle, version=F("version") + 1)
        consigner(evenement.dossier, Journal.Action.EVENEMENT_MODIFIE, objet=evenement.title, evenement=evenement,
                  details={"changements": differences(avant, instantane(evenement)), "source": "courriel"})
        self.stdout.write(self.style.SUCCESS("n°%d : écrit." % evenement.pk))
