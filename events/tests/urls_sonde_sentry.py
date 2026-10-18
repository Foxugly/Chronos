"""Routes de sonde pour `test_sentry_filtres` : elles échouent exprès, sous les
mêmes préfixes à jeton que les vraies (`partage/`, `invitation/`)."""

import logging

from django.contrib.auth.decorators import login_not_required
from django.urls import path
from django.views.decorators.csrf import csrf_exempt


@csrf_exempt
@login_not_required
def echoue(request, **kwargs):
    """Le message de l'exception cite ce qui a été saisi, comme le ferait une
    erreur de validation ou de base de données."""
    logging.getLogger("django.request").warning("Sonde sur %s", request.get_full_path())
    raise RuntimeError("Sonde : %s" % request.POST.get("title"))


urlpatterns = [
    path("partage/<str:token>/events/add/", echoue),
    path("invitation/<str:token>/", echoue),
]
