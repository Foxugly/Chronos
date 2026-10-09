# L1 — Fondations : auth, dossiers et documents protégés — Plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rendre SDBTimeline entièrement privé, rattacher ses 24 évènements et 49 documents à un dossier dont le propriétaire contrôle les accès en lecture ou lecture/écriture, et ne plus servir aucun fichier sans vérifier les droits.

**Architecture:** Un modèle d'accès explicite (`Dossier` + `DossierAccess`) remplace l'absence totale de permissions. `LoginRequiredMiddleware` ferme l'application par défaut, django-allauth fournit la connexion avec l'inscription désactivée, et un `DossierQuerySet` plus quatre helpers centralisent toutes les décisions d'autorisation pour qu'aucune vue n'en reconstruise la logique. Les documents cessent d'être servis en statique au profit d'une vue `FileResponse` qui vérifie la lecture.

**Tech Stack:** Python 3.14, Django 6.1, django-allauth >= 65, SQLite, Bootstrap 5, vis-timeline. Tests via `manage.py test` (pas de pytest dans ce projet).

**Spec:** `docs/superpowers/specs/2026-10-07-l1-fondations-auth-dossiers-design.md`

## Global Constraints

- **Pas de git.** Le projet n'est pas un dépôt et l'utilisateur a choisi de ne pas l'initialiser. Chaque tâche se termine donc par un point de contrôle qui relance toute la suite de tests, non par un commit. Aucune modification n'est annulable : lire un fichier avant de l'écraser.
- **Python du poste :** `C:\Users\Renaud\AppData\Local\Python\pythoncore-3.14-64\python.exe`. Le `.venv` actuel est inutilisable (il pointe sur `C:\Python314`, absent, et vient d'une autre machine).
- **Shell :** PowerShell. Les commandes du plan sont écrites pour lui.
- **Versions épinglées :** `Django>=6.1,<6.2` et `django-allauth>=65,<66`. Le plancher 65 n'est pas négociable : c'est la version qui a ajouté le support de `LoginRequiredMiddleware` (spec D7).
- **Pas de `django.contrib.sites`, pas de `SITE_ID`** (spec D9).
- **`AUTHENTICATION_BACKENDS` ne contient que le backend allauth** (spec D6).
- **Un dossier inaccessible renvoie 404, jamais 403** (spec D5).
- **Langue de l'interface : français.** Tous les libellés visibles sont en français, comme l'existant.
- **La base contient les seules données réelles du projet.** Avant toute migration : copier `db.sqlite3`. C'est l'étape 1 de la tâche 3.

## Structure des fichiers

| Fichier | Responsabilité | Tâche |
|---|---|---|
| `requirements.txt` | Dépendances directes, en UTF-8 | T1 |
| `config/settings.py` | Réglages : env, timezone, allauth, middleware, templates | T1, T4 |
| `events/models.py` | `DossierQuerySet`, `Dossier`, `DossierAccess`, `Event`, `Document` | T2, T3 |
| `events/migrations/0002_dossier_dossieraccess.py` | Création des deux modèles | T2 |
| `events/migrations/0003_event_dossier.py` | `Event.dossier` + rattachement des données | T3 |
| `events/adapters.py` | Adaptateur allauth fermant l'inscription | T4 |
| `templates/base.html` | Gabarit racine minimal (L2 l'étoffera) | T4 |
| `events/permissions.py` | Les quatre helpers d'autorisation | T5 |
| `events/views.py` | Vues, toutes filtrées par dossier | T6, T7 |
| `events/urls.py` | Routes, y compris `event_edit` enfin branchée | T6, T7 |
| `events/templates/events/dossier_list.html` | Nouvelle page d'accueil | T6 |
| `events/templates/events/timeline.html` | Chronologie d'un dossier donné | T6, T7 |
| `events/templates/events/event_form.html` | Correction minimale du bloc documents | T7 |
| `config/urls.py` | Retrait du service statique de `MEDIA_ROOT` | T7 |
| `events/admin.py` | `Dossier`, `DossierAccess` en inline, `dossier` sur `EventAdmin` | T8 |
| `events/tests/` | Paquet de tests remplaçant `events/tests.py` vide | T2→T8 |

`DossierQuerySet` vit dans `events/models.py` juste avant `Dossier`, et référence `DossierAccess.Role.WRITE` dans le corps de ses méthodes. Le corps ne s'exécute qu'à l'appel, donc après le chargement complet du module : pas d'import circulaire, et pas besoin d'un fichier séparé pour un objet de quinze lignes.

---

### Task 1 : Hygiène du projet — venv, dépendances, réglages

Rien ne peut être vérifié avant cette tâche : l'interpréteur du projet n'existe pas.

**Files:**
- Recreate: `.venv/`
- Modify: `requirements.txt` (intégralement réécrit)
- Modify: `config/settings.py:22` (`SECRET_KEY`), `:25` (`DEBUG`), `:27` (`ALLOWED_HOSTS`), `:98` (`TIME_ZONE`)

**Interfaces:**
- Consumes: rien.
- Produces: `.venv\Scripts\python.exe` fonctionnel avec Django 6.1 et django-allauth >= 65 installés. Toutes les tâches suivantes l'utilisent.

- [ ] **Step 1 : Supprimer le venv cassé**

```powershell
Remove-Item -Recurse -Force .venv
```

- [ ] **Step 2 : Recréer le venv avec le Python du poste**

```powershell
& "C:\Users\Renaud\AppData\Local\Python\pythoncore-3.14-64\python.exe" -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
```

- [ ] **Step 3 : Réécrire `requirements.txt` en UTF-8**

Le fichier actuel est en UTF-16 et contient un `pip list` collé (`pip 26.2.1` inclus). Contenu de remplacement, exactement :

```
Django>=6.1,<6.2
django-allauth>=65,<66
```

`asgiref`, `sqlparse` et `tzdata` sont des dépendances transitives de Django : elles s'installent seules et n'ont pas à être épinglées ici.

- [ ] **Step 4 : Installer les dépendances**

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip check
```

Attendu : `No broken requirements found.`

- [ ] **Step 5 : Vérifier la version d'allauth installée**

```powershell
.\.venv\Scripts\python.exe -m pip show django-allauth | Select-String "^Version"
```

Attendu : `Version: 65.x` ou plus. Si la version est inférieure à 65, **arrêter** : la contrainte D7 n'est pas respectée et la page de connexion bouclera sur elle-même.

- [ ] **Step 6 : Remplacer les réglages sensibles dans `config/settings.py`**

En tête de fichier, après `from pathlib import Path` :

```python
import os
```

Puis remplacer les trois lignes `SECRET_KEY`, `DEBUG` et `ALLOWED_HOSTS` par :

```python
# Clé de développement : régénérée, et remplacée en production par
# DJANGO_SECRET_KEY (SSM). Ne jamais réutiliser cette valeur ailleurs.
SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "dev-only-sdbtimeline-CHANGE-ME-AT-IMPLEMENTATION",
)

DEBUG = os.environ.get("DJANGO_DEBUG", "True") == "True"

ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "").split(",") if os.environ.get("DJANGO_ALLOWED_HOSTS") else []
```

À l'implémentation, remplacer `dev-only-sdbtimeline-CHANGE-ME-AT-IMPLEMENTATION` par une vraie clé générée :

```powershell
.\.venv\Scripts\python.exe -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

L'ancienne clé (`django-insecure-jms4j9jxbfno722=oq4p+ti)18vnflvqcy6fpdbs_d%vx+s3$8`) ne doit plus apparaître dans le fichier.

