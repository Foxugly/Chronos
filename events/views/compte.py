"""Le compte de la personne connectée : sa suppression, en libre-service (RGPD, audit M8)."""

from django import forms
from django.contrib import messages
from django.contrib.auth import logout
from django.db import transaction
from django.db.models import ProtectedError
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from ..journal import consigner
from ..models import Dossier, DossierAccess, Journal


class SuppressionDeCompteForm(forms.Form):
    password = forms.CharField(
        label=gettext_lazy("Mot de passe actuel"),
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password", "class": "form-control"}),
    )

    def __init__(self, *args, utilisateur, **kwargs):
        super().__init__(*args, **kwargs)
        self.utilisateur = utilisateur

    def clean_password(self):
        mot_de_passe = self.cleaned_data["password"]
        if not self.utilisateur.check_password(mot_de_passe):
            raise forms.ValidationError(gettext_lazy("Mot de passe incorrect."))
        return mot_de_passe


def compte_supprimer(request):
    """Supprime le compte connecté, après confirmation par le mot de passe. POST pour agir.

    Refusé tant que le compte possède un dossier : `Dossier.owner` est en
    `PROTECT` — un dossier de preuves ne disparaît pas avec son propriétaire ;
    il faut d'abord le confier à quelqu'un d'autre (administration).

    Les accès accordés au compte disparaissent avec lui ; chacun est inscrit au
    journal du dossier, pour que le propriétaire sache pourquoi. Le journal,
    en ajout seul, garde ce qui s'est passé : l'auteur de ses entrées devient
    inconnu (`SET_NULL`), le libellé qu'elles portent reste.
    """
    utilisateur = request.user
    dossiers_possedes = Dossier.objects.filter(owner=utilisateur).count()
    form = SuppressionDeCompteForm(request.POST or None, utilisateur=utilisateur)
    if request.method == "POST" and not dossiers_possedes and form.is_valid():
        nom = utilisateur.get_username()
        try:
            with transaction.atomic():
                for acces in DossierAccess.objects.filter(user=utilisateur).select_related("dossier"):
                    consigner(
                        acces.dossier, Journal.Action.ACCES_RETIRE, request=request, objet=nom,
                        details={"role": acces.role, "motif": "compte_supprime"},
                    )
                utilisateur.delete()
        except ProtectedError:
            # Un dossier créé entre l'affichage et l'envoi.
            messages.error(request, _("Ce compte possède un dossier : il ne peut pas être supprimé."))
            return redirect("compte_supprimer")
        logout(request)
        messages.success(request, _("Votre compte a été supprimé."))
        return redirect("account_login")
    return render(request, "events/compte_supprimer.html", {
        "form": form, "dossiers_possedes": dossiers_possedes,
    })
