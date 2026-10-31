"""La sauvegarde que les commandes de réparation font d'elles-mêmes avant d'écrire.

`relier_courriels` et `relire_courriels` réécrivent des évènements du litige en série.
Compter sur la personne qui les lance pour sauvegarder d'abord ne suffit pas : le
30/10, 31 descriptions ont été réécrites sans sauvegarde. Désormais `--appliquer`
sauvegarde avant toute écriture, et une sauvegarde ratée arrête la commande.
"""

from django.core.management import call_command


def sauvegarder_avant_d_ecrire(commande):
    """Lance `manage.py sauvegarder` ; ses erreurs (`CommandError`) remontent telles quelles.

    À appeler hors transaction : `sauvegarder` refuse une transaction ouverte."""
    call_command("sauvegarder", stdout=commande.stdout, stderr=commande.stderr)
