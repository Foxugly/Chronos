"""Export d'un dossier pour un avocat ou un expert : un zip autoportant.

Contenu :

- `chronologie.pdf` — les faits dans l'ordre, chacun avec sa période, sa
  description et ses pièces (nom, numéro, empreinte SHA-256), puis le contrôle
  d'intégrité ;
- `pieces/NNN-nom.ext` — chaque pièce une seule fois, numérotée dans l'ordre
  de sa première apparition dans la chronologie ;
- `empreintes.sha256` — au format de `sha256sum -c` ;
- `LISEZMOI.txt` — ce que contient l'archive et comment vérifier les pièces ;
- `journal.pdf` — sur demande seulement : le journal du dossier, tel que la
  personne qui exporte peut le lire.

**Sélection.** L'export peut ne porter que sur les évènements retenus par les
filtres de la vue (recherche, expéditeur, période). Il ne contient alors que
leurs pièces, et la chronologie dit en tête quelle sélection elle montre —
une archive partielle ne doit pas pouvoir passer pour le dossier entier.

**Contrôle d'intégrité.** L'empreinte de chaque fichier est recalculée au
moment de l'export et comparée à celle enregistrée au dépôt
(`Document.content_hash`). Une différence voudrait dire que le fichier a
changé sur le disque depuis son dépôt : elle est signalée en tête du PDF, et
l'export ne la masque pas.

Le PDF utilise les polices standard (Helvetica) : sans police à embarquer,
l'export fonctionne à l'identique sous Windows et sous Linux. Leur jeu de
caractères est celui de l'Europe occidentale ; un caractère hors de ce jeu
(un emoji dans un titre) s'affiche en « ? » plutôt que de faire échouer
l'export.
"""

import hashlib
import html
import io
import re
import tempfile
import unicodedata
import zipfile
from xml.sax.saxutils import escape

from django.utils import timezone
from django.utils.formats import date_format
from django.utils.html import strip_tags
from django.utils.text import get_valid_filename
from django.utils.translation import gettext as _
from django.utils.translation import ngettext
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from .models import Event

TAILLE_DE_BLOC = 1024 * 1024


def _empreinte_du_fichier(document):
    digest = hashlib.sha256()
    with document.file.open("rb") as fichier:
        for bloc in iter(lambda: fichier.read(TAILLE_DE_BLOC), b""):
            digest.update(bloc)
    return digest.hexdigest()


def _texte_pdf(texte):
    """Du texte d'utilisateur, prêt pour un `Paragraph` de reportlab.

    `Paragraph` lit un petit balisage XML : tout est échappé, rien de ce
    qu'un contributeur a écrit ne devient une balise. Les caractères hors du
    jeu des polices standard deviennent « ? ».
    """
    return escape(_texte_brut(texte))


def _texte_brut(texte):
    """Ramené au jeu de caractères des polices standard (« ? » au-delà)."""
    return texte.encode("cp1252", errors="replace").decode("cp1252")


def _description_en_paragraphes(contenu):
    """La description (HTML assaini par nh3) en paragraphes de texte.

    Fins de paragraphe et de ligne deviennent des coupures, les éléments de
    liste des puces ; le reste du balisage est retiré.
    """
    if not contenu:
        return []
    texte = re.sub(r"<li[^>]*>", "\n• ", contenu)
    texte = re.sub(r"</(p|li|h[1-6])>|<br\s*/?>", "\n", texte)
    # Le HTML assaini code les apostrophes et esperluettes en entités
    # (`&#x27;`) : une fois les balises retirées, on les décode.
    texte = html.unescape(strip_tags(texte))
    return [ligne.strip() for ligne in texte.split("\n") if ligne.strip()]


