"""Relier les courriels déjà importés, et repérer ceux qui sont arrivés deux fois.

Pour les évènements importés **avant** `events/liens.py` (2026-10-09) : relit leur `.eml`
stocké, retrouve la couche qui leur correspond, puis

- note le courriel qu'elle cite (`CourrielImporte.cite_*`), pour relier les suivants ;
- complète l'expéditeur quand il n'avait pas été lu (`x@y <x@y>`, `Nom [mailto:…]`) ;
- relie chaque réponse au courriel qu'elle cite (`Event.reponse_a`), si celui-ci est
  dans le dossier ;
- liste les **doublons** — le même courriel arrivé par deux chemins — et l'évènement à
  garder, le plus riche (le plus de pièces). Il ne fusionne rien : la page Doublons du
  dossier les propose désormais, et la fusion y est journalisée et ne perd aucune pièce.

**Essai par défaut** : tout est fait dans une transaction **annulée** à la fin, donc le
rapport est exactement ce que `--appliquer` écrira. Rien n'est jamais supprimé ici.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from events.courriels import analyser_en_couches
from events.doublons import doublons_possibles
from events.import_courriels import (
    _richesse, adresses_de_la_boite, champs_cites, cle_du_courriel, expediteur_du_dossier, relier,
)
from events.journal import consigner
from events.models import CourrielImporte, Dossier, Event, Journal


class Command(BaseCommand):
    help = "Relie les courriels importés entre eux et liste les doublons (essai par défaut)."

    def add_arguments(self, parser):
        parser.add_argument("--appliquer", action="store_true",
                            help="Écrit les liens (sinon : essai, tout est annulé à la fin).")
        parser.add_argument("--dossier", type=int, help="Un seul dossier (identifiant).")

    def handle(self, *args, appliquer, dossier, **options):
        dossiers = Dossier.objects.filter(pk=dossier) if dossier else Dossier.objects.all()
        with transaction.atomic():
            for d in dossiers.order_by("pk"):
                self._dossier(d)
            if not appliquer:
                transaction.set_rollback(True)
                self.stdout.write(self.style.WARNING("\nEssai : rien n'a été écrit. Relancer avec --appliquer."))
            else:
                self.stdout.write(self.style.SUCCESS("\nÉcrit."))

    def _couche(self, evenement, ignorer):
        cles = set(CourrielImporte.objects.filter(evenement=evenement).values_list("message_id", flat=True))
        for document in evenement.documents.all():
            if not document.file.name.lower().endswith(".eml"):
                continue
            try:
                with document.file.open("rb") as fichier:
                    couches = analyser_en_couches(fichier.read(), ignorer=ignorer)
            except Exception as erreur:  # un .eml illisible n'arrête pas les autres
                self.stderr.write("  n°%d : %s illisible (%s)" % (evenement.pk, document.file.name, erreur))
                continue
            for couche in couches:
                if cles & {cle_du_courriel(couche), couche.cle_ancienne}:
                    return couche
        return None

    def _dossier(self, dossier):
        self.stdout.write(self.style.MIGRATE_HEADING("Dossier n°%d — %s" % (dossier.pk, dossier.name)))
        ignorer = adresses_de_la_boite()
        evenements = list(Event.objects.filter(dossier=dossier, pk__in=CourrielImporte.objects.filter(
            dossier=dossier).values("evenement")).prefetch_related("documents").order_by("date", "heure", "pk"))

        # 1. Citations et expéditeurs, d'abord pour tous : relier demande de connaître
        #    ce que chacun cite, en amont comme en aval.
        couches = {}
        for evenement in evenements:
            couche = self._couche(evenement, ignorer)
            if couche is None:
                self.stdout.write("  n°%d : aucune couche retrouvée dans ses .eml" % evenement.pk)
                continue
            couches[evenement.pk] = couche
            cite_de, cite_le, cite_objet = champs_cites(couche)
            CourrielImporte.objects.filter(evenement=evenement, cite_le=None).update(
                cite_de=cite_de, cite_le=cite_le, cite_objet=cite_objet)
            if evenement.expediteur_id is None:
                expediteur = expediteur_du_dossier(dossier, couche.expediteur)
                if expediteur is not None:
                    Event.objects.filter(pk=evenement.pk).update(expediteur=expediteur)
                    self.stdout.write("  n°%d : expéditeur retrouvé — %s" % (evenement.pk, expediteur.adresse))

        # 2. Les liens « en réponse à ».
        traces = list(CourrielImporte.objects.filter(dossier=dossier).exclude(evenement=None)
                      .select_related("evenement"))
        nombre = 0
        for evenement in evenements:
            if evenement.pk not in couches:
                continue
            evenement.refresh_from_db(fields=["reponse_a"])
            for reponse, cite in relier(evenement, couches[evenement.pk], traces):
                nombre += 1
                consigner(dossier, Journal.Action.EVENEMENT_MODIFIE, objet=reponse.title, evenement=reponse,
                          details={"changements": {"reponse_a": ["", cite.title]}, "source": "courriel"})
                self.stdout.write("  lien : n°%d « %s » — en réponse à n°%d « %s »" % (
                    reponse.pk, reponse.title[:50], cite.pk, cite.title[:50]))
        self.stdout.write("  %d lien(s)." % nombre)

        # 3. Les doublons : le même courriel arrivé par deux chemins.
        paires = [p for p in doublons_possibles(dossier) if p["a"].pk in couches and p["b"].pk in couches]
        if not paires:
            self.stdout.write("  Aucun doublon.")
        for paire in paires:
            garde = paire["garder"]
            autre = paire["b"] if garde.pk == paire["a"].pk else paire["a"]
            self.stdout.write("  doublon : garder n°%d « %s » (%d pièce(s)), fondre n°%d (%d pièce(s)) — %s" % (
                garde.pk, garde.title[:50], _richesse(garde), autre.pk, _richesse(autre),
                ", ".join(str(r) for r in paire["raisons"])))
