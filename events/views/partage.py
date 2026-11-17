"""Le partage, réservé au propriétaire (invariant 5) : accès, invitations, liens."""

import logging

from allauth.account.models import EmailAddress
from django.contrib import messages
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.utils import translation
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache

from ..filtres import identifiant
from ..forms import DossierAccessForm, InvitationForm, ShareLinkEditForm, ShareLinkForm
from ..journal import changements, consigner, etat_du_lien
from ..models import DossierAccess, Invitation, Journal
from ..permissions import get_dossier_for_owner

logger = logging.getLogger(__name__)


# Le geste et sa trace au journal : une seule transaction (`journal.py`).
@transaction.atomic
@never_cache
def dossier_share(request, dossier_id):
    """Qui a accès au dossier, et comment le leur accorder ou le reprendre.

    Réservé au propriétaire : `get_dossier_for_owner` renvoie 404 à un
    collaborateur en écriture, qui peut tout modifier dans le dossier
    mais ne décide pas qui y entre.
    """
    dossier = get_dossier_for_owner(request.user, dossier_id)

    if request.method == "POST":
        form = DossierAccessForm(request.POST, dossier=dossier)
        if form.is_valid():
            acces = form.save(commit=False)
            acces.dossier = dossier
            acces.origin = DossierAccess.Origin.DIRECT
            acces.granted_by = request.user
            acces.save()
            consigner(
                dossier, Journal.Action.ACCES_ACCORDE, request=request,
                objet=acces.user.get_username(), details={"role": acces.role},
            )
            messages.success(
                request,
                _("Accès accordé à %(compte)s (%(droit)s).")
                % {
                    "compte": acces.user.get_username(),
                    "droit": acces.get_role_display(),
                },
            )
            return redirect("dossier_share", dossier_id=dossier.pk)
    else:
        form = DossierAccessForm(dossier=dossier)

    return render(request, "events/dossier_share.html", _contexte_du_partage(
        request, dossier, form=form, lien_cree=_lien_tout_juste_cree(request, dossier)))


def _contexte_du_partage(request, dossier, **surcharges):
    """Ce que montre l'écran de partage ; `surcharges` remplace un formulaire
    (celui qui revient avec ses erreurs) ou ajoute le lien tout juste créé."""
    contexte = {
        "dossier": dossier,
        "form": DossierAccessForm(dossier=dossier),
        "invitation_form": InvitationForm(dossier=dossier),
        "accesses": dossier.accesses.select_related(
            "user", "granted_by"
        ).order_by("user__username"),
        "invitations": dossier.invitations.select_related("created_by"),
        "share_link_form": ShareLinkForm(),
        "share_links": [
            # Chaque lien non révoqué porte son formulaire de modification,
            # rendu dans sa popup (`auto_id` : des identifiants uniques par lien).
            (lien, None if lien.revoked_at else ShareLinkEditForm(instance=lien, auto_id=f"lien-{lien.pk}-%s"))
            for lien in dossier.share_links.all()
        ],
    }
    contexte.update(surcharges)
    return contexte


def _lien_tout_juste_cree(request, dossier):
    """Le lien que `share_link_create` vient d'émettre (`?nouveau=<pk>`), pour le
    montrer en tête avec son bouton « Copier ». Borné au dossier, et seulement
    s'il est encore actif : un identifiant d'ailleurs, ou d'un lien coupé, ne
    montre rien."""
    valeur = identifiant(request.GET.get("nouveau"))
    if valeur is None:
        return None
    lien = dossier.share_links.filter(pk=valeur).first()
    return lien if lien is not None and lien.is_active else None


@transaction.atomic
def dossier_access_revoke(request, dossier_id, access_id):
    """Reprend un accès. POST uniquement : un GET suffirait sinon à le
    révoquer depuis un lien, une image ou un préchargement.
    """
    if request.method != "POST":
        return JsonResponse({"success": False}, status=405)

    dossier = get_dossier_for_owner(request.user, dossier_id)
    acces = get_object_or_404(dossier.accesses, pk=access_id)
    nom = acces.user.get_username()
    consigner(
        dossier, Journal.Action.ACCES_RETIRE, request=request, objet=nom,
        details={"role": acces.role},
    )
    acces.delete()
    messages.success(request, _("Accès retiré à %(compte)s.") % {"compte": nom})
    return redirect("dossier_share", dossier_id=dossier.pk)