def _nom_dans_l_archive(numero, document):
    """`pieces/NNN-nom.ext`, en ASCII.

    Un nom accentué (« deuxième ») est codé de deux façons dans un zip selon
    l'outil qui le relit : l'archive s'ouvrirait avec un nom brouillé, et
    `sha256sum -c` ne trouverait plus le fichier. Le titre exact reste dans
    le PDF, à côté du numéro.
    """
    ascii_ = unicodedata.normalize("NFKD", document.title).encode("ascii", "ignore").decode()
    nom = get_valid_filename(ascii_) or "piece"
    return "pieces/%03d-%s" % (numero, nom)


def construire_export(dossier, auteur, evenements=None, selection="", total=None, journal=None):
    """Écrit le zip dans un fichier temporaire ; rend `(archive, nombre de pièces)`.

    `evenements` : la sélection à exporter ; `None` pour tout le dossier.
    `selection` : les critères en clair, repris en tête du PDF. `journal` :
    les lignes du journal (`views.lignes_du_journal`) à joindre, ou `None`.

    Les pièces peuvent peser des dizaines de mégaoctets : l'archive est
    écrite sur disque au-delà de 20 Mo plutôt que tenue en mémoire.
    """
    complet = evenements is None
    if complet:
        evenements = list(dossier.events.prefetch_related("documents", "etiquettes"))

    # Chaque pièce porte son numéro **définitif** (`Document.numero`) : la
    # pièce 12 est la pièce 12 dans tous les exports, complets ou partiels,
    # d'hier et de demain. Un export partiel a donc des trous ; c'est voulu.
    # Ordre : celui des numéros. Un export complet inclut aussi les pièces
    # liées à aucun évènement — il n'omet rien.
    retenues = {}
    for evenement in evenements:
        for document in evenement.documents.all():
            retenues.setdefault(document.pk, document)
    if complet:
        for document in dossier.documents.all():
            retenues.setdefault(document.pk, document)
    numeros = {
        document.pk: (document.numero, document)
        for document in sorted(retenues.values(), key=lambda d: (d.numero or 0, d.pk))
    }

    controles = []
    for numero, document in numeros.values():
        calculee = _empreinte_du_fichier(document)
        controles.append((numero, document, calculee, calculee == document.content_hash))

    archive = tempfile.SpooledTemporaryFile(max_size=20 * 1024 * 1024)
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zip_:
        zip_.writestr(
            "chronologie.pdf",
            _pdf(dossier, evenements, numeros, controles, auteur, selection=selection,
                 total=total),
        )
        if journal is not None:
            zip_.writestr("journal.pdf", _pdf_du_journal(dossier, journal, auteur))
        lignes_empreintes = []
        for numero, document, calculee, _conforme in controles:
            nom = _nom_dans_l_archive(numero, document)
            with document.file.open("rb") as fichier:
                with zip_.open(nom, "w", force_zip64=True) as cible:
                    for bloc in iter(lambda: fichier.read(TAILLE_DE_BLOC), b""):
                        cible.write(bloc)
            lignes_empreintes.append("%s  %s" % (calculee, nom))
        zip_.writestr("empreintes.sha256", "\n".join(lignes_empreintes) + "\n")
        zip_.writestr("LISEZMOI.txt", _lisezmoi(dossier, auteur, selection, journal is not None))
    archive.seek(0)
    return archive, len(numeros)