- [ ] **Step 7 : Corriger le fuseau horaire**

Remplacer `TIME_ZONE = 'UTC'` par :

```python
TIME_ZONE = "Europe/Brussels"
```

- [ ] **Step 8 : Vérifier que Django démarre**

```powershell
.\.venv\Scripts\python.exe manage.py check
```

Attendu : `System check identified no issues (0 silenced).`

- [ ] **Step 9 : Point de contrôle**

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe -m pip check
```

Les deux doivent passer. Il n'y a pas encore de tests à lancer.

---

### Task 2 : Modèles `Dossier` et `DossierAccess`

**Files:**
- Modify: `events/models.py` (ajouts avant la classe `Event`)
- Create: `events/migrations/0002_dossier_dossieraccess.py` (généré)
- Delete: `events/tests.py` (vide)
- Create: `events/tests/__init__.py`, `events/tests/factories.py`, `events/tests/test_models.py`

**Interfaces:**
- Consumes: le venv de T1.
- Produces:
  - `Dossier(name: str, owner: User, created_at: datetime)`, manager `Dossier.objects` de type `DossierQuerySet`.
  - `Dossier.objects.accessible_by(user) -> QuerySet[Dossier]`
  - `Dossier.objects.writable_by(user) -> QuerySet[Dossier]`
  - `DossierAccess(dossier, user, role, origin, granted_at, granted_by)` avec `DossierAccess.Role.READ` / `.WRITE` et `DossierAccess.Origin.DIRECT` / `.INVITATION` / `.LINK`.
  - `events/tests/factories.py` : `make_user(username)`, `make_dossier(owner, name)`, `grant(dossier, user, role)` — utilisés par toutes les tâches suivantes.

- [ ] **Step 1 : Créer le paquet de tests et ses fabriques**

Supprimer `events/tests.py` (il est vide) et créer `events/tests/__init__.py` vide, puis `events/tests/factories.py` :

```python
from django.contrib.auth import get_user_model

from events.models import Dossier, DossierAccess


def make_user(username, password="pw-for-tests", **kwargs):
    return get_user_model().objects.create_user(
        username=username, email=f"{username}@example.test", password=password, **kwargs
    )


def make_dossier(owner, name="Dossier de test"):
    return Dossier.objects.create(name=name, owner=owner)


def grant(dossier, user, role, granted_by=None):
    return DossierAccess.objects.create(
        dossier=dossier, user=user, role=role, granted_by=granted_by
    )
```

- [ ] **Step 2 : Écrire les tests qui échouent**

`events/tests/test_models.py` :

```python
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.test import TestCase

from events.models import Dossier, DossierAccess
from events.tests.factories import grant, make_dossier, make_user


class DossierQuerySetTests(TestCase):
    def setUp(self):
        self.owner = make_user("owner")
        self.reader = make_user("reader")
        self.writer = make_user("writer")
        self.stranger = make_user("stranger")
        self.dossier = make_dossier(self.owner)
        grant(self.dossier, self.reader, DossierAccess.Role.READ)
        grant(self.dossier, self.writer, DossierAccess.Role.WRITE)

    def test_owner_can_access(self):
        self.assertIn(self.dossier, Dossier.objects.accessible_by(self.owner))

    def test_reader_can_access(self):
        self.assertIn(self.dossier, Dossier.objects.accessible_by(self.reader))

    def test_writer_can_access(self):
        self.assertIn(self.dossier, Dossier.objects.accessible_by(self.writer))

    def test_stranger_cannot_access(self):
        self.assertNotIn(self.dossier, Dossier.objects.accessible_by(self.stranger))

    def test_owner_can_write(self):
        self.assertIn(self.dossier, Dossier.objects.writable_by(self.owner))

    def test_writer_can_write(self):
        self.assertIn(self.dossier, Dossier.objects.writable_by(self.writer))

    def test_reader_cannot_write(self):
        self.assertNotIn(self.dossier, Dossier.objects.writable_by(self.reader))

    def test_stranger_cannot_write(self):
        self.assertNotIn(self.dossier, Dossier.objects.writable_by(self.stranger))

    def test_accessible_by_does_not_duplicate_rows(self):
        # Un dossier ne doit apparaître qu'une fois, même avec plusieurs accès.
        grant(self.dossier, make_user("other"), DossierAccess.Role.READ)
        self.assertEqual(Dossier.objects.accessible_by(self.owner).count(), 1)


class DossierAccessConstraintTests(TestCase):
    def test_one_access_per_user_per_dossier(self):
        owner = make_user("owner")
        reader = make_user("reader")
        dossier = make_dossier(owner)
        grant(dossier, reader, DossierAccess.Role.READ)
        with self.assertRaises(IntegrityError), transaction.atomic():
            grant(dossier, reader, DossierAccess.Role.WRITE)


class DossierDeletionTests(TestCase):
    def test_deleting_owner_is_refused(self):
        owner = make_user("owner")
        make_dossier(owner)
        with self.assertRaises(ProtectedError):
            owner.delete()

    def test_deleting_granted_user_removes_only_the_access(self):
        owner = make_user("owner")
        reader = make_user("reader")
        dossier = make_dossier(owner)
        grant(dossier, reader, DossierAccess.Role.READ)
        reader.delete()
        self.assertEqual(DossierAccess.objects.count(), 0)
        self.assertTrue(Dossier.objects.filter(pk=dossier.pk).exists())
```

- [ ] **Step 3 : Lancer les tests pour vérifier qu'ils échouent**

```powershell
.\.venv\Scripts\python.exe manage.py test events.tests.test_models -v 2
```

Attendu : échec à l'import, `ImportError: cannot import name 'Dossier' from 'events.models'`.

- [ ] **Step 4 : Écrire les modèles**

Dans `events/models.py`, en tête :

```python
from django.conf import settings
from django.db import models
from django.db.models import Q
```

Puis, **avant** la classe `Event` :

```python
class DossierQuerySet(models.QuerySet):
    def accessible_by(self, user):
        """Dossiers que `user` peut consulter : les siens et ceux partagés."""
        return self.filter(Q(owner=user) | Q(accesses__user=user)).distinct()

    def writable_by(self, user):
        """Dossiers que `user` peut modifier : les siens et ceux partagés en écriture."""
        return self.filter(
            Q(owner=user)
            | Q(accesses__user=user, accesses__role=DossierAccess.Role.WRITE)
        ).distinct()


class Dossier(models.Model):
    name = models.CharField("nom", max_length=255)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="propriétaire",
        related_name="dossiers_owned",
        on_delete=models.PROTECT,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    objects = DossierQuerySet.as_manager()

    class Meta:
        ordering = ["name"]
        verbose_name = "dossier"
        verbose_name_plural = "dossiers"

    def __str__(self):
        return self.name


class DossierAccess(models.Model):
    class Role(models.TextChoices):
        READ = "read", "Lecture"
        WRITE = "write", "Lecture et écriture"

    class Origin(models.TextChoices):
        DIRECT = "direct", "Compte choisi"
        INVITATION = "invitation", "Invitation par e-mail"
        LINK = "link", "Lien de partage"

    dossier = models.ForeignKey(
        Dossier, related_name="accesses", on_delete=models.CASCADE
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="utilisateur",
        related_name="dossier_accesses",
        on_delete=models.CASCADE,
    )
    role = models.CharField("rôle", max_length=10, choices=Role.choices)
    origin = models.CharField(
        "origine", max_length=10, choices=Origin.choices, default=Origin.DIRECT
    )
    granted_at = models.DateTimeField(auto_now_add=True)
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="accordé par",
        null=True,
        blank=True,
        related_name="+",
        on_delete=models.SET_NULL,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["dossier", "user"],
                name="unique_access_per_user_per_dossier",
            ),
        ]
        verbose_name = "accès"
        verbose_name_plural = "accès"

    def __str__(self):
        return f"{self.user} — {self.dossier} ({self.get_role_display()})"