def _date_courte(moment):
    """Une date à l'heure de Bruxelles, au format court de la langue courante.

    `strftime` sur l'instant UTC stocké donnait la veille pour une
    expiration posée avant 2 h du matin, et figeait le format français.
    """
    return date_format(timezone.localtime(moment), "SHORT_DATE_FORMAT")


def _envoyer_invitation(request, invitation):
    """Envoie le courriel portant le lien d'acceptation.

    Le backend est la console : en développement, le message s'affiche dans
    le terminal du serveur. C'est volontairement visible — une invitation
    silencieusement perdue serait pire qu'une invitation non envoyée.
    """
    lien = request.build_absolute_uri(
        reverse("invitation_accept", args=[invitation.token])
    )
    # Dans la langue de la personne invitée : objet, texte, droit et format de
    # la date. L'interface du propriétaire, elle, reste dans la sienne.
    with translation.override(invitation.langue):
        send_mail(
            subject=_("Accès au dossier « %(dossier)s »")
            % {"dossier": invitation.dossier.name},
            message=(
                _(
                    "%(auteur)s vous donne accès au dossier « %(dossier)s ».\n\n"
                    "Droit accordé : %(droit)s\n\n"
                    "Pour l'accepter, ouvrez ce lien :\n%(lien)s\n\n"
                    "Ce lien expire le %(date)s. Il ne sert qu'une fois.\n"
                )
                % {
                    "auteur": invitation.created_by.get_username(),
                    "dossier": invitation.dossier.name,
                    "droit": invitation.get_role_display(),
                    "lien": lien,
                    "date": _date_courte(invitation.expires_at),
                }
            ),
            from_email=None,
            recipient_list=[invitation.email],
        )
    return lien


def dossier_invite(request, dossier_id):
    """Émet une invitation. POST uniquement, depuis l'écran de partage."""
    dossier = get_dossier_for_owner(request.user, dossier_id)

    if request.method != "POST":
        return redirect("dossier_share", dossier_id=dossier.pk)

    form = InvitationForm(request.POST, dossier=dossier)
    if form.is_valid():
        # Tout ou rien : une invitation dont le courriel n'est pas parti
        # n'existe pas, et le journal ne dit pas « envoyée » à tort. Sinon,
        # l'échec laissait une invitation en attente qui bloquait la suivante.
        try:
            with transaction.atomic():
                invitation = form.save(commit=False)
                invitation.dossier = dossier
                invitation.created_by = request.user
                invitation.save()
                consigner(
                    dossier, Journal.Action.INVITATION_EMISE, request=request,
                    objet=invitation.email,
                    details={"role": invitation.role, "expire": invitation.expires_at.isoformat()},
                )
                _envoyer_invitation(request, invitation)
        except Exception:
            logger.exception("Invitation : le courriel n'a pas pu partir.")
            messages.error(
                request,
                _("Le courriel d'invitation n'a pas pu partir : rien n'a été envoyé. Réessayez plus tard."),
            )
            return redirect("dossier_share", dossier_id=dossier.pk)
        messages.success(
            request,
            _("Invitation envoyée à %(email)s. Elle expire le %(date)s.")
            % {
                "email": invitation.email,
                "date": _date_courte(invitation.expires_at),
            },
        )
        return redirect("dossier_share", dossier_id=dossier.pk)

    return render(request, "events/dossier_share.html",
                  _contexte_du_partage(request, dossier, invitation_form=form), status=400)


