"""Doublons possibles, et leur fusion.

Le cas réel : un courriel saisi à la main (« RV mise en demeure ACME BAT »,
avec la pièce jointe déposée) puis importé par courriel (« Mise en demeure EV
HOME – Travaux… », avec la même pièce, l'heure exacte et le courriel complet).
Deux évènements pour un même fait.

**Détection** — une paire d'évènements du dossier est proposée si :

- elle partage au moins une pièce (le même fichier : les pièces sont
  dédoublonnées par empreinte) **et** tombe à un jour d'écart au plus ;
- ou elle tombe le même jour avec des titres proches.

Partager une pièce ne suffit pas : un courriel joint des photos déjà déposées
des semaines plus tôt, sans être pour autant le même fait. Deux évènements
importés de deux courriels distincts ne sont jamais proposés — ce sont deux
courriels. **Sauf s'ils sont le même courriel arrivé par deux chemins** (même
empreinte, `events/liens.py`, et aucun ne cite l'autre) : on propose alors de
garder le plus riche, celui qui a le plus de pièces. Un évènement et celui qu'il transfère (`Event.transfert_de`) non plus,
eux qui partagent pourtant le même `.eml`. Une paire écartée (`PaireIgnoree`)
n'est plus proposée.

**Fusion** — on garde un évènement et on y verse l'autre ; rien n'est perdu :

- les pièces de l'absorbé sont rattachées au gardé ;
- sa description (si elle diffère) est ajoutée à la suite, sous une mention
  de son titre et de sa date d'origine ;
- l'expéditeur et la couleur, si le gardé n'en a pas ;
- les étiquettes des deux ;
- la mémoire d'import (`CourrielImporte`) suit, pour que le courriel ne soit
  pas réimporté ;
- le journal garde l'évènement absorbé entier (`EVENEMENT_SUPPRIME`) et ce
  qui a changé sur le gardé (`EVENEMENTS_FUSIONNES`).
"""

import html
import re

from django.db import transaction
from django.db.models import F
from django.utils.translation import gettext as _

from .filtres import normaliser
from .journal import consigner, consigner_pieces, differences, instantane
from .models import CourrielImporte, Event, Journal, PaireIgnoree
from .sanitize import est_vide, nettoyer

ECART_MAX_JOURS = 1
SIMILARITE_MIN = 0.5


def _mots(titre):
    # Découpés sur tout ce qui n'est ni lettre ni chiffre (« d'acompte » →
    # « acompte ») ; les mots courts (« de », « la », « RE ») ne disent rien
    # de la proximité.
    return {mot for mot in re.findall(r"[a-z0-9]+", normaliser(titre)) if len(mot) >= 3}


def similarite(titre_a, titre_b):
    """Indice de Jaccard sur les mots des titres, de 0 à 1."""
    a, b = _mots(titre_a), _mots(titre_b)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def doublons_possibles(dossier, evenements=None):
    """Les paires à proposer, des plus sûres aux moins sûres.

    Chaque paire : `{"a", "b", "communes", "raisons", "garder"}` ; `garder`
    est la suggestion — l'évènement importé, qui porte l'heure exacte et le
    courriel d'origine, sinon le plus ancien.
    """
    if evenements is None:
        evenements = list(dossier.events.select_related("expediteur").prefetch_related("documents"))
    pieces = {e.pk: {d.pk: d for d in e.documents.all()} for e in evenements}
    importes = set(
        CourrielImporte.objects.filter(dossier=dossier, evenement__isnull=False)
        .values_list("evenement_id", flat=True)
    )
    ecartees = set(dossier.paires_ignorees.values_list("evenement_a_id", "evenement_b_id"))
    from .import_courriels import _richesse, citation_de_trace, empreinte_de_trace

    empreintes, citations = {}, {}
    for trace in CourrielImporte.objects.filter(dossier=dossier).exclude(evenement=None).select_related("evenement"):
        empreintes.setdefault(trace.evenement_id, empreinte_de_trace(trace))
        citations.setdefault(trace.evenement_id, citation_de_trace(trace))

    def meme_courriel(a, b):
        ea, eb = empreintes.get(a.pk), empreintes.get(b.pk)
        return bool(
            ea and ea.correspond(eb)
            and not ea.correspond(citations.get(b.pk))
            and not eb.correspond(citations.get(a.pk))
            and a.reponse_a_id != b.pk and b.reponse_a_id != a.pk
            and a.transfert_de_id != b.pk and b.transfert_de_id != a.pk
        )

    paires = []
    tries = sorted(evenements, key=lambda e: e.pk)
    for i, a in enumerate(tries):
        for b in tries[i + 1:]:
            if (a.pk, b.pk) in ecartees:
                continue
            deux_importes = a.pk in importes and b.pk in importes
            if deux_importes and not meme_courriel(a, b):
                continue
            if a.transfert_de_id == b.pk or b.transfert_de_id == a.pk:
                continue  # un transfert et ce qu'il transfère : deux courriels, pas un doublon
            ecart = abs((a.date - b.date).days)
            if ecart > ECART_MAX_JOURS:
                continue
            communes = [pieces[a.pk][pk] for pk in pieces[a.pk].keys() & pieces[b.pk].keys()]
            proche = similarite(a.title, b.title)
            raisons = []
            if communes:
                raisons.append(_("%(n)s pièce(s) en commun") % {"n": len(communes)})
            if ecart == 0:
                raisons.append(_("même jour"))
            else:
                raisons.append(_("un jour d'écart"))
            if proche >= SIMILARITE_MIN:
                raisons.append(_("titres proches"))
            if deux_importes:
                raisons.insert(0, _("le même courriel, arrivé par deux chemins"))
            elif not (communes or (ecart == 0 and proche >= SIMILARITE_MIN)):
                continue
            if deux_importes:
                garder = max((a, b), key=lambda e: (_richesse(e), -e.pk))
            elif b.pk in importes and a.pk not in importes:
                garder = b
            elif a.pk in importes:
                garder = a
            else:
                garder = a if (a.date, a.pk) <= (b.date, b.pk) else b
            paires.append({
                "a": a, "b": b,
                "communes": sorted(communes, key=lambda d: d.title),
                "raisons": raisons,
                "garder": garder,
                "score": (len(communes), ecart == 0, proche),
            })
    paires.sort(key=lambda p: p["score"], reverse=True)
    return paires


