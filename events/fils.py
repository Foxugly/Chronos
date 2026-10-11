"""Le fil de discussion d'un courriel, et les courriels cités absents du dossier.

Rien n'est stocké : les deux se calculent à la lecture, depuis les traces d'import
(`CourrielImporte`). Un fil est donc toujours à jour, et un courriel manquant
cesse de l'être dès qu'il arrive, par la boîte ou par un transfert.

**Le fil** regroupe les courriels importés du dossier qui portent le même objet,
sans ses préfixes (TR:, RE:…), ses accents ni sa casse (`liens.objet_normalise`).
Il rapproche ce que les liens ne peuvent prouver : l'architecte qui écrit, puis
l'envoi de son avis à l'entrepreneur, sans que l'un cite l'autre. Les faits
saisis à la main n'en sont jamais : deux « Devis » ne sont pas une conversation.

**Un courriel manquant** est celui qu'un courriel importé cite directement (le
bloc « De : … Envoyé : … » auquel il répond, `CourrielImporte.cite_*`) et
qu'aucun courriel importé du dossier ne reconnaît (`Empreinte.correspond` : même
expéditeur, même objet, à 20 minutes près). Seule la citation directe est gardée
à l'import : un ancêtre plus lointain absent n'est pas signalé.
"""

from .import_courriels import citation_de_trace, empreinte_de_trace
from .liens import objet_normalise
from .models import CourrielImporte, Event

def _traces(dossier_id):
    return list(
        CourrielImporte.objects.filter(dossier_id=dossier_id, evenement__isnull=False)
        .select_related("evenement")
    )


def fil_de_discussion(event):
    """Les autres courriels importés du même dossier et du même objet, dans l'ordre
    de la chronologie. Vide pour un fait saisi à la main."""
    importes = set(
        CourrielImporte.objects.filter(dossier_id=event.dossier_id, evenement__isnull=False)
        .values_list("evenement_id", flat=True)
    )
    objet = objet_normalise(event.title)
    # Un objet vide — « (sans objet) » compris — ne fait pas un fil.
    if event.pk not in importes or not objet:
        return []
    return [
        autre for autre in Event.objects.filter(dossier_id=event.dossier_id, pk__in=importes)
        .exclude(pk=event.pk)
        if objet_normalise(autre.title) == objet
    ]


def courriels_manquants(dossier):
    """Les courriels cités qu'aucun courriel du dossier ne reconnaît, du plus ancien au
    plus récent : `{"de", "le", "objet", "a", "cite_par": [évènements qui le citent]}`.
    `a` : ses destinataires, tels que le premier courriel qui les donne les cite.

    Deux réponses au même absent n'en font qu'un (même empreinte)."""
    traces = _traces(dossier.pk)
    presents = [empreinte_de_trace(t) for t in traces]
    manquants = []
    for trace in traces:
        cite = citation_de_trace(trace)
        if cite is None or any(cite.correspond(p) for p in presents):
            continue
        groupe = next((m for m in manquants if m["empreinte"].correspond(cite)), None)
        if groupe is None:
            groupe = {"empreinte": cite, "de": trace.cite_de, "le": trace.cite_le,
                      "objet": trace.cite_objet, "a": "", "cite_par": []}
            manquants.append(groupe)
        if not groupe["a"]:
            groupe["a"] = trace.cite_a
        if trace.evenement not in groupe["cite_par"]:
            groupe["cite_par"].append(trace.evenement)
    for groupe in manquants:
        groupe["cite_par"].sort(key=lambda e: (e.date, e.heure is not None, e.heure, e.pk))
        del groupe["empreinte"]
    return sorted(manquants, key=lambda m: m["le"])
