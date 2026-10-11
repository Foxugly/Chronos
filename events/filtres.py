"""Recherche et filtres sur les évènements d'un dossier.

Les mêmes paramètres d'URL servent toutes les vues (chronologie, calendriers,
vues sans JavaScript) et l'export : ce qu'on voit est ce qu'on exporte.

- `q` : des mots, **tous** requis, cherchés sans tenir compte des accents ni
  des majuscules dans le titre, la description, le nom des pièces et le texte
  des pièces `.md` (la version texte des courriels importés) ;
- `expediteur` : l'identifiant d'un expéditeur du dossier, ou `aucun` pour
  les évènements saisis à la main ;
- `etiquette` : l'identifiant d'une étiquette **du dossier** ;
- `du` / `au` : des mois `AAAA-MM`. Un évènement est retenu s'il touche la
  période (un chantier d'août à octobre est dans « septembre »).

Tout paramètre invalide est ignoré, jamais une erreur : une URL bricolée
montre le dossier entier, pas une page d'erreur. Un expéditeur d'un autre
dossier est invalide ici — `lire_filtres` ne regarde que ceux du dossier.

La recherche se fait en Python, pas en SQL : SQLite ne sait ignorer ni les
accents ni la casse hors ASCII, et le texte des `.md` est dans des fichiers.
À l'échelle d'un dossier (des dizaines d'évènements), c'est instantané.
"""

import calendar
import datetime
import html
import re
import unicodedata
from dataclasses import dataclass, field

from django.utils.formats import date_format
from django.utils.html import strip_tags
from django.utils.translation import gettext as _

SANS_EXPEDITEUR = "aucun"
MOIS = re.compile(r"^(\d{4})-(\d{2})$")
#: Au-delà, le texte d'une pièce `.md` n'est pas lu : une pièce démesurée ne
#: doit pas rendre la recherche lente pour tout le monde.
TAILLE_MAX_TEXTE = 2 * 1024 * 1024


def normaliser(texte):
    """Sans accents, sans casse, espaces réduits : « Évènement » → « evenement »."""
    decompose = unicodedata.normalize("NFKD", texte or "")
    sans_accents = "".join(c for c in decompose if not unicodedata.combining(c))
    return " ".join(sans_accents.casefold().split())


def _mois(valeur):
    correspondance = MOIS.match(valeur or "")
    if not correspondance:
        return None
    annee, mois = int(correspondance.group(1)), int(correspondance.group(2))
    if not (1 <= mois <= 12 and 1900 <= annee <= 2999):
        return None
    return datetime.date(annee, mois, 1)


def _fin_du_mois(premier):
    return premier.replace(day=calendar.monthrange(premier.year, premier.month)[1])


@dataclass
class Filtres:
    q: str = ""
    expediteur: str = ""
    expediteur_libelle: str = ""
    etiquette: str = ""
    etiquette_libelle: str = ""
    du: datetime.date | None = None
    au: datetime.date | None = None
    mots: list = field(default_factory=list)

    @property
    def actifs(self):
        return bool(self.mots or self.expediteur or self.etiquette or self.du or self.au)

    def parametres(self):
        """Les paramètres à reporter sur un lien ou dans un formulaire."""
        paires = []
        if self.q:
            paires.append(("q", self.q))
        if self.expediteur:
            paires.append(("expediteur", self.expediteur))
        if self.etiquette:
            paires.append(("etiquette", self.etiquette))
        if self.du:
            paires.append(("du", self.du.strftime("%Y-%m")))
        if self.au:
            paires.append(("au", self.au.strftime("%Y-%m")))
        return paires

    def description(self):
        """Les critères en clair, pour l'en-tête de l'export et le journal."""
        morceaux = []
        if self.q:
            morceaux.append(_("recherche « %(q)s »") % {"q": self.q})
        if self.expediteur:
            morceaux.append(_("expéditeur : %(nom)s") % {"nom": self.expediteur_libelle})
        if self.etiquette:
            morceaux.append(_("étiquette : %(nom)s") % {"nom": self.etiquette_libelle})
        if self.du and self.au:
            morceaux.append(_("de %(du)s à %(au)s") % {
                "du": date_format(self.du, "YEAR_MONTH_FORMAT"),
                "au": date_format(self.au, "YEAR_MONTH_FORMAT")})
        elif self.du:
            morceaux.append(_("à partir de %(du)s") % {"du": date_format(self.du, "YEAR_MONTH_FORMAT")})
        elif self.au:
            morceaux.append(_("jusqu'à %(au)s") % {"au": date_format(self.au, "YEAR_MONTH_FORMAT")})
        return " ; ".join(morceaux)