```

`DossierQuerySet.writable_by` nomme `DossierAccess` avant sa définition : c'est volontaire et correct, le corps de la méthode ne s'évalue qu'à l'appel.

- [ ] **Step 5 : Générer la migration**

```powershell
.\.venv\Scripts\python.exe manage.py makemigrations events --name dossier_dossieraccess
```

Attendu : création de `events/migrations/0002_dossier_dossieraccess.py` avec deux `CreateModel` et la `UniqueConstraint`. Aucune question interactive ne doit être posée — si Django en pose une, c'est que `Event.dossier` a été ajouté par erreur dans cette tâche : il appartient à T3.

- [ ] **Step 6 : Lancer les tests pour vérifier qu'ils passent**

```powershell
.\.venv\Scripts\python.exe manage.py test events.tests.test_models -v 2
```

Attendu : 12 tests, tous au vert.

- [ ] **Step 7 : Point de contrôle**

```powershell
.\.venv\Scripts\python.exe manage.py test events -v 2
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
```

Le second doit signaler qu'aucune migration ne manque.

---

### Task 3 : `Event.dossier` et rattachement des données existantes

C'est la tâche qui touche aux données réelles. Elle commence par une copie de la base.

**Files:**
- Modify: `events/models.py` (classe `Event`)
- Create: `events/migrations/0003_event_dossier.py` (écrite à la main)
- Create: `events/tests/test_migration_data.py`

**Interfaces:**
- Consumes: `Dossier` (T2).
- Produces: `Event.dossier` non-nullable, `dossier.events` en accès inverse. Toutes les vues de T6 en dépendent.

- [ ] **Step 1 : Sauvegarder la base et les fichiers**

```powershell
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
Copy-Item db.sqlite3 "db.sqlite3.backup-$stamp"
Copy-Item -Recurse media "media.backup-$stamp"
```

Vérifier que les deux copies existent avant d'aller plus loin. 24 évènements et 49 documents de preuves n'ont aucune autre sauvegarde.

- [ ] **Step 2 : Écrire le test de migration qui échoue**

`events/tests/test_migration_data.py` :

```python
from django.test import TestCase

from events.models import Event


class EventDossierTests(TestCase):
    def test_every_event_belongs_to_a_dossier(self):
        """Le champ est non-nullable : aucun évènement ne peut être orphelin."""
        field = Event._meta.get_field("dossier")
        self.assertFalse(field.null)
        self.assertEqual(field.remote_field.related_name, "events")

    def test_events_are_reachable_from_their_dossier(self):
        from events.tests.factories import make_dossier, make_user

        dossier = make_dossier(make_user("owner"))
        event = Event.objects.create(
            dossier=dossier, title="Devis signé", date="2026-06-02"
        )
        self.assertEqual(list(dossier.events.all()), [event])
```

- [ ] **Step 3 : Lancer le test pour vérifier qu'il échoue**

```powershell
.\.venv\Scripts\python.exe manage.py test events.tests.test_migration_data -v 2
```

Attendu : `FieldDoesNotExist: Event has no field named 'dossier'`.

- [ ] **Step 4 : Ajouter le champ au modèle**

Dans la classe `Event` de `events/models.py`, en premier champ :

```python
    dossier = models.ForeignKey(
        Dossier, verbose_name="dossier", related_name="events", on_delete=models.CASCADE
    )
```

- [ ] **Step 5 : Créer une migration vide puis l'écrire à la main**

`makemigrations` ne sait pas ajouter une colonne non-nullable sur une table peuplée sans poser de question. On écrit donc les trois opérations nous-mêmes.

```powershell
.\.venv\Scripts\python.exe manage.py makemigrations events --empty --name event_dossier
```

Puis remplacer le contenu de `events/migrations/0003_event_dossier.py` par :

```python
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

    dossier = Dossier.objects.create(name=INITIAL_DOSSIER_NAME, owner=owner)
    orphans.update(dossier=dossier)


def detach_events(apps, schema_editor):
    Event = apps.get_model("events", "Event")
    Dossier = apps.get_model("events", "Dossier")

    Event.objects.filter(dossier__name=INITIAL_DOSSIER_NAME).update(dossier=None)
    Dossier.objects.filter(name=INITIAL_DOSSIER_NAME, events__isnull=True).delete()


class Migration(migrations.Migration):
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
        migrations.RunPython(attach_orphan_events, detach_events),
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
```

- [ ] **Step 6 : Appliquer la migration sur la vraie base**

```powershell
.\.venv\Scripts\python.exe manage.py migrate
```

- [ ] **Step 7 : Vérifier le rattachement des 24 évènements**

```powershell
.\.venv\Scripts\python.exe manage.py shell -c "from events.models import Dossier, Event; print('orphelins:', Event.objects.filter(dossier__isnull=True).count()); print([(d.name, d.owner.username, d.events.count()) for d in Dossier.objects.all()])"
```

Attendu : `orphelins: 0` et une seule ligne, `('Salle de bain — litige ACME Bat', 'rvilain', 24)`.

- [ ] **Step 8 : Lancer les tests pour vérifier qu'ils passent**

```powershell
.\.venv\Scripts\python.exe manage.py test events.tests.test_migration_data -v 2
```

- [ ] **Step 9 : Point de contrôle**

```powershell
.\.venv\Scripts\python.exe manage.py test events -v 2
```

**Note pour le relecteur :** à la fin de cette tâche, `event_add` est temporairement cassée — elle construit un `Event` sans `dossier`, désormais obligatoire. C'est assumé : T6 réécrit cette vue pour qu'elle reçoive son dossier par l'URL. Aucun test ne couvre `event_add` avant T6.

---

### Task 4 : allauth, mode tout privé et inscription fermée

**Files:**
- Modify: `config/settings.py` (`INSTALLED_APPS`, `MIDDLEWARE`, `TEMPLATES`, bloc allauth en fin de fichier)
- Modify: `config/urls.py` (inclusion de `allauth.urls`)
- Create: `events/adapters.py`
- Create: `templates/base.html`
- Create: `events/tests/test_auth.py`

**Interfaces:**
- Consumes: rien de T2/T3.
- Produces: toute URL exige une session ; `/accounts/login/`, `/accounts/logout/`, `/accounts/password/reset/` existent ; `/accounts/signup/` est fermée. Les tests de T6 et T7 s'appuient sur `self.client.force_login(user)`.

- [ ] **Step 1 : Écrire les tests qui échouent**

`events/tests/test_auth.py` :

```python
from django.test import TestCase
from django.urls import reverse

from events.tests.factories import make_user


class LoginRequiredTests(TestCase):
    def test_root_redirects_anonymous_to_login(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])

    def test_login_page_is_reachable_anonymously(self):
        """Garde-fou contre la boucle de redirection : allauth >= 65 marque
        ses vues avec login_not_required."""
        response = self.client.get(reverse("account_login"))
        self.assertEqual(response.status_code, 200)

    def test_password_reset_is_reachable_anonymously(self):
        """Un mot de passe oublié doit pouvoir être réinitialisé sans session."""
        response = self.client.get(reverse("account_reset_password"))
        self.assertEqual(response.status_code, 200)