def ecarter(premier, second, *, request):
    """Déclare les deux évènements distincts : la paire n'est plus proposée."""
    a, b = PaireIgnoree.ordonner(premier, second)
    with transaction.atomic():
        _paire, creee = PaireIgnoree.objects.get_or_create(
            dossier=a.dossier, evenement_a=a, evenement_b=b,
            defaults={"ignoree_par": request.user},
        )
        if creee:
            consigner(
                a.dossier, Journal.Action.DOUBLON_ECARTE, request=request,
                objet="%s / %s" % (a.title, b.title)[:255],
                details={"evenements": [a.title, b.title]},
            )


def fusionner(garde, absorbe, *, request):
    """Verse `absorbe` dans `garde`, puis supprime `absorbe`. Rend `garde`.

    Une seule transaction : une fusion à moitié faite (pièces déplacées,
    absorbé encore là) n'existe jamais.
    """
    if garde.pk == absorbe.pk or garde.dossier_id != absorbe.dossier_id:
        raise ValueError("Fusion impossible : deux évènements distincts d'un même dossier.")
    with transaction.atomic():
        avant = instantane(garde)
        valeurs_absorbe = instantane(absorbe)

        deja = set(garde.documents.values_list("pk", flat=True))
        rattachees = []
        for document in absorbe.documents.all():
            if document.pk not in deja:
                document.events.add(garde)
                rattachees.append((document, False))

        if not est_vide(absorbe.description) and absorbe.description != garde.description:
            mention = _("Fusionné depuis « %(titre)s » (%(periode)s) :") % {
                "titre": absorbe.title, "periode": absorbe.periode_affichee,
            }
            garde.description = nettoyer(
                (garde.description or "")
                + "<p><em>" + html.escape(mention) + "</em></p>"
                + absorbe.description
            )
        if garde.expediteur_id is None and absorbe.expediteur_id is not None:
            garde.expediteur_id = absorbe.expediteur_id
        if not garde.couleur and absorbe.couleur:
            garde.couleur = absorbe.couleur
        garde.save(update_fields=["description", "expediteur", "couleur"])
        # Un formulaire ouvert sur l'un ou l'autre avant la fusion doit être
        # refusé à l'enregistrement : la version change.
        Event.objects.filter(pk=garde.pk).update(version=F("version") + 1)

        CourrielImporte.objects.filter(evenement=absorbe).update(evenement=garde)
        # Les transferts et réponses qui renvoyaient à l'absorbé renvoient au gardé.
        Event.objects.filter(transfert_de=absorbe).exclude(pk=garde.pk).update(transfert_de=garde)
        Event.objects.filter(reponse_a=absorbe).exclude(pk=garde.pk).update(reponse_a=garde)
        if garde.transfert_de_id is None and absorbe.transfert_de_id not in (None, garde.pk):
            Event.objects.filter(pk=garde.pk).update(transfert_de=absorbe.transfert_de_id)
        if garde.reponse_a_id is None and absorbe.reponse_a_id not in (None, garde.pk):
            Event.objects.filter(pk=garde.pk).update(reponse_a=absorbe.reponse_a_id)
        garde.etiquettes.add(*absorbe.etiquettes.all())

        consigner(
            garde.dossier, Journal.Action.EVENEMENTS_FUSIONNES, request=request,
            objet=garde.title, evenement=garde,
            details={
                "absorbe": absorbe.title,
                "changements": differences(avant, instantane(garde)),
            },
        )
        consigner_pieces(garde, rattachees, request=request)
        consigner(
            garde.dossier, Journal.Action.EVENEMENT_SUPPRIME, request=request,
            objet=absorbe.title,
            details={"valeurs": valeurs_absorbe, "fusionne_dans": garde.title},
        )
        absorbe.delete()
    garde.refresh_from_db()
    return garde