def lire_filtres(parametres, dossier):
    """Les filtres d'une requête (`request.GET` ou `request.POST`), validés."""
    q = " ".join((parametres.get("q") or "").split())[:200]
    filtres = Filtres(q=q, mots=normaliser(q).split())

    expediteur = (parametres.get("expediteur") or "").strip()
    if expediteur == SANS_EXPEDITEUR:
        filtres.expediteur = SANS_EXPEDITEUR
        filtres.expediteur_libelle = _("saisis à la main")
    elif expediteur.isdigit():
        trouve = dossier.expediteurs.filter(pk=int(expediteur)).first()
        if trouve is not None:
            filtres.expediteur = str(trouve.pk)
            filtres.expediteur_libelle = str(trouve)

    etiquette = (parametres.get("etiquette") or "").strip()
    if etiquette.isdigit():
        trouvee = dossier.etiquettes.filter(pk=int(etiquette)).first()
        if trouvee is not None:
            filtres.etiquette = str(trouvee.pk)
            filtres.etiquette_libelle = trouvee.nom

    filtres.du = _mois(parametres.get("du"))
    au = _mois(parametres.get("au"))
    filtres.au = _fin_du_mois(au) if au else None
    if filtres.du and filtres.au and filtres.du > filtres.au:
        filtres.du, filtres.au = filtres.au.replace(day=1), _fin_du_mois(filtres.du)
    return filtres


def _texte_de_piece(document, cache):
    """Le texte d'une pièce `.md`, lu une fois par requête ; vide sinon."""
    if document.pk in cache:
        return cache[document.pk]
    texte = ""
    if document.title.lower().endswith(".md"):
        try:
            if document.file.size <= TAILLE_MAX_TEXTE:
                with document.file.open("rb") as fichier:
                    texte = normaliser(fichier.read().decode("utf-8", errors="replace"))
        except (OSError, ValueError):
            # Fichier absent du disque : la recherche continue sans lui ; le
            # contrôle d'intégrité de l'export, lui, le signalera.
            texte = ""
    cache[document.pk] = texte
    return texte


def _sources(evenement, cache):
    """Où chercher : (libellé, texte normalisé), du plus parlant au moins."""
    yield _("titre"), normaliser(evenement.title)
    yield _("description"), normaliser(html.unescape(strip_tags(evenement.description or "")))
    for document in evenement.documents.all():
        texte = _texte_de_piece(document, cache)
        if document.est_version_texte:
            # Jamais affichée : on cherche dans son texte, on ne la nomme pas.
            if texte:
                yield _("texte du courriel"), texte
            continue
        yield (_("pièce « %(nom)s »") % {"nom": document.title},
               normaliser("%s %s" % (document.numero or "", document.title)))
        if texte:
            yield _("texte de « %(nom)s »") % {"nom": document.title}, texte


def appliquer(evenements, filtres):
    """Les évènements retenus, et pour chacun où la recherche l'a trouvé.

    Rend `(retenus, correspondances)` ; `correspondances[pk]` liste les
    endroits où au moins un mot a été trouvé (vide sans recherche).
    """
    retenus, correspondances, cache = [], {}, {}
    for evenement in evenements:
        if filtres.expediteur == SANS_EXPEDITEUR and evenement.expediteur_id is not None:
            continue
        if filtres.expediteur.isdigit() and str(evenement.expediteur_id) != filtres.expediteur:
            continue
        if filtres.etiquette and int(filtres.etiquette) not in {e.pk for e in evenement.etiquettes.all()}:
            continue
        fin = evenement.date_fin or evenement.date
        if filtres.du and fin < filtres.du:
            continue
        if filtres.au and evenement.date > filtres.au:
            continue
        if filtres.mots:
            endroits, trouves = [], set()
            for libelle, texte in _sources(evenement, cache):
                ici = {mot for mot in filtres.mots if mot in texte}
                if ici:
                    endroits.append(libelle)
                    trouves |= ici
            if trouves != set(filtres.mots):
                continue
            correspondances[evenement.pk] = endroits
        retenus.append(evenement)
    return retenus, correspondances


