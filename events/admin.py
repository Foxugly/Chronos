from django.contrib import admin
from django.utils.translation import gettext_lazy
from .journal import consigner, differences, instantane
from .models import Etiquette, Document, Dossier, DossierAccess, Event, Journal


def _journal_admin(request, dossier, action, **kwargs):
    """Toute trace laissée depuis l'admin porte `via_admin` : le journal doit
    dire qu'une modification n'est pas passée par l'application."""
    return consigner(dossier, action, request=request, via_admin=True, **kwargs)


def _champs_changes(avant, apres):
    """`{champ: [avant, après]}` pour deux dictionnaires de valeurs simples."""
    return {champ: [avant[champ], apres[champ]] for champ in avant if avant[champ] != apres[champ]}


class DocumentInline(admin.TabularInline):
    """Les pièces liées à un évènement.

    Le lien est desormais plusieurs-à-plusieurs : une même pièce peut
    illustrer plusieurs évènements sans être stockée deux fois. L'inline
    porte donc sur la table de liaison, et non sur `Document` lui-même —
    y attacher `Document` directement proposerait de créer une nouvelle
    pièce là où l'on veut seulement en rattacher une existante.
    """

    model = Document.events.through
    extra = 1
    verbose_name = gettext_lazy("pièce liée")
    verbose_name_plural = gettext_lazy("pièces liées")


class DossierAccessInline(admin.TabularInline):
    """Les accès s'accordent depuis la page du dossier.

    C'est l'unique moyen de partager un dossier tant que L4 n'a pas
    livré l'interface d'invitation.

    `fields` est énuméré afin que `granted_at` (auto) reste hors du
    formulaire, et `fk_name` est explicite : `DossierAccess` n'a
    aujourd'hui qu'une seule clé vers `Dossier`, mais Django choisirait
    sinon tout seul, et l'ajout d'une seconde clé casserait l'inline
    sans que rien ne le dise ici.
    """

    model = DossierAccess
    fk_name = "dossier"
    extra = 1
    autocomplete_fields = ("user",)
    fields = ("user", "role", "origin", "granted_by")


class NoBulkDeleteMixin:
    """Retire « Supprimer les objets sélectionnés » de la liste déroulante.

    L'action groupée supprime en une validation tout ce qui est coché, et la
    case d'en-tête coche la page entière. Sur un dossier de preuves dont la
    base est l'unique copie, c'est une perte irréversible à deux clics. La
    suppression unitaire, depuis la fiche, reste possible.
    """

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        return actions


def _acces_du_dossier(dossier):
    """`{pk: {"user": nom, "role": code}}` : l'état des accès, pour comparer."""
    return {
        acces.pk: {"user": acces.user.get_username(), "role": acces.role}
        for acces in DossierAccess.objects.filter(dossier=dossier).select_related("user")
    }


def _consigner_acces(request, dossier, avant, apres):
    """Compare deux états des accès d'un dossier et inscrit ce qui a changé."""
    for pk, etat in apres.items():
        if pk not in avant:
            _journal_admin(request, dossier, Journal.Action.ACCES_ACCORDE,
                           objet=etat["user"], details={"role": etat["role"]})
        elif avant[pk] != etat:
            _journal_admin(request, dossier, Journal.Action.ACCES_MODIFIE, objet=etat["user"],
                           details={"modele": "acces", "changements": _champs_changes(avant[pk], etat)})
    for pk, etat in avant.items():
        if pk not in apres:
            _journal_admin(request, dossier, Journal.Action.ACCES_RETIRE,
                           objet=etat["user"], details={"role": etat["role"]})


