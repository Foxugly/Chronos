"""Refus CSRF : rattraper le seul cas où l'erreur ne veut rien dire.

Se connecter renouvelle le jeton CSRF (Django, `rotate_token`). Tout
formulaire de connexion envoyé ensuite — second clic, gestionnaire de mots
de passe, autre onglet resté sur la page — porte un jeton périmé et finit en
403, alors que la personne est **déjà connectée** : vu en prod les 16 et
17/11/2026.

Ce cas-là, et lui seul, est redirigé comme allauth le ferait pour quelqu'un
de connecté qui ouvre la page de connexion : vers `next` s'il désigne ce
site, sinon l'accueil. Aucune action n'est exécutée — la requête refusée ne
l'est pas moins, on évite seulement une page d'erreur trompeuse. Partout
ailleurs, et pour un visiteur anonyme, le refus de Django reste tel quel.
"""

from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.csrf import csrf_failure


def echec_csrf(request, reason=""):
    deja_connecte = getattr(request, "user", None) is not None and request.user.is_authenticated
    if request.method == "POST" and deja_connecte and request.path == reverse("account_login"):
        suivant = request.POST.get("next", "")
        if not url_has_allowed_host_and_scheme(
            suivant, allowed_hosts={request.get_host()}, require_https=request.is_secure()
        ):
            suivant = settings.LOGIN_REDIRECT_URL
        return redirect(suivant)
    return csrf_failure(request, reason=reason)
