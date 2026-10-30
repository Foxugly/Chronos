"""Reconnaître un même courriel arrivé par deux chemins, et ce qu'il cite.

Un courriel importé n'a pas toujours la même clé selon le chemin : en direct il a son
Message-ID, recopié dans le texte d'un transfert il n'a qu'un bloc « De : … Envoyé : … ».
Son **empreinte** ne dépend pas du chemin : l'expéditeur, l'objet sans ses préfixes
(TR:, FW:, RE:…) et l'heure, **à 20 minutes près** — l'heure d'envoi (bloc recopié) et
l'heure de réception (courriel joint) diffèrent de 2 à 13 minutes sur les vrais exemples.

Ce module est pur : il compare des chaînes et des dates, il ne lit ni la base ni le réseau.
"""

import datetime
import re
import unicodedata
from dataclasses import dataclass
from email.utils import getaddresses

from .courriels import (
    BRUXELLES, CITATIONS, EN_UNE_LIGNE, TRANSFERTS, _bloc_de_transfert, bloc_et_position,
    date_en_toutes_lettres,
)

TOLERANCE = datetime.timedelta(minutes=20)
#: Deux heures d'**envoi** lues dans le texte sont écrites à la minute : au-delà, ce sont deux
#: courriels. Paul Durand a écrit deux fois au même fil le 03/06, à 09:13 et à 09:27 — à
#: 20 minutes près, celui de 09:27 passait pour celui de 09:13, absent de la liste des
#: manquants, puis « déjà importé » quand il est arrivé.
A_LA_MINUTE = datetime.timedelta(seconds=60)
#: Une heure de réception suit l'heure d'envoi (de 2 à 13 minutes sur les vrais exemples) ;
#: elle ne la précède qu'au décalage d'horloge près.
AVANCE_D_HORLOGE = datetime.timedelta(minutes=2)
ENVOI, RECEPTION = "envoi", "reception"

#: Tous les préfixes, transfert **et** réponse : « RE: sdb » et « sdb » sont le même fil.
PREFIXES = re.compile(r"^\s*((tr|fw|fwd|wg|doorst|trans|re|aw|antw|sv)\s*:\s*)+", re.IGNORECASE)
ADRESSE = re.compile(r"[\w.+'-]+@[\w-]+(?:\.[\w-]+)+")


def _sans_accents(texte):
    return "".join(c for c in unicodedata.normalize("NFKD", texte or "") if not unicodedata.combining(c))


#: Le titre que l'import donne à un courriel sans objet (`courriels.nettoyer_titre`).
#: Le bloc cité par une réponse, lui, a une ligne « Objet : » vide : comparés, les
#: deux sont le même objet — vide. Sans cela, aucune réponse ne se reliait à un
#: courriel sans objet, compté de plus comme manquant.
SANS_OBJET = "(sans objet)"


def objet_normalise(objet):
    objet = " ".join(_sans_accents(PREFIXES.sub("", objet or "")).lower().split())
    return "" if objet == SANS_OBJET else objet


def adresse_de(texte):
    """L'adresse d'un « De : », même mal formé : `x@y <x@y>`, `Nom [mailto:x@y]`…
    `getaddresses` échoue sur ces deux formes, fréquentes chez Outlook."""
    chevrons = re.search(r"<\s*(" + ADRESSE.pattern + r")\s*>", texte or "")
    if chevrons:
        return chevrons.group(1).lower()
    trouvee = ADRESSE.search(texte or "")
    return trouvee.group(0).lower() if trouvee else ""


def expediteur_normalise(texte):
    """`(adresse, nom)` — l'un ou l'autre peut manquer."""
    adresse = adresse_de(texte)
    nom = ""
    paires = getaddresses([texte or ""])
    if paires and paires[0][0] and "@" not in paires[0][0]:
        nom = paires[0][0]
    elif not adresse:
        nom = texte or ""
    nom = re.sub(r"\[mailto:[^\]]*\]|<[^>]*>|\"", " ", nom)
    return adresse, " ".join(_sans_accents(nom).lower().split())


def meme_expediteur(a, b):
    if a[0] and b[0]:
        return a[0] == b[0]
    nom_a = a[1] or a[0].split("@")[0]
    nom_b = b[1] or b[0].split("@")[0]
    return bool(nom_a and nom_b and (nom_a == nom_b or nom_a in nom_b or nom_b in nom_a))