@admin.register(Dossier)
class DossierAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "created_at")
    search_fields = ("name",)
    inlines = [DossierAccessInline]

    def has_delete_permission(self, request, obj=None):
        """Un dossier ne se supprime pas depuis l'admin.

        `Event` et `Document` sont en `CASCADE` : supprimer un dossier efface
        ses 24 évènements et ses 49 pièces, sans autre confirmation que la
        page récapitulative de Django. Il n'existe aucun cas d'usage où cela
        doit se faire d'un clic ; quand il s'en présentera un, il passera par
        une opération délibérée en base, sauvegarde faite.
        """
        return False

    @staticmethod
    def _etat(dossier):
        return {"name": dossier.name, "owner": dossier.owner.get_username(),
                "alias_courriel": dossier.alias_courriel or ""}

    def save_model(self, request, obj, form, change):
        avant = self._etat(Dossier.objects.get(pk=obj.pk)) if change else None
        super().save_model(request, obj, form, change)
        if not change:
            _journal_admin(request, obj, Journal.Action.DOSSIER_CREE, objet=obj.name,
                           details={"modele": "dossier", "valeurs": self._etat(obj)})
            return
        changements = _champs_changes(avant, self._etat(obj))
        if changements:
            _journal_admin(request, obj, Journal.Action.DOSSIER_MODIFIE, objet=obj.name,
                           details={"modele": "dossier", "changements": changements})

    def save_formset(self, request, form, formset, change):
        """Les accès accordés, modifiés ou retirés depuis la fiche du dossier."""
        dossier = form.instance
        avant = _acces_du_dossier(dossier) if dossier.pk else {}
        super().save_formset(request, form, formset, change)
        _consigner_acces(request, dossier, avant, _acces_du_dossier(dossier))


@admin.register(DossierAccess)
class DossierAccessAdmin(admin.ModelAdmin):
    list_display = ("dossier", "user", "role", "origin", "granted_at")
    list_filter = ("role", "origin")

    def save_model(self, request, obj, form, change):
        dossiers = {obj.dossier_id}
        if change:
            dossiers.add(DossierAccess.objects.get(pk=obj.pk).dossier_id)
        avant = {pk: _acces_du_dossier(pk) for pk in dossiers}
        super().save_model(request, obj, form, change)
        for pk in dossiers:
            _consigner_acces(request, Dossier.objects.get(pk=pk), avant[pk], _acces_du_dossier(pk))

    def delete_model(self, request, obj):
        _journal_admin(request, obj.dossier, Journal.Action.ACCES_RETIRE,
                       objet=obj.user.get_username(), details={"role": obj.role})
        super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        """L'action groupée : une entrée par accès retiré."""
        for acces in queryset.select_related("dossier", "user"):
            _journal_admin(request, acces.dossier, Journal.Action.ACCES_RETIRE,
                           objet=acces.user.get_username(), details={"role": acces.role})
        super().delete_queryset(request, queryset)


def _consigner_liens(request, avant, apres, *, evenement=None, document=None):
    """Liens pièce ↔ évènement ajoutés ou retirés, vus d'un côté ou de l'autre.

    `avant` / `apres` : ensembles de paires `(evenement_id, document_id)`.
    """
    for evenement_id, document_id in sorted(apres - avant):
        e = evenement or Event.objects.get(pk=evenement_id)
        d = document or Document.objects.get(pk=document_id)
        _journal_admin(request, e.dossier, Journal.Action.PIECE_AJOUTEE, objet=d.title, evenement=e,
                       details={"empreinte": d.content_hash, "deja_presente": True, "evenement": e.title})
    for evenement_id, document_id in sorted(avant - apres):
        e = evenement or Event.objects.filter(pk=evenement_id).first()
        d = document or Document.objects.get(pk=document_id)
        _journal_admin(request, d.dossier, Journal.Action.PIECE_RETIREE, objet=d.title, evenement=e,
                       details={"empreinte": d.content_hash, "evenement": e.title if e else ""})


def _paires(**filtre):
    return set(Document.events.through.objects.filter(**filtre).values_list("event_id", "document_id"))


