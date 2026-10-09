"""Contexte partagé par tous les gabarits.

La navbar propose un sélecteur de dossier sur chaque page, ce qui demande la
liste des dossiers accessibles partout — y compris sur les pages où elle ne
sert à rien, comme la connexion.

D'où le `SimpleLazyObject` : la requête n'est émise que si un gabarit lit
réellement la variable. Les pages d'allauth, qui n'héritent pas de la navbar,
ne paient donc rien.
"""

from django.conf import settings
from django.utils.functional import SimpleLazyObject

from .models import Dossier


def accessible_dossiers(request):
    def charger():
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return Dossier.objects.none()
        # Passe par le queryset d'autorisation, jamais par `DossierAccess` :
        # le propriétaire n'y a pas de ligne, ses droits viennent de `owner`.
        return Dossier.objects.accessible_by(user)

    return {"accessible_dossiers": SimpleLazyObject(charger)}


#: Jetons communs aux formats de date de Django et de flatpickr : `d j m n Y y`
#: ont le même sens des deux côtés. Un format qui en emploierait d'autres
#: (un nom de mois, par exemple) n'est pas transmis tel quel.
_JETONS_COMMUNS = set("djmnYy") | set("./- ")


def langue(request):
    """La langue courante et ses formats, pour les bibliothèques JavaScript.

    FullCalendar, vis-timeline et flatpickr ont chacun
    leur nom pour une langue ; tous partent d'ici, et rien n'est écrit en dur
    dans les gabarits. `format_date` est le format court de Django pour la
    langue — celui que l'utilisateur voit dans un champ de date.
    """
    from django.utils import formats, translation

    code = (translation.get_language() or settings.LANGUAGE_CODE).lower()
    format_date = formats.get_format("SHORT_DATE_FORMAT")
    if not set(format_date) <= _JETONS_COMMUNS:
        format_date = "Y-m-d"
    return {
        "langue": {
            "code": code,
            "base": code.split("-")[0],
            "format_date": format_date,
        }
    }


def application(request):
    """Le nom, la version et l'année du footer (standard de la flotte)."""
    from django.utils import timezone

    return {"application": {"version": settings.VERSION, "annee": timezone.localdate().year}}