@transaction.atomic
def invitation_revoke(request, dossier_id, invitation_id):
    """Annule une invitation en attente. POST uniquement."""
    if request.method != "POST":
        return JsonResponse({"success": False}, status=405)

    dossier = get_dossier_for_owner(request.user, dossier_id)
    invitation = get_object_or_404(dossier.invitations, pk=invitation_id)
    if invitation.is_usable:
        invitation.revoked_at = timezone.now()
        invitation.save(update_fields=["revoked_at"])
        consigner(
            dossier, Journal.Action.INVITATION_ANNULEE, request=request,
            objet=invitation.email,
        )
        messages.success(
            request,
            _("Invitation annulée pour %(email)s.") % {"email": invitation.email},
        )
    return redirect("dossier_share", dossier_id=dossier.pk)


@never_cache
def invitation_accept(request, token):
    """Page d'acceptation d'une invitation.

    Le jeton vaut autorisation : qui le détient peut l'utiliser, avec le
    compte sous lequel il est connecté. C'est la nature d'un lien
    transmissible, et la raison pour laquelle l'octroi direct reste préférable
    pour les personnes que l'on connaît déjà.

    L'acceptation exige un POST : un GET suffirait sinon à consommer
    l'invitation depuis un aperçu de lien ou un préchargement.
    """
    invitation = get_object_or_404(Invitation, token=token)
    bonne_adresse = _adresse_invitee(request.user, invitation)

    if request.method == "POST" and bonne_adresse:
        # Sous verrou, et tout ou rien : deux acceptations simultanées (deux
        # comptes, ou un double clic) ne doivent ni ouvrir deux accès ni finir
        # en erreur serveur sur la contrainte d'unicité.
        with transaction.atomic():
            invitation = Invitation.objects.select_for_update().get(pk=invitation.pk)
            acceptee = (invitation.is_usable
                        and not _deja_autorise(request.user, invitation.dossier)
                        and _accorder_par_invitation(request, invitation))
        if acceptee:
            messages.success(
                request,
                _("Vous avez désormais accès au dossier « %(dossier)s ».")
                % {"dossier": invitation.dossier.name},
            )
            return redirect("timeline", dossier_id=invitation.dossier_id)

    return render(
        request,
        "events/invitation_accept.html",
        {"invitation": invitation, "deja_autorise": _deja_autorise(request.user, invitation.dossier),
         "bonne_adresse": bonne_adresse},
    )


def _deja_autorise(utilisateur, dossier):
    return dossier.owner_id == utilisateur.id or dossier.accesses.filter(user=utilisateur).exists()


def _adresse_invitee(utilisateur, invitation):
    """L'invitation vaut pour l'adresse invitée, pas pour qui tient le lien :
    le compte doit posséder cette adresse, **vérifiée** (allauth). Un courriel
    retransféré, ou un compte ouvert par un tiers, ne reçoit rien."""
    return EmailAddress.objects.filter(
        user=utilisateur, email__iexact=invitation.email, verified=True
    ).exists()


def _accorder_par_invitation(request, invitation):
    """Crée l'accès, marque l'invitation acceptée et journalise ; dans la
    transaction de l'appelant. `False` si l'accès existait déjà (course)."""
    try:
        with transaction.atomic():
            DossierAccess.objects.create(
                dossier=invitation.dossier,
                user=request.user,
                role=invitation.role,
                origin=DossierAccess.Origin.INVITATION,
                granted_by=invitation.created_by,
            )
    except IntegrityError:
        return False
    invitation.accepted_at = timezone.now()
    invitation.accepted_by = request.user
    invitation.save(update_fields=["accepted_at", "accepted_by"])
    consigner(
        invitation.dossier, Journal.Action.INVITATION_ACCEPTEE, request=request,
        objet=invitation.email, details={"role": invitation.role},
    )
    return True


