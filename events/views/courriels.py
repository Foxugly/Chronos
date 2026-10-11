"""Les courriels cités qui manquent au dossier (`events/fils.py`)."""

from django.shortcuts import render

from ..fils import courriels_manquants
from ..permissions import get_dossier_for_read


def dossier_manquants(request, dossier_id):
    """Les courriels que des courriels du dossier citent, sans qu'ils y soient.

    Pour qui peut lire : la page ne montre que des expéditeurs, dates et objets
    déjà lisibles dans le texte des courriels qui les citent. Pas par un lien
    de partage, comme le journal.
    """
    dossier = get_dossier_for_read(request.user, dossier_id)
    return render(request, "events/manquants.html", {
        "dossier": dossier,
        "manquants": courriels_manquants(dossier),
    })
