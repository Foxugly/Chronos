"""Consigner ce qui se passe dans un dossier (`Journal`).

Les vues appellent `consigner` après chaque geste qui change le dossier, dans
la même transaction que le geste quand il y en a une : un changement sans
trace, ou une trace sans changement, ne doivent pas pouvoir exister.
"""

from .models import Journal

#: Les champs d'un évènement dont on garde l'avant et l'après.
CHAMPS_SUIVIS = ("title", "date", "heure", "date_fin", "heure_fin", "description", "couleur")


def _valeur(valeur):
    """Une valeur sérialisable en JSON : dates et heures en ISO."""
    if valeur is None:
        return None
    if hasattr(valeur, "isoformat"):
        return valeur.isoformat()
    return valeur


def instantane(event):
    """L'état d'un évènement, champ par champ, tel qu'il est en base.

    Les étiquettes y figurent, en noms triés : les ajouter ou les retirer est
    une modification de l'évènement comme une autre.
    """
    etat = {champ: _valeur(getattr(event, champ)) for champ in CHAMPS_SUIVIS}
    etat["etiquettes"] = (
        ", ".join(sorted(event.etiquettes.values_list("nom", flat=True))) if event.pk else ""
    )
    return etat


def differences(avant, apres):
    """`{champ: [avant, après]}` pour chaque champ qui a changé."""
    return {
        champ: [avant.get(champ), apres.get(champ)]
        for champ in CHAMPS_SUIVIS + ("etiquettes",)
        if (avant.get(champ) or "") != (apres.get(champ) or "")
    }


def consigner(dossier, action, *, request=None, lien=None, via_admin=False,
              objet="", evenement=None, details=None):
    """Ajoute une entrée au journal du dossier.

    L'auteur est le compte connecté ; par un lien de partage anonyme, c'est
    le lien qui est noté — on ne sait pas qui le tenait.
    """
    utilisateur = getattr(request, "user", None)
    return Journal.objects.create(
        dossier=dossier,
        action=action,
        auteur=utilisateur if utilisateur is not None and utilisateur.is_authenticated else None,
        lien=lien,
        via_admin=via_admin,
        objet=str(objet)[:255],
        evenement=evenement,
        details=details or {},
    )


def consigner_pieces(event, pieces, source=None, **contexte):
    """Une entrée par pièce jointe lors d'un enregistrement.

    `pieces` : la liste `(document, nouvelle)` rendue par `_save_documents` ;
    une pièce déjà présente dans le dossier (même contenu) est rattachée,
    pas stockée deux fois — le journal le dit. `source="courriel"` marque
    les pièces arrivées par l'import, qui n'a pas d'auteur.
    """
    for document, nouvelle in pieces:
        consigner(
            event.dossier,
            Journal.Action.PIECE_AJOUTEE,
            objet=document.title,
            evenement=event,
            details={
                "empreinte": document.content_hash,
                "deja_presente": not nouvelle,
                "evenement": event.title,
                **({"source": source} if source else {}),
            },
            **contexte,
        )
