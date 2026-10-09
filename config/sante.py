"""`/health/` : ce que surveille UptimeRobot (OPERATIONS.md §3.9).

Un vrai contrôle de la base (`SELECT 1`) : une page « ok » qui répondrait base
tombée mentirait. 503 si la base ne répond pas. Public (`login_not_required`)
et sans rien de confidentiel : ni noms, ni comptes, ni message d'erreur
détaillé — seulement « ok » ou « degraded », et la version déployée.
"""

import os

from django.conf import settings
from django.contrib.auth.decorators import login_not_required
from django.db import DatabaseError, connection
from django.http import JsonResponse
from django.views.decorators.cache import never_cache


@login_not_required
@never_cache
def sante(request):
    try:
        with connection.cursor() as curseur:
            curseur.execute("SELECT 1")
            curseur.fetchone()
        base_ok = True
    except DatabaseError:
        base_ok = False
    return JsonResponse(
        {
            "status": "ok" if base_ok else "degraded",
            "version": os.environ.get("SENTRY_RELEASE") or settings.VERSION,
            "checks": {"db": {"ok": base_ok}},
        },
        status=200 if base_ok else 503,
    )