@transaction.atomic
def share_link_create(request, dossier_id):
    """Émet un lien de consultation anonyme. POST uniquement."""
    dossier = get_dossier_for_owner(request.user, dossier_id)

    if request.method != "POST":
        return redirect("dossier_share", dossier_id=dossier.pk)

    form = ShareLinkForm(request.POST)
    if not form.is_valid():
        # Jamais en silence : avant, un champ manquant renvoyait sur la page
        # sans un mot, et l'on croyait le lien créé.
        for nom in form.errors:
            if nom in form.fields:
                classes = form.fields[nom].widget.attrs.get("class", "")
                form.fields[nom].widget.attrs["class"] = (classes + " is-invalid").strip()
        return render(request, "events/dossier_share.html",
                      _contexte_du_partage(request, dossier, share_link_form=form), status=400)
    lien = form.save(commit=False)
    lien.dossier = dossier
    lien.created_by = request.user
    lien.expires_at = form.expiration()
    lien.save()
    consigner(
        dossier, Journal.Action.LIEN_CREE, request=request, lien=lien,
        objet=lien.label,
        details={
            "role": lien.role,
            "expire": lien.expires_at.isoformat() if lien.expires_at else None,
        },
    )
    # Le lien s'affiche en tête de la page, prêt à copier (`_lien_tout_juste_cree`).
    return redirect("%s?nouveau=%d" % (reverse("dossier_share", args=[dossier.pk]), lien.pk))


@never_cache
def share_link_detail(request, dossier_id, link_id):
    """Le lien à nouveau, prêt à copier : la page qu'ouvre « Voir » sans
    JavaScript (avec, c'est une popup de la page de partage). Propriétaire
    seul, lien actif seulement ; sinon 404, comme pour un lien inconnu."""
    dossier = get_dossier_for_owner(request.user, dossier_id)
    lien = get_object_or_404(dossier.share_links, pk=link_id)
    if not lien.is_active:
        raise Http404
    return render(request, "events/lien_partage.html", {"dossier": dossier, "lien": lien})


@never_cache
def share_link_edit(request, dossier_id, link_id):
    """Modifie le destinataire, le droit ou la validité d'un lien.

    Propriétaire seul (invariant 5) ; un lien révoqué ne se modifie pas — il
    est coupé pour de bon (404). Prolonger un lien expiré le remet en service.
    La page sert sans JavaScript et quand le formulaire est refusé ; sinon
    la popup de la page de partage poste ici directement.
    """
    dossier = get_dossier_for_owner(request.user, dossier_id)
    lien = get_object_or_404(dossier.share_links, pk=link_id, revoked_at__isnull=True)
    ids = f"lien-{lien.pk}-%s"

    if request.method != "POST":
        form = ShareLinkEditForm(instance=lien, auto_id=ids)
        return render(request, "events/lien_partage_modifier.html",
                      {"dossier": dossier, "lien": lien, "form": form})

    avant = etat_du_lien(lien)
    form = ShareLinkEditForm(request.POST, instance=lien, auto_id=ids)
    if not form.is_valid():
        for nom in form.errors:
            if nom in form.fields:
                classes = form.fields[nom].widget.attrs.get("class", "")
                form.fields[nom].widget.attrs["class"] = (classes + " is-invalid").strip()
        return render(request, "events/lien_partage_modifier.html",
                      {"dossier": dossier, "lien": lien, "form": form}, status=400)
    with transaction.atomic():
        form.save()
        modifs = changements(avant, etat_du_lien(lien))
        if modifs:
            consigner(dossier, Journal.Action.LIEN_MODIFIE, request=request, lien=lien,
                      objet=lien.label, details={"modele": "lien", "changements": modifs})
    if modifs:
        messages.success(request, _("Lien modifié."))
    return redirect("dossier_share", dossier_id=dossier.pk)


@transaction.atomic
def share_link_revoke(request, dossier_id, link_id):
    """Coupe un lien. POST uniquement, et sans délai."""
    if request.method != "POST":
        return JsonResponse({"success": False}, status=405)

    dossier = get_dossier_for_owner(request.user, dossier_id)
    lien = get_object_or_404(dossier.share_links, pk=link_id)
    if lien.is_active:
        lien.revoked_at = timezone.now()
        lien.save(update_fields=["revoked_at"])
        consigner(
            dossier, Journal.Action.LIEN_REVOQUE, request=request, lien=lien,
            objet=lien.label,
        )
        messages.success(request, _("Lien révoqué. Il ne donne plus accès à rien."))
    return redirect("dossier_share", dossier_id=dossier.pk)
