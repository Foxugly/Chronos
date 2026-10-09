import datetime
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.formats import date_format, time_format
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from .couleurs import couleur_du_texte, valider_couleur


class DossierQuerySet(models.QuerySet):
    """Les trois questions de droits, et les seules façons de les poser.

    Un visiteur anonyme n'a droit à rien : chaque méthode renvoie un
    queryset vide plutôt que de filtrer sur `AnonymousUser`, ce qui lève
    `TypeError` — un 500 là où toute autre absence de droit est un 404.
    `LoginRequiredMiddleware` l'arrête en amont aujourd'hui ; la garde ne
    dépend pas de ce qu'une future vue `login_not_required` oublierait.
    """

    def accessible_by(self, user):
        """Dossiers que `user` peut consulter : les siens et ceux partagés."""
        if not user.is_authenticated:
            return self.none()
        return self.filter(Q(owner=user) | Q(accesses__user=user)).distinct()

    def owned_by(self, user):
        """Dossiers dont `user` est le propriétaire — `owner` fait foi (spec D2)."""
        if not user.is_authenticated:
            return self.none()
        return self.filter(owner=user)

    def writable_by(self, user):
        """Dossiers que `user` peut modifier : les siens et ceux partagés en écriture."""
        if not user.is_authenticated:
            return self.none()
        return self.filter(
            Q(owner=user)
            | Q(accesses__user=user, accesses__role=DossierAccess.Role.WRITE)
        ).distinct()


class Dossier(models.Model):
    name = models.CharField(gettext_lazy("nom"), max_length=255)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=gettext_lazy("propriétaire"),
        related_name="dossiers_owned",
        on_delete=models.PROTECT,
    )
    created_at = models.DateTimeField(gettext_lazy("créé le"), auto_now_add=True)
    # L'adresse à laquelle on transfère un courriel pour en faire un évènement
    # de ce dossier (`manage.py importer_courriels`). C'est un alias de la
    # boîte d'import, créé dans Exchange : quiconque la connaît peut ajouter
    # un évènement — choix du propriétaire, 2026-10-09. Unique : un courriel
    # ne doit pouvoir atterrir que dans un seul dossier.
    alias_courriel = models.EmailField(
        gettext_lazy("alias de courriel"),
        unique=True,
        null=True,
        blank=True,
        help_text=gettext_lazy(
            "Les courriels transférés à cette adresse deviennent des évènements du dossier."
        ),
    )

    # Le dernier numéro de pièce attribué dans ce dossier. Un compteur, et non
    # « le plus grand numéro existant » : supprimer la dernière pièce ne doit
    # pas rendre son numéro, déjà cité peut-être dans un courrier d'avocat.
    dernier_numero_piece = models.PositiveIntegerField(default=0, editable=False)

    objects = DossierQuerySet.as_manager()

    class Meta:
        ordering = ["name"]
        verbose_name = gettext_lazy("dossier")
        verbose_name_plural = gettext_lazy("dossiers")

    def __str__(self):
        return self.name


class DossierAccess(models.Model):
    class Role(models.TextChoices):
        READ = "read", gettext_lazy("Lecture")
        WRITE = "write", gettext_lazy("Lecture et écriture")

    class Origin(models.TextChoices):
        DIRECT = "direct", gettext_lazy("Compte choisi")
        INVITATION = "invitation", gettext_lazy("Invitation par e-mail")
        LINK = "link", gettext_lazy("Lien de partage")

    dossier = models.ForeignKey(
        Dossier, related_name="accesses", on_delete=models.CASCADE
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=gettext_lazy("utilisateur"),
        related_name="dossier_accesses",
        on_delete=models.CASCADE,
    )
    role = models.CharField(gettext_lazy("rôle"), max_length=10, choices=Role.choices)
    origin = models.CharField(
        gettext_lazy("origine"), max_length=10, choices=Origin.choices, default=Origin.DIRECT
    )
    granted_at = models.DateTimeField(gettext_lazy("accordé le"), auto_now_add=True)
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=gettext_lazy("accordé par"),
        null=True,
        blank=True,
        related_name="+",
        on_delete=models.SET_NULL,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["dossier", "user"],
                name="unique_access_per_user_per_dossier",
            ),
        ]
        verbose_name = gettext_lazy("accès")
        verbose_name_plural = gettext_lazy("accès")

    def __str__(self):
        return f"{self.user} — {self.dossier} ({self.get_role_display()})"


