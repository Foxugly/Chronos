"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.conf import settings
from django.contrib import admin
from django.contrib.auth.decorators import login_not_required
from django.http import Http404
from django.shortcuts import redirect
from django.urls import path, include, reverse

from config.sante import sante
from django.views.generic import TemplateView
from django.views.i18n import set_language


def account_email_closed(request):
    """Ferme la gestion d'e-mails en self-service d'allauth.

    Les comptes sont créés et gérés par l'administrateur ; il n'y a pas de
    raison qu'un utilisateur attache lui-même des adresses secondaires.
    Enregistrée avant `include("allauth.urls")`, cette route intercepte
    `/accounts/email/` et répond 404 plutôt qu'une redirection ou un 403 :
    conformément à la convention du projet, une fonctionnalité indisponible
    est indiscernable d'une fonctionnalité inexistante.
    """
    raise Http404


@login_not_required
def admin_login_redirect(request):
    """Ferme la page de connexion propre à l'admin.

    `/admin/login/` porte `@login_not_required` chez Django : elle est donc
    atteignable sans session, et elle n'est soumise à aucune limitation de
    tentatives, alors que `/accounts/login/` l'est par allauth. Deux portes
    d'entrée pour les mêmes mots de passe, dont une seule compte les échecs.

    Comme pour `/accounts/email/`, la protection tient à l'**ordre de
    déclaration** : cette route doit rester *avant* `admin.site.urls`, sinon
    l'admin reprend la main et la porte se rouvre.

    Un utilisateur déjà authentifié mais non `staff` ne doit
    jamais être redirigé ici vers `/accounts/login/` : cette vue d'allauth
    renvoie quiconque est déjà connecté directement vers `next`, donc vers
    `/admin/`, qui le renvoie à son tour ici faute de statut `staff` -- une
    boucle infinie. Comme pour `/accounts/email/`, une fonctionnalité
    indisponible pour cet utilisateur est indiscernable d'une
    fonctionnalité inexistante : 404, et la boucle s'arrête là.
    """
    if request.user.is_authenticated:
        raise Http404
    return redirect(f"{reverse('account_login')}?next={reverse('admin:index')}")


urlpatterns = [
    path("health/", sante, name="sante"),
# Le sélecteur de langue sert aussi avant la connexion et sur les liens
# de partage : `set_language` ne lit ni n'écrit aucune donnée du dossier,
# il pose un cookie et redirige vers `next`, qu'il valide lui-même
# (`url_has_allowed_host_and_scheme`) — pas de redirection ouverte.
path("i18n/setlang/", login_not_required(set_language), name="set_language"),
    # Publique comme la connexion : on doit pouvoir la lire avant de créer un compte.
    path("confidentialite/", login_not_required(TemplateView.as_view(
        template_name="confidentialite.html",
        extra_context={"retention_partages_jours": settings.RETENTION_PARTAGES_JOURS})),
         name="confidentialite"),
path("admin/login/", admin_login_redirect),
path("admin/", admin.site.urls),
path("accounts/email/", account_email_closed),
path("accounts/", include("allauth.urls")),
path("", include("events.urls")),
]

# `MEDIA_ROOT` n'est plus monté en statique, même en DEBUG : la vue
# `document_download` est le seul accès aux fichiers, et elle vérifie les
# droits. Le montage précédent laissait tout compte connecté télécharger
# le document de n'importe quel dossier.