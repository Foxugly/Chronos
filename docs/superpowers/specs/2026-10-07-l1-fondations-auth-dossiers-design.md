# L1 — Fondations : authentification, dossiers et documents protégés

Date : 2026-10-07
Statut : en attente de relecture
Lot : 1 sur 4 (L2 Layout, L3 Modal + upload, L4 Partage suivent)

## 1. Contexte

SDBTimeline est une application Django 6.1 qui retrace la chronologie d'un litige
de chantier de salle de bain : 24 évènements et 49 documents (devis, factures,
photos de malfaçons, avis d'architecte, échanges TestAchats, mises en demeure).
C'est un dossier de preuves.

Aujourd'hui l'application est entièrement ouverte : aucune authentification, et
`config/urls.py` expose `MEDIA_ROOT` en statique, donc tout fichier est
téléchargeable par quiconque connaît son URL.

L1 pose les fondations sur lesquelles reposent les trois lots suivants : qui est
l'utilisateur, à quel dossier il a droit, et comment un document lui parvient.

## 2. Objectifs

1. Rien n'est accessible sans être connecté ; les comptes sont créés par
   l'administrateur, l'inscription libre est fermée.
2. Les évènements et les documents appartiennent à un dossier. Le propriétaire
   d'un dossier peut y donner accès en lecture ou en lecture/écriture.
3. Les documents ne sont plus servis en statique mais par une vue qui vérifie
   les droits.
4. Les données existantes sont préservées et rattachées à un dossier initial.
5. Le modèle de permissions est couvert par des tests.

## 3. Hors périmètre

- Tout travail de mise en page au-delà d'un `base.html` minimal (→ L2).
- Formulaires en modal, soumission AJAX, réparation du multi-upload (→ L3).
- Les trois mécanismes de partage : sélection de compte, invitation par e-mail,
  lien de partage secret (→ L4). L1 prépare le modèle qui les recevra.
- Déploiement sur la flotte EC2. L'application reste locale.

## 4. Décisions arrêtées

| # | Décision | Raison |
|---|---|---|
| D1 | Modèle d'accès explicite `Dossier` + `DossierAccess`, sans django-guardian | Deux rôles seulement ; guardian imposerait une dépendance et des tables génériques sans résoudre les invitations ni le lien secret, qui demanderaient un modèle à côté de toute façon |
| D2 | Le propriétaire n'a **pas** de ligne dans `DossierAccess` | Deux sources de vérité pourraient se contredire et devraient être resynchronisées à chaque opération |
| D3 | `Document` n'a pas de FK vers `Dossier` | Les droits se déduisent de `document.event.dossier` ; dupliquer la FK rendrait possible un document rattaché à un autre dossier que son évènement |
| D4 | `Dossier.owner` en `PROTECT`, `DossierAccess.user` en `CASCADE` | Supprimer un compte invité retire son accès ; supprimer le propriétaire d'un dossier de preuves doit être refusé, pas emporter 24 évènements |
| D5 | Un dossier inaccessible renvoie 404, jamais 403 | Un 403 confirmerait l'existence du dossier |
| D6 | `AUTHENTICATION_BACKENDS` ne contient que le backend allauth | La doc allauth déconseille d'ajouter `ModelBackend` pour le login admin, dont la vue n'est pas rate-limitée |
| D7 | `django-allauth >= 65` épinglé | La 65.0.0 a ajouté le support transparent de `LoginRequiredMiddleware` ; sur une version antérieure la page de login se redirigerait sur elle-même |
| D8 | `ACCOUNT_EMAIL_VERIFICATION = "none"` | Les comptes sont créés par l'administrateur ; exiger une confirmation bloquerait un compte que personne ne viendra confirmer |
| D9 | Pas de `django.contrib.sites` ni de `SITE_ID` | La liste des apps requises par allauth se limite à `auth`, `messages`, `allauth`, `allauth.account` |
| D10 | Le service des fichiers passe par un `FileResponse` Django, pas par nginx | 49 fichiers en local ; le jour d'un passage en production on bascule sur `X-Accel-Redirect` sans toucher au modèle |
| D11 | `TIME_ZONE` passe de `UTC` à `Europe/Brussels` | `LANGUAGE_CODE` est déjà `fr-be` ; les `created_at` et `uploaded_at` s'affichent aujourd'hui en UTC dans l'admin. Les dates d'évènement sont des `DateField`, donc non affectées. Réversible en une ligne |
| D12 | La `SECRET_KEY` actuelle est régénérée, pas seulement déplacée | Elle est en clair dans `config/settings.py` depuis la création du projet ; la déplacer sans la changer conserverait une clé exposée |

## 5. Modèle de données

### 5.1 Nouveaux modèles

```python
class Dossier(models.Model):
    name = models.CharField(max_length=255)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL,
                              related_name="dossiers_owned",
                              on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = DossierQuerySet.as_manager()

    class Meta:
        ordering = ["name"]


class DossierAccess(models.Model):
    class Role(models.TextChoices):
        READ  = "read",  "Lecture"
        WRITE = "write", "Lecture et écriture"

    class Origin(models.TextChoices):
        DIRECT     = "direct",     "Compte choisi"
        INVITATION = "invitation", "Invitation par e-mail"
        LINK       = "link",       "Lien de partage"

    dossier    = models.ForeignKey(Dossier, related_name="accesses",
                                   on_delete=models.CASCADE)
    user       = models.ForeignKey(settings.AUTH_USER_MODEL,
                                   related_name="dossier_accesses",
                                   on_delete=models.CASCADE)
    role       = models.CharField(max_length=10, choices=Role.choices)
    origin     = models.CharField(max_length=10, choices=Origin.choices,
                                  default=Origin.DIRECT)
    granted_at = models.DateTimeField(auto_now_add=True)
    granted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   related_name="+", on_delete=models.SET_NULL)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["dossier", "user"],
                                    name="unique_access_per_user_per_dossier"),
        ]
```

`role` et `origin` sont en `max_length=10` plutôt qu'au plus juste, pour qu'un
libellé plus long n'impose pas une migration.

### 5.2 Modèles existants

`Event` reçoit `dossier = ForeignKey(Dossier, related_name="events", on_delete=CASCADE)`.
`Document` n'est pas modifié (cf. D3).

### 5.3 Migration des données

Une seule migration, en trois opérations, réversible :

1. `AddField` de `Event.dossier` en `null=True`.
2. `RunPython` : crée le dossier « Salle de bain — litige ACME Bat », propriétaire
   = **le premier superuser existant** (et non un nom codé en dur, pour que la
   migration passe aussi sur une base vierge ou chez un autre développeur), puis
   rattache tous les évènements orphelins à ce dossier. Si aucun superuser
   n'existe et qu'il y a des évènements à rattacher, la migration échoue avec un
   message explicite plutôt que de créer un propriétaire fantôme.
3. `AlterField` de `Event.dossier` en `null=False`.

Le sens inverse remet la colonne en nullable et supprime le dossier créé s'il
est vide.

## 6. Authentification

### 6.1 Dépendance

`django-allauth >= 65, < 66`, sans l'extra `socialaccount` : aucun provider
social n'est demandé.

### 6.2 Réglages

- `INSTALLED_APPS` += `allauth`, `allauth.account`.
- `MIDDLEWARE` += `allauth.account.middleware.AccountMiddleware` (obligatoire),
  et `django.contrib.auth.middleware.LoginRequiredMiddleware` juste après
  `AuthenticationMiddleware`.
- `AUTHENTICATION_BACKENDS = ["allauth.account.auth_backends.AuthenticationBackend"]`.
- `ACCOUNT_LOGIN_METHODS = {"username", "email"}` — le compte existant
  (`rvilain`) a un identifiant, et l'e-mail est plus naturel pour un invité.
- `ACCOUNT_EMAIL_VERIFICATION = "none"` (D8).
- `ACCOUNT_ADAPTER` pointe sur un adaptateur maison (§6.3).
- `LOGIN_REDIRECT_URL = "/"` (la liste des dossiers) et
  `ACCOUNT_LOGOUT_REDIRECT_URL = "/accounts/login/"`.
- `TEMPLATES[0]["DIRS"] = [BASE_DIR / "templates"]` (aujourd'hui `[]`), requis
  pour surcharger les gabarits allauth.

### 6.3 Fermeture de l'inscription

```python
class ClosedSignupAccountAdapter(DefaultAccountAdapter):
    def is_open_for_signup(self, request):
        return False
```

`CloseableSignupMixin`, dont hérite la `SignupView` d'allauth, interroge cette
méthode et rend la page « inscription fermée ». Les comptes sont créés dans
l'admin Django.

### 6.4 Gabarits

L1 crée `templates/base.html` minimal (doctype, Bootstrap, un bloc `content`) et
laisse allauth utiliser ses gabarits par défaut, qui en héritent. L2 reprend ce
fichier pour y poser la navbar et le reste.

### 6.5 Mot de passe oublié

Fourni par allauth. Le `MAILERS` actuel est sur le backend console, donc en L1
le lien s'affiche dans le terminal. C'est suffisant pour un compte que tu
administres ; L4 bascule sur SMTP parce que les invitations en dépendent.

## 7. Autorisation

### 7.1 Queryset

```python
class DossierQuerySet(models.QuerySet):
    def accessible_by(self, user):
        return self.filter(Q(owner=user) | Q(accesses__user=user)).distinct()

    def writable_by(self, user):
        return self.filter(
            Q(owner=user)
            | Q(accesses__user=user, accesses__role=DossierAccess.Role.WRITE)
        ).distinct()
```

Les deux méthodes lisent systématiquement « propriétaire **ou** accès », jamais
l'accès seul : c'est la contrepartie de D2, et la raison pour laquelle aucune vue
ne doit filtrer `DossierAccess` à la main.

### 7.2 Helpers

Nouveau `events/permissions.py` :

- `get_dossier_for_read(user, pk)` → `Dossier` ou `Http404`.
- `get_dossier_for_write(user, pk)` → `Dossier` ou `Http404`.
- `get_event_for_read(user, pk)` / `get_event_for_write(user, pk)` → résolvent
  l'évènement **et** le droit sur son dossier en une requête.

Tous lèvent `Http404` (D5).

### 7.3 Vues et URLs

| URL | Vue | Droit |
|---|---|---|
| `/` | `dossier_list` (nouvelle) | connecté |
| `/dossiers/<id>/` | `timeline` | lecture |
| `/dossiers/<id>/events/add/` | `event_add` | écriture |
| `/events/<id>/` | `event_detail` (JSON) | lecture |
| `/events/<id>/edit/` | `event_edit` | écriture |
| `/documents/<id>/` | `document_download` (nouvelle) | lecture |
| `/documents/<id>/delete/` | `document_delete` | écriture |

`event_edit` existe déjà dans `events/views.py` mais n'est ni importée ni routée
dans `events/urls.py` : elle est donc inatteignable aujourd'hui. L1 la route.

Corrections au passage, dans le périmètre parce que ces lignes sont réécrites :

- Suppression du `print(timeline_items)` (`events/views.py:22`).
- `event_detail` utilise `.get(id=...)` (`events/views.py:31`), qui rend un 500
  sur un identifiant inconnu : passage à `get_object_or_404`.
- `timeline` sérialise les évènements du seul dossier demandé, plus
  `Event.objects.all()`.

### 7.4 Admin

`Dossier` et `DossierAccess` sont enregistrés, avec `DossierAccess` en inline de
`Dossier` : c'est par là que tu donneras les premiers accès avant que L4
n'apporte l'interface. `EventAdmin` gagne `dossier` dans `list_display` et
`list_filter`. L'admin reste réservé aux superusers et n'est pas filtré par
dossier.

## 8. Documents protégés

Suppression des deux dernières lignes de `config/urls.py` qui montent
`static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)`. `MEDIA_ROOT`
reste `BASE_DIR / "media"` mais plus rien ne l'expose.

```python
def document_download(request, document_id):
    document = get_object_or_404(
        Document.objects.select_related("event__dossier"), pk=document_id
    )
    get_dossier_for_read(request.user, document.event.dossier_id)  # lève 404
    return FileResponse(document.file.open("rb"), filename=document.title)
```

Le retour du helper est volontairement ignoré : il n'est appelé que pour son
effet de garde. C'est la seule occurrence de ce style dans le lot, et elle est
commentée pour qu'on ne la prenne pas pour un oubli.

Conséquence sur les gabarits : `doc.file.url` ne doit plus apparaître nulle
part. Le JSON de `event_detail` renvoie désormais
`reverse("document_download", args=[doc.id])`, et le bloc « Documents
existants » du formulaire utilise `{% url %}`. Les images et la lightbox
continuent de fonctionner, ce sont de simples URLs.

`upload_to="documents/"` est laissé tel quel : le modifier ne déplacerait pas
les 49 fichiers déjà enregistrés et n'apporterait rien ici.

## 9. Hygiène du projet

1. **Venv.** `.venv/pyvenv.cfg` pointe sur `C:\Python314` (absent de ce poste) et
   a été créé sous `C:\Users\rvilain\...`, soit une autre machine. Rien ne peut
   tourner en l'état. Reconstruction avec le Python 3.14 du poste
   (`C:\Users\Renaud\AppData\Local\Python\pythoncore-3.14-64\python.exe`), comme
   le fait `_venv_rebuild.ps1` à la racine du workspace. C'est la toute première
   étape du plan : sans elle, aucune vérification n'est possible.
2. **`requirements.txt`.** Le fichier est en UTF-16 et contient un `pip list`
   collé, `pip 26.2.1` compris. Réécriture en UTF-8 avec les seules dépendances
   directes : `Django>=6.1,<6.2` et `django-allauth>=65,<66`. `asgiref`,
   `sqlparse` et `tzdata` sont transitives et disparaissent.
3. **Réglages sensibles.** `SECRET_KEY`, `DEBUG` et `ALLOWED_HOSTS` sont lus
   depuis l'environnement (`DJANGO_SECRET_KEY`, `DJANGO_DEBUG`,
   `DJANGO_ALLOWED_HOSTS`). Pour que `manage.py runserver` continue de marcher
   sans aucune configuration préalable, `SECRET_KEY` garde une valeur de repli
   explicitement nommée clé de développement, **régénérée** (D12) ; la clé
   actuelle, exposée en clair depuis la création du projet, disparaît. `DEBUG`
   vaut `True` par défaut tant que le projet reste local. Le jour d'un passage
   en production, les trois viennent de SSM comme le reste de la flotte, et la
   valeur de repli n'est pas utilisée.
4. **`TIME_ZONE`** → `Europe/Brussels` (D11).

## 10. Tests

`events/tests.py` est vide : il n'existe aucun test. Un modèle de permissions
écrit à la main sans tests est le défaut le plus coûteux que ce lot pourrait
laisser. `events/tests.py` devient un paquet `events/tests/` :

- `test_models.py` — `accessible_by` et `writable_by` pour les quatre profils
  (propriétaire, lecture, écriture, étranger) ; la contrainte d'unicité
  `(dossier, user)` ; le `PROTECT` sur la suppression d'un propriétaire.
- `test_permissions.py` — chaque helper renvoie l'objet ou lève `Http404`, pour
  les quatre profils.
- `test_views.py` — pour chaque vue du tableau §7.3 : un étranger obtient 404,
  un lecteur obtient 200 en lecture et 404 sur les vues d'écriture, un rédacteur
  obtient 200 partout. `dossier_list` ne montre que les dossiers accessibles.
- `test_documents.py` — `document_download` sert le fichier à un lecteur, rend
  404 à un étranger ; plus aucune URL `/media/` n'est servie.
- `test_auth.py` — un anonyme est redirigé vers la connexion depuis chaque URL ;
  `/accounts/signup/` rend la page « inscription fermée » ; la connexion marche
  par identifiant et par e-mail.

Un test vérifie aussi qu'après migration les 24 évènements ont un dossier et que
ce dossier a un propriétaire.

## 11. Risques et points ouverts

- **`DossierAccess.Origin.LINK` est ambigu à ce stade.** Un `DossierAccess`
  exige un `user`, alors qu'un lien de partage secret est par nature destiné à
  quelqu'un sans compte. La valeur `LINK` n'a donc de sens que si le
  destinataire d'un lien se connecte ou se crée un compte. L4 devra trancher :
  soit le lien donne un accès anonyme et relève d'un modèle `ShareLink` distinct
  — l'hypothèse la plus probable —, soit il convertit son destinataire en
  utilisateur et `origin=LINK` s'applique. Rien en L1 ne dépend de ce choix ;
  la valeur est réservée, pas utilisée.
- **Le lien de partage secret contredira « tout privé ».** Réserve déjà émise et
  maintenue : il ouvrira une porte sans compte. La décision est prise en
  connaissance de cause ; L4 le concevra en lecture seule, révocable, avec
  expiration optionnelle.
- **Le mot de passe oublié dépend d'un envoi d'e-mail** qui n'existe pas encore
  (backend console). Acceptable en L1 pour un compte administré ; bloquant en L4.
- **Les 49 fichiers de `media/` ne sont pas sauvegardés** et `db.sqlite3` non
  plus. C'est un dossier de preuves sans copie de sauvegarde ; hors périmètre de
  L1, mais à traiter.
- **La migration de données s'exécute sur une base contenant les seules données
  réelles du projet.** Elle doit être essayée sur une copie de `db.sqlite3`
  avant d'être appliquée.

## 12. Suite

- **L2 Layout** — `base.html` étoffé, navbar, sélecteur de dossier, Bootstrap
  unifié (la timeline est en 5.3.8, le formulaire en 5.3.3), polish de la
  chronologie et de la grille de documents.
- **L3 Modal + upload** — formulaire create/edit en modal soumis en AJAX, et
  réparation du multi-upload : le champ s'appelle `documents[]` dans le gabarit
  alors que la vue lit `getlist("documents")`, donc aucun fichier n'est
  enregistré depuis le formulaire. Le bloc « Documents existants » est par
  ailleurs du HTML cassé, avec des `</a>` sans balise ouvrante et des balises
  de gabarit affichées en texte brut.
- **L4 Partage** — sélection d'un compte existant, invitation par e-mail, lien
  secret révocable ; bascule de `MAILERS` sur SMTP.
