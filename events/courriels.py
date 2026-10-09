"""Lire un courriel transféré et en tirer un évènement.

Ce module ne parle ni au réseau ni à la base : il prend les octets d'un
courriel (MIME) et rend une `Analyse` — titre, moment, texte, pièces. La
lecture de la boîte (`events/graph.py`) et l'enregistrement (`importer`, plus
bas) sont à part, pour que l'analyse se teste sur des courriels écrits à la
main ou déposés dans `exemples_courriels/`.

**Trois formes de courriel**, reconnues dans cet ordre :

1. **Transféré en pièce jointe** — le courriel d'origine voyage intact
   (`message/rfc822`) : son en-tête `Received` le plus récent donne la vraie
   date de **réception**, son objet et son texte sont les siens.
2. **Transféré dans le texte** — Outlook (français, anglais, néerlandais) ou
   Gmail recopie un bloc « De : … Envoyé : … Objet : … ». Seulement si
   l'objet est celui d'un transfert ou qu'une ligne l'annonce : une réponse
   cite le message précédent sous le même bloc (`est_un_transfert`). Seule la date
   d'**envoi** de l'original y figure (à quelques secondes de la réception),
   à l'heure de Bruxelles. Le texte au-dessus du bloc est le commentaire de
   la personne qui a transféré ; il est gardé, séparé.
3. **Envoyé directement** à l'alias — sa propre date de réception.

Si aucune date d'origine n'est lisible, c'est la date de réception du
courriel transféré qui sert, et l'`Analyse` le signale (`date_approchee`).
"""

import datetime
import email
import hashlib
import email.policy
import html
import re
from dataclasses import dataclass, field
from email.utils import getaddresses, parsedate_to_datetime
from zoneinfo import ZoneInfo

from django.utils.html import strip_tags

BRUXELLES = ZoneInfo("Europe/Brussels")

#: Préfixes de transfert et de réponse retirés du titre, dans les trois langues
#: et chez les principaux clients de messagerie.
PREFIXES_TRANSFERT = re.compile(
    r"^\s*((tr|fw|fwd|wg|doorst|trans)\s*:\s*)+", re.IGNORECASE
)

#: Mois écrits en toutes lettres, abrégés ou non, en français, anglais, néerlandais.
MOIS = {
    "janvier": 1, "janv": 1, "january": 1, "jan": 1, "januari": 1,
    "février": 2, "fevrier": 2, "févr": 2, "fevr": 2, "february": 2, "feb": 2, "februari": 2,
    "mars": 3, "march": 3, "mar": 3, "maart": 3, "mrt": 3,
    "avril": 4, "avr": 4, "april": 4, "apr": 4,
    "mai": 5, "may": 5, "mei": 5,
    "juin": 6, "june": 6, "jun": 6, "juni": 6,
    "juillet": 7, "juil": 7, "july": 7, "jul": 7, "juli": 7,
    "août": 8, "aout": 8, "august": 8, "aug": 8, "augustus": 8,
    "septembre": 9, "sept": 9, "september": 9, "sep": 9,
    "octobre": 10, "oct": 10, "october": 10, "oktober": 10, "okt": 10,
    "novembre": 11, "nov": 11, "november": 11,
    "décembre": 12, "decembre": 12, "déc": 12, "dec": 12, "december": 12,
}

#: Les libellés des lignes d'un bloc de transfert, par rôle.
LIBELLES = {
    "de": ("de", "from", "van"),
    "date": ("envoyé", "envoye", "sent", "verzonden", "date", "datum"),
    "a": ("à", "a", "to", "aan"),
    "objet": ("objet", "subject", "onderwerp"),
}

LIGNE_DE_BLOC = re.compile(r"^\s*\*?([A-Za-zÀ-ÿ]+)\*?\s*:\s*(.*)$")


@dataclass
class Piece:
    nom: str
    contenu: bytes
    type_mime: str


@dataclass
class Analyse:
    titre: str
    moment: datetime.datetime  # conscient du fuseau, ramené à Bruxelles
    expediteur: str  # l'auteur du courriel d'origine, « Nom <adresse> » quand le nom est connu
    destinataires: str
    texte: str  # le texte du courriel d'origine
    commentaire: str = ""  # ce que la personne qui a transféré a écrit au-dessus
    pieces: list = field(default_factory=list)
    eml: bytes = b""  # le courriel d'origine complet quand il est joint, sinon le courriel reçu
    message_id: str = ""  # identifiant du courriel d'origine (ou reçu), pour ne pas importer deux fois
    forme: str = "direct"  # "joint", "texte" ou "direct"
    date_approchee: bool = False
    transfert: bool = False  # cette couche transfère la couche du dessous (`analyser_en_couches`)
    cle: str = ""  # clé d'une couche lue dans le texte, qui n'a pas de Message-ID à elle
    cle_ancienne: str = ""  # la clé qu'un import d'avant les couches lui a donnée


