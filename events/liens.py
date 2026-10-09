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

from .courriels import BRUXELLES, _bloc_de_transfert, date_en_toutes_lettres

TOLERANCE = datetime.timedelta(minutes=20)

#: Tous les préfixes, transfert **et** réponse : « RE: sdb » et « sdb » sont le même fil.
PREFIXES = re.compile(r"^\s*((tr|fw|fwd|wg|doorst|trans|re|aw|antw|sv)\s*:\s*)+", re.IGNORECASE)
ADRESSE = re.compile(r"[\w.+'-]+@[\w-]+(?:\.[\w-]+)+")


def _sans_accents(texte):
    return "".join(c for c in unicodedata.normalize("NFKD", texte or "") if not unicodedata.combining(c))


def objet_normalise(objet):
    return " ".join(_sans_accents(PREFIXES.sub("", objet or "")).lower().split())


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
    objet: str

    def correspond(self, autre):
        return (
            autre is not None
            and self.objet == autre.objet
            and meme_expediteur(self.expediteur, autre.expediteur)
            and abs(self.moment - autre.moment) <= TOLERANCE
        )


def empreinte(expediteur, moment, objet):
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=BRUXELLES)
    return Empreinte(expediteur_normalise(expediteur), moment, objet_normalise(objet))


def empreinte_de_couche(analyse):
    return empreinte(analyse.expediteur, analyse.moment, analyse.titre)


def empreinte_d_evenement(evenement, expediteur):
    """L'empreinte d'un évènement importé ; `expediteur` : celui noté à l'import."""
    heure = evenement.heure or datetime.time(0, 0)
    return empreinte(expediteur, datetime.datetime.combine(evenement.date, heure), evenement.title)


#: « Le lun. 28 sept. 2026 à 20:29, ACME Bat <x@y> a écrit : » (Gmail, Apple Mail),
#: « On Mon, Sep 28, 2026 at 8:29 PM ACME Bat <x@y> wrote: », « Op … schreef … : ».
A_ECRIT = re.compile(
    r"(?im)^[ >]*(?:le|on|op|am)\s+(?P<date>.{6,80}?\d{1,2}[:h.]\d{2}(?:\s*[ap]\.?m\.?)?)[,\s]+"
    r"(?P<de>[^\n]{1,160}?)\s+(?:a écrit|wrote|schreef|schrieb)\s*:\s*$")


def citations(analyse):
    """Les courriels que cette couche cite, du plus récent (le premier cité, celui
    auquel elle répond) au plus ancien : chaque bloc « De : … Envoyé : … » du texte,
    puis chaque ligne « … a écrit : ». Une citation sans date lisible est omise.

    Les objets des lignes « a écrit » ne sont pas écrits : l'objet de la réponse
    elle-même en tient lieu (même fil)."""
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
            trouvees.append((position, empreinte(
                champs.get("de", ""), ecrite.replace(tzinfo=BRUXELLES), champs.get("objet", ""))))
        if bloc[2] == reste:
            break
        reste = bloc[2]
    for m in A_ECRIT.finditer(texte):
        ecrite = date_en_toutes_lettres(m.group("date"))
        if ecrite is not None:
            trouvees.append((m.start(), empreinte(
                m.group("de"), ecrite.replace(tzinfo=BRUXELLES), analyse.titre)))
    return [e for _position, e in sorted(trouvees, key=lambda t: t[0])]


def citation(analyse):
    """Le courriel auquel cette couche répond — le premier qu'elle cite —, ou `None`.

    Une réponse cite le message auquel elle répond ; un transfert, ce qu'il transfère
    (déjà relié par `transfert_de`). Sans date lisible, pas d'empreinte : rien à comparer.
    """
    trouvees = citations(analyse)
    return trouvees[0] if trouvees else None
