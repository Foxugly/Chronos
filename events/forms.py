from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db.models import F
from django.utils import timezone
from django import forms
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from .sanitize import est_vide, nettoyer
from .couleurs import PALETTE
from .models import Dossier, DossierAccess, Event, Invitation, ShareLink


#: Ce qu'exigent `<input type="date">` et `<input type="time">`, quelle que
#: soit la langue. Sans format explicite, Django rend la date dans celui de
#: la locale — `05.08.2026` en `fr-be` — que le navigateur rejette : le champ
#: s'affichait vide, et modifier un évènement obligeait à ressaisir sa date.
FORMAT_DATE_HTML = "%Y-%m-%d"
FORMAT_HEURE_HTML = "%H:%M"


class EventForm(forms.ModelForm):
    """Titre, situation dans le temps, description, pièces.

    La situation dans le temps se choisit explicitement — une date, un
    instant, une période — et ce choix **efface** les champs qui ne lui
    appartiennent pas. Sans cela, un utilisateur qui saisit une heure puis
    revient à « une date » garderait un évènement marqué comme instant par
    une valeur qu'il ne voit plus.
    """

    PRECISIONS = [
        ("date", gettext_lazy("Une date")),
        ("instant", gettext_lazy("Une date et une heure")),
        ("intervalle", gettext_lazy("Une période")),
    ]

    # Pas obligatoire : un POST direct, un client HTTP ou un vieux formulaire
    # en cache n'envoie que les dates. Les exiger ensemble ferait d'un mode
    # d'affichage une condition d'enregistrement — et le mode se déduit très
    # bien de ce qui est envoyé.
    precision = forms.ChoiceField(
        choices=PRECISIONS,
        initial="date",
        required=False,
        label=gettext_lazy("Quand ?"),
        widget=forms.RadioSelect(attrs={"class": "form-check-input"}),
    )

    # La version de l'évènement telle que le formulaire l'a chargée. Pas
    # obligatoire, pour la même raison que `precision` : elle protège les
    # personnes qui passent par le formulaire, elle ne fait pas d'un champ
    # caché une condition d'enregistrement pour un client qui ne l'envoie pas.
    version = forms.IntegerField(
        required=False, min_value=1, widget=forms.HiddenInput
    )

    CONFLIT = gettext_lazy(
        "Cet évènement a été modifié entre-temps par quelqu'un d'autre. "
        "Vos changements n'ont pas été enregistrés : copiez ce que vous "
        "voulez garder, puis rechargez la page pour repartir de la version "
        "actuelle."
    )

    class Meta:
        model = Event
        fields = ["title", "date", "heure", "date_fin", "heure_fin", "description", "couleur"]

        widgets = {
            "title": forms.TextInput(
                attrs={"class": "form-control"}
            ),
            "date": forms.DateInput(
                format=FORMAT_DATE_HTML,
                attrs={
                    "class": "form-control",
                    "type": "date"
                }
            ),
            "heure": forms.TimeInput(
                format=FORMAT_HEURE_HTML,
                attrs={"class": "form-control", "type": "time"}
            ),
            "date_fin": forms.DateInput(
                format=FORMAT_DATE_HTML,
                attrs={"class": "form-control", "type": "date"}
            ),
            "heure_fin": forms.TimeInput(
                format=FORMAT_HEURE_HTML,
                attrs={"class": "form-control", "type": "time"}
            ),

            "description": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "rows": 5,
                    # L'éditeur riche se greffe sur ce champ. Sans
                    # JavaScript, il reste une zone de texte utilisable.
                    "data-editeur-riche": "",
                }
            )
        }


    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Les bornes ne sont jamais exigées par le modèle : c'est le mode
        # choisi qui décide laquelle devient obligatoire, dans `clean`.
        for nom in ("heure", "date_fin", "heure_fin"):
            self.fields[nom].required = False
        # La couleur se choisit dans la palette (ou « par défaut ») : pas de
        # saisie libre, et une valeur hors palette déjà posée (par l'admin)
        # reste proposée pour ne pas être effacée par mégarde.
        choix = [("", gettext_lazy("Par défaut"))] + [(c, c) for c in PALETTE]
        actuelle = getattr(self.instance, "couleur", "")
        if actuelle and actuelle not in PALETTE:
            choix.append((actuelle, actuelle))
        self.fields["couleur"] = forms.ChoiceField(
            choices=choix, required=False, label=gettext_lazy("Couleur"),
            widget=forms.RadioSelect,
        )
        if self.instance and self.instance.pk:
            self.fields["precision"].initial = self.instance.precision
            self.fields["version"].initial = self.instance.version

    @staticmethod
    def _mode_deduit(donnees):
        """Le mode qu'indiquent les champs eux-mêmes.

        Même règle que `Event.precision`, appliquée aux données reçues : un
        envoi sans mode est alors traité exactement comme l'évènement qu'il
        décrit, et rien n'est effacé à tort.
        """
        if donnees.get("date_fin"):
            return "intervalle"
        if donnees.get("heure"):
            return "instant"
        return "date"

    def clean(self):
        """Le mode choisi fait autorité sur les champs.

        On efface plutôt que de refuser : une heure laissée par un
        changement d'avis n'est pas une erreur de l'utilisateur, c'est un
        reste. Le refuser l'obligerait à vider un champ qu'il ne voit plus.
        """
        donnees = super().clean()
        mode = donnees.get("precision") or self._mode_deduit(donnees)

        if mode == "date":
            donnees["heure"] = None
            donnees["date_fin"] = None
            donnees["heure_fin"] = None
        elif mode == "instant":
            donnees["date_fin"] = None
            donnees["heure_fin"] = None
            if not donnees.get("heure"):
                self.add_error("heure", _("Indiquez l'heure, ou choisissez « une date »."))
        elif mode == "intervalle":
            if not donnees.get("date_fin"):
                self.add_error(
                    "date_fin", _("Indiquez la date de fin, ou choisissez un autre mode.")
                )
            elif donnees.get("date") and donnees["date_fin"] < donnees["date"]:
                self.add_error("date_fin", _("La fin ne peut pas précéder le début."))
            elif (
                donnees.get("heure")
                and donnees.get("heure_fin")
                and donnees["date_fin"] == donnees.get("date")
                and donnees["heure_fin"] < donnees["heure"]
            ):
                self.add_error("heure_fin", _("La fin ne peut pas précéder le début."))

        return donnees

    def clean_description(self):
        """Assainit le HTML reçu. **C'est le seul rempart.**

        L'éditeur du navigateur est un confort : un POST direct, un
        lien de partage en écriture ou n'importe quel client HTTP le
        contourne. Du script injecté ici s'exécuterait dans la session
        du propriétaire, avec son cookie et son jeton CSRF.
        """
        html = self.cleaned_data.get("description", "")
        if est_vide(html):
            return ""
        return nettoyer(html)

    def save(self, commit=True):
        """Enregistre seulement si la version chargée est toujours la bonne.

        Le contrôle et l'incrément tiennent en **une** requête
        conditionnelle : `UPDATE … WHERE version = attendue`. Lire la version
        puis écrire laisserait une fenêtre où deux enregistrements lisent la
        même valeur et passent tous les deux. Ici, la base sérialise les
        écritures : la seconde ne trouve plus de ligne à mettre à jour.

        Lève `ValidationError` en cas de conflit, que les vues reportent sur
        le formulaire ; elles appellent `save` dans une transaction, donc les
        pièces jointes du même envoi ne sont pas enregistrées non plus.
        """
        if not (commit and self.instance.pk):
            return super().save(commit)

        attendue = self.cleaned_data.get("version")
        lignes = Event.objects.filter(pk=self.instance.pk)
        if attendue is None:
            lignes.update(version=F("version") + 1)
        elif not lignes.filter(version=attendue).update(version=F("version") + 1):
            raise ValidationError(self.CONFLIT, code="conflit")
        self.instance.version = lignes.values_list("version", flat=True).get()
        return super().save(commit)