def lire(octets):
    return email.message_from_bytes(octets, policy=email.policy.default)


def adresses(message, *entetes):
    """Toutes les adresses des en-têtes donnés, en minuscules."""
    valeurs = []
    for entete in entetes:
        valeurs.extend(str(v) for v in message.get_all(entete, []))
    return [adresse.lower() for _nom, adresse in getaddresses(valeurs) if adresse]


def date_de_reception(message):
    """Le moment où le courriel est arrivé : le `Received` le plus récent.

    Chaque serveur traversé ajoute un `Received` *au-dessus* des autres ; le
    premier de la liste est donc le dernier saut, la remise dans la boîte.
    Sa date suit le dernier « ; ». À défaut, l'en-tête `Date`.
    """
    for recu in message.get_all("Received", []):
        _avant, _sep, date = str(recu).rpartition(";")
        try:
            return parsedate_to_datetime(date.strip())
        except (TypeError, ValueError, IndexError):
            continue
    if message["Date"]:
        try:
            return parsedate_to_datetime(str(message["Date"]))
        except (TypeError, ValueError, IndexError):
            pass
    return None


def date_en_toutes_lettres(texte):
    """« lundi 5 août 2026 14:32 », « Monday, August 5, 2026 2:32 PM »,
    « maandag 5 augustus 2026 14:32 », « mar. 5 août 2026 à 14:32 »,
    « 05/08/2026 14:32 »… → datetime naïf, ou `None`.

    Le mois est cherché **contre le jour et l'année** (« 5 août 2026 »,
    « August 5, 2026 ») et non n'importe où : Gmail abrège « mardi » en
    « mar. », qui est aussi l'abréviation anglaise de mars.

    Ces dates sont écrites à l'heure de la personne qui a transféré : on les
    lit comme l'heure de Bruxelles.
    """
    t = " " + texte.lower().replace(",", " ") + " "
    t = re.sub(r"\s+(à|om|at)\s+", " ", t)
    jour = mois = annee = None

    numerique = re.search(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})\b", t)
    if numerique:
        jour, mois, annee = int(numerique.group(1)), int(numerique.group(2)), int(numerique.group(3))
    else:
        for motif, ordre in (
            (r"\b(\d{1,2})\.?\s+([a-zéû]+)\.?\s+(\d{4})\b", "jma"),  # 5 août 2026
            (r"\b([a-zéû]+)\.?\s+(\d{1,2})\s+(\d{4})\b", "mja"),      # August 5 2026
        ):
            for m in re.finditer(motif, t):
                if ordre == "jma":
                    j, nom, a = m.group(1), m.group(2), m.group(3)
                else:
                    nom, j, a = m.group(1), m.group(2), m.group(3)
                if nom in MOIS:
                    jour, mois, annee = int(j), MOIS[nom], int(a)
                    break
            if mois:
                break
    if not mois:
        return None

    h = m_ = s = 0
    heure = re.search(r"\b(\d{1,2})[:h](\d{2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?", t)
    if heure:
        h, m_, s = int(heure.group(1)), int(heure.group(2)), int(heure.group(3) or 0)
        suffixe = (heure.group(4) or "").replace(".", "")
        if suffixe == "pm" and h < 12:
            h += 12
        elif suffixe == "am" and h == 12:
            h = 0
    try:
        return datetime.datetime(annee, mois, jour, h, m_, s)
    except ValueError:
        return None


#: La suite d'un corps qu'Exchange a détachée en « pièces » ATT00001.txt, ATT00003.htm…
#: quand un serveur a touché au message en route. La citation du courriel précédent
#: s'y trouve souvent (n° 12 et 18 du dossier réel) : c'est du texte, pas une pièce.
SUITE_EXCHANGE = re.compile(r"^ATT\d{5}\.(txt|htm|html)$", re.IGNORECASE)


def _texte_de_partie(partie):
    try:
        contenu = partie.get_content()
    except (LookupError, ValueError):
        return ""
    if not isinstance(contenu, str):
        return ""
    if partie.get_content_type() == "text/html":
        contenu = re.sub(r"(?is)<(script|style).*?</\1>", "", contenu)
        contenu = re.sub(r"(?i)<br\s*/?>|</(p|div|li|tr|h[1-6])>", "\n", contenu)
        contenu = html.unescape(strip_tags(contenu))
    contenu = contenu.replace("\r\n", "\n").replace("\xa0", " ")
    return re.sub(r"\n{3,}", "\n\n", contenu).strip()


