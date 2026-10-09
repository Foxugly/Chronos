"""Moteur d'envoi de courriels Django par Microsoft Graph (`/sendMail`).

Sur le modèle de QuizOnline (`core/email_backends/microsoft_graph.py`), mais
sur le client Graph de Chronos (`events/graph.py`, bibliothèque standard
seulement). Expéditeur : `GRAPH_SENDER` (convention de la flotte, §3.14).
Utilisé par `MAILERS` hors DEBUG quand Graph est configuré : invitations,
réinitialisation de mot de passe d'allauth.

Un échec lève une exception (`fail_silently` mis à part) : une invitation que
personne ne reçoit doit se voir, pas disparaître.
"""

from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend

from .graph import ClientGraph, ErreurGraph


class EmailBackend(BaseEmailBackend):
    def send_messages(self, email_messages):
        if not email_messages:
            return 0
        try:
            client = ClientGraph(settings.GRAPH_TENANT_ID, settings.GRAPH_CLIENT_ID,
                                 settings.GRAPH_CLIENT_SECRET, settings.GRAPH_SENDER)
        except ErreurGraph:
            if self.fail_silently:
                return 0
            raise
        envoyes = 0
        for message in email_messages:
            destinataires = [a for a in message.recipients() if a]
            if not destinataires:
                continue
            try:
                client.envoyer(destinataires, message.subject, message.body,
                               html=message.content_subtype == "html")
            except ErreurGraph:
                if not self.fail_silently:
                    raise
                continue
            envoyes += 1
        return envoyes
