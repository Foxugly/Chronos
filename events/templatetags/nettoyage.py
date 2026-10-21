"""`|nettoye` : une description re-nettoyée à la sortie, comme le détail.

Les descriptions sont nettoyées à l'enregistrement (`events/sanitize.py`), mais
l'administration enregistre par un ModelForm brut : la sortie ne fait donc
confiance à rien (audit du 2026-10-18, M6). Jamais `|safe` sur une description.
"""

from django import template
from django.utils.safestring import mark_safe

from ..sanitize import nettoyer

register = template.Library()


@register.filter
def nettoye(html):
    # La sortie de nh3, en liste blanche : sûre par construction.
    return mark_safe(nettoyer(html or ""))