class DossierAccessForm(forms.ModelForm):
    """Accorder un accès à un compte existant.

    Le choix est volontairement restreint aux comptes qui n'ont pas
    déjà accès, propriétaire compris : proposer un compte déjà
    autorisé mènerait à une violation de la contrainte d'unicité,
    c'est-à-dire à une erreur serveur pour un geste légitime.
    """

    class Meta:
        model = DossierAccess
        fields = ["user", "role"]
        widgets = {
            "user": forms.Select(attrs={"class": "form-select"}),
            "role": forms.Select(attrs={"class": "form-select"}),
        }
        labels = {
            "user": gettext_lazy("Compte"),
            "role": gettext_lazy("Droit accordé"),
        }

    def __init__(self, *args, dossier=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.dossier = dossier
        if dossier is not None:
            deja = list(
                dossier.accesses.values_list("user_id", flat=True)
            ) + [dossier.owner_id]
            self.fields["user"].queryset = (
                get_user_model()
                .objects.exclude(pk__in=deja)
                .order_by("username")
            )
            self.fields["user"].empty_label = _("Choisir un compte…")


class InvitationForm(forms.ModelForm):
    """Inviter une adresse à rejoindre un dossier.

    Rien n'empêche d'inviter quelqu'un qui a déjà accès : ce serait une
    invitation sans effet, et le dire vaut mieux que de la laisser partir.
    """

    class Meta:
        model = Invitation
        fields = ["email", "role"]
        widgets = {
            "email": forms.EmailInput(
                attrs={"class": "form-control", "placeholder": gettext_lazy("adresse@exemple.be")}
            ),
            "role": forms.Select(attrs={"class": "form-select"}),
        }
        labels = {
            "email": gettext_lazy("Adresse électronique"),
            "role": gettext_lazy("Droit accordé"),
        }

    def __init__(self, *args, dossier=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.dossier = dossier

    def clean_email(self):
        email = self.cleaned_data["email"]
        if self.dossier is None:
            return email

        if self.dossier.owner.email and self.dossier.owner.email.lower() == email.lower():
            raise forms.ValidationError(
                _("Cette adresse est la vôtre : vous êtes déjà propriétaire du dossier.")
            )

        deja = self.dossier.accesses.filter(user__email__iexact=email).exists()
        if deja:
            raise forms.ValidationError(
                _("Un compte portant cette adresse a déjà accès à ce dossier.")
            )

        en_cours = [
            i
            for i in self.dossier.invitations.filter(email__iexact=email)
            if i.is_usable
        ]
        if en_cours:
            raise forms.ValidationError(
                _("Une invitation est déjà en attente pour cette adresse.")
            )
        return email


class ShareLinkForm(forms.ModelForm):
    """Créer un lien de consultation anonyme.

    L'expiration est proposée comme un choix explicite plutôt que laissée
    vide par défaut : un lien sans fin transmis une fois reste ouvert pour
    toujours, et personne ne pense à le révoquer des mois plus tard.
    """

    DUREES = [
        ("7", gettext_lazy("7 jours")),
        ("30", gettext_lazy("30 jours")),
        ("90", gettext_lazy("90 jours")),
        ("", gettext_lazy("Sans expiration")),
    ]

    duree = forms.ChoiceField(
        choices=DUREES,
        initial="30",
        required=False,
        label=gettext_lazy("Validité"),
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    class Meta:
        model = ShareLink
        fields = ["label", "role"]
        widgets = {
            "label": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": gettext_lazy("Maître Dupont, expert, TestAchats…"),
                }
            ),
            "role": forms.Select(attrs={"class": "form-select"}),
        }
        labels = {
            "label": gettext_lazy("Destinataire"),
            "role": gettext_lazy("Droit accordé"),
        }

    def expiration(self):
        """Date d'expiration déduite du choix, ou `None`."""
        jours = self.cleaned_data.get("duree")
        if not jours:
            return None
        return timezone.now() + timedelta(days=int(jours))


class AliasCourrielForm(forms.ModelForm):
    """L'alias de courriel du dossier : vide pour couper l'import.

    L'adresse est normalisée (espaces, minuscules) **avant** la validation :
    normalisée après, « Pris@exemple.be » passait le contrôle d'unicité puis
    heurtait la contrainte de la base à l'enregistrement.
    """

    class Meta:
        model = Dossier
        fields = ["alias_courriel"]

    def clean_alias_courriel(self):
        alias = (self.cleaned_data.get("alias_courriel") or "").strip().lower()
        if not alias:
            return None
        boite = (getattr(settings, "COURRIEL_BOITE", "") or "").strip().lower()
        if boite and alias == boite:
            # La boîte elle-même reçoit tout : en faire l'alias d'un dossier
            # y verserait chaque courriel arrivé, quel qu'en soit le sujet.
            raise ValidationError(
                _("C'est l'adresse de la boîte d'import elle-même : choisissez un alias propre à ce dossier.")
            )
        if Dossier.objects.filter(alias_courriel=alias).exclude(pk=self.instance.pk).exists():
            raise ValidationError(_("Cet alias est déjà utilisé par un autre dossier."))
        return alias


class DossierNomForm(forms.ModelForm):
    """Le nom du dossier, tel qu'il apparaît partout (liste, en-tête, export)."""

    class Meta:
        model = Dossier
        fields = ["name"]
        widgets = {"name": forms.TextInput(attrs={"class": "form-control"})}

    def clean_name(self):
        nom = (self.cleaned_data.get("name") or "").strip()
        if not nom:
            raise ValidationError(_("Le nom du dossier ne peut pas être vide."))
        return nom
