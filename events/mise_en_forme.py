"""La description d'un courriel importé, mise en forme : gras, italique, liens, citations.

Le texte d'une couche (`Analyse.texte`) sert à tout reconnaître — transferts, citations,
dates ; il vient souvent de la partie texte brut. La description, elle, se lit à l'écran :
elle vient de préférence de la partie **HTML** du même courriel, ramenée à une liste de
**lignes** (`Ligne`) : le texte de la ligne, son HTML (mise en forme en ligne seulement)
et sa profondeur de citation (`blockquote`, ou `>` en texte brut).

Le texte des lignes HTML se lit comme `courriels._texte_de_partie` le ferait : les mêmes
fonctions y trouvent les mêmes blocs « De : … Envoyé : … » et lignes « … a écrit : », en
numéros de ligne. La couche se retrouve dans le HTML en comparant les mots : le morceau
de HTML qui ressemble le plus au texte de la couche, s'il lui ressemble assez. Sinon —
pas de HTML, ou rien d'assez proche —, la description est faite du texte, comme avant :
on ne montre jamais un morceau de HTML dont on n'est pas sûr qu'il soit le bon.

Le HTML produit ne contient que `p`, `br`, `strong`, `em`, `u`, `s`, `code`, `a` et
`blockquote`, et passe encore par `sanitize.nettoyer`.
"""

import html
import re
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
from html.parser import HTMLParser

from .courriels import SUITE_EXCHANGE, _compact, _decoder, bloc_en_lignes, lire
from .liens import debut_des_citations, est_une_annonce

#: Ressemblance minimale (mots, de 0 à 1) entre le texte d'une couche et le morceau de
#: HTML retenu. Le texte brut écrit les liens en clair (`texte <https://…>`) : le HTML,
#: sans eux, en diffère de quelques mots.
RESSEMBLANCE = 0.7


@dataclass(frozen=True)
class Ligne:
    texte: str
    html: str
    profondeur: int = 0
    #: La ligne finit un paragraphe du HTML (`</p>`) : sans ligne vide dans le texte, où
    #: elle déplacerait les blocs « De : … », elle en ferme un dans le rendu.
    fin_de_paragraphe: bool = False

    @property
    def vide(self):
        return not self.texte.strip(" \t>")


# ------------------------------------------------------------------ lecture du HTML

#: Les balises qui terminent une ligne, comme dans `courriels._texte_de_partie`.
_FIN_DE_LIGNE = {"p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre", "textarea"}
_PARAGRAPHES = {"p", "h1", "h2", "h3", "h4", "h5", "h6"}
_IGNOREES = {"script", "style", "head", "title"}
#: La mise en forme gardée, par balise d'origine.
_EN_LIGNE = {"b": "strong", "strong": "strong", "i": "em", "em": "em", "u": "u", "s": "s",
             "strike": "s", "del": "s", "code": "code", "a": "a"}
_SCHEMAS = ("http://", "https://", "mailto:")