def _suites_exchange(message):
    """Le texte des parties ATTnnnnn, dans l'ordre : le texte brut d'abord ; le HTML
    seulement s'il n'y a aucune suite en texte brut (Exchange met souvent les deux)."""
    parties = [p for p in message.iter_attachments() if SUITE_EXCHANGE.match(p.get_filename() or "")]
    brutes = [p for p in parties if p.get_content_type() == "text/plain"]
    return [t for t in (_texte_de_partie(p) for p in (brutes or parties)) if t]


def corps_en_texte(message):
    """Le texte d'un courriel : la partie texte, ou le HTML ramené à du texte, plus la
    suite qu'Exchange en aurait détachée (sans répéter ce que le corps contient déjà)."""
    partie = message.get_body(preferencelist=("plain", "html"))
    contenu = _texte_de_partie(partie) if partie is not None else ""
    for suite in _suites_exchange(message):
        if suite not in contenu:
            contenu = (contenu + "\n\n" + suite).strip()
    return contenu


def pieces_jointes(message, ignorer=()):
    """Les pièces jointes d'un message, sans les images de signature.

    Une image incluse dans le corps (`Content-ID`, sans nom de fichier ou en
    `inline`) est un logo ou une signature, pas une pièce. Un courriel joint
    (`message/rfc822`) n'est pas rendu ici : il est traité comme l'original.
    """
    pieces = []
    for partie in message.iter_attachments():
        if partie in ignorer or partie.get_content_type() == "message/rfc822":
            continue
        nom = partie.get_filename()
        if nom and SUITE_EXCHANGE.match(nom):
            continue  # la suite du corps, lue par `corps_en_texte`
        inline = (partie.get_content_disposition() == "inline") or (partie["Content-ID"] and not nom)
        if partie.get_content_maintype() == "image" and inline:
            continue
        if not nom:
            continue
        pieces.append(Piece(nom=nom, contenu=partie.get_payload(decode=True) or b"",
                            type_mime=partie.get_content_type()))
    return pieces


def _courriel_joint(message):
    for partie in message.iter_attachments():
        if partie.get_content_type() == "message/rfc822":
            interne = partie.get_content()
            if isinstance(interne, email.message.Message):
                return partie, interne
    return None, None


def _bloc_de_transfert(texte):
    """Le bloc « De : … Envoyé : … Objet : … » d'un transfert dans le texte.

    Rend `(commentaire, champs, suite)` : le texte au-dessus du bloc, les
    champs lus, le texte d'origine en dessous. `None` si aucun bloc.
    """
    lignes = texte.split("\n")
    for i, ligne in enumerate(lignes):
        m = LIGNE_DE_BLOC.match(ligne)
        if not m or m.group(1).lower() not in LIBELLES["de"]:
            continue
        champs, j = {}, i
        while j < len(lignes) and j < i + 8:
            m2 = LIGNE_DE_BLOC.match(lignes[j])
            if not m2:
                if lignes[j].strip() == "" and champs:
                    break
                j += 1
                continue
            cle = m2.group(1).lower()
            for role, libelles in LIBELLES.items():
                if cle in libelles and role not in champs:
                    champs[role] = m2.group(2).strip()
            j += 1
        if "date" in champs and ("objet" in champs or "a" in champs):
            commentaire = "\n".join(lignes[:i])
            commentaire = re.sub(r"(?im)^[-_ ]*(message transféré|forwarded message|doorgestuurd bericht|original message|message d'origine|oorspronkelijk bericht)[-_ ]*$", "", commentaire)
            commentaire = re.sub(r"(?m)^[-_]{5,}\s*$", "", commentaire).strip()
            return commentaire, champs, "\n".join(lignes[j:]).strip()
    return None


#: Une ligne qui annonce un transfert dans le texte (Gmail, Outlook, Apple Mail).
MARQUE_DE_TRANSFERT = re.compile(
    r"(?im)^[-_ >]*(message transféré|forwarded message|begin forwarded message|"
    r"doorgestuurd bericht|weitergeleitete nachricht)\b"
)


def est_un_transfert(sujet, texte):
    """Vrai si le courriel *transfère* un autre courriel dans son texte.

    Un bloc « De : … Envoyé : … » ne suffit pas : une **réponse** cite le
    message précédent sous le même bloc. Prendre une réponse pour un
    transfert donnerait à l'évènement la date, l'auteur et le texte du
    message cité, et perdrait la réponse elle-même. On exige donc un objet
    de transfert (TR:, FW:, Fwd:…) ou une ligne « message transféré ».
    """
    return bool(PREFIXES_TRANSFERT.match(sujet or "")) or bool(MARQUE_DE_TRANSFERT.search(texte or ""))