def _lisezmoi(dossier, auteur, selection="", avec_journal=False):
    maintenant = date_format(timezone.localtime(), "SHORT_DATETIME_FORMAT")
    texte = _(
        "Dossier : %(dossier)s\n"
        "Exporté le %(date)s par %(auteur)s.\n"
        "\n"
        "Contenu :\n"
        "- chronologie.pdf : les faits dans l'ordre, avec leurs pièces ;\n"
        "- pieces/ : chaque pièce une seule fois, numérotée comme dans la chronologie ;\n"
        "- empreintes.sha256 : l'empreinte SHA-256 de chaque pièce.\n"
        "\n"
        "Vérifier qu'une pièce n'a pas été modifiée :\n"
        "- Linux, macOS : sha256sum -c empreintes.sha256\n"
        "- Windows : certutil -hashfile pieces\\NOM_DU_FICHIER SHA256\n"
        "  puis comparer avec la ligne correspondante de empreintes.sha256.\n"
        "\n"
        "L'empreinte enregistrée lors du dépôt de chaque pièce a été comparée, au\n"
        "moment de l'export, à celle du fichier exporté : le résultat figure dans\n"
        "chronologie.pdf, section « Contrôle d'intégrité ».\n"
    ) % {"dossier": dossier.name, "date": maintenant, "auteur": auteur}
    if selection:
        texte += "\n" + _(
            "Export partiel : seuls les évènements retenus par cette sélection, et leurs\n"
            "pièces, figurent dans l'archive — %(selection)s.\n"
        ) % {"selection": selection}
    if avec_journal:
        texte += "\n" + _("journal.pdf : le journal des modifications du dossier.\n")
    return texte


def _styles():
    styles = getSampleStyleSheet()
    normal = ParagraphStyle("normal", parent=styles["Normal"], fontSize=9.5, leading=13)
    return {
        "normal": normal,
        "petit": ParagraphStyle("petit", parent=normal, fontSize=8, leading=10.5,
                                textColor=colors.HexColor("#555555")),
        "gras": ParagraphStyle("gras", parent=normal, fontName="Helvetica-Bold"),
        "titre": styles["Title"],
    }


def _construire_pdf(dossier, corps, maintenant):
    # `drawString` dessine du texte brut : pas d'échappement XML ici.
    pied = _texte_brut("%s — %s" % (dossier.name, maintenant))

    def pied_de_page(canvas, document):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(colors.HexColor("#777777"))
        canvas.drawString(18 * mm, 10 * mm, pied)
        canvas.drawRightString(A4[0] - 18 * mm, 10 * mm,
                               _("page %(n)s") % {"n": document.page})
        canvas.restoreState()

    tampon = io.BytesIO()
    SimpleDocTemplate(
        tampon, pagesize=A4, title=dossier.name, pageCompression=0,
        leftMargin=18 * mm, rightMargin=18 * mm, topMargin=18 * mm, bottomMargin=18 * mm,
    ).build(corps, onFirstPage=pied_de_page, onLaterPages=pied_de_page)
    return tampon.getvalue()


def _pdf_du_journal(dossier, lignes, auteur):
    """Le journal en PDF : une entrée par bloc, du plus récent au plus ancien.

    Pas de tableau : une description de courriel importé ferait une cellule
    plus haute qu'une page, que reportlab ne sait pas couper.
    """
    s = _styles()
    maintenant = date_format(timezone.localtime(), "SHORT_DATETIME_FORMAT")
    corps = [
        Paragraph(_texte_pdf(_("Journal — %(nom)s") % {"nom": dossier.name}), s["titre"]),
        Paragraph(_texte_pdf(_("Exporté le %(date)s par %(auteur)s.")
                             % {"date": maintenant, "auteur": auteur}), s["petit"]),
        Spacer(1, 6 * mm),
    ]
    for ligne in lignes:
        entree = ligne["entree"]
        quand = date_format(timezone.localtime(entree.moment), "SHORT_DATETIME_FORMAT")
        en_tete = "%s — %s" % (quand, entree.get_action_display())
        if entree.objet:
            en_tete += " — " + entree.objet
        if entree.auteur:
            qui = entree.auteur.get_username()
        elif entree.lien:
            qui = _("lien de partage « %(nom)s »") % {"nom": entree.lien.label or "—"}
        elif ligne.get("par_import"):
            qui = _("import de courriels")
        else:
            qui = _("auteur inconnu")
        if entree.via_admin:
            qui += " (" + _("via l'administration") + ")"
        corps.append(Paragraph(_texte_pdf(en_tete), s["gras"]))
        corps.append(Paragraph(_texte_pdf(qui), s["petit"]))
        for m in ligne["modifications"]:
            if ligne["comparaison"]:
                texte = "%s : %s → %s" % (m["champ"], m["avant"] or "—", m["apres"] or "—")
            else:
                texte = "%s : %s" % (m["champ"], m["apres"])
            for morceau in str(texte).split("\n"):
                if morceau.strip():
                    corps.append(Paragraph(_texte_pdf(morceau), s["petit"]))
        if ligne.get("empreinte"):
            corps.append(Paragraph(_texte_pdf("SHA-256 " + ligne["empreinte"]), s["petit"]))
        corps.append(Spacer(1, 3 * mm))
    if not lignes:
        corps.append(Paragraph(_texte_pdf(_("Rien n'a encore été inscrit au journal.")), s["normal"]))
    return _construire_pdf(dossier, corps, maintenant)


