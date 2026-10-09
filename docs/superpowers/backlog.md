# Backlog Chronos

**État au 2026-10-09.** Lots L1 à L6 livrés, plus la précision temporelle, le lot
robustesse + sauvegarde et l'import de courriels. **En production depuis le 2026-10-09** sur
`https://chronos.foxugly.com` (base vide au départ, remplie par l'import de courriels).
**652 tests**, verts ; dernière migration `0019_event_transfert_de`, aucune en attente.

Lire d'abord la section « Ouvert » : c'est tout ce qui reste. Le reste du fichier est
l'historique de ce qui a été tranché, et pourquoi.

## Ouvert

### Production : ce qui reste

- **Changer l'alias du dossier ACMEBAT** : il date d'avant le suffixe `.chronos@foxugly.com`.
  Il reçoit toujours ; la page Paramètres le signale. L'enregistrer à nouveau impose le format
  (Chronos ajoute le nouveau dans Exchange puis retire l'ancien).
- **`OPERATIONS.md`** (dépôt `foxugly-ops`) : ligne d'inventaire, rôle `chronos-deploy` dans la
  table du §3.11 — et Facilitation, qui y manque aussi.
- **Sauvegardes : décision du propriétaire (2026-10-09), ne plus en parler.** L'hébergement AWS est jugé
  suffisant pour un usage non commercial : pas de copie hors machine, pas de vérification de la
  première sauvegarde PostgreSQL. `chronos-sauvegarde.timer` tourne quand même chaque nuit.
- **HSTS envoyé deux fois** (Django et nginx, durées différentes) : en retirer un.
- **Échéances** : secret de l'application « Chronos » et certificat « Chronos Exchange »
  expirent le **2028-10-09** (relancer `deploy/m365-setup.ps1`).
- **Vérifier dans un navigateur** le champ d'alias en `input-group` et le bouton « Copier »
  (couverts par les tests seulement).

### Liens et doublons entre courriels (2026-10-09)

**Fait** : `events/liens.py` (empreinte à 20 min), reconnaissance du même courriel à l'import,
lien « En réponse à » (`Event.reponse_a`, migration `0020`) en amont et en aval, page Doublons qui
propose le même courriel arrivé deux fois (on garde le plus riche), expéditeurs mal formés lus,
commande `manage.py relier_courriels` (essai par défaut, `--appliquer`). **Reste** : lancer la
commande sur la production, fusionner n° 28 dans n° 1 par la page Doublons, et la piste « fil de
discussion » pour les cas comme n° 12 / n° 47 qui ne citent rien.

Constat de départ :

Constaté en production (47 évènements importés) par une analyse en lecture seule des `.eml` :

- **Un vrai doublon** : n° 1 et n° 28, la mise en demeure du 28/09 20:29 arrivée par deux chemins
  (en direct, puis dans un autre transfert) — deux clés, deux évènements. La page Doublons ne le
  proposait pas : deux évènements importés n'y sont jamais comparés.
- **17 liens « répond à / transfère »** détectables : un courriel cite, sous un bloc « De : …
  Envoyé : … », un courriel présent dans le dossier (même expéditeur, même objet hors TR:/RE:, à
  20 minutes près — l'heure d'envoi et l'heure de réception diffèrent de 2 à 13 min).
- **Faux doublons à écarter** : n° 4/5 et n° 40/41, même expéditeur et même objet à quelques
  minutes — mais l'un répond à l'autre. Un courriel et sa réponse ne sont jamais des doublons.
- **6 citations sans correspondance** : les courriels cités n'ont pas été importés (BNQ, Julie
  Petit, Paul Durand) — normal.
- **n° 12 → n° 47** (l'architecte, puis l'envoi à l'entrepreneur) : n° 12 ne cite rien, aucun lien
  n'est prouvable. Seul indice : le même fil (« sdb : jonctions sol/murs »). Piste : une vue
  « fil de discussion » par objet normalisé, sans lien stocké.
- **Expéditeur manquant** sur une dizaine d'évènements : un « De : » écrit
  `jdupont@perso.example <jdupont@perso.example>` ou `Nom [mailto:…]` n'était pas lu comme une adresse.

### Monitoring de Chronos

- ~~Sentry~~ **fait le 2026-10-09** : projet `chronos-backend` (créé par le propriétaire — les
  membres ne peuvent pas créer de projet dans l'org), `SENTRY_DSN` (SecureString) dans
  `/chronos/prod`, message de test reçu (CHRONOS-BACKEND-1, résolu). Environnement `production`,
  sans données personnelles, sans traces de performance.
- ~~UptimeRobot~~ **fait le 2026-10-09** : moniteur sur `https://chronos.foxugly.com/health/`
  (créé par le propriétaire dans le tableau de bord).

### Flotte, relevé en chemin (2026-10-09)

- ~~Facilitation → Redis db6~~ **fait le 2026-10-09** (SSM puis redémarrage ; Celery connecté à
  db6). Même jour : son API passe sous gunicorn (8011), daphne ne garde que `/ws/`, et
  `NUM_PROXIES = 1` ferme le contournement des limites par `X-Forwarded-For`.
- ~~poker et pushit partagent Redis db3~~ **corrigé le 2026-10-09** : les résultats Celery de pushit
  passent en db7 (SSM puis redémarrage ; pushit ne les relit jamais). Prochaine base libre : db8.
- **Mémoire** : swap occupé à 1,47 Go sur 2 Go, 960 Mo disponibles après la séparation
  daphne/gunicorn de Facilitation (gunicorn + daphne ≈ 325 Mo). À surveiller.

### Demande une décision du propriétaire

- **Une sauvegarde sur le même disque ne protège pas d'une panne de ce disque.**
  `manage.py sauvegarder` écrit par défaut dans `sauvegardes/`, à côté de la base. L'option
  `--destination` (ou `-Destination` du script) accepte n'importe quel dossier : un second
  disque, un disque externe, ou le dossier Dropbox. Ce dernier choix **envoie les pièces du
  litige chez un tiers** : c'est au propriétaire de le décider, pas au code.
- **Quatre `.jpg` sans ligne `Document`** dans `media/`, antérieurs à L1. Laissés en place :
  supprimer un fichier que personne ne sait expliquer est la pire des deux erreurs.

### Multilingue : ce qui reste

Le site est en FR / NL / EN (voir « Fait »). Restent :

- **Relecture du néerlandais** par un locuteur natif : les traductions ont été écrites d'un
  seul jet. Elles vivent dans `locale/nl/LC_MESSAGES/django.po`.
- **Langue d'un courriel d'invitation** : aujourd'hui celle de l'émetteur au moment de l'envoi.
  Celle du destinataire demanderait de la connaître (champ à ajouter à l'invitation).
- Un anglais américain demanderait de lire `FIRST_DAY_OF_WEEK` dans `calendrier.py`, qui
  suppose le lundi.

### Laissé de côté volontairement

- La vue mois affiche `↔` pour un intervalle mais pas ses heures : la case est trop étroite.
  L'attribut `title` porte la période complète.
- **Onglets FullCalendar après un enregistrement** : la page se recharge (la chronologie, elle,
  se met à jour sur place).
- **Le journal ne couvre pas** ce qui précède le 2026-10-08.
- Aucun calcul de durée (« 42 jours ») : une durée en jours ouvrables ou calendaires est une
  question juridique, pas un arrondi d'affichage.
- **Le verrouillage optimiste ne couvre que le formulaire de l'évènement.** Ajouter ou
  retirer une pièce n'incrémente pas la version : ces gestes ajoutent ou retirent un lien,
  ils n'écrasent rien de ce qu'un autre a saisi.
- **Un envoi sans champ `version` n'est pas contrôlé** (dernier arrivé gagne), comme
  `precision` qui reste facultatif. Le verrou protège les personnes qui utilisent le
  formulaire, il ne conditionne pas l'enregistrement pour un client HTTP direct.
- **L'admin garde le dernier mot** entre deux administrateurs, mais incrémente la version :
  un formulaire ouvert avant sa correction est refusé, il ne l'écrase pas.
- `apps.get_model("auth", "User")` est codé en dur dans la migration `0003` au lieu de
  `AUTH_USER_MODEL`. Elle est appliquée et irréversible : la corriger ne changerait rien.
- `_dossier_temporarily_nullable` (helper de test) supprime au nettoyage les `Event`
  orphelins, ce qui cascaderait sur leurs `Document`. Sans effet dans les tests actuels.

## Fait

### Habillage de la flotte — 2026-10-09

- Chronos suit `STANDARD-frontend-layout.md` : topbar sombre (marque, « Mes dossiers », thème →
  langue → utilisateur, tiroir sous 1024 px), footer (version, © Foxugly, Confidentialité),
  en-tête de page 3 colonnes partout, carte commune aux pages de connexion, tokens et couleurs
  sémantiques de la flotte (emerald, vert créer, bleu éditer, rouge supprimer), **thème sombre**
  (sans flash : script en tête avec nonce ; chronologie, calendrier et éditeur retouchés).
- Favicon (fin du 404), version affichée (`settings.VERSION`, aussi dans `/health/`).
- **À relire par le propriétaire** : la page `/confidentialite/` (factuelle, rédigée d'après le
  code et l'hébergement).

### Mise en production — 2026-10-09

- `https://chronos.foxugly.com`, port **8010** (8009 était pris par Facilitation), PostgreSQL 16,
  dépôt GitHub **privé** `Foxugly/Chronos` (clé de déploiement en lecture seule sur la machine).
- **Base de production vide, par choix** : les 30 évènements et 77 pièces du PC n'ont pas été
  transférés ; le dossier ACMEBAT se remplit par l'import de courriels.
- IAM (`deploy/iam-cloudshell.sh`) et SSM (`deploy/ssm-cloudshell.sh`) depuis CloudShell ;
  Microsoft 365 par `deploy/m365-setup.ps1` (boîte `hello@foxugly.com`, droits Exchange limités à
  elle). Import toutes les 10 minutes (`chronos-courriels.timer`).
- Corrigé en chemin : le contrôle de déploiement en CI (`GRAPH_SENDER`), le refus de sauvegarder
  dans une transaction ouvert à PostgreSQL, et un déploiement qui se disait réussi sans avoir
  migré (`set -eu` en tête des commandes SSM).
- Mot de passe de la base changé après avoir été collé en clair dans la conversation.

### Une couche de transfert, un évènement — 2026-10-09

- Un courriel transféré à l'avocate puis à l'alias donne **deux évènements** : l'original (la mise en
  demeure du 28/09) et le transfert (07/10, avec le mot écrit à l'avocate), relié au premier
  (`Event.transfert_de`, migration `0019`). Le détail affiche « Transfert de : … » et « Transféré
  ensuite : … ». L'enveloppe envoyée à l'alias n'est un évènement que si elle s'adresse aussi à
  quelqu'un d'autre. Une couche déjà importée n'est pas recréée ; le nouveau transfert s'y relie.
- Le lien apparaît aussi dans la **vue liste** (calculé sur la liste déjà chargée, aucune requête de
  plus) et dans l'**export PDF** (« Transfert de : … », « Transféré ensuite : … », avec « hors de
  cette sélection » quand l'évènement lié n'est pas dans un export partiel).

### Bouton « Relever les courriels » — 2026-10-09

- Dans l'en-tête du dossier, pour qui peut écrire : relève la boîte tout de suite. Verrou PostgreSQL
  partagé avec la minuterie ; le message ne cite que les courriels de ce dossier.

### Import de courriels sur une boîte partagée — 2026-10-09

- Seuls les courriels des **dernières 24 h** sont lus (`COURRIEL_FENETRE`). Ceux qui visent un alias
  sont importés puis rangés dans un dossier Outlook **du nom du dossier Chronos** ; les autres ne
  sont ni déplacés ni cités dans les journaux. Les dossiers « Importés » / « Non importés » ne
  servent plus. Décision du propriétaire : la boîte d'import est `hello@`, qui sert à autre chose.
- `deploy/m365-setup.ps1` : applications Entra, droits Exchange limités, SSM et activation sur la
  machine, en un script.

### Numéros de pièces, étiquettes, navigation, impression, tableau de bord — 2026-10-09

- **Numéros de pièces définitifs** (1 à 77 attribués dans l'ordre de la chronologie) : affichés
  partout, repris tels quels par l'export, jamais réattribués.
- **Étiquettes** (plusieurs par évènement), filtre, gestion dans Paramètres.
- **Détail** : fait précédent / suivant (boutons, flèches ← →), rang « 29 / 30 ».
- **Date des photos** : une photo JPEG déposée dans un nouvel évènement sans date en donne la date
  et l'heure de prise de vue (EXIF, lu dans le navigateur).
- **Raccourcis** : `/` recherche, `n` nouvel évènement, ← → dans le détail.
- **Impression** des vues Liste et Documents (bouton « Imprimer », sélection rappelée en tête).
- **Accueil en tableau de bord** ; **chronologie** avec zoom, « Tout voir », « Aujourd'hui » et
  légende des couleurs.

### Quatre onglets, mise en page harmonisée — 2026-10-09

- Chronologie, Calendrier (mois / semaine / liste / frise dans la barre de FullCalendar), Liste,
  Documents. Les vues mois et semaine sans JavaScript restent servies (repli, anciens liens).
- Même cadre pour les quatre vues : en-tête, onglets et filtres ne bougent plus d'un onglet à l'autre.
- L'audit frontend et la direction visuelle proposée sont dans `docs/superpowers/direction-visuelle.md`
  (pas encore appliquée).

### Onglet « Documents » — 2026-10-09

- Toutes les pièces du dossier en tableau (évènements liés, taille, date de dépôt, empreinte),
  sans JavaScript. Recherche dans le nom et le texte de la pièce, filtre par type (PDF, images,
  courriels, bureautique), par expéditeur et par période.

### Recherche, filtres, export ciblé, doublons, tests rapides — 2026-10-09

- **Recherche** dans toutes les vues (titre, description, noms des pièces, texte des `.md` des
  courriels), sans accents ni majuscules, tous les mots requis ; « Trouvé dans : … » en vue liste.
- **Filtres** par expéditeur (ou « saisis à la main ») et par période (mois), gardés d'un onglet à
  l'autre et par les liens de partage.
- **Export ciblé** : le bouton exporte la sélection affichée (« Exporter la sélection (n) »), signalée
  comme partielle dans l'archive ; option « Avec le journal du dossier » (`journal.pdf`).
- **Doublons** : page « n doublons possibles » (rédacteurs) ; fusion sans perte, journalisée, ou paire
  écartée. Sur le dossier réel : 3 paires détectées (saisie manuelle + courriel importé), **aucune
  fusionnée** — la décision revient au propriétaire.
- **Journal** : « par l'import de courriels » au lieu de « auteur inconnu ».
- **Tests** : 14 min → 13 s (hachage MD5 sous `manage.py test` seulement).

### Page Paramètres du dossier — 2026-10-09

- `/dossiers/<id>/parametres/` (roue dentée dans l'en-tête, propriétaire seul) : renommer le
  dossier ; **ajouter, modifier ou désactiver l'alias** d'import par courriel, avec l'état
  (activé / désactivé), une proposition d'adresse difficile à deviner sur le domaine de la boîte
  d'import, et la commande `Set-Mailbox` à lancer dans Exchange. Tout est journalisé. L'alias a
  quitté la page Partager. Refusés : un alias déjà pris (même en majuscules) et l'adresse de la
  boîte d'import elle-même.

### Suppression d'un évènement, icônes, mise en page — 2026-10-09

- **Supprimer un évènement** : bouton poubelle (rouge) dans le détail et dans la vue liste, pour
  qui peut écrire (y compris par un lien de partage en écriture). Confirmation obligatoire — dans
  le modal, ou sur une page sans JavaScript — qui dit, pièce par pièce, ce qui part et ce qui
  reste. Une pièce liée à un autre évènement est conservée ; les autres partent avec leur
  fichier. Le journal garde l'évènement entier et chaque pièce (nom, empreinte). Un courriel
  importé par erreur peut être réimporté après suppression.
- **Icônes Bootstrap Icons** sur les boutons (exporter, journal, expéditeurs, partager, nouvel
  évènement, enregistrer, annuler, inviter, révoquer, se déconnecter…), chargées une fois par
  `base.html`. Les boutons « Retirer » sont des poubelles `btn-outline-danger`.
- **Modal à largeur adaptée** : normal pour un évènement court, large pour un évènement avec
  pièces ou long texte, un aperçu PDF ou le formulaire ; plein écran sur téléphone.
- **Mise en page** : retours harmonisés (lien discret au-dessus du titre) ; longues descriptions
  repliées dans la vue liste (entières à l'impression), défilantes dans le journal et dans
  l'éditeur ; plus aucun débordement horizontal à 400 px de large.

### Couleurs, et import vérifié sur de vrais courriels — 2026-10-09

- **Une couleur par évènement** : celle de son expéditeur pour un évènement importé (lue chez
  lui, jamais recopiée : la changer sur la page « Expéditeurs » recolore tous ses évènements),
  sinon celle choisie dans la palette du formulaire. Partout : chronologie, FullCalendar, vues
  sans JavaScript, détail. Migration `0015`.
- **Six vrais courriels** analysés : trois réponses « RE: » étaient prises pour des transferts
  (leur citation « De : … Envoyé : … »), avec la date et l'auteur du message cité. Corrigé :
  un bloc cité ne vaut transfert que si l'objet ou une ligne l'annonce. Un test rejoue les
  exemples de `exemples_courriels/` quand ils sont présents.
- **Transfert de transfert** (la mise en demeure transférée à l'avocate, puis à la boîte) :
  l'analyse déballe chaque couche jusqu'au courriel d'origine. Les exemples sont rejoués
  transférés des deux façons ; l'enveloppe du transfert n'est jamais retenue.

### Import de courriels — 2026-10-09

- Un alias de courriel par dossier ; les courriels qui lui sont transférés deviennent des
  évènements (date de réception d'origine, objet, texte, pièces jointes, `.eml` complet et
  version `.md` pour la recherche et l'IA). Lecture de la boîte Exchange Online par Microsoft
  Graph ; rangement dans « Importés » / « Non importés ». Migration `0014`.
- **Choix du propriétaire : n'importe qui peut écrire à l'alias.** Alias difficiles à deviner,
  modifiables, et chaque import est inscrit au journal avec son expéditeur.

### Journal, export, retour à la vue d'origine, libellés — 2026-10-08 / 09

- **Journal des modifications** par dossier (bouton « Journal ») : qui, quand, quoi, avant →
  après, en ajout seul. Les entrées de partage ne sont visibles que du propriétaire. Commence le
  2026-10-08 ; migration `0012_journal`, appliquée après sauvegarde.
- **Export** (bouton « Exporter ») : zip avec la chronologie en PDF, les pièces numérotées,
  `empreintes.sha256` et un `LISEZMOI.txt`. Empreintes recalculées et comparées à celles du
  dépôt ; vérifié sur le vrai dossier : 49 pièces conformes, `sha256sum -c` OK. Nouvelle
  dépendance : reportlab.
- **Retour à la vue d'origine** après un enregistrement ou une annulation (`?retour=`).
- **Journal de l'admin** : tout ce qui s'y modifie (dossiers, accès, évènements, pièces, liens
  pièce ↔ évènement, suppressions) est inscrit, marqué « via l'administration » ; migration
  `0013`. **Export par lien de partage**, inscrit au journal au nom du lien.
- **Libellés entiers** : « Date de début », « Heure de début », et une seule phrase pour la
  zone de dépôt. Relecture du néerlandais (tournures calquées sur le français corrigées).

### Site en trois langues : FR / NL / EN — 2026-10-08

- Sélecteur de langue dans la barre de navigation, sur le modèle de QuizOnline (bouton au code
  de la langue, menu FR / NL / EN, langue courante cochée), y compris avant la connexion et sur
  les liens de partage ; absent de la page 404, qui doit rester identique pour un dossier
  interdit et un dossier inexistant.
- Tous les textes visibles marqués (Python, gabarits, JavaScript des gabarits) ; 225 textes
  traduits en néerlandais et en anglais (`locale/`). Pages d'allauth traduites par allauth.
- Trois pièges rencontrés et documentés dans `CLAUDE.md` (« Architecture: translations »).

### Champ de pièces jointes refait — 2026-10-08

Demande du propriétaire : « revoir le multi fileupload ». Vérifié dans un navigateur, envoi
intercepté (rien n'a touché la base) : bootstrap-fileinput tenait sa propre liste à côté de
celle du champ, et les deux divergeaient.

- **Perte silencieuse de pièces :** son bouton « vider » et le refus d'un `.exe` ajouté à côté
  d'un PDF vidaient le champ — **aucune** pièce ne partait, pas même le PDF valide — pendant
  que la liste affichait encore les fichiers.
- Impossible de retirer une seule pièce (aucun bouton), vignettes dédoublées, icônes absentes,
  message « Transmission du fichier 2 sur 3 » alors que rien n'était transmis.

Remplacé par un composant maison sans dépendance (`equiperChampFichiers`) : une seule
sélection, qui est exactement ce qui part ; zone de dépôt ; retrait pièce par pièce ;
contrôle d'extension, de taille et de doublon fichier par fichier, chaque refus annoncé ;
vignette pour les images ; dépôt hors zone neutralisé. Extensions et limite viennent du
serveur. bootstrap-fileinput **et jQuery** ne sont plus chargés.

### Dates selon la langue courante, flatpickr — 2026-10-08

Demande du propriétaire : « il faut que le format soit adapté en fonction de la langue courante
partout dans le site » — en prévision d'un passage FR / NL / EN.

- **Défaut corrigé en route :** en `fr-be`, le champ date du formulaire de modification
  s'affichait **vide** (Django y écrivait `05.08.2026`, que le navigateur rejette).
- Toutes les dates affichées passent par les formats nommés de Django ; noms de mois et de
  jours traduits par Django ; FullCalendar, vis-timeline et flatpickr
  reçoivent la langue courante. Règle et pièges : « Architecture: dates and the current
  language » dans `CLAUDE.md`.
- **Langue du site passée de `fr-be` à `fr`** à la demande du propriétaire : les dates
  s'affichent `05/08/2026` (et non `05.08.2026`, format court de `fr-be`). Effet restant :
  l'heure `14:32` au lieu de `14h32`.
- Date d'expiration des invitations : calculée en heure de Bruxelles, et non plus en UTC (une
  expiration entre minuit et 2 h affichait la veille).

### Détail en modal, FullCalendar, huit onglets — 2026-10-08

- **Détail d'un évènement en modal, dans toutes les vues.** Le panneau sous la frise, souvent
  hors écran, a disparu. Un fragment rendu par Django (`_detail_evenement.html`) sert la
  page autonome `/events/<id>/` (repli sans JavaScript, ctrl-clic) et le modal. Aperçu PDF
  et visionneuse d'images dans le modal ; Échap ferme la visionneuse sans fermer le modal.
  Le formulaire s'ouvre dans le même modal depuis le bouton crayon.
- **Huit onglets** : Chronologie (vis-timeline) ; FullCalendar Mois, Semaine, Liste, Timeline
  (thème Bootstrap 5) ; Mois, Semaine, Liste sans JavaScript.
- **Licence de la Timeline FullCalendar** : clé CC BY-NC-ND, **usage non commercial
  uniquement**, choix du propriétaire. Voir *Front end* dans `CLAUDE.md`.
- Boutons « Modifier » remplacés par un crayon SVG en `btn-outline-primary`.
- `timeline.html` sauvegardé avant refonte : `timeline.html.backup-20261008-144515`
  (ignoré par git, comme tout `*.backup-*`).

### Robustesse et sauvegarde — 2026-10-08

- **Copies manuelles supprimées** (5 `db.sqlite3.backup-*`, 2 `media.backup-*`), à la
  demande du propriétaire, après vérification que la dernière sauvegarde automatique est
  saine et que chaque fichier des copies existait encore dans `media/`.

- **Sauvegarde automatique.** `manage.py sauvegarder` copie la base par l'API de
  sauvegarde de SQLite (instantané cohérent même serveur lancé) et `media/` en entier, relit
  la copie (`PRAGMA integrity_check`, SHA-256 de chaque pièce comparé à la source), écrit un
  `manifeste.json`, puis élague au-delà de 14. Construite sous `.partiel` et renommée une
  fois vérifiée : un dossier au nom définitif est toujours complet, et la rotation ne compte
  et ne supprime que ceux-là. `scripts/sauvegarde.ps1` l'appelle et journalise dans
  `sauvegardes/sauvegarde.log`. Tâche Windows **« SDBTimeline - sauvegarde »**, chaque jour
  à 20h00, rattrapée au démarrage suivant si le PC était éteint.
- **Verrouillage optimiste** sur `Event` (champ `version`, migration `0011`) : un second
  enregistrement fait sur une version périmée est refusé avec un message, au lieu d'écraser
  le premier. Contrôle et incrément en une requête conditionnelle, sans fenêtre de course.
  Couvre aussi le chemin des liens de partage en écriture.
- **Taille des pièces bornée** à `DOCUMENT_TAILLE_MAX` (50 Mo ; la plus lourde actuelle pèse
  16,7 Mo), côté serveur, et annoncée à bootstrap-fileinput. Au passage : `_save_documents`
  contrôle désormais **tous** les fichiers avant d'en écrire un seul. Auparavant, un refus
  au deuxième fichier laissait le premier sur le disque sans ligne en base — la transaction
  annule les lignes, pas les fichiers. Le défaut existait déjà pour les extensions refusées.
- **`ALLOWED_HOSTS`** retire les espaces autour des virgules et ignore les entrées vides
  (`hotes_autorises` dans `config/settings.py`).
- **Garde `AnonymousUser`** dans `DossierQuerySet` : `accessible_by`, `writable_by` et le
  nouveau `owned_by` renvoient un queryset vide. Les sept helpers de `permissions.py`
  répondent 404 et non plus 500.

### Précision temporelle d'un évènement — 2026-10-08

Demande du propriétaire : « Possible d'avoir l'option date, datetime, daterange pour un
event ? » **Pas de champ « type »** : trois colonnes facultatives — `heure`, `date_fin`,
`heure_fin` — et `Event.precision` déduit « date », « instant » ou « intervalle ». Détail
et pièges dans « Architecture: how an event sits in time » de `CLAUDE.md`. Migration
`0010` ; 34 tests dans `test_precision_temporelle.py`.

### L1 à L6 — 2026-10-07 et 2026-10-08

- **L1** — dossiers, droits propriétaire / lecture / écriture, `permissions.py` point de
  passage unique, refus en 404, authentification allauth, tout privé par défaut.
- **L2** — `base.html` avec navbar, sélecteur de dossier et déconnexion ; `404.html` et
  `500.html` en français ; `event_form.html` étend la base, Bootstrap unifié en 5.3.8.
- **L3** — formulaire en modal soumis en AJAX, page complète gardée en repli sans
  JavaScript ; multi-upload réparé. Pièces dédupliquées par empreinte au sein d'un dossier,
  partageables entre évènements ; « retirer » une pièce détache le lien, et le fichier
  n'est supprimé qu'avec le dernier lien (`document_detach`). Ceci tranche l'ancienne
  question « supprimer une ligne laisse son fichier ».
- **L4** — partage à un compte existant, invitation par e-mail avec expiration, lien secret
  anonyme révocable, en lecture ou en écriture. Réservés au propriétaire.
- **L5** — éditeur Quill ; HTML assaini côté serveur par nh3 (`events/sanitize.py`) à
  l'enregistrement. Les descriptions existantes ont été converties en HTML par la
  migration `0009`, retours à la ligne préservés.
- **L6** — quatre vues : chronologie (vis-timeline), mois, semaine, liste. Les trois
  dernières sont rendues côté serveur, sans JavaScript ni bibliothèque de calendrier.

### Vague de correctifs du 2026-10-07

- Trois chemins de XSS stockée : la charge utile de la chronologie (`json.dumps` + `|safe`),
  le panneau de détails (`innerHTML`), et `document_download` servant un `.html` déposé en
  `text/html` *inline*. Voir « Architecture: output encoding » dans `CLAUDE.md`.
- `/admin/login/`, seconde porte de connexion sans limitation de tentatives.
- Suppression en cascade d'un `Dossier` depuis l'admin, et actions groupées de suppression
  sur `Event` et `Document`.
- `dossier_list` : `select_related("owner")` mort et double `COUNT` par ligne, remplacés par
  un `annotate(Count("events", distinct=True))`.
- `DEBUG` qui valait `True` par défaut, et `SECRET_KEY` de développement utilisable en
  production : les deux échouent fermé désormais.
- La chronologie est revenue en pleine largeur, via un bloc `main_class` dans `base.html`.
- La marche arrière de la migration `0003` (`detach_events`), qui fusionnait silencieusement
  des affaires distinctes sur un aller-retour : la migration est maintenant irréversible.
- `test_every_event_belongs_to_a_dossier`, qui n'inspectait que les métadonnées du champ.
- Les deux tests `/media/`, désormais capables de virer au rouge : ils rechargent
  `config.urls` sous `DEBUG=True`, et un troisième test prouve que le harnais verrait un
  montage statique réintroduit.
- `event_form.html` postait `documents[]` quand les vues lisent `documents` : chaque pièce
  jointe était reçue puis abandonnée sans un mot.