def nettoyer_titre(sujet):
    sujet = PREFIXES_TRANSFERT.sub("", sujet or "").strip()
    return sujet[:255] or "(sans objet)"


def _joindre(*textes):
    return "\n\n".join(t for t in textes if t)


def _cle_de_couche(champs):
    """Une couche lue dans le texte n'a pas de Message-ID : son auteur, sa date
    et son objet l'identifient."""
    brut = "|".join((champs.get("de", ""), champs.get("date", ""), champs.get("objet", ""))).lower()
    return "couche:" + hashlib.sha256(" ".join(brut.split()).encode("utf-8")).hexdigest()


def _couches(octets, *, enveloppe, ignorer, repli):
    """Les couches d'un courriel, de la plus ancienne (l'origine) à la plus récente.

    `enveloppe` : ce courriel est celui reçu par la boîte. Il n'est alors une
    couche que s'il s'adresse à quelqu'un d'autre que les adresses `ignorer`
    (l'alias, la boîte) ; sinon ce n'est qu'une enveloppe, dont le mot et les
    pièces passent à la couche du dessous. `ignorer=None` : toujours une
    enveloppe (l'ancienne lecture, `analyser`).
    """
    recu = lire(octets)
    reception = date_de_reception(recu) or repli
    sujet = str(recu["Subject"] or "")
    message_id = str(recu["Message-ID"] or "").strip()

    def ici(texte, pieces):
        return Analyse(
            titre=(sujet.strip() or "(sans objet)")[:255],
            moment=reception,
            expediteur=str(recu["From"] or ""),
            destinataires=", ".join(adresses(recu, "To", "Cc")),
            texte=texte, pieces=pieces, eml=octets, message_id=message_id,
            forme="direct", date_approchee=reception is None, transfert=True,
        )

    partie_jointe, original = _courriel_joint(recu)
    if original is not None:
        dessous = _couches(original.as_bytes(), enveloppe=False, ignorer=ignorer, repli=reception)
        for couche in dessous:
            if couche.forme == "direct":
                couche.forme = "joint"
        couche = ici(corps_en_texte(recu), pieces_jointes(recu, ignorer=(partie_jointe,)))
        return _empiler(dessous, couche, recu, enveloppe, ignorer)

    texte = corps_en_texte(recu)
    bloc = _bloc_de_transfert(texte) if est_un_transfert(sujet, texte) else None
    if bloc is None:
        # Envoyé directement : le courriel lui-même, toujours une couche.
        return [Analyse(
            titre=nettoyer_titre(sujet), moment=reception, expediteur=str(recu["From"] or ""),
            destinataires=", ".join(adresses(recu, "To", "Cc")), texte=texte,
            pieces=pieces_jointes(recu), eml=octets, message_id=message_id, forme="direct",
            date_approchee=date_de_reception(recu) is None,
        )]

    # Transfert dans le texte : un bloc « De : … Envoyé : … » par couche. Tant que
    # le bloc lu annonce lui-même un transfert, on descend d'une couche.
    blocs = [bloc]
    while est_un_transfert(blocs[-1][1].get("objet", ""), "") and _bloc_de_transfert(blocs[-1][2]):
        blocs.append(_bloc_de_transfert(blocs[-1][2]))
    dessous = []
    for k, (_mot, champs, suite) in enumerate(blocs):
        origine = k == len(blocs) - 1
        ecrite = date_en_toutes_lettres(champs.get("date", ""))
        moment = ecrite.replace(tzinfo=BRUXELLES) if ecrite else None
        objet = champs.get("objet") or sujet
        dessous.insert(0, Analyse(
            titre=nettoyer_titre(objet) if origine else (objet.strip() or "(sans objet)")[:255],
            moment=moment or reception,
            expediteur=champs.get("de", ""),
            destinataires=champs.get("a", ""),
            # Le texte d'une couche intermédiaire est le mot écrit au-dessus du bloc suivant.
            texte=suite if origine else blocs[k + 1][0],
            pieces=pieces_jointes(recu) if origine else [],
            eml=octets,
            forme="texte",
            date_approchee=moment is None,
            transfert=not origine,
            cle=_cle_de_couche(champs),
            # Avant les couches, l'origine prenait le Message-ID du courriel reçu.
            cle_ancienne=message_id if origine else "",
        ))
    return _empiler(dessous, ici(blocs[0][0], []), recu, enveloppe, ignorer)


