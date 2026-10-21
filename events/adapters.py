from allauth.account.adapter import DefaultAccountAdapter
from django.conf import settings
from django.shortcuts import resolve_url
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme, urlencode


class SdbAccountAdapter(DefaultAccountAdapter):
    """Adaptateur de comptes du projet.

    L'inscription a d'abord été fermée (seul l'administrateur créait les
    comptes), puis **rouverte** sur décision du propriétaire, en même temps
    que le mécanisme d'invitation à un dossier.

    Ce que l'ouverture change, et ce qu'elle ne change pas :

    - elle ne donne accès à **aucun** dossier. Un compte neuf ne voit rien :
      l'accès vient d'un `DossierAccess`, que seul le propriétaire du dossier
      accorde, ou d'une invitation qu'il a émise ;
    - elle élargit en revanche la surface d'attaque, puisqu'une session
      valide s'obtient désormais sans geste du propriétaire. Les trois
      injections de script trouvées par la revue finale exigeaient
      précisément un compte.

    La classe est conservée plutôt que supprimée : c'est ici que se règlent
    les questions de création de compte, et un `ACCOUNT_ADAPTER` explicite
    évite d'avoir à deviner, plus tard, si l'ouverture est délibérée ou
    résulte d'un réglage oublié.
    """

    def is_open_for_signup(self, request):
        return True

    def get_email_verification_redirect_url(self, email_address):
        """Après la confirmation d'une adresse, sans session ouverte.

        Dans le navigateur de l'inscription, allauth connecte la personne et
        reprend le `next` qu'elle portait (`ACCOUNT_LOGIN_ON_EMAIL_CONFIRMATION`),
        donc l'invitation d'où elle venait : cette méthode n'est pas appelée.
        Elle l'est quand le lien est ouvert ailleurs (le téléphone qui lit le
        courrier) : sans elle, la personne aboutirait à l'accueil, vide, et
        l'invitation qui l'a amenée serait oubliée. Si une invitation encore
        utilisable attend l'adresse qu'elle vient de prouver posséder, la page
        de connexion l'y renvoie. Le jeton n'apprend rien à qui le reçoit : il
        est arrivé dans la même boîte que le lien de confirmation.
        """
        url = super().get_email_verification_redirect_url(email_address)
        if self.request.user.is_authenticated:
            return url
        from .models import Invitation

        invitation = (
            Invitation.objects.filter(
                email__iexact=email_address.email,
                accepted_at__isnull=True,
                revoked_at__isnull=True,
                expires_at__gt=timezone.now(),
            )
            .order_by("-created_at")
            .first()
        )
        if invitation is None:
            return url
        suite = reverse("invitation_accept", args=[invitation.token])
        if not url_has_allowed_host_and_scheme(
            suite, allowed_hosts={self.request.get_host()},
            require_https=self.request.is_secure(),
        ):
            return url
        return f"{resolve_url(settings.LOGIN_URL)}?{urlencode({'next': suite})}"