def options_de_mois(evenements):
    """Les mois que couvre le dossier, pour les listes « du » / « au »."""
    if not evenements:
        return []
    debut = min(e.date for e in evenements).replace(day=1)
    fin = max((e.date_fin or e.date) for e in evenements).replace(day=1)
    mois, courant = [], debut
    while courant <= fin:
        mois.append((courant.strftime("%Y-%m"), date_format(courant, "YEAR_MONTH_FORMAT")))
        courant = (courant + datetime.timedelta(days=32)).replace(day=1)
    return mois


#: Les types de pièce proposés par l'onglet « Documents », par extension.
TYPES_DE_PIECE = {
    "pdf": {"pdf"},
    "image": {"jpg", "jpeg", "png"},
    "courriel": {"eml", "md"},
    "bureautique": {"doc", "docx", "xls", "xlsx"},
}


def libelles_des_types():
    return [
        ("pdf", _("PDF")),
        ("image", _("Images")),
        ("courriel", _("Courriels")),
        ("bureautique", _("Bureautique")),
    ]


def _extension(nom):
    return nom.rsplit(".", 1)[-1].lower() if "." in nom else ""


def _radical(nom):
    """`courriel-20260928-2029.eml` et `.md` → `courriel-20260928-2029`."""
    return nom.rsplit(".", 1)[0].lower() if "." in nom else nom.lower()


def pieces_filtrees(dossier, evenements, filtres, type_piece=""):
    """L'onglet « Documents » : les pièces, avec leurs évènements.

    L'expéditeur et la période choisissent les évènements, donc leurs pièces ;
    la recherche, elle, porte sur la pièce même (son nom, le texte d'un `.md`) ;
    `type_piece` restreint à une famille d'extensions. Sans filtre d'évènement,
    une pièce rattachée à aucun évènement est montrée aussi.

    La version texte d'un courriel (`.md`) n'est jamais une ligne : son texte
    est cherché sur le courriel (`.eml`) du même nom, qui la représente.

    Rend `(lignes, total)` ; chaque ligne : `document`, `extension`,
    `evenements` (du dossier, dans l'ordre), `trouve_dans`.
    """
    tous, versions_texte = {}, {}
    for evenement in evenements:
        for document in evenement.documents.all():
            if document.est_version_texte:
                versions_texte.setdefault(_radical(document.title), []).append(document)
            else:
                tous.setdefault(document.pk, document)
    sans_evenement = [d for d in dossier.documents.filter(events__isnull=True)
                      if not d.est_version_texte]
    total = len(tous) + len(sans_evenement)

    filtres_evenements = Filtres(expediteur=filtres.expediteur, etiquette=filtres.etiquette,
                                 du=filtres.du, au=filtres.au)
    retenus, _vide = appliquer(evenements, filtres_evenements)
    candidats = {}
    for evenement in retenus:
        for document in evenement.pieces_affichees:
            candidats.setdefault(document.pk, document)
    if not filtres_evenements.actifs:
        for document in sans_evenement:
            candidats.setdefault(document.pk, document)

    par_document = {}
    for evenement in evenements:
        for document in evenement.documents.all():
            par_document.setdefault(document.pk, []).append(evenement)

    extensions = TYPES_DE_PIECE.get(type_piece)
    lignes, cache = [], {}
    for document in candidats.values():
        extension = _extension(document.title)
        if extensions and extension not in extensions:
            continue
        trouve_dans = []
        if filtres.mots:
            nom = normaliser("%s %s" % (document.numero or "", document.title))
            texte = " ".join([_texte_de_piece(document, cache)] + [
                _texte_de_piece(version, cache)
                for version in versions_texte.get(_radical(document.title), [])
            ])
            if not all(mot in nom or mot in texte for mot in filtres.mots):
                continue
            if any(mot in nom for mot in filtres.mots):
                trouve_dans.append(_("nom"))
            if any(mot in texte for mot in filtres.mots):
                trouve_dans.append(_("texte"))
        lignes.append({
            "document": document,
            "extension": extension,
            "evenements": par_document.get(document.pk, []),
            "trouve_dans": trouve_dans,
        })
    # Dans l'ordre des numéros de pièces : celui d'un bordereau.
    lignes.sort(key=lambda l: (l["document"].numero or 0, l["document"].pk))
    return lignes, total