def _jour(valeur):
    """Une `date`, quelle que soit la forme reçue.

    Django accepte `date="2026-10-05"` à l'enregistrement et garde la chaîne
    sur l'objet en mémoire : un évènement tout juste créé peut donc porter
    une chaîne là où un évènement relu porte une `date`. Sans cette
    normalisation, le calcul des jours couverts tombe selon d'où vient
    l'objet — et c'est dans les vues calendrier qu'on s'en apercevrait.
    """
    if isinstance(valeur, str):
        annee, mois, numero = valeur.split("-")
        return datetime.date(int(annee), int(mois), int(numero))
    return valeur


class Expediteur(models.Model):
    """L'auteur d'un courriel importé dans un dossier, et sa couleur.

    Propre au dossier : la même adresse peut avoir une couleur dans une
    affaire et une autre ailleurs, et un dossier n'apprend rien des autres.
    Les évènements importés pointent vers lui, et **ne recopient pas** sa
    couleur : la changer ici recolore tous ses évènements à la fois.
    """

    dossier = models.ForeignKey(
        Dossier, verbose_name=gettext_lazy("dossier"), related_name="expediteurs",
        on_delete=models.CASCADE,
    )
    adresse = models.EmailField(gettext_lazy("adresse"))
    nom = models.CharField(gettext_lazy("nom"), max_length=255, blank=True)
    couleur = models.CharField(
        gettext_lazy("couleur"), max_length=7, validators=[valider_couleur],
    )

    class Meta:
        ordering = ["nom", "adresse"]
        verbose_name = gettext_lazy("expéditeur")
        verbose_name_plural = gettext_lazy("expéditeurs")
        constraints = [
            models.UniqueConstraint(fields=["dossier", "adresse"], name="expediteur_unique_par_dossier"),
        ]

    def __str__(self):
        return self.nom or self.adresse

    @property
    def couleur_du_texte(self):
        return couleur_du_texte(self.couleur)


