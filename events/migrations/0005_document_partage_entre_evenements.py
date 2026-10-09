"""Une pièce peut illustrer plusieurs évènements sans être stockée deux fois.

`Document.event` (une seule clé) devient `Document.events` (plusieurs-à-plusieurs),
et la pièce porte désormais son `dossier` en propre ainsi qu'une empreinte de son
contenu. L'unicité `(dossier, content_hash)` garantit l'exemplaire unique.

La déduplication est bornée au dossier : le même fichier présent dans deux
affaires reste deux pièces distinctes, stockées séparément. Deux dossiers ne
partagent jamais d'octets.

Irréversible : le sens inverse devrait choisir, pour chaque pièce liée à
plusieurs évènements, lequel conserver — et jeter les autres liens en silence.
Sur un dossier de preuves, mieux vaut refuser que deviner.
"""

import hashlib

import django.db.models.deletion
from django.db import migrations, models


def _empreinte(fichier):
    """SHA-256 du contenu, lu par blocs : une pièce peut peser plusieurs Mo."""
    digest = hashlib.sha256()
    fichier.open("rb")
    try:
        for bloc in iter(lambda: fichier.read(65536), b""):
            digest.update(bloc)
    finally:
        fichier.close()
    return digest.hexdigest()


def rattacher_et_dedupliquer(apps, schema_editor):
    Document = apps.get_model("events", "Document")

    # Chaque pièce existante a exactement un évènement : on reprend son
    # dossier, on calcule son empreinte, et on recrée le lien.
    for document in Document.objects.select_related("event__dossier"):
        document.dossier_id = document.event.dossier_id
        try:
            document.content_hash = _empreinte(document.file)
        except FileNotFoundError:
            # La ligne existe mais le fichier a disparu du disque. On ne
            # peut pas l'empreindre ; une valeur unique evite de la faire
            # entrer en collision avec une autre piece.
            document.content_hash = "fichier-absent-%d" % document.pk
        document.save(update_fields=["dossier", "content_hash"])
        document.events.add(document.event)

    # Deux pieces d'un meme dossier partageant un contenu identique doivent
    # fusionner avant que la contrainte d'unicite ne soit posee. Il n'y en a
    # aucune dans la base actuelle, mais une copie restauree pourrait en
    # contenir, et la migration echouerait sans explication.
    vus = {}
    for document in Document.objects.order_by("pk"):
        cle = (document.dossier_id, document.content_hash)
        garde = vus.get(cle)
        if garde is None:
            vus[cle] = document
            continue
        for evenement in document.events.all():
            garde.events.add(evenement)
        document.delete()


def refuser_le_retour(apps, schema_editor):
    raise RuntimeError(
        "Migration irréversible : revenir à une pièce par évènement exigerait "
        "de choisir lequel conserver pour chaque pièce partagée, et de "
        "supprimer les autres liens sans trace. Restaurez une sauvegarde."
    )


class Migration(migrations.Migration):
    dependencies = [
        ("events", "0004_alter_dossier_created_at_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="document",
            name="dossier",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="documents",
                to="events.dossier",
                verbose_name="dossier",
            ),
        ),
        migrations.AddField(
            model_name="document",
            name="content_hash",
            field=models.CharField(
                default="", editable=False, max_length=64,
                verbose_name="empreinte du contenu",
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="document",
            name="events",
            field=models.ManyToManyField(
                blank=True,
                related_name="documents_m2m_temporaire",
                to="events.event",
                verbose_name="évènements",
            ),
        ),
        migrations.RunPython(rattacher_et_dedupliquer, refuser_le_retour),
        migrations.RemoveField(model_name="document", name="event"),
        migrations.AlterField(
            model_name="document",
            name="events",
            field=models.ManyToManyField(
                blank=True,
                related_name="documents",
                to="events.event",
                verbose_name="évènements",
            ),
        ),
        migrations.AlterField(
            model_name="document",
            name="dossier",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="documents",
                to="events.dossier",
                verbose_name="dossier",
            ),
        ),
        migrations.AlterField(
            model_name="document",
            name="title",
            field=models.CharField(max_length=255, verbose_name="titre"),
        ),
        migrations.AlterField(
            model_name="document",
            name="file",
            field=models.FileField(upload_to="documents/", verbose_name="fichier"),
        ),
        migrations.AlterModelOptions(
            name="document",
            options={"verbose_name": "document", "verbose_name_plural": "documents"},
        ),
        migrations.AddConstraint(
            model_name="document",
            constraint=models.UniqueConstraint(
                fields=("dossier", "content_hash"),
                name="unique_document_per_dossier",
            ),
        ),
    ]