def _liens_de_transfert(dossier, evenements):
    """`{pk: [(gabarit, évènement lié)]}` : ce que chaque évènement transfère,
    et ses transferts ultérieurs. Deux requêtes bornées au dossier ; un
    évènement lié peut être hors d'un export partiel, le PDF le dit."""
    retenus = {e.pk for e in evenements}
    origines = {e.pk: e for e in Event.objects.filter(
        dossier=dossier, pk__in={e.transfert_de_id for e in evenements if e.transfert_de_id})}
    liens = {"retenus": retenus}
    for evenement in evenements:
        if evenement.transfert_de_id in origines:
            liens.setdefault(evenement.pk, []).append(
                (_("Transfert de : %(titre)s (%(periode)s)"), origines[evenement.transfert_de_id]))
    for suite in Event.objects.filter(dossier=dossier, transfert_de__in=retenus):
        liens.setdefault(suite.transfert_de_id, []).append(
            (_("Transféré ensuite : %(titre)s (%(periode)s)"), suite))
    repondus = {e.pk: e for e in Event.objects.filter(
        dossier=dossier, pk__in={e.reponse_a_id for e in evenements if e.reponse_a_id})}
    for evenement in evenements:
        if evenement.reponse_a_id in repondus:
            liens.setdefault(evenement.pk, []).append(
                (_("En réponse à : %(titre)s (%(periode)s)"), repondus[evenement.reponse_a_id]))
    for reponse in Event.objects.filter(dossier=dossier, reponse_a__in=retenus):
        liens.setdefault(reponse.reponse_a_id, []).append(
            (_("Réponse : %(titre)s (%(periode)s)"), reponse))
    return liens