@dataclass(frozen=True)
class Empreinte:
    expediteur: tuple
    moment: datetime.datetime  # avec fuseau
    objet: str | None  # `None` : objet cité illisible, il ne départage pas
    #: `ENVOI` : heure écrite dans le texte (bloc, « a écrit : ») ; `RECEPTION` : en-tête
    #: `Received` ; `None` : inconnue (date approchée).
    nature: str | None = None

    def correspond(self, autre):
        return (
            autre is not None
            and (self.objet is None or autre.objet is None or self.objet == autre.objet)
            and meme_expediteur(self.expediteur, autre.expediteur)
            and self._meme_moment(autre)
        )

    def _meme_moment(self, autre):
        natures = (self.nature, autre.nature)
        if natures == (ENVOI, ENVOI):
            return abs(self.moment.replace(second=0) - autre.moment.replace(second=0)) <= A_LA_MINUTE
        if set(natures) == {ENVOI, RECEPTION}:
            envoi, reception = (self, autre) if self.nature == ENVOI else (autre, self)
            return -AVANCE_D_HORLOGE <= reception.moment - envoi.moment <= TOLERANCE
        return abs(self.moment - autre.moment) <= TOLERANCE


def empreinte(expediteur, moment, objet, nature=None):
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=BRUXELLES)
    return Empreinte(expediteur_normalise(expediteur), moment, objet_normalise(objet), nature)


def empreinte_citee(expediteur, moment, objet):
    """L'empreinte d'un courriel **cité**. Un objet vide est un objet qu'on n'a pas pu
    lire (« … a écrit : », bloc Samsung d'une ligne) : il ne départage pas. L'objet de
    la réponse n'en tient pas lieu — on y ouvre souvent un nouveau fil en citant un
    courriel d'un autre (ACMEBAT, n° 210 cité par le n° 212)."""
    cite = empreinte(expediteur, moment, objet, ENVOI)
    if cite is not None and not cite.objet:
        cite = Empreinte(cite.expediteur, cite.moment, None, ENVOI)
    return cite


def nature_de_couche(analyse):
    if analyse.date_approchee:
        return None
    return ENVOI if analyse.forme == "texte" else RECEPTION


def empreinte_de_couche(analyse):
    return empreinte(analyse.expediteur, analyse.moment, analyse.titre, nature_de_couche(analyse))


def empreinte_d_evenement(evenement, expediteur, nature=None):
    """L'empreinte d'un évènement importé ; `expediteur` : celui noté à l'import."""
    heure = evenement.heure or datetime.time(0, 0)
    return empreinte(expediteur, datetime.datetime.combine(evenement.date, heure), evenement.title, nature)


#: La date d'une ligne « … a écrit : », avec le fuseau que certains y ajoutent : Yahoo
#: « 14:31:07 UTC+2 », Apple « CEST », Thunderbird « +0200 », Gmail allemand « 14:31 Uhr ».
_DATE_ECRITE = (
    r"(?P<date>.{6,80}?\d{1,2}[:h.]\d{2}(?::\d{2})?(?:\s*[ap]\.?m\.?)?(?:\s*uhr)?"
    r"(?:\s*\(?(?:gmt|utc)\s*(?:[+-]\d{1,2}(?::?\d{2})?)?\)?|\s+[+-]\d{4}|\s+(?:cet|cest|wet|west|bst)\b)?)"
)
#: L'auteur, que Gmail coupe parfois en deux lignes au milieu de l'adresse (« <\nx@y> »).
_AUTEUR = r"(?P<de>(?:[^\n]|\n[ \t>]*(?=\S)){1,200}?)"

#: « Le lun. 28 sept. 2026 à 20:29, ACME Bat <x@y> a écrit : » (Gmail, Apple Mail, Yahoo,
#: Thunderbird, Proton), « On Mon, Sep 28, 2026 at 8:29 PM ACME Bat <x@y> wrote: ».
A_ECRIT = re.compile(
    r"(?im)^[ \t>]*(?:le|on)\s+" + _DATE_ECRITE + r"[,\s]+" + _AUTEUR
    + r"\s+(?:a[\s>]+écrit|wrote)\s*:\s*$")
#: Le néerlandais et l'allemand nomment l'auteur après le verbe : « Op di 25 aug 2026 om
#: 14:31 schreef ACME Bat <x@y>: », « Am Di., 25. Aug. 2026 um 14:31 Uhr schrieb … : ».
SCHREEF = re.compile(
    r"(?im)^[ \t>]*(?:op|am)\s+" + _DATE_ECRITE + r"[,\s]+(?:schreef|schrieb)\s+" + _AUTEUR + r"\s*:\s*$")

