from django.db import migrations, models
import django.db.models.deletion

INITIAL_DOSSIER_NAME = "Salle de bain — litige ACME Bat"


def attach_orphan_events(apps, schema_editor):
    Event = apps.get_model("events", "Event")
    Dossier = apps.get_model("events", "Dossier")
    User = apps.get_model("auth", "User")

    orphans = Event.objects.filter(dossier__isnull=True)
    if not orphans.exists():
        return

    owner = User.objects.filter(is_superuser=True).order_by("pk").first()
    if owner is None:
        raise RuntimeError(
            "Aucun superuser en base : impossible de désigner le propriétaire du "
            "dossier initial. Créez-en un avec `manage.py createsuperuser`, puis "
            "relancez la migration."
        )

    dossier, _ = Dossier.objects.get_or_create(
        name=INITIAL_DOSSIER_NAME, defaults={"owner": owner}
    )
    orphans.update(dossier=dossier)


class Migration(migrations.Migration):
    """Rattachement unique, et **irréversible**.

    Il n'y a pas de marche arrière honnête à cette migration. Défaire
    l'ajout du champ supprimerait la colonne `dossier` pour *tous* les
    dossiers, pas seulement celui que cette migration a créé, et le
    `CASCADE` emporterait au passage les `DossierAccess` accordés des mois
    plus tard. Pire : un aller-retour arrière-puis-avant rattacherait tous
    les évènements de la base au seul dossier initial, fusionnant
    silencieusement des affaires distinctes en une seule chronologie.

    Faute de `reverse_code`, `migrate events 0002` s'arrête sur
    `IrreversibleError` : revenir en arrière devient une décision explicite
    prise hors du framework, sauvegarde en main.
    """

    dependencies = [
        ("events", "0002_dossier_dossieraccess"),
    ]

    operations = [
        migrations.AddField(
            model_name="event",
            name="dossier",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="events",
                to="events.dossier",
                verbose_name="dossier",
            ),
        ),
        migrations.RunPython(attach_orphan_events),
        migrations.AlterField(
            model_name="event",
            name="dossier",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="events",
                to="events.dossier",
                verbose_name="dossier",
            ),
        ),
    ]