class Event(models.Model):
    """Un fait du dossier, situé dans le temps avec la précision voulue.

    Trois situations, et un seul jeu de champs :

    - **une date** — « devis signé le 02/06 » ;
    - **un instant** — « mail envoyé le 21/08 à 14h32 », quand l'heure fait
      partie de la preuve ;
    - **un intervalle** — « chantier du 18 août au 28 septembre ».

    Le type n'est pas stocké, il se **déduit** de ce qui est rempli. Un champ
    « type » à côté des dates pourrait les contredire : un évènement marqué
    « intervalle » sans date de fin, ou l'inverse. Ici, c'est impossible.
    """

    dossier = models.ForeignKey(
        Dossier, verbose_name=gettext_lazy("dossier"), related_name="events", on_delete=models.CASCADE
    )
    title = models.CharField(gettext_lazy("titre"), max_length=255)

    date = models.DateField(gettext_lazy("date"))
    heure = models.TimeField(
        gettext_lazy("heure"),
        null=True,
        blank=True,
        help_text=gettext_lazy("Laissez vide si l'heure n'a pas d'importance."),
    )
    date_fin = models.DateField(
        gettext_lazy("date de fin"),
        null=True,
        blank=True,
        help_text=gettext_lazy("Pour un fait qui s'étend sur plusieurs jours."),
    )
    heure_fin = models.TimeField(gettext_lazy("heure de fin"), null=True, blank=True)

    description = models.TextField(gettext_lazy("description"), blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    # Verrouillage optimiste. Le formulaire renvoie la version qu'il a
    # chargée ; l'enregistrement n'aboutit que si personne n'est passé entre
    # temps (`EventForm.save`). Sans cela, deux personnes sur le même
    # évènement s'écrasaient l'une l'autre sans qu'aucune ne le sache.
    version = models.PositiveIntegerField(gettext_lazy("version"), default=1, editable=False)

    # La couleur : celle de l'expéditeur pour un évènement importé par
    # courriel (lue chez lui à chaque affichage, jamais recopiée), sinon
    # celle choisie à la main, sinon aucune — la couleur par défaut du thème.
    expediteur = models.ForeignKey(
        Expediteur, verbose_name=gettext_lazy("expéditeur"), related_name="evenements",
        null=True, blank=True, on_delete=models.SET_NULL,
    )
    couleur = models.CharField(
        gettext_lazy("couleur"), max_length=7, blank=True, validators=[valider_couleur],
    )
    # Le courriel que cet évènement transfère : la mise en demeure du 28/09 pour
    # le transfert à l'avocate du 07/10 (`analyser_en_couches`). Toujours dans le
    # même dossier (`clean`, et l'import ne relie que dans le dossier qu'il remplit).
    transfert_de = models.ForeignKey(
        "self", verbose_name=gettext_lazy("transfert de"), related_name="transferts",
        null=True, blank=True, on_delete=models.SET_NULL,
    )
    # Les destinataires (À, Cc) d'un évènement issu d'un courriel, tels qu'écrits.
    destinataires = models.TextField(gettext_lazy("destinataires"), blank=True)
    # Le courriel auquel celui-ci répond — celui qu'il cite sous un bloc « De : …
    # Envoyé : … » (`events/liens.py`). Même règle : même dossier seulement.
    reponse_a = models.ForeignKey(
        "self", verbose_name=gettext_lazy("en réponse à"), related_name="reponses",
        null=True, blank=True, on_delete=models.SET_NULL,
    )

    class Meta:
        # Classé par début, puis par heure : deux faits du même jour se lisent
        # dans l'ordre où ils se sont produits quand l'heure est connue.
        ordering = ["date", models.F("heure").asc(nulls_first=True), "pk"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(date_fin__isnull=True)
                | models.Q(date_fin__gte=models.F("date")),
                name="fin_apres_debut",
            ),
        ]

    def __str__(self):
        return self.title

    @property
    def precision(self):
        """« date », « instant » ou « intervalle », déduit des champs."""
        if self.date_fin:
            return "intervalle"
        if self.heure:
            return "instant"
        return "date"

    @property
    def jours_couverts(self):
        """Tous les jours que l'évènement touche, bornes comprises.

        Un intervalle doit apparaître sur chaque jour qu'il couvre dans les
        vues mois et semaine : l'afficher sur le seul premier jour ferait
        disparaître un chantier de six semaines derrière une seule case.
        """
        from datetime import timedelta

        debut = _jour(self.date)
        fin = _jour(self.date_fin) or debut
        jour = debut
        jours = []
        while jour <= fin:
            jours.append(jour)
            jour += timedelta(days=1)
        return jours

    @property
    def periode_affichee(self):
        """La période dans la langue courante, dans la forme qui convient à sa précision.

        Les trois cas se lisent différemment : on ne dit pas « le 02/06 » de
        la même manière qu'« du 18/08 au 28/09 ».

        Dates et heures prennent le format court de la langue active
        (`SHORT_DATE_FORMAT`, `TIME_FORMAT`) — jamais un format écrit ici —
        et les tournures passent par `gettext`, prêtes pour le néerlandais et
        l'anglais. Les identifiants de traduction sont en français, langue
        source du projet.
        """
        def jour(valeur):
            return date_format(_jour(valeur), "SHORT_DATE_FORMAT")

        def heure(valeur):
            return time_format(valeur, "TIME_FORMAT")

        debut = jour(self.date)
        if self.precision == "date":
            return debut
        if self.precision == "instant":
            return _("%(date)s à %(heure)s") % {"date": debut, "heure": heure(self.heure)}

        fin = jour(self.date_fin)
        if self.heure and self.heure_fin and _jour(self.date_fin) == _jour(self.date):
            # « du 20/10 à 09h00 au 20/10 à 12h30 » répète la date pour dire
            # une réunion d'une matinée. On la dit comme on la dirait.
            return _("le %(date)s de %(debut)s à %(fin)s") % {
                "date": debut,
                "debut": heure(self.heure),
                "fin": heure(self.heure_fin),
            }
        if self.heure and self.heure_fin:
            return _("du %(debut)s à %(heure_debut)s au %(fin)s à %(heure_fin)s") % {
                "debut": debut,
                "heure_debut": heure(self.heure),
                "fin": fin,
                "heure_fin": heure(self.heure_fin),
            }
        return _("du %(debut)s au %(fin)s") % {"debut": debut, "fin": fin}

    @property
    def couleur_affichee(self):
        """La couleur de l'évènement : l'expéditeur prime, puis le choix manuel."""
        if self.expediteur_id:
            return self.expediteur.couleur
        return self.couleur or None

    @property
    def couleur_du_texte(self):
        couleur = self.couleur_affichee
        return couleur_du_texte(couleur) if couleur else None

    def pour_chronologie(self):
        """Ce que vis-timeline attend.

        Un `end` transforme le point en barre : c'est ainsi qu'un chantier de
        six semaines se voit comme six semaines, et non comme un point au
        premier jour.
        """
        from datetime import datetime, timedelta

        element = {"id": self.id, "content": self.title}

        if self.heure:
            element["start"] = datetime.combine(self.date, self.heure).isoformat()
        else:
            element["start"] = self.date.isoformat()

        if self.date_fin:
            if self.heure_fin:
                element["end"] = datetime.combine(
                    self.date_fin, self.heure_fin
                ).isoformat()
            else:
                # vis-timeline exclut la borne de fin : sans ce jour de plus,
                # un intervalle s'arrêterait la veille de sa date de fin.
                element["end"] = (self.date_fin + timedelta(days=1)).isoformat()
            element["type"] = "range"

        couleur = self.couleur_affichee
        if couleur:
            # Validée au format #rrggbb à l'enregistrement : rien d'autre ne
            # peut entrer dans cet attribut `style`.
            element["style"] = "background-color: %s; border-color: %s; color: %s;" % (
                couleur, couleur, couleur_du_texte(couleur)
            )
        return element

    def pour_calendrier(self):
        """Ce que FullCalendar attend.

        Sans heure, l'évènement est « toute la journée » : FullCalendar le
        pose dans la bande du haut des vues semaine et timeline, et non à
        minuit. Comme vis-timeline, il exclut la borne de fin : un intervalle
        en jours s'arrête donc au lendemain de `date_fin`.

        Une heure d'un seul côté (début sans fin, ou l'inverse) suffit à en
        faire un évènement horaire : l'heure saisie fait partie de la preuve,
        elle ne doit pas disparaître dans une case « journée ».
        """
        from datetime import datetime, time, timedelta

        element = {"id": self.id, "title": self.title}
        horaire = bool(self.heure or self.heure_fin)
        element["allDay"] = not horaire

        if horaire:
            element["start"] = datetime.combine(
                self.date, self.heure or time(0, 0)
            ).isoformat()
        else:
            element["start"] = self.date.isoformat()

        if self.date_fin:
            if horaire and self.heure_fin:
                element["end"] = datetime.combine(
                    self.date_fin, self.heure_fin
                ).isoformat()
            elif horaire:
                element["end"] = datetime.combine(
                    self.date_fin + timedelta(days=1), time(0, 0)
                ).isoformat()
            else:
                element["end"] = (self.date_fin + timedelta(days=1)).isoformat()

        couleur = self.couleur_affichee
        if couleur:
            element["backgroundColor"] = couleur
            element["borderColor"] = couleur
            element["textColor"] = couleur_du_texte(couleur)
        return element

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.transfert_de_id and self.transfert_de.dossier_id != self.dossier_id:
            # Un lien vers un autre dossier ouvrirait une fenêtre d'une affaire sur l'autre.
            raise ValidationError(
                {"transfert_de": _("Un transfert ne peut renvoyer qu'à un évènement du même dossier.")}
            )
        if self.reponse_a_id and self.reponse_a.dossier_id != self.dossier_id:
            raise ValidationError(
                {"reponse_a": _("Une réponse ne peut renvoyer qu'à un évènement du même dossier.")}
            )
        if self.date_fin and self.date and self.date_fin < self.date:
            raise ValidationError(
                {"date_fin": _("La fin ne peut pas précéder le début.")}
            )
        if (
            self.date_fin
            and self.date_fin == self.date
            and self.heure
            and self.heure_fin
            and self.heure_fin < self.heure
        ):
            raise ValidationError(
                {"heure_fin": _("La fin ne peut pas précéder le début.")}
            )
        if self.heure_fin and not self.heure and not self.date_fin:
            raise ValidationError(
                {
                    "heure_fin": _("Renseignez l'heure de début, ou une date de fin."),
                }
            )