@dataclass(frozen=True)
class Citee:
    """Un courriel cité, tel que le texte l'écrit, et son empreinte."""
    empreinte: Empreinte
    de: str
    objet: str  # vide quand illisible
    a: str


def citees(analyse):
    """Les courriels que cette couche cite, du plus récent (le premier cité, celui
    auquel elle répond) au plus ancien : chaque bloc « De : … Envoyé : … » du texte,
    puis chaque ligne « … a écrit : » et chaque bloc tenant sur une ligne (Samsung
    Email). Une citation sans date lisible est omise.

    Les objets des lignes « a écrit » ne sont pas écrits, ceux des blocs d'une ligne
    pas lisibles : vides, ils ne départagent pas (`empreinte_citee`)."""
    texte = analyse.texte or ""
    trouvees = []
    reste = texte
    while True:
        bloc = _bloc_de_transfert(reste)
        if not bloc:
            break
        champs = bloc[1]
        ecrite = date_en_toutes_lettres(champs.get("date", ""))
        if ecrite is not None:
            position = len(texte) - len(bloc[2])
            de, objet = champs.get("de", "").strip(), champs.get("objet", "").strip()
            trouvees.append((position, Citee(
                empreinte_citee(de, ecrite.replace(tzinfo=BRUXELLES), objet), de, objet, champs.get("a", ""))))
        if bloc[2] == reste:
            break
        reste = bloc[2]
    for m in (*A_ECRIT.finditer(texte), *SCHREEF.finditer(texte), *EN_UNE_LIGNE.finditer(texte)):
        ecrite = date_en_toutes_lettres(m.group("date"))
        if ecrite is not None:
            de = re.sub(r"<\s+", "<", re.sub(r"\s*\n[ \t>]*", " ", m.group("de"))).strip()
            trouvees.append((m.start(), Citee(empreinte_citee(de, ecrite.replace(tzinfo=BRUXELLES), ""), de, "", "")))
    # Un même bloc lu deux fois (bloc « De : … » et forme Samsung d'une ligne) ne compte
    # qu'une fois, avec son objet quand une des deux lectures l'a lu.
    uniques, rang = [], {}
    for _position, cite in sorted(trouvees, key=lambda t: t[0]):
        cle = (cite.empreinte.expediteur, cite.empreinte.moment)
        if cle not in rang:
            rang[cle] = len(uniques)
            uniques.append(cite)
        elif cite.objet and not uniques[rang[cle]].objet:
            uniques[rang[cle]] = cite
    return uniques


#: Ce qui reste au-dessus d'une citation et lui appartient : un séparateur (« ____ »,
#: « -----Original Message----- »), une ligne vide.
_ANNONCE = re.compile(r"(?im)(?:^[-_ >]*(?:%s|%s)?[-_ :]*$\n?)+\Z" % (TRANSFERTS, CITATIONS))


def texte_sans_citations(texte):
    """Le texte d'un courriel sans le courriel qu'il cite, ni ceux que celui-ci cite : tout
    ce qui suit la première citation (bloc « De : … Envoyé : … », ligne « … a écrit : »,
    bloc Samsung d'une ligne). Le texte entier s'il ne cite rien, ou s'il n'est que citation."""
    texte = texte or ""
    debuts = []
    bloc = bloc_et_position(texte)
    if bloc:
        debuts.append(bloc[0])
    for motif in (A_ECRIT, SCHREEF, EN_UNE_LIGNE):
        m = motif.search(texte)
        if m:
            debuts.append(m.start())
    if not debuts:
        return texte
    garde = _ANNONCE.sub("", texte[:min(debuts)]).rstrip()
    return garde or texte


def citations(analyse):
    """Les empreintes des courriels cités, dans l'ordre de `citees`."""
    return [c.empreinte for c in citees(analyse)]


def citation(analyse):
    """Le courriel auquel cette couche répond — le premier qu'elle cite —, ou `None`.

    Une réponse cite le message auquel elle répond ; un transfert, ce qu'il transfère
    (déjà relié par `transfert_de`). Sans date lisible, pas d'empreinte : rien à comparer.
    """
    trouvees = citations(analyse)
    return trouvees[0] if trouvees else None