def _pdf(dossier, evenements, numeros, controles, auteur, selection="", total=None):
    styles = getSampleStyleSheet()
    normal = ParagraphStyle("normal", parent=styles["Normal"], fontSize=9.5, leading=13)
    petit = ParagraphStyle("petit", parent=normal, fontSize=8, leading=10.5,
                           textColor=colors.HexColor("#555555"))
    periode = ParagraphStyle("periode", parent=petit, textColor=colors.HexColor("#1f4e79"),
                             fontName="Helvetica-Bold")
    titre_fait = ParagraphStyle("titre_fait", parent=normal, fontName="Helvetica-Bold",
                                fontSize=11, leading=14, spaceAfter=2)
    titre = styles["Title"]
    section = ParagraphStyle("section", parent=styles["Heading2"], spaceBefore=10)
    alerte = ParagraphStyle("alerte", parent=normal, textColor=colors.HexColor("#a4161a"),
                            fontName="Helvetica-Bold")

    maintenant = date_format(timezone.localtime(), "SHORT_DATETIME_FORMAT")
    corps = [
        Paragraph(_texte_pdf(dossier.name), titre),
        Paragraph(_texte_pdf(_("Chronologie exportée le %(date)s par %(auteur)s.")
                             % {"date": maintenant, "auteur": auteur}), petit),
        Paragraph(_texte_pdf(_("%(n)s évènements, %(p)s pièces.")
                             % {"n": len(evenements), "p": len(numeros)}), petit),
    ]
    if selection:
        corps.append(Spacer(1, 2 * mm))
        corps.append(Paragraph(_texte_pdf(ngettext(
            "Export partiel — sélection : %(selection)s. %(n)s évènement sur %(total)s.",
            "Export partiel — sélection : %(selection)s. %(n)s évènements sur %(total)s.",
            len(evenements),
        ) % {"selection": selection, "n": len(evenements), "total": total}), alerte))
    corps.append(Spacer(1, 6 * mm))

    non_conformes = [c for c in controles if not c[3]]
    if non_conformes:
        corps.append(Paragraph(_texte_pdf(_(
            "Attention : %(n)s pièce(s) ne correspondent plus à l'empreinte enregistrée "
            "lors de leur dépôt. Voir « Contrôle d'intégrité »."
        ) % {"n": len(non_conformes)}), alerte))
        corps.append(Spacer(1, 4 * mm))

    corps.append(Paragraph(_texte_pdf(_("Chronologie")), section))
    liens = _liens_de_transfert(dossier, evenements)
    for evenement in evenements:
        bloc = [
            Paragraph(_texte_pdf(evenement.periode_affichee), periode),
            Paragraph(_texte_pdf(evenement.title), titre_fait),
        ]
        noms = [e.nom for e in evenement.etiquettes.all()]
        if noms:
            bloc.append(Paragraph(_texte_pdf(_("Étiquettes : %(noms)s") % {"noms": ", ".join(noms)}), petit))
        for gabarit, autre in liens.get(evenement.pk, []):
            texte = gabarit % {"titre": autre.title, "periode": autre.periode_affichee}
            if autre.pk not in liens["retenus"]:
                texte += " — " + _("hors de cette sélection")
            bloc.append(Paragraph(_texte_pdf(texte), petit))
        for paragraphe in _description_en_paragraphes(evenement.description):
            bloc.append(Paragraph(_texte_pdf(paragraphe), normal))
        for document in evenement.documents.all():
            numero = numeros[document.pk][0]
            bloc.append(Paragraph(
                _texte_pdf(_("Pièce %(numero)s : %(nom)s") % {"numero": numero, "nom": document.title})
                + "<br/>" + _texte_pdf("SHA-256 " + document.content_hash),
                petit,
            ))
        bloc.append(Spacer(1, 5 * mm))
        corps.append(KeepTogether(bloc))

    corps.append(Paragraph(_texte_pdf(_("Contrôle d'intégrité")), section))
    corps.append(Paragraph(_texte_pdf(_(
        "Pour chaque pièce, l'empreinte SHA-256 du fichier exporté a été recalculée et "
        "comparée à celle enregistrée lors de son dépôt."
    )), petit))
    corps.append(Spacer(1, 3 * mm))
    lignes = [[_("N°"), _("Pièce"), _("SHA-256"), _("Contrôle")]]
    for numero, document, calculee, conforme in controles:
        lignes.append([
            str(numero),
            Paragraph(_texte_pdf(document.title), petit),
            Paragraph(_texte_pdf(calculee), petit),
            _("conforme") if conforme else _("DIFFÉRENTE"),
        ])
    tableau = Table(lignes, colWidths=[10 * mm, 60 * mm, 80 * mm, 22 * mm], repeatRows=1)
    style = [
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8),
        ("FONT", (0, 1), (-1, -1), "Helvetica", 8),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#bbbbbb")),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eeeeee")),
    ]
    for rang, (_n, _d, _c, conforme) in enumerate(controles, start=1):
        if not conforme:
            style.append(("TEXTCOLOR", (3, rang), (3, rang), colors.HexColor("#a4161a")))
    tableau.setStyle(TableStyle(style))
    corps.append(tableau)
    return _construire_pdf(dossier, corps, maintenant)