class Document(models.Model):
    """Une pièce du dossier, stockée une seule fois par dossier.

    Un même fichier peut illustrer plusieurs évènements : il est alors
    référencé plusieurs fois mais stocké une seule, d'où le lien
    plusieurs-à-plusieurs et l'unicité sur `(dossier, content_hash)`.

    La déduplication est **bornée au dossier**, volontairement. Le même
    fichier présent dans deux affaires n'est pas un doublon : ce sont deux
    pièces distinctes, stockées séparément. Deux dossiers ne partagent
    jamais d'octets, donc une suppression dans l'un ne peut rien atteindre
    dans l'autre — c'est la même isolation que pour les évènements.

    `dossier` est porté en propre et non déduit des évènements liés :
    avec plusieurs évènements, « le » dossier du document n'existerait
    plus, et une pièce liée à deux dossiers deviendrait une fuite entre
    deux affaires. Ce champ renverse la décision D3 du spec, qui valait
    tant qu'un document n'avait qu'un seul évènement.
    """

    dossier = models.ForeignKey(
        Dossier,
        verbose_name=gettext_lazy("dossier"),
        related_name="documents",
        on_delete=models.CASCADE,
    )
    events = models.ManyToManyField(
        Event,
        verbose_name=gettext_lazy("évènements"),
        related_name="documents",
        blank=True,
    )

    title = models.CharField(gettext_lazy("titre"), max_length=255)
    file = models.FileField(gettext_lazy("fichier"), upload_to="documents/")
    content_hash = models.CharField(
        gettext_lazy("empreinte du contenu"), max_length=64, editable=False
    )

    uploaded_at = models.DateTimeField(auto_now_add=True)

    #: Le numéro de la pièce dans son dossier — « pièce 12 » —, attribué au
    #: dépôt et **jamais changé ni réattribué** : un numéro cité dans un
    #: courrier ou un bordereau doit désigner la même pièce pour toujours.
    #: Des trous apparaissent quand une pièce est supprimée ; c'est voulu.
    numero = models.PositiveIntegerField(gettext_lazy("numéro"), null=True, blank=True, editable=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["dossier", "content_hash"],
                name="unique_document_per_dossier",
            ),
            models.UniqueConstraint(
                fields=["dossier", "numero"],
                name="numero_de_piece_unique_par_dossier",
            ),
        ]
        verbose_name = gettext_lazy("document")
        verbose_name_plural = gettext_lazy("documents")

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        """Attribue le numéro au premier enregistrement, par tous les chemins
        (formulaire, import de courriels, administration).

        L'incrément est une seule requête conditionnelle sur le compteur du
        dossier (`UPDATE … SET n = n + 1`), dans la même transaction que la
        pièce : deux dépôts simultanés ne peuvent pas obtenir le même numéro,
        et un dépôt annulé ne consomme qu'un numéro (un trou, jamais un
        doublon).
        """
        if self.numero is None and self.dossier_id is not None:
            with transaction.atomic():
                Dossier.objects.filter(pk=self.dossier_id).update(
                    dernier_numero_piece=models.F("dernier_numero_piece") + 1
                )
                self.numero = Dossier.objects.values_list(
                    "dernier_numero_piece", flat=True
                ).get(pk=self.dossier_id)
                return super().save(*args, **kwargs)
        return super().save(*args, **kwargs)


