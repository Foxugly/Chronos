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


def citation(analyse):
    """Le courriel que cette couche cite sous un bloc « De : … Envoyé : … », ou `None`.

    Une réponse cite le message auquel elle répond ; un transfert, ce qu'il transfère
    (déjà relié par `transfert_de`). Sans date lisible, pas d'empreinte : rien à comparer.
    """
    bloc = _bloc_de_transfert(analyse.texte or "")
    if not bloc:
        return None
    champs = bloc[1]
    ecrite = date_en_toutes_lettres(champs.get("date", ""))
    if ecrite is None:
        return None
    return empreinte(champs.get("de", ""), ecrite.replace(tzinfo=BRUXELLES), champs.get("objet", ""))