class _Lecteur(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.lignes = []
        self.texte, self.html = [], []
        self.ouvertes = []  # (balise, balise ouvrante en HTML)
        self.profondeur = 0
        self.profondeur_ligne = None
        self.ignore = 0
        self.pre = 0

    def _fin(self, paragraphe=False):
        texte = "".join(self.texte).replace("\xa0", " ").strip()
        contenu = "".join(self.html) + "".join("</%s>" % b for b, _ in reversed(self.ouvertes))
        profondeur = self.profondeur if self.profondeur_ligne is None else self.profondeur_ligne
        self.lignes.append(Ligne(texte, contenu.replace("\xa0", " ").strip() if texte else "", profondeur,
                                 paragraphe))
        self.texte, self.html = [], [o for _, o in self.ouvertes]
        self.profondeur_ligne = None

    def _ecrire(self, texte):
        if not texte:
            return
        if self.profondeur_ligne is None and texte.strip():
            self.profondeur_ligne = self.profondeur
        self.texte.append(texte)
        self.html.append(html.escape(texte, quote=False))

    def handle_starttag(self, balise, attributs):
        if balise in _IGNOREES:
            self.ignore += 1
        if self.ignore:
            return
        if balise == "br":
            self._fin()
        elif balise == "blockquote":
            self._fin()
            self.profondeur += 1
        elif balise == "pre" or balise == "textarea":
            self.pre += 1
        elif balise == "li":
            self._ecrire("• ")
        elif balise in _EN_LIGNE:
            nom = _EN_LIGNE[balise]
            ouvrante = "<%s>" % nom
            if nom == "a":
                cible = dict(attributs).get("href") or ""
                if not cible.lower().startswith(_SCHEMAS):
                    return  # un lien sans cible sûre : son texte seul
                ouvrante = '<a href="%s">' % html.escape(cible)
            self.ouvertes.append((nom, ouvrante))
            self.html.append(ouvrante)

    def handle_startendtag(self, balise, attributs):
        self.handle_starttag(balise, attributs)
        if balise != "br" and balise in _EN_LIGNE:
            self.handle_endtag(balise)

    def handle_endtag(self, balise):
        if balise in _IGNOREES:
            self.ignore = max(0, self.ignore - 1)
            return
        if self.ignore:
            return
        if balise in _FIN_DE_LIGNE:
            self._fin(paragraphe=balise in _PARAGRAPHES)
            if balise == "blockquote":
                self.profondeur = max(0, self.profondeur - 1)
            elif balise in ("pre", "textarea"):
                self.pre = max(0, self.pre - 1)
        elif balise in _EN_LIGNE:
            nom = _EN_LIGNE[balise]
            for k in range(len(self.ouvertes) - 1, -1, -1):
                if self.ouvertes[k][0] == nom:
                    fermer = self.ouvertes[k:]
                    del self.ouvertes[k:]
                    self.html.extend("</%s>" % b for b, _ in reversed(fermer))
                    # Les balises ouvertes après celle-ci restent ouvertes.
                    for b, o in fermer[1:]:
                        self.ouvertes.append((b, o))
                        self.html.append(o)
                    break

    def handle_data(self, donnees):
        if self.ignore:
            return
        if self.pre:
            morceaux = donnees.replace("\r\n", "\n").split("\n")
            for k, morceau in enumerate(morceaux):
                if k:
                    self._fin()
                self._ecrire(morceau)
        else:
            self._ecrire(re.sub(r"\s+", " ", donnees))

    def close(self):
        super().close()
        if self.texte or self.html:
            self._fin()


def _resserrer(lignes):
    """Une seule ligne vide d'affilée, aucune au début ni à la fin."""
    resserrees = []
    for ligne in lignes:
        if ligne.vide and (not resserrees or resserrees[-1].vide):
            continue
        resserrees.append(Ligne("", "", ligne.profondeur) if ligne.vide else ligne)
    while resserrees and resserrees[-1].vide:
        resserrees.pop()
    return resserrees


def lignes_du_html(source):
    lecteur = _Lecteur()
    lecteur.feed(source or "")
    lecteur.close()
    return _resserrer(lecteur.lignes)


_CITEE = re.compile(r"^[ \t]*((?:>[ \t]?)+)")


def lignes_du_texte(texte):
    """Un texte brut en lignes : une ligne citée (`> …`) l'est par sa profondeur."""
    lignes = []
    for ligne in (texte or "").replace("\r\n", "\n").split("\n"):
        m = _CITEE.match(ligne)
        profondeur = m.group(1).count(">") if m else 0
        sans = ligne[m.end():] if m else ligne
        lignes.append(Ligne(ligne, html.escape(sans.strip(), quote=False), profondeur))
    return _resserrer(lignes)


def texte_de(lignes):
    return "\n".join(ligne.texte for ligne in lignes)


# ------------------------------------------------------------- retrouver la couche

def _mots(texte):
    return re.findall(r"\w+", (texte or "").lower())


def _ressemblance(a, b):
    if not a and not b:
        return 1.0
    comparaison = SequenceMatcher(None, a, b, autojunk=False)
    if comparaison.real_quick_ratio() < RESSEMBLANCE or comparaison.quick_ratio() < RESSEMBLANCE:
        return 0.0
    return comparaison.ratio()


def _sans_annonces(lignes):
    """Sans les séparateurs et lignes vides qui bordent un morceau."""
    debut, fin = 0, len(lignes)
    while debut < fin and est_une_annonce(lignes[debut].texte):
        debut += 1
    while fin > debut and est_une_annonce(lignes[fin - 1].texte):
        fin -= 1
    return lignes[debut:fin]


def _morceaux(lignes):
    """Les morceaux où une couche peut se trouver : tout, puis autour de chaque bloc de
    transfert (au-dessus, au-dessous), de bloc en bloc."""
    yield lignes
    reste = lignes
    for _ in range(12):
        trouve = bloc_en_lignes(texte_de(reste))
        if trouve is None:
            return
        i, j, niveau = trouve[:3]
        yield _sans_annonces(reste[:i])
        reste = [replace(ligne, profondeur=max(0, ligne.profondeur - niveau)) for ligne in reste[j:]]
        yield _resserrer(_sans_annonces(reste))


def _html_du_courriel(message):
    """Le HTML du courriel et celui de la suite qu'Exchange en aurait détachée."""
    corps = message.get_body(preferencelist=("html",))
    if corps is None:
        return None
    lignes = lignes_du_html(_decoder(corps))
    for partie in message.iter_attachments():
        if SUITE_EXCHANGE.match(partie.get_filename() or "") and partie.get_content_type() == "text/html":
            suite = lignes_du_html(_decoder(partie))
            if _compact(texte_de(suite)) not in _compact(texte_de(lignes)):
                lignes = lignes + [Ligne("", "")] + suite
    return lignes


def lignes_de_couche(analyse):
    """Les lignes HTML de cette couche, ou `None` si on ne les retrouve pas avec assez
    de certitude dans le HTML du courriel (`analyse.eml`)."""
    if not analyse.eml or not analyse.texte:
        return None
    try:
        lignes = _html_du_courriel(lire(analyse.eml))
    except Exception:  # un HTML illisible : la description vient du texte, comme avant
        return None
    if not lignes:
        return None
    cible = _mots(analyse.texte)
    meilleur, score = None, 0.0
    for morceau in _morceaux(lignes):
        mots = _mots(texte_de(morceau))
        if not _memes_bords(cible, mots):
            continue
        s = _ressemblance(cible, mots)
        if s > score:
            meilleur, score = morceau, s
    return meilleur if score >= RESSEMBLANCE else None


#: Le nombre de mots comparés au début et à la fin, et leur ressemblance minimale.
BORDS, RESSEMBLANCE_DES_BORDS = 30, 0.6


def _memes_bords(cible, mots):
    """Le morceau commence et finit comme le texte. Sans cela, le texte d'une réponse —
    qui contient toute la chaîne de ses citations — ressemble presque autant au seul
    courriel cité, sans les mots de la réponse (ACMEBAT n° 12 : 0,957 contre 0,954)."""
    def semblables(a, b):
        return SequenceMatcher(None, a, b, autojunk=False).ratio() >= RESSEMBLANCE_DES_BORDS
    return semblables(cible[:BORDS], mots[:BORDS]) and semblables(cible[-BORDS:], mots[-BORDS:])


# ------------------------------------------------------------- sans les citations

def sans_citations(lignes):
    """Les lignes au-dessus de la première citation, ou toutes :

    - si le texte ne cite rien, ou n'est que citation ;
    - si quelqu'un a écrit **après** le début du texte cité (réponses glissées entre les
      passages cités, ou sous la citation) : couper perdrait ses mots. Cela ne se voit
      que si la citation est marquée (`blockquote`, `>`) ; une citation Outlook, sans
      marque, est coupée comme avant.
    """
    debut = debut_des_citations(texte_de(lignes))
    if debut is None:
        return lignes
    i = texte_de(lignes)[:debut].count("\n")
    base = lignes[i].profondeur
    citee = next((k for k in range(i, len(lignes)) if lignes[k].profondeur > base), None)
    if citee is not None and any(
            not ligne.vide and ligne.profondeur <= base for ligne in lignes[citee + 1:]):
        return lignes
    garde = _sans_annonces(lignes[:i])
    return garde or lignes


# ------------------------------------------------------------------------ rendu

def en_html(lignes):
    """Les lignes en paragraphes, chaque niveau de citation dans un `blockquote`."""
    morceaux, paragraphe, profondeur = [], [], 0

    def fermer_paragraphe():
        if paragraphe:
            morceaux.append("<p>%s</p>" % "<br>".join(paragraphe))
            paragraphe.clear()

    for ligne in lignes:
        if ligne.vide:
            fermer_paragraphe()
            continue
        if ligne.profondeur != profondeur:
            fermer_paragraphe()
            while profondeur < ligne.profondeur:
                morceaux.append("<blockquote>")
                profondeur += 1
            while profondeur > ligne.profondeur:
                morceaux.append("</blockquote>")
                profondeur -= 1
        paragraphe.append(ligne.html)
        if ligne.fin_de_paragraphe:
            fermer_paragraphe()
    fermer_paragraphe()
    morceaux.extend(["</blockquote>"] * profondeur)
    return "".join(morceaux)


def description_en_html(analyse, couper=False):
    """La description de la couche, en HTML pas encore nettoyé (`sanitize.nettoyer`)."""
    lignes = lignes_de_couche(analyse) or lignes_du_texte(analyse.texte)
    if couper:
        lignes = sans_citations(lignes)
    return en_html(lignes)
