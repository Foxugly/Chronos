"""Mise en forme des évènements pour les vues mois, semaine et liste sans JavaScript.

Rendu côté serveur, sans bibliothèque de calendrier : une grille mensuelle
est un tableau de six lignes que Python calcule en vingt lignes. Les onglets
FullCalendar offrent les mêmes vues avec JavaScript ; celles-ci restent le
repli quand JavaScript ou un CDN fait défaut, et la version qui s'imprime.

**Noms de mois et de jours : ceux de la langue courante**, fournis par les
traductions de Django (`date_format`). Aucune liste écrite en français ici :
le site doit pouvoir passer en néerlandais ou en anglais sans toucher ce
module. Le premier jour de la semaine reste le lundi : c'est le cas des trois
langues prévues (fr, nl-be, en-gb) — un anglais américain, qui commence le
dimanche, demanderait de lire `FIRST_DAY_OF_WEEK`.
"""

import calendar
from datetime import date, timedelta

from django.utils.formats import date_format

#: Lundi en premier : convention des trois langues prévues, et celle de
#: `calendar` par défaut.
calendrier = calendar.Calendar(firstweekday=0)

#: Un lundi quelconque, pour nommer les sept jours dans la langue courante.
_UN_LUNDI = date(2024, 1, 1)


def titre_du_mois(valeur):
    """« août 2026 », « augustus 2026 », « August 2026 »."""
    return date_format(valeur, "YEAR_MONTH_FORMAT")


def nom_du_jour(valeur):
    return date_format(valeur, "l")


def noms_de_jours():
    """Les sept jours, lundi en premier, dans la langue courante.

    Une fonction et non une constante : calculée à l'import, la liste
    figerait la langue active au démarrage du serveur.
    """
    return [nom_du_jour(_UN_LUNDI + timedelta(days=decalage)) for decalage in range(7)]


def _jour_de(evenement):
    """La date de l'évènement, quelle que soit sa forme.

    Django accepte `date="2026-10-05"` à l'enregistrement et garde la chaîne
    sur l'objet en mémoire : un évènement tout juste créé peut donc porter
    une chaîne là où un évènement relu porte une `date`. Sans cette
    normalisation, le calcul tombe selon d'où vient l'objet.
    """
    jour = evenement.date
    if isinstance(jour, str):
        annee, mois, numero = jour.split("-")
        return date(int(annee), int(mois), int(numero))
    return jour


def _grouper_par_date(evenements):
    """Chaque jour touché porte l'évènement, bornes comprises.

    Un intervalle placé sur son seul premier jour ferait disparaître un
    chantier de six semaines derrière une unique case du calendrier.
    """
    par_date = {}
    for evenement in evenements:
        jours = getattr(evenement, "jours_couverts", None)
        if jours is None:
            jours = [_jour_de(evenement)]
        for jour in jours:
            par_date.setdefault(jour, []).append(evenement)
    return par_date


def mois_demande(parametre, aujourdhui=None):
    """Le mois à afficher, lu depuis l'URL.

    Un paramètre illisible retombe sur le mois courant plutôt que de lever :
    une URL tronquée dans un courriel ne doit pas produire une page d'erreur.
    """
    reference = aujourdhui or date.today()
    if parametre:
        try:
            annee, mois = parametre.split("-")
            return date(int(annee), int(mois), 1)
        except (ValueError, TypeError):
            pass
    return reference.replace(day=1)


def semaine_demandee(parametre, aujourdhui=None):
    """Le lundi de la semaine à afficher."""
    reference = aujourdhui or date.today()
    if parametre:
        try:
            annee, mois, jour = parametre.split("-")
            reference = date(int(annee), int(mois), int(jour))
        except (ValueError, TypeError):
            pass
    return reference - timedelta(days=reference.weekday())


def grille_du_mois(premier_du_mois, evenements, aujourdhui=None):
    """Six semaines de sept jours, chacun portant ses évènements.

    Les jours des mois voisins sont inclus et marqués : une grille qui
    s'arrête au 31 laisse des trous et se lit mal.
    """
    par_date = _grouper_par_date(evenements)
    aujourdhui = aujourdhui or date.today()

    semaines = []
    for semaine in calendrier.monthdatescalendar(
        premier_du_mois.year, premier_du_mois.month
    ):
        semaines.append(
            [
                {
                    "date": jour,
                    "evenements": par_date.get(jour, []),
                    "hors_mois": jour.month != premier_du_mois.month,
                    "aujourdhui": jour == aujourdhui,
                }
                for jour in semaine
            ]
        )
    return semaines


def jours_de_la_semaine(lundi, evenements, aujourdhui=None):
    par_date = _grouper_par_date(evenements)
    aujourdhui = aujourdhui or date.today()
    return [
        {
            "date": lundi + timedelta(days=decalage),
            "nom": nom_du_jour(lundi + timedelta(days=decalage)),
            "evenements": par_date.get(lundi + timedelta(days=decalage), []),
            "aujourdhui": (lundi + timedelta(days=decalage)) == aujourdhui,
        }
        for decalage in range(7)
    ]


def liste_par_mois(evenements):
    """Les évènements groupés par mois, du plus ancien au plus récent.

    L'ordre suit celui du dossier : une chronologie se lit dans le sens où
    les faits se sont produits.
    """
    groupes = []
    courant = None
    for evenement in evenements:
        jour = _jour_de(evenement)
        cle = (jour.year, jour.month)
        if courant is None or courant["cle"] != cle:
            courant = {
                "cle": cle,
                "titre": titre_du_mois(date(cle[0], cle[1], 1)),
                "evenements": [],
            }
            groupes.append(courant)
        courant["evenements"].append(evenement)
    return groupes


def mois_voisins(premier_du_mois):
    precedent = (premier_du_mois - timedelta(days=1)).replace(day=1)
    jours = calendar.monthrange(premier_du_mois.year, premier_du_mois.month)[1]
    suivant = premier_du_mois + timedelta(days=jours)
    return precedent, suivant


def semaines_voisines(lundi):
    return lundi - timedelta(days=7), lundi + timedelta(days=7)