@admin.register(Event)
class EventAdmin(NoBulkDeleteMixin, admin.ModelAdmin):
    list_display = ("title", "date", "dossier")
    list_filter = ("dossier",)
    search_fields = ("title",)
    inlines = [DocumentInline]
    readonly_fields = ("version",)

    def save_model(self, request, obj, form, change):
        """Une correction faite ici invalide les formulaires ouverts ailleurs.

        L'admin n'envoie pas de version : il garde le dernier mot entre
        deux administrateurs. Mais il l'incrémente, pour qu'un collaborateur
        qui enregistrerait ensuite un formulaire chargé avant soit refusé
        plutôt que d'écraser la correction.
        """
        if change:
            obj.version += 1
        # L'état d'avant est relu en base : le formulaire de l'admin a déjà
        # recopié les nouvelles valeurs sur `obj`.
        avant = instantane(Event.objects.get(pk=obj.pk)) if change else None
        obj._liens_avant = _paires(event_id=obj.pk) if change else set()
        super().save_model(request, obj, form, change)
        if not change:
            _journal_admin(request, obj.dossier, Journal.Action.EVENEMENT_CREE, objet=obj.title,
                           evenement=obj, details={"valeurs": instantane(obj)})
        else:
            changements = differences(avant, instantane(obj))
            if changements:
                _journal_admin(request, obj.dossier, Journal.Action.EVENEMENT_MODIFIE,
                               objet=obj.title, evenement=obj, details={"changements": changements})

    def save_related(self, request, form, formsets, change):
        """Les pièces rattachées ou détachées par l'inline."""
        super().save_related(request, form, formsets, change)
        evenement = form.instance
        _consigner_liens(request, getattr(evenement, "_liens_avant", set()),
                         _paires(event_id=evenement.pk), evenement=evenement)

    def delete_model(self, request, obj):
        _journal_admin(request, obj.dossier, Journal.Action.EVENEMENT_SUPPRIME, objet=obj.title,
                       details={"valeurs": instantane(obj)})
        super().delete_model(request, obj)


@admin.register(Document)
class DocumentAdmin(NoBulkDeleteMixin, admin.ModelAdmin):
    list_display = ("title", "dossier", "nombre_d_evenements", "uploaded_at")
    list_filter = ("dossier",)
    search_fields = ("title",)
    filter_horizontal = ("events",)
    readonly_fields = ("content_hash",)

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("events")

    @admin.display(description=gettext_lazy("évènements"))
    def nombre_d_evenements(self, obj):
        """Rend visible le partage : une pièce à zéro évènement est
        orpheline et ne devrait pas exister — la vue de détachement la
        supprime quand son dernier lien tombe."""
        return obj.events.count()

    @staticmethod
    def _etat(document):
        return {"title": document.title, "file": document.file.name}

    def save_model(self, request, obj, form, change):
        """Un fichier remplacé ici garde l'empreinte de l'ancien (`content_hash`
        est en lecture seule) : l'export le signalera comme différent. C'est
        voulu — une pièce de preuve remplacée doit se voir, et le journal
        dit quand et par qui."""
        ancien = Document.objects.get(pk=obj.pk) if change else None
        obj._etat_avant = self._etat(ancien) if change else None
        obj._liens_avant = _paires(document_id=obj.pk) if change else set()
        super().save_model(request, obj, form, change)
        if change:
            changements = _champs_changes(obj._etat_avant, self._etat(obj))
            if changements:
                _journal_admin(request, obj.dossier, Journal.Action.PIECE_MODIFIEE, objet=obj.title,
                               details={"modele": "document", "changements": changements,
                                        "empreinte": obj.content_hash})

    def save_related(self, request, form, formsets, change):
        """Les évènements liés, choisis dans le sélecteur de l'admin."""
        super().save_related(request, form, formsets, change)
        document = form.instance
        _consigner_liens(request, getattr(document, "_liens_avant", set()),
                         _paires(document_id=document.pk), document=document)

    def delete_model(self, request, obj):
        """Django ne touche pas au fichier : il reste sur le disque, sans ligne
        pour le retrouver. Le journal garde son nom et son empreinte."""
        _journal_admin(request, obj.dossier, Journal.Action.PIECE_SUPPRIMEE, objet=obj.title,
                       details={"empreinte": obj.content_hash, "fichier": obj.file.name})
        super().delete_model(request, obj)


@admin.register(Etiquette)
class EtiquetteAdmin(admin.ModelAdmin):
    list_display = ("nom", "dossier")
    list_filter = ("dossier",)
    search_fields = ("nom",)
    filter_horizontal = ("evenements",)


@admin.register(Journal)
class JournalAdmin(admin.ModelAdmin):
    """Le journal se consulte ; il ne s'écrit, ne se corrige ni ne s'efface
    jamais à la main — le modèle le refuse de toute façon."""

    list_display = ("moment", "dossier", "action", "objet", "auteur", "lien", "via_admin")
    list_filter = ("dossier", "action", "via_admin")
    search_fields = ("objet",)
    date_hierarchy = "moment"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
