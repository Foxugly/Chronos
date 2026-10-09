"""Convertit les descriptions existantes, en texte brut, vers du HTML.

Les descriptions deviennent du HTML assaini : elles sont désormais rendues
comme du balisage et non plus comme du texte. Sans cette conversion, les
retours à la ligne des 9 descriptions existantes disparaîtraient d'un coup —
une énumération « 1. … 2. … 3. … » écrite sur trois lignes se retrouverait
collée en une seule.

L'échappement est fait avant tout découpage : aucune description actuelle ne
contient de chevron, mais la migration doit rester juste sur une base
restaurée qui en contiendrait.

Réversible : le sens inverse retire le balisage et rend le texte, ce qui
n'est fidèle que pour les descriptions converties ici. Une description
enrichie après coup perdrait sa mise en forme — c'est écrit, et c'est le
prix d'un retour en arrière.
"""

import html as html_module
import re

from django.db import migrations


def _texte_vers_html(texte):
    if not texte or not texte.strip():
        return ""
    echappe = html_module.escape(texte.replace("\r\n", "\n").replace("\r", "\n"))
    paragraphes = [p for p in re.split(r"\n\s*\n", echappe) if p.strip()]
    return "".join(
        "<p>%s</p>" % p.strip().replace("\n", "<br>") for p in paragraphes
    )


def _html_vers_texte(html):
    if not html:
        return ""
    sans_br = re.sub(r"<br\s*/?>", "\n", html)
    sans_p = re.sub(r"</p>\s*<p>", "\n\n", sans_br)
    nu = re.sub(r"<[^>]+>", "", sans_p)
    return html_module.unescape(nu).strip()


def vers_html(apps, schema_editor):
    Event = apps.get_model("events", "Event")
    for event in Event.objects.exclude(description=""):
        converti = _texte_vers_html(event.description)
        if converti != event.description:
            event.description = converti
            event.save(update_fields=["description"])


def vers_texte(apps, schema_editor):
    Event = apps.get_model("events", "Event")
    for event in Event.objects.exclude(description=""):
        event.description = _html_vers_texte(event.description)
        event.save(update_fields=["description"])


class Migration(migrations.Migration):
    dependencies = [
        ("events", "0008_share_link_role"),
    ]

    operations = [
        migrations.RunPython(vers_html, vers_texte),
    ]