class Invitation(models.Model):
    """Invitation à rejoindre un dossier, envoyée par courriel.

    Le jeton vaut autorisation : il désigne un dossier, un rôle, et une
    adresse précise. L'accepter crée le `DossierAccess` correspondant, avec
    `origin=INVITATION` — ce qui permet de distinguer plus tard un accès
    accordé de la main du propriétaire d'un accès venu d'un lien.

    Trois garde-fous, parce qu'un jeton circule par courriel et qu'un
    courriel se retransfère :

    - **expiration** obligatoire, par défaut quatorze jours. Une invitation
      oubliée cesse d'être une porte ouverte ;
    - **usage unique** : `accepted_at` est posé à l'acceptation, et une
      invitation acceptée ne vaut plus rien ;
    - **révocation** : le propriétaire peut l'annuler avant acceptation.

    L'adresse n'est pas vérifiée à l'acceptation. Qui détient le jeton peut
    s'en servir, quel que soit le compte avec lequel il est connecté : c'est
    la nature d'un lien transmissible, et c'est pourquoi ces invitations ne
    doivent pas remplacer l'octroi direct pour les personnes que l'on
    connaît déjà.
    """

    DUREE_PAR_DEFAUT = timedelta(days=14)

    class Status(models.TextChoices):
        PENDING = "pending", gettext_lazy("En attente")
        ACCEPTED = "accepted", gettext_lazy("Acceptée")
        REVOKED = "revoked", gettext_lazy("Révoquée")

    dossier = models.ForeignKey(
        Dossier,
        verbose_name=gettext_lazy("dossier"),
        related_name="invitations",
        on_delete=models.CASCADE,
    )
    email = models.EmailField(gettext_lazy("adresse invitée"))
    role = models.CharField(
        gettext_lazy("rôle"), max_length=10, choices=DossierAccess.Role.choices
    )
    token = models.CharField(gettext_lazy("jeton"), max_length=64, unique=True, editable=False)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=gettext_lazy("émise par"),
        related_name="invitations_emises",
        on_delete=models.CASCADE,
    )
    created_at = models.DateTimeField(gettext_lazy("émise le"), auto_now_add=True)
    expires_at = models.DateTimeField(gettext_lazy("expire le"))

    accepted_at = models.DateTimeField(gettext_lazy("acceptée le"), null=True, blank=True)
    accepted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=gettext_lazy("acceptée par"),
        null=True,
        blank=True,
        related_name="invitations_acceptees",
        on_delete=models.SET_NULL,
    )
    revoked_at = models.DateTimeField(gettext_lazy("révoquée le"), null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = gettext_lazy("invitation")
        verbose_name_plural = gettext_lazy("invitations")

    def __str__(self):
        return f"{self.email} — {self.dossier}"

    def save(self, *args, **kwargs):
        if not self.token:
            # 32 octets d'entropie : un jeton d'invitation vaut un accès en
            # lecture à des pièces de dossier, il ne doit pas se deviner.
            self.token = secrets.token_urlsafe(32)[:64]
        if not self.expires_at:
            self.expires_at = timezone.now() + self.DUREE_PAR_DEFAUT
        return super().save(*args, **kwargs)

    @property
    def status(self):
        if self.revoked_at:
            return self.Status.REVOKED
        if self.accepted_at:
            return self.Status.ACCEPTED
        return self.Status.PENDING

    @property
    def is_expired(self):
        return self.expires_at <= timezone.now()

    @property
    def is_usable(self):
        """Une invitation ne sert qu'une fois, avant sa date, et non révoquée."""
        return self.status == self.Status.PENDING and not self.is_expired


class ShareLink(models.Model):
    """Lien de consultation d'un dossier, utilisable **sans compte**.

    C'est le seul accès anonyme de l'application, et il contredit
    délibérément la règle « tout est privé » posée en L1. Le propriétaire l'a
    demandé en connaissance de cause, pour transmettre un dossier à un
    avocat, un expert ou une association de consommateurs qui n'ont pas de
    compte et n'en veulent pas.

    Deux limites lui sont imposées par construction :

    - **un seul dossier**, celui qui le porte ;
    - **révocable à tout moment**, et expirable — éventuellement sans fin,
      sur décision explicite du propriétaire.

    Le droit accordé est en revanche au choix du propriétaire, lecture ou
    écriture. Un lien en écriture mérite d'être pesé : il permet de
    modifier des évènements et de retirer des pièces **sans compte**, donc
    sans que rien ne relie l'action à une personne. Le compteur de
    consultations est le seul témoin qui reste.

    Les vues qui le servent forment un chemin d'accès séparé : elles ne
    passent par aucun des helpers de permission, ne lisent jamais
    `request.user`, et bornent chaque requête au dossier du lien. Un accès
    anonyme greffé sur le modèle de droits des comptes aurait fini par
    hériter d'un privilège que personne n'a voulu lui donner.

    Le jeton voyage dans l'URL. Les pages servies par ce chemin portent donc
    `Referrer-Policy: no-referrer` et `X-Robots-Tag: noindex`, pour qu'il ne
    parte pas dans l'en-tête d'un site tiers ni dans un index public.
    """

    dossier = models.ForeignKey(
        Dossier,
        verbose_name=gettext_lazy("dossier"),
        related_name="share_links",
        on_delete=models.CASCADE,
    )

    role = models.CharField(
        gettext_lazy("droit accordé"),
        max_length=10,
        choices=DossierAccess.Role.choices,
        default=DossierAccess.Role.READ,
    )
    label = models.CharField(
        gettext_lazy("destinataire"),
        max_length=255,
        blank=True,
        help_text=gettext_lazy("Pour vous souvenir à qui ce lien a été transmis."),
    )
    token = models.CharField(gettext_lazy("jeton"), max_length=64, unique=True, editable=False)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=gettext_lazy("créé par"),
        related_name="share_links_crees",
        on_delete=models.CASCADE,
    )
    created_at = models.DateTimeField(gettext_lazy("créé le"), auto_now_add=True)
    expires_at = models.DateTimeField(gettext_lazy("expire le"), null=True, blank=True)
    revoked_at = models.DateTimeField(gettext_lazy("révoqué le"), null=True, blank=True)

    last_used_at = models.DateTimeField(gettext_lazy("dernier accès"), null=True, blank=True)
    use_count = models.PositiveIntegerField(gettext_lazy("consultations"), default=0)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = gettext_lazy("lien de partage")
        verbose_name_plural = gettext_lazy("liens de partage")

    def __str__(self):
        return self.label or _("Lien vers %(dossier)s") % {"dossier": self.dossier}

    def save(self, *args, **kwargs):
        if not self.token:
            # Ce jeton ouvre un dossier de pièces à qui le détient : il doit
            # être hors de portée d'une énumération.
            self.token = secrets.token_urlsafe(32)[:64]
        return super().save(*args, **kwargs)

    @property
    def is_expired(self):
        return self.expires_at is not None and self.expires_at <= timezone.now()

    @property
    def is_active(self):
        return self.revoked_at is None and not self.is_expired

    def enregistrer_consultation(self):
        """Trace l'usage : un lien qui circule doit se voir circuler.

        Sans ce compteur, le propriétaire n'a aucun moyen de remarquer qu'un
        lien transmis à une personne est consulté par dix.
        """
        self.use_count = models.F("use_count") + 1
        self.last_used_at = timezone.now()
        self.save(update_fields=["use_count", "last_used_at"])


