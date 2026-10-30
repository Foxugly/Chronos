"""Relire le texte de courriels déjà importés, après une amélioration de la lecture.

Pour chaque évènement **nommé** (`--evenement`), relit son `.eml` stocké avec le code
actuel, retrouve la couche qui lui correspond et en refait la description — comme à
l'import. Le titre, la date, les pièces et la version texte (`.md`, une pièce numérotée)
ne changent pas.

Seulement les évènements nommés, et jamais un évènement dont quelqu'un a modifié la
description à la main (le journal le dit) : on n'écrase pas un texte retouché.

**Essai par défaut** : rien n'est écrit sans `--appliquer`. Chaque description remplacée
est journalisée (avant → après) et la version de l'évènement augmente.
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import F

from events.import_courriels import adresses_de_la_boite, description_de
from events.journal import consigner, differences, instantane
from events.management.commands.relier_courriels import Command as Relier
from events.models import CourrielImporte, Event, Journal


class Command(BaseCommand):
    help = "Refait la description de courriels importés à partir de leur .eml (essai par défaut)."

    def add_arguments(self, parser):
        parser.add_argument("--evenement", type=int, nargs="+", required=True,
                            help="Les évènements à relire (identifiants).")
        parser.add_argument("--appliquer", action="store_true", help="Écrit (sinon : essai).")

    def handle(self, *args, evenement, appliquer, **options):
        evenements = list(Event.objects.filter(pk__in=evenement).select_related("dossier")
                          .prefetch_related("documents").order_by("pk"))
        inconnus = set(evenement) - {e.pk for e in evenements}
        if inconnus:
            raise CommandError("Évènement(s) inconnu(s) : %s" % ", ".join(map(str, sorted(inconnus))))
        ignorer = adresses_de_la_boite()
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
        nouvelle = description_de(couche)
        if nouvelle == evenement.description:
            self.stdout.write("n°%d : inchangée." % evenement.pk)
            return
        self.stdout.write(self.style.MIGRATE_HEADING("n°%d — %s : %d → %d caractères" % (
            evenement.pk, evenement.title, len(evenement.description or ""), len(nouvelle))))
        self.stdout.write(couche.texte)
        if not appliquer:
            return
        avant = instantane(evenement)
        evenement.description = nouvelle
        Event.objects.filter(pk=evenement.pk).update(description=nouvelle, version=F("version") + 1)
        consigner(evenement.dossier, Journal.Action.EVENEMENT_MODIFIE, objet=evenement.title, evenement=evenement,
                  details={"changements": differences(avant, instantane(evenement)), "source": "courriel"})
        self.stdout.write(self.style.SUCCESS("n°%d : écrit." % evenement.pk))