def _empiler(dessous, couche, recu, enveloppe, ignorer):
    """Ajoute `couche` au-dessus de `dessous`, sauf si ce n'est qu'une enveloppe."""
    autres = None if ignorer is None else (
        {a.lower() for a in adresses(recu, "To", "Cc")} - {a.lower() for a in ignorer}
    )
    if not enveloppe or autres:
        return dessous + [couche]
    haut = dessous[-1]
    haut.commentaire = _joindre(couche.texte, haut.commentaire)
    haut.pieces = haut.pieces + couche.pieces
    return dessous


def _finaliser(analyse):
    if analyse.moment is None:
        analyse.moment = datetime.datetime.now(tz=BRUXELLES)
        analyse.date_approchee = True
    analyse.moment = analyse.moment.astimezone(BRUXELLES)
    return analyse


def analyser_en_couches(octets, ignorer=()):
    """Le courriel reçu → une `Analyse` par couche, de l'origine au plus récent.

    Une mise en demeure reçue, transférée à l'avocate, puis ce transfert
    envoyé à l'alias : deux couches — la mise en demeure, et le transfert à
    l'avocate (`transfert=True`, son mot pour texte). L'enveloppe envoyée à
    l'alias n'en est une que si elle s'adresse aussi à quelqu'un d'autre que
    les adresses `ignorer` (les alias, la boîte d'import).
    """
    return [_finaliser(c) for c in _couches(octets, enveloppe=True, ignorer=ignorer, repli=None)]


def analyser(octets, repli=None):
    """Le courriel reçu → `Analyse` de son courriel d'**origine**.

    L'enveloppe d'un transfert ne compte pas : seul le courriel transféré
    fait l'évènement. Et un transfert peut en contenir un autre — la mise en
    demeure qu'on a transférée à son avocate, puis à la boîte d'import —
    d'où la **récursion** : chaque couche est déballée jusqu'au courriel
    d'origine, en pièce jointe comme dans le texte. Les mots écrits à chaque
    couche (« Pour le dossier », « Bonjour Maître… ») sont gardés, du plus
    récent au plus ancien, dans `commentaire`. `analyser_en_couches` garde,
    elle, chaque couche.

    `repli` : la date à prendre si celle du courriel est illisible — celle de
    la couche qui l'enveloppait.
    """
    couches = _couches(octets, enveloppe=True, ignorer=None, repli=repli)
    origine = couches[0]
    mots = []
    for couche in reversed(couches[1:]):
        mots += [couche.commentaire, couche.texte]
        origine.pieces = origine.pieces + couche.pieces
    origine.commentaire = _joindre(*mots, origine.commentaire)
    if origine.cle_ancienne:
        origine.message_id = origine.cle_ancienne
    origine.transfert = False
    return _finaliser(origine)


def en_markdown(analyse, refusees=()):
    """La version texte du courriel, pour la recherche et l'IA.

    En-têtes en tête, texte d'origine, puis le commentaire du transfert et la
    liste des pièces. Du Markdown simple : lisible tel quel, et par un outil.
    """
    lignes = [
        "# %s" % analyse.titre,
        "",
        "- De : %s" % (analyse.expediteur or "—"),
        "- À : %s" % (analyse.destinataires or "—"),
        "- Date : %s%s" % (analyse.moment.strftime("%Y-%m-%d %H:%M:%S %z"),
                           " (approchée)" if analyse.date_approchee else ""),
        "- Identifiant : %s" % (analyse.message_id or "—"),
        "- Forme : %s" % analyse.forme,
        "",
        "## Texte",
        "",
        analyse.texte or "—",
    ]
    if analyse.commentaire:
        lignes += ["", "## Commentaire de la personne qui a transféré", "", analyse.commentaire]
    if analyse.pieces:
        lignes += ["", "## Pièces jointes", ""]
        lignes += ["- %s (%s, %d octets)" % (p.nom, p.type_mime, len(p.contenu)) for p in analyse.pieces]
    if refusees:
        lignes += ["", "## Pièces jointes non importées", ""]
        lignes += ["- %s — %s" % (nom, raison) for nom, raison in refusees]
    return "\n".join(lignes) + "\n"


def texte_en_html(texte):
    """Du texte en paragraphes HTML échappés, prêt pour `nettoyer`."""
    paragraphes = [p.strip() for p in re.split(r"\n\s*\n", texte or "") if p.strip()]
    return "".join(
        "<p>%s</p>" % "<br>".join(html.escape(ligne) for ligne in p.split("\n"))
        for p in paragraphes
    )
