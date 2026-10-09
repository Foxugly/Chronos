from allauth.account.adapter import DefaultAccountAdapter


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
