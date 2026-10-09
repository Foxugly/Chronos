"""Les couleurs des évènements et des expéditeurs.

Une couleur finit dans un attribut `style` (vues serveur, vis-timeline) et
dans le JSON de FullCalendar : elle n'est acceptée qu'au format strict
`#rrggbb` (`valider_couleur`), jamais telle qu'elle arrive.
"""

import re

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy

FORMAT = re.compile(r"^#[0-9a-f]{6}$")

#: Teintes assez soutenues pour se distinguer sur fond blanc, assez
#: différentes entre elles pour qu'on ne confonde pas deux expéditeurs.
PALETTE = (
    "#2563eb",  # bleu
    "#dc2626",  # rouge
    "#16a34a",  # vert
    "#d97706",  # ambre
    "#7c3aed",  # violet
    "#0891b2",  # cyan
    "#db2777",  # rose
    "#65a30d",  # olive
    "#c2410c",  # orange brûlé
    "#4b5563",  # gris
)


def valider_couleur(valeur):
    if valeur and not FORMAT.match(valeur):
        raise ValidationError(
            gettext_lazy("Couleur invalide : attendu #rrggbb."), code="couleur_invalide"
        )


def couleur_du_texte(fond):
    """Noir ou blanc, selon ce qui se lit le mieux sur `fond`.

    Luminance relative (WCAG) : au-delà de 0,45, le fond est clair et le texte
    passe en noir.
    """
    r, g, b = (int(fond[i:i + 2], 16) / 255 for i in (1, 3, 5))

    def lineaire(c):
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    luminance = 0.2126 * lineaire(r) + 0.7152 * lineaire(g) + 0.0722 * lineaire(b)
    return "#000000" if luminance > 0.45 else "#ffffff"


def prochaine_couleur(deja_prises):
    """La première couleur de la palette qui n'est pas déjà prise ; on
    recommence la palette quand toutes le sont."""
    deja_prises = list(deja_prises)
    for couleur in PALETTE:
        if couleur not in deja_prises:
            return couleur
    return PALETTE[len(deja_prises) % len(PALETTE)]