class ClosedSignupTests(TestCase):
    def test_signup_page_does_not_offer_a_form(self):
        response = self.client.get(reverse("account_signup"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "<form", html=False)


class LoginMethodTests(TestCase):
    def setUp(self):
        self.user = make_user("someone", password="a-good-password")

    def test_login_with_username(self):
        self.assertTrue(
            self.client.login(username="someone", password="a-good-password")
        )

    def test_authenticated_user_reaches_root(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get("/").status_code, 200)
```

**Correction apportée en cours d'exécution (2026-10-07).** Ce plan annonçait ici que `test_authenticated_user_reaches_root` échouerait jusqu'à T6, « parce que `/` exige maintenant un `dossier_id` ». C'était faux : la vue actuelle est `def timeline(request)`, sans paramètre de dossier — c'est T6 qui lui en ajoute un. Le test passe donc dès T4, et T4 se termine avec la suite entièrement au vert.

Ce qui reste vrai, et qui est la véritable raison d'être de T6 : la vue `timeline` interroge `Event.objects.all()` **sans aucun filtrage**. Tout utilisateur connecté voit donc les 24 évènements de tous les dossiers. Ce n'est pas un plantage, c'est une faille de confidentialité — et elle est désormais atteignable par n'importe quel compte, puisque T4 vient d'ouvrir l'authentification. T6 la ferme.

- [ ] **Step 2 : Lancer les tests pour vérifier qu'ils échouent**

```powershell
.\.venv\Scripts\python.exe manage.py test events.tests.test_auth -v 2
```

Attendu : `NoReverseMatch: 'account_login' is not a valid view function or pattern name`.

- [ ] **Step 3 : Configurer allauth dans `config/settings.py`**

Ajouter à `INSTALLED_APPS`, après `'events'` :

```python
    "allauth",
    "allauth.account",
```

Dans `MIDDLEWARE`, ajouter `LoginRequiredMiddleware` juste après `AuthenticationMiddleware`, et `AccountMiddleware` en fin de liste :

```python
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.auth.middleware.LoginRequiredMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "allauth.account.middleware.AccountMiddleware",
]
```

Donner un répertoire de gabarits au projet — `TEMPLATES[0]["DIRS"]` vaut `[]` aujourd'hui :

```python
        "DIRS": [BASE_DIR / "templates"],
```

Et en fin de fichier :

```python
# Authentification (django-allauth)

AUTHENTICATION_BACKENDS = [
    "allauth.account.auth_backends.AuthenticationBackend",
]

ACCOUNT_ADAPTER = "events.adapters.ClosedSignupAccountAdapter"
ACCOUNT_LOGIN_METHODS = {"username", "email"}
ACCOUNT_SIGNUP_FIELDS = ["username*", "email*", "password1*", "password2*"]
ACCOUNT_EMAIL_VERIFICATION = "none"

LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/"
ACCOUNT_LOGOUT_REDIRECT_URL = "/accounts/login/"
```

`ModelBackend` est volontairement absent : la doc allauth déconseille de l'ajouter pour le login admin, dont la vue n'est pas rate-limitée (spec D6).

- [ ] **Step 4 : Écrire l'adaptateur qui ferme l'inscription**

`events/adapters.py` :

```python
from allauth.account.adapter import DefaultAccountAdapter


class ClosedSignupAccountAdapter(DefaultAccountAdapter):
    """Les comptes sont créés par l'administrateur, pas par les visiteurs.

    `CloseableSignupMixin`, dont hérite la vue d'inscription d'allauth,
    interroge cette méthode et rend la page « inscription fermée ».
    """

    def is_open_for_signup(self, request):
        return False
```

- [ ] **Step 5 : Brancher les URLs allauth**

Dans `config/urls.py`, ajouter avant l'inclusion de `events.urls` :

```python
    path("accounts/", include("allauth.urls")),
```

- [ ] **Step 6 : Créer le gabarit racine minimal**

`templates/base.html` — délibérément pauvre, L2 l'étoffera :

```html
<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{% block title %}SDB Timeline{% endblock %}</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.8/dist/css/bootstrap.min.css"
          rel="stylesheet"
          integrity="sha384-sRIl4kxILFvY47J16cr9ZwB07vP4J8+LH7qKQnuqkuIAvNWLzeN8tE5YBujZqJLB"
          crossorigin="anonymous">
    {% block extra_css %}{% endblock %}
</head>
<body class="bg-body-tertiary">
<main class="container py-4">
    {% block content %}{% endblock %}
</main>
<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.8/dist/js/bootstrap.bundle.min.js"
        integrity="sha384-FKyoEForCGlyvwx9Hj09JcYn3nv7wiPVlz7YYwJrWVcXK/BmnVDxM+D2scQbITxI"
        crossorigin="anonymous"></script>
{% block extra_js %}{% endblock %}
</body>
</html>
```

Les deux `integrity` sont reprises telles quelles de `events/templates/events/timeline.html`, qui charge déjà Bootstrap 5.3.8 : sans elles, une compromission du CDN injecterait du code dans une application qui sert des pièces de dossier. Même exigence pour les feuilles vis-timeline, Font Awesome et glightbox déplacées au step 6 de T6 — elles portent déjà leurs hachages dans le gabarit actuel, les conserver au passage.

- [ ] **Step 7 : Lancer les tests**

```powershell
.\.venv\Scripts\python.exe manage.py test events.tests.test_auth -v 2
```

Attendu : les six tests au vert, aucun échec. (Voir la correction au step 1 : ce plan prévoyait à tort un échec ici.)

- [ ] **Step 8 : Vérifier manuellement l'absence de boucle de redirection**

```powershell
.\.venv\Scripts\python.exe manage.py runserver
```

Ouvrir `http://127.0.0.1:8000/` en navigation privée. Attendu : la page de connexion allauth s'affiche. Si le navigateur signale trop de redirections, la version d'allauth est inférieure à 65 — revenir au step 5 de T1.

Vérifier aussi `http://127.0.0.1:8000/admin/` en navigation privée : Django doit mener à une page de connexion utilisable, pas boucler. Le comportement de `LoginRequiredMiddleware` face à la vue de connexion de l'admin n'a pas été vérifié dans la doc au moment d'écrire ce plan — c'est le seul point du lot qui reste à confirmer par l'essai.

Si le navigateur boucle sur `/admin/login/`, créer `config/middleware.py` :

```python
from django.contrib.auth.middleware import LoginRequiredMiddleware


class LoginRequiredExceptAdminLogin(LoginRequiredMiddleware):
    """L'admin a sa propre page de connexion ; l'exempter évite la boucle."""

    EXEMPT_PREFIXES = ("/admin/login/",)

    def process_view(self, request, view_func, view_args, view_kwargs):
        if request.path.startswith(self.EXEMPT_PREFIXES):
            return None
        return super().process_view(request, view_func, view_args, view_kwargs)
```

puis remplacer dans `MIDDLEWARE` la ligne `django.contrib.auth.middleware.LoginRequiredMiddleware` par `config.middleware.LoginRequiredExceptAdminLogin`, et ajouter ce test à `events/tests/test_auth.py` :

```python
    def test_admin_login_page_does_not_loop(self):
        response = self.client.get("/admin/login/", follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertLess(len(response.redirect_chain), 3)
```

Si le navigateur ne boucle pas, ne rien ajouter : un middleware sur mesure sans problème à résoudre est du code mort.

- [ ] **Step 9 : Point de contrôle**

```powershell
.\.venv\Scripts\python.exe manage.py test events -v 2
```