class JournalQuerySet(models.QuerySet):
    def delete(self):
        raise PermissionError("Le journal d'un dossier ne se supprime pas.")

    def update(self, **kwargs):
        raise PermissionError("Le journal d'un dossier ne se modifie pas.")


class Journal(models.Model):
    """Ce qui s'est passé dans un dossier : qui, quand, quoi.

    Modifier un évènement écrase l'ancienne version ; sur un dossier de
    preuves à plusieurs mains, il faut pouvoir dire après coup qui a changé
    quoi, et ce qu'il y avait avant. Le journal le garde.

    **Ajout seul.** Une entrée ne se modifie ni ne se supprime — ni par
    `save()` sur une entrée existante, ni par `delete()`, ni par un queryset.
    Le dossier est en `PROTECT` : un dossier qui a un historique ne peut pas
    disparaître en emportant la trace de ce qui lui est arrivé. Les clés vers
    l'auteur, le lien, l'évènement sont en `SET_NULL`, et `objet` garde le
    libellé au moment des faits : l'entrée reste lisible si l'objet disparaît.

    Il commence le 2026-10-08 : ce qui précède n'a laissé aucune trace.
    """

    class Action(models.TextChoices):
        EVENEMENT_CREE = "evenement_cree", gettext_lazy("Évènement créé")
        EVENEMENT_MODIFIE = "evenement_modifie", gettext_lazy("Évènement modifié")
        EVENEMENT_SUPPRIME = "evenement_supprime", gettext_lazy("Évènement supprimé")
        PIECE_AJOUTEE = "piece_ajoutee", gettext_lazy("Pièce ajoutée")
        PIECE_RETIREE = "piece_retiree", gettext_lazy("Pièce retirée d'un évènement")
        PIECE_SUPPRIMEE = "piece_supprimee", gettext_lazy("Pièce supprimée")
        PIECE_MODIFIEE = "piece_modifiee", gettext_lazy("Pièce modifiée")
        ACCES_ACCORDE = "acces_accorde", gettext_lazy("Accès accordé")
        ACCES_RETIRE = "acces_retire", gettext_lazy("Accès retiré")
        ACCES_MODIFIE = "acces_modifie", gettext_lazy("Accès modifié")
        INVITATION_EMISE = "invitation_emise", gettext_lazy("Invitation envoyée")
        INVITATION_ANNULEE = "invitation_annulee", gettext_lazy("Invitation annulée")
        INVITATION_ACCEPTEE = "invitation_acceptee", gettext_lazy("Invitation acceptée")
        LIEN_CREE = "lien_cree", gettext_lazy("Lien de partage créé")
        LIEN_REVOQUE = "lien_revoque", gettext_lazy("Lien de partage révoqué")
        EXPORT = "export", gettext_lazy("Dossier exporté")
        COURRIEL_IMPORTE = "courriel_importe", gettext_lazy("Évènement importé par courriel")
        COURRIELS_RELEVES = "courriels_releves", gettext_lazy("Courriels relevés")
        DOSSIER_CREE = "dossier_cree", gettext_lazy("Dossier créé")
        DOSSIER_MODIFIE = "dossier_modifie", gettext_lazy("Dossier modifié")
        EVENEMENTS_FUSIONNES = "evenements_fusionnes", gettext_lazy("Évènements fusionnés")
        DOUBLON_ECARTE = "doublon_ecarte", gettext_lazy("Doublon possible écarté")

    #: Ce qui touche au partage : seul le propriétaire le voit, comme il est
    #: seul à le gérer (invariant 5).
    ACTIONS_DE_PARTAGE = frozenset({
        Action.ACCES_ACCORDE, Action.ACCES_RETIRE, Action.ACCES_MODIFIE,
        Action.INVITATION_EMISE, Action.INVITATION_ANNULEE, Action.INVITATION_ACCEPTEE,
        Action.LIEN_CREE, Action.LIEN_REVOQUE,
    })

    dossier = models.ForeignKey(
        Dossier, verbose_name=gettext_lazy("dossier"), related_name="journal",
        on_delete=models.PROTECT,
    )
    moment = models.DateTimeField(gettext_lazy("moment"), auto_now_add=True, db_index=True)
    auteur = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name=gettext_lazy("auteur"),
        related_name="+", null=True, blank=True, on_delete=models.SET_NULL,
    )
    lien = models.ForeignKey(
        "ShareLink", verbose_name=gettext_lazy("lien de partage"),
        related_name="+", null=True, blank=True, on_delete=models.SET_NULL,
    )
    #: Une modification faite depuis l'administration, et non l'application.
    via_admin = models.BooleanField(gettext_lazy("via l'administration"), default=False)
    action = models.CharField(gettext_lazy("action"), max_length=30, choices=Action.choices)
    evenement = models.ForeignKey(
        Event, verbose_name=gettext_lazy("évènement"), related_name="+",
        null=True, blank=True, on_delete=models.SET_NULL,
    )
    objet = models.CharField(gettext_lazy("objet"), max_length=255, blank=True)
    details = models.JSONField(gettext_lazy("détails"), default=dict, blank=True)

    objects = JournalQuerySet.as_manager()

    class Meta:
        ordering = ["-moment", "-pk"]
        verbose_name = gettext_lazy("entrée du journal")
        verbose_name_plural = gettext_lazy("journal")

    def __str__(self):
        return "%s — %s" % (self.get_action_display(), self.objet)

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise PermissionError("Une entrée du journal ne se modifie pas.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("Une entrée du journal ne se supprime pas.")


class CourrielImporte(models.Model):
    """Un courriel déjà transformé en évènement : on ne l'importe pas deux fois.

    La clé est l'identifiant (`Message-ID`) du courriel **d'origine** : le même
    courriel transféré deux fois au même dossier ne crée qu'un évènement. Le
    même courriel transféré à deux dossiers en crée un dans chacun — deux
    affaires ne partagent rien.
    """

    dossier = models.ForeignKey(
        Dossier, verbose_name=gettext_lazy("dossier"), related_name="courriels_importes",
        on_delete=models.PROTECT,
    )
    message_id = models.CharField(gettext_lazy("identifiant du courriel"), max_length=255)
    evenement = models.ForeignKey(
        Event, verbose_name=gettext_lazy("évènement"), related_name="+",
        null=True, blank=True, on_delete=models.SET_NULL,
    )
    expediteur = models.CharField(gettext_lazy("expéditeur"), max_length=255, blank=True)
    # Le courriel que celui-ci cite (`liens.citation`) : de quoi le relier *en aval*,
    # quand le courriel cité arrive après lui, sans relire son .eml.
    cite_de = models.CharField(gettext_lazy("cite : expéditeur"), max_length=255, blank=True)
    cite_le = models.DateTimeField(gettext_lazy("cite : date"), null=True, blank=True)
    cite_objet = models.CharField(gettext_lazy("cite : objet"), max_length=255, blank=True)
    importe_le = models.DateTimeField(gettext_lazy("importé le"), auto_now_add=True)

    class Meta:
        verbose_name = gettext_lazy("courriel importé")
        verbose_name_plural = gettext_lazy("courriels importés")
        constraints = [
            models.UniqueConstraint(fields=["dossier", "message_id"], name="courriel_une_fois_par_dossier"),
        ]

    def __str__(self):
        return self.message_id


class PaireIgnoree(models.Model):
    """Deux évènements que la détection de doublons prenait pour un seul, et
    qu'une personne a déclarés distincts : on ne les propose plus.

    `evenement_a` a toujours le plus petit identifiant des deux (`ordonner`),
    pour qu'une paire n'existe qu'une fois quel que soit l'ordre du clic.
    """

    dossier = models.ForeignKey(
        Dossier, verbose_name=gettext_lazy("dossier"), related_name="paires_ignorees",
        on_delete=models.CASCADE,
    )
    evenement_a = models.ForeignKey(Event, related_name="+", on_delete=models.CASCADE)
    evenement_b = models.ForeignKey(Event, related_name="+", on_delete=models.CASCADE)
    ignoree_par = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="+", null=True, blank=True,
        on_delete=models.SET_NULL,
    )
    ignoree_le = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = gettext_lazy("paire de doublons écartée")
        verbose_name_plural = gettext_lazy("paires de doublons écartées")
        constraints = [
            models.UniqueConstraint(fields=["evenement_a", "evenement_b"], name="paire_ignoree_une_fois"),
        ]

    @staticmethod
    def ordonner(premier, second):
        return (premier, second) if premier.pk < second.pk else (second, premier)


class Etiquette(models.Model):
    """Une étiquette d'évènement — « devis », « mise en demeure », « photo du
    chantier » —, propre à un dossier : deux affaires ne partagent pas leur
    vocabulaire, et renommer une étiquette ici ne touche rien ailleurs.

    Un évènement peut en porter plusieurs (un courriel qui est aussi une mise
    en demeure). Elles servent de filtre dans toutes les vues.
    """

    dossier = models.ForeignKey(
        Dossier, verbose_name=gettext_lazy("dossier"), related_name="etiquettes",
        on_delete=models.CASCADE,
    )
    nom = models.CharField(gettext_lazy("nom"), max_length=60)
    evenements = models.ManyToManyField(
        Event, verbose_name=gettext_lazy("évènements"), related_name="etiquettes", blank=True,
    )

    class Meta:
        ordering = ["nom"]
        verbose_name = gettext_lazy("étiquette")
        verbose_name_plural = gettext_lazy("étiquettes")
        constraints = [
            models.UniqueConstraint(fields=["dossier", "nom"], name="etiquette_unique_par_dossier"),
        ]

    def __str__(self):
        return self.nom
