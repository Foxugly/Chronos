"""Le partage, réservé au propriétaire (invariant 5) : accès, invitations, liens."""

from django.contrib import messages
from django.core.mail import send_mail
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.utils import translation
from django.utils.translation import gettext as _

from ..forms import DossierAccessForm, InvitationForm, ShareLinkForm
from ..journal import consigner
from ..models import DossierAccess, Invitation, Journal
from ..permissions import get_dossier_for_owner


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
        "share_links": dossier.share_links.all(),
    }
    contexte.update(surcharges)
    return contexte


def _lien_tout_juste_cree(request, dossier):
    """Le lien que `share_link_create` vient d'émettre (`?nouveau=<pk>`), pour le
    montrer en tête avec son bouton « Copier ». Borné au dossier, et seulement
    s'il est encore actif : un identifiant d'ailleurs, ou d'un lien coupé, ne
    montre rien."""
    valeur = request.GET.get("nouveau", "")
    if not valeur.isdigit():
        return None
    lien = dossier.share_links.filter(pk=int(valeur)).first()
    return lien if lien is not None and lien.is_active else None


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

    deja_autorise = (
        invitation.dossier.owner_id == request.user.id
        or invitation.dossier.accesses.filter(user=request.user).exists()
    )

    if request.method == "POST" and invitation.is_usable and not deja_autorise:
        DossierAccess.objects.create(
            dossier=invitation.dossier,
            user=request.user,
            role=invitation.role,
            origin=DossierAccess.Origin.INVITATION,
            granted_by=invitation.created_by,
        )
        invitation.accepted_at = timezone.now()
        invitation.accepted_by = request.user
        invitation.save(update_fields=["accepted_at", "accepted_by"])
        consigner(
            invitation.dossier, Journal.Action.INVITATION_ACCEPTEE, request=request,
            objet=invitation.email, details={"role": invitation.role},
        )
        messages.success(
            request,
            _("Vous avez désormais accès au dossier « %(dossier)s ».")
            % {"dossier": invitation.dossier.name},
        )
        return redirect("timeline", dossier_id=invitation.dossier_id)

    return render(
        request,
        "events/invitation_accept.html",
        {"invitation": invitation, "deja_autorise": deja_autorise},
    )


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