Attendu : toute la suite au vert (26 tests à ce stade de l'exécution réelle).

---

### Task 5 : Helpers d'autorisation

**Files:**
- Create: `events/permissions.py`
- Create: `events/tests/test_permissions.py`

**Interfaces:**
- Consumes: `Dossier.objects.accessible_by` / `writable_by` (T2), `Event.dossier` (T3).
- Produces, toutes levant `Http404` en cas de refus :
  - `get_dossier_for_read(user, dossier_id) -> Dossier`
  - `get_dossier_for_write(user, dossier_id) -> Dossier`
  - `get_event_for_read(user, event_id) -> Event`
  - `get_event_for_write(user, event_id) -> Event`

- [ ] **Step 1 : Écrire les tests qui échouent**

`events/tests/test_permissions.py` :

```python
from django.http import Http404
from django.test import TestCase

from events.models import DossierAccess, Event
from events.permissions import (
    get_dossier_for_read,
    get_dossier_for_write,
    get_event_for_read,
    get_event_for_write,
)
from events.tests.factories import grant, make_dossier, make_user


class PermissionHelperTests(TestCase):
    def setUp(self):
        self.owner = make_user("owner")
        self.reader = make_user("reader")
        self.writer = make_user("writer")
        self.stranger = make_user("stranger")
        self.dossier = make_dossier(self.owner)
        grant(self.dossier, self.reader, DossierAccess.Role.READ)
        grant(self.dossier, self.writer, DossierAccess.Role.WRITE)
        self.event = Event.objects.create(
            dossier=self.dossier, title="Devis signé", date="2026-06-02"
        )

    def test_read_allowed_for_owner_reader_and_writer(self):
        for user in (self.owner, self.reader, self.writer):
            with self.subTest(user=user.username):
                self.assertEqual(
                    get_dossier_for_read(user, self.dossier.pk), self.dossier
                )

    def test_read_refused_for_stranger(self):
        with self.assertRaises(Http404):
            get_dossier_for_read(self.stranger, self.dossier.pk)

    def test_write_allowed_for_owner_and_writer(self):
        for user in (self.owner, self.writer):
            with self.subTest(user=user.username):
                self.assertEqual(
                    get_dossier_for_write(user, self.dossier.pk), self.dossier
                )

    def test_write_refused_for_reader(self):
        with self.assertRaises(Http404):
            get_dossier_for_write(self.reader, self.dossier.pk)

    def test_write_refused_for_stranger(self):
        with self.assertRaises(Http404):
            get_dossier_for_write(self.stranger, self.dossier.pk)

    def test_event_read_follows_dossier_rights(self):
        self.assertEqual(get_event_for_read(self.reader, self.event.pk), self.event)
        with self.assertRaises(Http404):
            get_event_for_read(self.stranger, self.event.pk)

    def test_event_write_follows_dossier_rights(self):
        self.assertEqual(get_event_for_write(self.writer, self.event.pk), self.event)
        with self.assertRaises(Http404):
            get_event_for_write(self.reader, self.event.pk)

    def test_unknown_ids_raise_404(self):
        with self.assertRaises(Http404):
            get_dossier_for_read(self.owner, 999999)
        with self.assertRaises(Http404):
            get_event_for_read(self.owner, 999999)
```

- [ ] **Step 2 : Lancer les tests pour vérifier qu'ils échouent**

```powershell
.\.venv\Scripts\python.exe manage.py test events.tests.test_permissions -v 2
```

Attendu : `ModuleNotFoundError: No module named 'events.permissions'`.

- [ ] **Step 3 : Écrire les helpers**

`events/permissions.py` :

```python
"""Point de passage unique pour toute décision d'autorisation.

Un dossier auquel l'utilisateur n'a pas droit lève `Http404` et non
`PermissionDenied` : un 403 confirmerait son existence (spec D5).

Toutes les requêtes passent par `Dossier.objects.accessible_by` /
`writable_by`, qui lisent « propriétaire ou accès ». Le propriétaire n'a
pas de ligne dans `DossierAccess` (spec D2) : aucune vue ne doit donc
interroger `DossierAccess` directement.
"""

from django.shortcuts import get_object_or_404

from .models import Dossier, Event


def get_dossier_for_read(user, dossier_id):
    return get_object_or_404(Dossier.objects.accessible_by(user), pk=dossier_id)


def get_dossier_for_write(user, dossier_id):
    return get_object_or_404(Dossier.objects.writable_by(user), pk=dossier_id)


def get_event_for_read(user, event_id):
    return get_object_or_404(
        Event.objects.select_related("dossier").filter(
            dossier__in=Dossier.objects.accessible_by(user)
        ),
        pk=event_id,
    )


def get_event_for_write(user, event_id):
    return get_object_or_404(
        Event.objects.select_related("dossier").filter(
            dossier__in=Dossier.objects.writable_by(user)
        ),
        pk=event_id,
    )
```

- [ ] **Step 4 : Lancer les tests pour vérifier qu'ils passent**

```powershell
.\.venv\Scripts\python.exe manage.py test events.tests.test_permissions -v 2
```

Attendu : 8 tests au vert.

- [ ] **Step 5 : Point de contrôle**

```powershell
.\.venv\Scripts\python.exe manage.py test events -v 2
```

---

### Task 6 : Vues et routes filtrées par dossier

**Files:**
- Modify: `events/views.py` (réécriture de `timeline`, `event_detail`, `event_add`, `event_edit` ; ajout de `dossier_list`)
- Modify: `events/urls.py` (intégralement réécrit)
- Create: `events/templates/events/dossier_list.html`
- Modify: `events/templates/events/timeline.html` (hérite de `base.html`, reçoit un dossier)
- Create: `events/tests/test_views.py`

**Interfaces:**
- Consumes: les quatre helpers (T5), `Event.dossier` (T3), allauth (T4).
- Produces les noms d'URL suivants, utilisés par les gabarits et par T7 :
  `dossier_list`, `timeline`, `event_add`, `event_detail`, `event_edit`, `document_delete`.

- [ ] **Step 1 : Écrire les tests qui échouent**

`events/tests/test_views.py` :

```python
from django.test import TestCase
from django.urls import reverse

from events.models import DossierAccess, Event
from events.tests.factories import grant, make_dossier, make_user


class ViewAccessTests(TestCase):
    def setUp(self):
        self.owner = make_user("owner")
        self.reader = make_user("reader")
        self.writer = make_user("writer")
        self.stranger = make_user("stranger")
        self.dossier = make_dossier(self.owner, name="Litige SDB")
        grant(self.dossier, self.reader, DossierAccess.Role.READ)
        grant(self.dossier, self.writer, DossierAccess.Role.WRITE)
        self.event = Event.objects.create(
            dossier=self.dossier, title="Devis signé", date="2026-06-02"
        )

    def test_dossier_list_shows_only_accessible_dossiers(self):
        other = make_dossier(make_user("elsewhere"), name="Dossier d'un autre")
        self.client.force_login(self.reader)
        response = self.client.get(reverse("dossier_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Litige SDB")
        self.assertNotContains(response, other.name)

    def test_timeline_readable_by_reader(self):
        self.client.force_login(self.reader)
        url = reverse("timeline", args=[self.dossier.pk])
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_timeline_hidden_from_stranger(self):
        self.client.force_login(self.stranger)
        url = reverse("timeline", args=[self.dossier.pk])
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_event_detail_readable_by_reader(self):
        self.client.force_login(self.reader)
        response = self.client.get(reverse("event_detail", args=[self.event.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["title"], "Devis signé")

    def test_event_detail_hidden_from_stranger(self):
        self.client.force_login(self.stranger)
        response = self.client.get(reverse("event_detail", args=[self.event.pk]))
        self.assertEqual(response.status_code, 404)

    def test_unknown_event_detail_returns_404_not_500(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("event_detail", args=[999999]))
        self.assertEqual(response.status_code, 404)

    def test_event_add_refused_to_reader(self):
        self.client.force_login(self.reader)
        url = reverse("event_add", args=[self.dossier.pk])
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_event_add_allowed_to_writer(self):
        self.client.force_login(self.writer)
        url = reverse("event_add", args=[self.dossier.pk])
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_event_add_attaches_event_to_the_dossier(self):
        self.client.force_login(self.writer)
        url = reverse("event_add", args=[self.dossier.pk])
        response = self.client.post(
            url, {"title": "Mise en demeure", "date": "2026-09-27", "description": ""}
        )
        self.assertEqual(response.status_code, 302)
        created = Event.objects.get(title="Mise en demeure")
        self.assertEqual(created.dossier, self.dossier)

    def test_event_edit_is_routed_and_refused_to_reader(self):
        """La vue existait mais n'était pas routée avant ce lot."""
        self.client.force_login(self.reader)
        url = reverse("event_edit", args=[self.event.pk])
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_event_edit_allowed_to_writer(self):
        self.client.force_login(self.writer)
        url = reverse("event_edit", args=[self.event.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
```

- [ ] **Step 2 : Lancer les tests pour vérifier qu'ils échouent**

```powershell
.\.venv\Scripts\python.exe manage.py test events.tests.test_views -v 2
```

Attendu : `NoReverseMatch: Reverse for 'dossier_list' not found`.

- [ ] **Step 3 : Réécrire `events/views.py`**

Remplacer les vues `timeline`, `event_detail`, `event_add`, `event_edit` et ajouter `dossier_list`. `document_delete` et `document_download` sont traitées en T7 — garder `document_delete` telle quelle pour l'instant.

```python
import json

from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse

from .forms import EventForm
from .models import Document, Dossier
from .permissions import (
    get_dossier_for_read,
    get_dossier_for_write,
    get_event_for_read,
    get_event_for_write,
)


def dossier_list(request):
    dossiers = Dossier.objects.accessible_by(request.user).select_related("owner")
    return render(request, "events/dossier_list.html", {"dossiers": dossiers})


def timeline(request, dossier_id):
    dossier = get_dossier_for_read(request.user, dossier_id)

    timeline_items = [
        {"id": e.id, "content": e.title, "start": e.date.strftime("%Y-%m-%d")}
        for e in dossier.events.all()
    ]

    return render(
        request,
        "events/timeline.html",
        {"dossier": dossier, "timeline_items": json.dumps(timeline_items)},
    )


def event_detail(request, event_id):
    event = get_event_for_read(request.user, event_id)
    documents = event.documents.all()

    return JsonResponse(
        {
            "id": event.id,
            "title": event.title,
            "date": event.date.strftime("%d/%m/%Y"),
            "description": event.description,
            "documents": [
                {
                    "id": doc.id,
                    "title": doc.title,
                    "url": reverse("document_download", args=[doc.id]),
                }
                for doc in documents
            ],
        }
    )


def _save_documents(event, files):
    for file in files:
        Document.objects.create(event=event, title=file.name, file=file)


def event_add(request, dossier_id):
    dossier = get_dossier_for_write(request.user, dossier_id)

    if request.method == "POST":
        form = EventForm(request.POST)
        if form.is_valid():
            event = form.save(commit=False)
            event.dossier = dossier
            event.save()
            _save_documents(event, request.FILES.getlist("documents"))
            return redirect("timeline", dossier_id=dossier.pk)
    else:
        form = EventForm()

    return render(
        request,
        "events/event_form.html",
        {"form": form, "event": None, "dossier": dossier},
    )


def event_edit(request, event_id):
    event = get_event_for_write(request.user, event_id)

    if request.method == "POST":
        form = EventForm(request.POST, instance=event)
        if form.is_valid():
            event = form.save()
            _save_documents(event, request.FILES.getlist("documents"))
            return redirect("timeline", dossier_id=event.dossier_id)
    else:
        form = EventForm(instance=event)

    return render(
        request,
        "events/event_form.html",
        {"form": form, "event": event, "dossier": event.dossier},
    )
```

Trois correctifs au passage, signalés dans la spec §7.3 : le `print(timeline_items)` disparaît, `event_detail` ne fait plus de `.get(id=...)` qui rendait un 500 sur identifiant inconnu, et `timeline` ne sérialise plus `Event.objects.all()`. Le `_save_documents` factorisé reste branché sur `getlist("documents")` : la clé `documents[]` du gabarit est le bug que **T3 de L3** corrigera, hors périmètre ici.

**Piège d'ordonnancement à connaître.** `event_detail` appelle `reverse("document_download", ...)`, un nom d'URL qui n'existe qu'à partir de T7. La ligne ne s'exécute que pour un évènement **portant des documents** : les évènements des tests de cette tâche n'en ont aucun, donc la liste reste vide et `reverse` n'est jamais appelé. C'est pourquoi `test_views.py` passe au vert dès T6. En revanche, ouvrir la vraie base entre T6 et T7 et cliquer un évènement documenté lèvera `NoReverseMatch` — c'est attendu, et T7 le résout en routant la vue. Ne pas « corriger » ce symptôme en revenant à `doc.file.url` : ce serait rouvrir la faille que tout le lot ferme.

- [ ] **Step 4 : Réécrire `events/urls.py`**

```python
from django.urls import path

from .views import (
    document_delete,
    dossier_list,
    event_add,
    event_detail,
    event_edit,
    timeline,
)

urlpatterns = [
    path("", dossier_list, name="dossier_list"),
    path("dossiers/<int:dossier_id>/", timeline, name="timeline"),
    path("dossiers/<int:dossier_id>/events/add/", event_add, name="event_add"),
    path("events/<int:event_id>/", event_detail, name="event_detail"),
    path("events/<int:event_id>/edit/", event_edit, name="event_edit"),
    path(
        "documents/<int:document_id>/delete/", document_delete, name="document_delete"
    ),
]
```

`event_edit` est la nouveauté : la vue existait dans `events/views.py` depuis le début mais n'était ni importée ni routée, donc inatteignable.

- [ ] **Step 5 : Créer `events/templates/events/dossier_list.html`**

```html
{% extends "base.html" %}

{% block title %}Mes dossiers{% endblock %}

{% block content %}
    <h1 class="h3 mb-4">Mes dossiers</h1>

    {% if dossiers %}
        <div class="list-group">
            {% for dossier in dossiers %}
                <a class="list-group-item list-group-item-action d-flex justify-content-between align-items-center"
                   href="{% url 'timeline' dossier.pk %}">
                    <span>{{ dossier.name }}</span>
                    <small class="text-muted">
                        {{ dossier.events.count }} évènement{{ dossier.events.count|pluralize }}
                    </small>
                </a>
            {% endfor %}
        </div>
    {% else %}
        <p class="text-muted">Aucun dossier ne vous est accessible.</p>
    {% endif %}
{% endblock %}
```

- [ ] **Step 6 : Adapter `events/templates/events/timeline.html`**

Deux changements ciblés, sans toucher au JavaScript de la chronologie (L2 reprendra la mise en page) :

1. Faire hériter le gabarit de `base.html` : remplacer le `<!DOCTYPE html>`, le `<head>` et les balises `<body>` par `{% extends "base.html" %}` et des blocs `title`, `extra_css`, `content`, `extra_js`. Les quatre feuilles de style vis-timeline / Font Awesome / glightbox vont dans `extra_css`, les scripts dans `extra_js`. Bootstrap vient désormais de `base.html` : retirer ses deux balises en doublon.
2. Remplacer le titre `<h1>Chronologie</h1>` par le nom du dossier et un lien de retour, et faire pointer le bouton d'ajout sur la nouvelle signature d'URL :

```html
    <div class="d-flex justify-content-between align-items-center mb-3">
        <div>
            <a href="{% url 'dossier_list' %}" class="text-decoration-none small">← Mes dossiers</a>
            <h1 class="h3 mb-0">{{ dossier.name }}</h1>
        </div>
        <a href="{% url 'event_add' dossier.pk %}" class="btn btn-primary">
            Nouvel évènement
        </a>
    </div>
```

- [ ] **Step 7 : Lancer les tests pour vérifier qu'ils passent**

```powershell
.\.venv\Scripts\python.exe manage.py test events.tests.test_views events.tests.test_auth -v 2
```

Attendu : les 11 tests de `test_views` au vert. `test_authenticated_user_reaches_root` de T4 passait déjà avant cette tâche (voir la correction au step 1 de T4) ; il doit continuer à passer, maintenant parce que `/` sert `dossier_list` et non plus la chronologie non filtrée.

- [ ] **Step 8 : Point de contrôle**

```powershell
.\.venv\Scripts\python.exe manage.py test events -v 2
```

Attendu : toute la suite au vert.

---

### Task 7 : Documents servis sous contrôle de droits

**Files:**
- Modify: `events/views.py` (ajout de `document_download`, réécriture de `document_delete`)
- Modify: `events/urls.py` (route `document_download`)
- Modify: `config/urls.py:25-27` (retrait de `static(...)`)
- Modify: `events/templates/events/event_form.html:70-90` (bloc « Documents existants »)
- Modify: `events/templates/events/timeline.html` (le JS consomme déjà `doc.url`, à vérifier)
- Create: `events/tests/test_documents.py`

**Interfaces:**
- Consumes: `get_event_for_read` / `get_event_for_write` (T5), le nom d'URL `document_download` déjà employé par `event_detail` (T6).
- Produces: `document_download(request, document_id)` sur `/documents/<id>/`. Plus aucune URL sous `/media/` n'est servie.

- [ ] **Step 1 : Écrire les tests qui échouent**

`events/tests/test_documents.py` :

```python
import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from events.models import Document, DossierAccess, Event
from events.tests.factories import grant, make_dossier, make_user

MEDIA_FOR_TESTS = tempfile.mkdtemp()


@override_settings(MEDIA_ROOT=MEDIA_FOR_TESTS)
class DocumentDownloadTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(MEDIA_FOR_TESTS, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.owner = make_user("owner")
        self.reader = make_user("reader")
        self.stranger = make_user("stranger")
        self.dossier = make_dossier(self.owner)
        grant(self.dossier, self.reader, DossierAccess.Role.READ)
        self.event = Event.objects.create(
            dossier=self.dossier, title="Devis signé", date="2026-06-02"
        )
        self.document = Document.objects.create(
            event=self.event,
            title="devis.pdf",
            file=SimpleUploadedFile("devis.pdf", b"contenu-du-devis"),
        )

    def test_reader_receives_the_file(self):
        self.client.force_login(self.reader)
        response = self.client.get(
            reverse("document_download", args=[self.document.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), b"contenu-du-devis")

    def test_stranger_gets_404(self):
        self.client.force_login(self.stranger)
        response = self.client.get(
            reverse("document_download", args=[self.document.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_anonymous_is_redirected_to_login(self):
        response = self.client.get(
            reverse("document_download", args=[self.document.pk])
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])

    def test_media_url_is_not_served_anymore(self):
        """La faille d'origine : MEDIA_ROOT était monté en statique."""
        response = self.client.get(f"/media/{self.document.file.name}")
        self.assertIn(response.status_code, (302, 404))

    def test_event_detail_exposes_the_protected_url_not_the_media_path(self):
        self.client.force_login(self.reader)
        payload = self.client.get(
            reverse("event_detail", args=[self.event.pk])
        ).json()
        self.assertEqual(
            payload["documents"][0]["url"],
            reverse("document_download", args=[self.document.pk]),
        )
        self.assertNotIn("/media/", payload["documents"][0]["url"])


@override_settings(MEDIA_ROOT=MEDIA_FOR_TESTS)
class DocumentDeleteTests(TestCase):
    def setUp(self):
        self.owner = make_user("owner")
        self.reader = make_user("reader")
        self.dossier = make_dossier(self.owner)
        grant(self.dossier, self.reader, DossierAccess.Role.READ)
        self.event = Event.objects.create(
            dossier=self.dossier, title="Devis signé", date="2026-06-02"
        )
        self.document = Document.objects.create(
            event=self.event,
            title="devis.pdf",
            file=SimpleUploadedFile("devis.pdf", b"contenu-du-devis"),
        )

    def test_reader_cannot_delete(self):
        self.client.force_login(self.reader)
        response = self.client.post(
            reverse("document_delete", args=[self.document.pk])
        )
        self.assertEqual(response.status_code, 404)
        self.assertTrue(Document.objects.filter(pk=self.document.pk).exists())

    def test_owner_can_delete(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            reverse("document_delete", args=[self.document.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Document.objects.filter(pk=self.document.pk).exists())

    def test_get_is_rejected(self):
        self.client.force_login(self.owner)
        response = self.client.get(
            reverse("document_delete", args=[self.document.pk])
        )
        self.assertEqual(response.status_code, 405)
```

- [ ] **Step 2 : Lancer les tests pour vérifier qu'ils échouent**

```powershell
.\.venv\Scripts\python.exe manage.py test events.tests.test_documents -v 2
```

Attendu : `NoReverseMatch: Reverse for 'document_download' not found`.

- [ ] **Step 3 : Ajouter les deux vues documents**

Dans `events/views.py`, compléter les imports :

```python
from django.http import FileResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
```

Puis remplacer `document_delete` et ajouter `document_download` :

```python
def document_download(request, document_id):
    document = get_object_or_404(
        Document.objects.select_related("event__dossier"), pk=document_id
    )
    get_event_for_read(request.user, document.event_id)  # lève 404 si refusé
    return FileResponse(document.file.open("rb"), filename=document.title)


def document_delete(request, document_id):
    if request.method != "POST":
        return JsonResponse({"success": False}, status=405)

    document = get_object_or_404(
        Document.objects.select_related("event__dossier"), pk=document_id
    )
    get_event_for_write(request.user, document.event_id)  # lève 404 si refusé
    document.delete()
    return JsonResponse({"success": True})
```

Le retour des helpers est volontairement ignoré : ils ne sont appelés que pour leur effet de garde, d'où le commentaire sur chaque ligne (spec §8).

- [ ] **Step 4 : Router `document_download`**

Dans `events/urls.py`, ajouter l'import `document_download` et la route, avant `document_delete` :

```python
    path("documents/<int:document_id>/", document_download, name="document_download"),
```

- [ ] **Step 5 : Fermer l'exposition statique de `MEDIA_ROOT`**

Dans `config/urls.py`, supprimer les deux dernières lignes :

```python
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
```

Et les deux imports devenus inutiles :

```python
from django.conf import settings
from django.conf.urls.static import static
```

C'est la correction de la faille décrite en spec §1 : jusqu'ici tout fichier était téléchargeable par quiconque connaissait son URL.

- [ ] **Step 6 : Corriger le bloc « Documents existants » du formulaire**

`events/templates/events/event_form.html` contient aujourd'hui du HTML cassé : `{{ document.file.url }}` et `{% url 'document_delete' document.id %}` sont rendus en texte brut, et deux `</a>` n'ont pas de balise ouvrante. Remplacer le corps de la boucle `{% for document in event.documents.all %}` par :

```html
                    <li class="list-group-item d-flex justify-content-between align-items-center">
                        <a href="{% url 'document_download' document.id %}" target="_blank"
                           rel="noopener noreferrer">{{ document.title }}</a>
                        <button type="button" class="btn btn-sm btn-outline-danger"
                                data-document-delete="{% url 'document_delete' document.id %}">
                            Supprimer
                        </button>
                    </li>
```

Correction minimale et volontairement sans JavaScript : le bouton porte son URL en attribut `data-`, et **L3 le câblera** en même temps qu'il refondra ce formulaire en modal. Le point acquis ici est qu'aucun `doc.file.url` ne subsiste.

- [ ] **Step 7 : Vérifier le JavaScript de la chronologie**

Dans `events/templates/events/timeline.html`, le script consomme `doc.url` tel que fourni par `event_detail`, qui renvoie désormais l'URL protégée. Aucun changement de code n'est nécessaire. Vérifier par lecture qu'aucune occurrence de `/media/` ne subsiste :

```powershell
Select-String -Path events\templates\events\*.html -Pattern "file\.url","/media/"
```

Attendu : aucun résultat.

- [ ] **Step 8 : Lancer les tests pour vérifier qu'ils passent**

```powershell
.\.venv\Scripts\python.exe manage.py test events.tests.test_documents -v 2
```

Attendu : 8 tests au vert.

- [ ] **Step 9 : Vérifier à la main sur les vraies données**

```powershell
.\.venv\Scripts\python.exe manage.py runserver
```

Connecté en `rvilain`, ouvrir le dossier et cliquer un évènement qui porte des documents : les vignettes d'images doivent s'afficher et un PDF s'ouvrir. En navigation privée, l'URL d'un document doit mener à la page de connexion.

- [ ] **Step 10 : Point de contrôle**

```powershell
.\.venv\Scripts\python.exe manage.py test events -v 2
```

---

### Task 8 : Administration des dossiers et des accès

C'est l'interface par laquelle les premiers accès seront donnés, en attendant L4.

**Files:**
- Modify: `events/admin.py`
- Create: `events/tests/test_admin.py`

**Interfaces:**
- Consumes: `Dossier`, `DossierAccess` (T2), `Event.dossier` (T3).
- Produces: `/admin/events/dossier/` avec les accès en inline. Rien ne dépend de cette tâche.

- [ ] **Step 1 : Écrire les tests qui échouent**

`events/tests/test_admin.py` :

```python
from django.contrib.admin.sites import site
from django.test import TestCase

from events.models import Dossier, DossierAccess, Event
from events.tests.factories import make_dossier, make_user


class AdminRegistrationTests(TestCase):
    def test_dossier_and_access_are_registered(self):
        self.assertIn(Dossier, site._registry)
        self.assertIn(DossierAccess, site._registry)

    def test_dossier_admin_shows_accesses_inline(self):
        inlines = site._registry[Dossier].inlines
        self.assertTrue(
            any(inline.model is DossierAccess for inline in inlines),
            "DossierAccess doit être éditable depuis la page du dossier.",
        )

    def test_event_admin_lists_and_filters_by_dossier(self):
        event_admin = site._registry[Event]
        self.assertIn("dossier", event_admin.list_display)
        self.assertIn("dossier", event_admin.list_filter)


class AdminAccessTests(TestCase):
    def test_superuser_reaches_the_dossier_changelist(self):
        admin = make_user("admin", is_staff=True, is_superuser=True)
        make_dossier(admin)
        self.client.force_login(admin)
        response = self.client.get("/admin/events/dossier/")
        self.assertEqual(response.status_code, 200)
```

- [ ] **Step 2 : Lancer les tests pour vérifier qu'ils échouent**

```powershell
.\.venv\Scripts\python.exe manage.py test events.tests.test_admin -v 2
```

Attendu : `AssertionError: <class 'events.models.Dossier'> not found in ...`.

- [ ] **Step 3 : Étendre `events/admin.py`**

```python
from django.contrib import admin

from .models import Document, Dossier, DossierAccess, Event


class DocumentInline(admin.TabularInline):
    model = Document
    extra = 1


class DossierAccessInline(admin.TabularInline):
    model = DossierAccess
    fk_name = "dossier"
    extra = 1
    autocomplete_fields = ("user",)
    fields = ("user", "role", "origin", "granted_by")


@admin.register(Dossier)
class DossierAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "created_at")
    search_fields = ("name",)
    inlines = [DossierAccessInline]


@admin.register(DossierAccess)
class DossierAccessAdmin(admin.ModelAdmin):
    list_display = ("dossier", "user", "role", "origin", "granted_at")
    list_filter = ("role", "origin")


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ("title", "date", "dossier")
    list_filter = ("dossier",)
    search_fields = ("title",)
    inlines = [DocumentInline]


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ("title", "event", "uploaded_at")
```

`autocomplete_fields = ("user",)` exige que le `ModelAdmin` de `User` déclare `search_fields`. Celui de Django le fait déjà — si `manage.py check` proteste malgré tout (`admin.E039`), retirer la ligne : c'est un confort, pas une exigence.

- [ ] **Step 4 : Lancer les tests pour vérifier qu'ils passent**

```powershell
.\.venv\Scripts\python.exe manage.py test events.tests.test_admin -v 2
.\.venv\Scripts\python.exe manage.py check
```

Attendu : 4 tests au vert et aucun avertissement de `check`.

- [ ] **Step 5 : Point de contrôle final du lot**

```powershell
.\.venv\Scripts\python.exe manage.py test -v 2
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
```

Les trois doivent passer. Toute la suite au vert, aucun échec connu, aucune migration manquante.

- [ ] **Step 6 : Recette manuelle de bout en bout**

Sur `runserver`, vérifier dans l'ordre :

1. En navigation privée, `/` mène à la page de connexion.
2. `/accounts/signup/` annonce que l'inscription est fermée.
3. Connecté en `rvilain`, `/` liste « Salle de bain — litige ACME Bat » avec 24 évènements.
4. La chronologie s'affiche, un clic sur un évènement charge ses documents, images et PDF compris.
5. « Nouvel évènement » crée un évènement rattaché au bon dossier.
6. Un évènement existant est modifiable — c'est inédit, la vue n'était pas routée avant.
7. Dans l'admin, créer un second compte et lui donner un accès en lecture sur le dossier. Connecté avec ce compte : la chronologie est visible, « Nouvel évènement » renvoie 404, et le bouton de suppression d'un document renvoie 404.

---

## Ce que ce lot ne corrige pas

À rappeler en ouvrant L2 et L3, pour que personne ne croie à une régression :

- **Le multi-upload ne sauvegarde toujours rien.** Le champ du gabarit s'appelle `documents[]` alors que la vue lit `getlist("documents")`. C'est le premier point de L3. L1 n'y touche pas pour ne pas modifier un formulaire que L3 réécrit en modal.
- **Le bouton « Supprimer » d'un document n'est pas câblé.** Il porte son URL en `data-document-delete` et attend le JavaScript de L3.
- **Les deux gabarits restent inégaux visuellement.** `timeline.html` hérite de `base.html`, `event_form.html` garde son propre `<head>` en Bootstrap 5.3.3 — c'est L2 qui unifie.
- **Aucune sauvegarde automatique** de `db.sqlite3` ni de `media/`. T3 en fait une copie horodatée ponctuelle, ce n'est pas une politique de sauvegarde.
