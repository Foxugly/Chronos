# L1 — décisions prises pendant l'exécution

Exécuté le 2026-10-07. Chaque ligne est une décision prise **sans valider avec le
propriétaire**, parce qu'attendre aurait arrêté le chantier. Elles sont listées pour
qu'il puisse défaire celles qu'il juge mauvaises. Le coût d'une erreur est indiqué.

Le projet n'étant pas sous git, ce fichier est le seul endroit durable où ces décisions
existent.

## Décisions de méthode

| # | Décision | Coût si c'est une erreur |
|---|---|---|
| 1 | **Pas de git** : espace de travail hors projet, paquets de revue par instantané avant/après au lieu de `git diff`. Le propriétaire a refusé `git init` après qu'on le lui ait proposé ; la méthode employée présuppose un dépôt et deux de ses trois scripts sont devenus inutilisables. | Aucun historique commit par commit, aucun retour arrière granulaire. |
| 6 | **8 tâches regroupées en 5 dispatches**, le propriétaire ayant signalé que le projet est petit. | Un diff de revue plus gros par lot, donc un défaut plus facile à manquer. C'est arrivé : les trois failles d'injection vivaient dans le plus gros des cinq. |
| 7 | **Git différé, pas abandonné.** Un `.gitignore` a été préparé, excluant les 65 Mo de sauvegardes. | 65 Mo de binaires dans l'historique initial si le `.gitignore` est oublié. |
| 10 | **Corriger le plan plutôt que l'implémentation** quand un implémenteur a contredit le plan et avait raison. | Nul — la correction ne touchait que de la documentation. |
| 12 | **Inclure un mineur dans un round de correction**, contre la règle qui veut qu'ils soient reportés : le test concerné était le seul à démontrer que l'inscription est fermée. | Un diff de correction un peu plus gros, et une entorse assumée au process. |
| 17 | **Fusionner une re-revue ciblée dans la revue finale**, la propriété en jeu ayant été établie par mesure directe du contrôleur. | Un diff de tests d'un seul fichier a échappé à une paire d'yeux indépendante jusqu'à la revue finale. |
| 18 | **Réparer deux régressions après la revue finale**, bien que la méthode interdise une seconde vague : ce n'étaient pas des constats résiduels mais des casses provoquées par la vague elle-même, dont une cassait la consultation des photos. | Un dispatch de plus, deux fichiers touchés, un correctif d'une ligne chacun. |

## Décisions techniques

| # | Décision | Coût si c'est une erreur |
|---|---|---|
| 2-4 | **Trois défauts du plan corrigés avant dispatch** (une fonction que le bloc de code supprimait, un import perdu, deux `{% url %}` sans argument). | Détectés immédiatement par les tests ; corrigés en amont, ils n'ont rien coûté. |
| 5 | **Un répertoire temporaire par classe de test** au lieu d'un partagé que la première classe effaçait. | Échec intermittent dépendant de l'ordre des tests. |
| 9 | **Migration `0004` appliquée** après vérification par `sqlmigrate` que ses deux opérations sont `-- (no-op)`. | Nul : l'opération n'émet aucun SQL. |
| 11 | **`/accounts/email/` fermé en masquant l'URL** plutôt qu'en activant la vérification d'e-mail : supprimer la surface plutôt que l'atténuer. Conséquence à connaître : plus personne, propriétaire inclus, ne peut changer son adresse — seul l'admin le peut. | Une ligne d'URL à retirer si un besoin légitime apparaît. |
| 15 | **Correction SRI ajoutée à un lot qui n'en avait pas la charge**, aucune tâche ne la couvrant : sans ça, trois ressources externes sans hachage partaient en production. | Trois lignes de plus dans ce lot. |
| 16 | **Versions de Bootstrap non alignées** (le formulaire reste en 5.3.3 face au 5.3.8 de la base) : aligner change le rendu, et c'est le lot de mise en page qui en a la charge. | Deux versions coexistent jusqu'au prochain lot — déjà le cas auparavant. |

## Décisions de séquencement sur des failles connues

| # | Décision | Coût si c'est une erreur |
|---|---|---|
| 13-14 | **Ne pas corriger depuis la session de contrôle** une faille destructive confirmée (suppression de pièce sans contrôle de droits), ni l'intégrer à un round de correction intermédiaire : la tâche suivante en avait explicitement la charge, et un seul compte existait donc la surface d'exploitation réelle était nulle. | La fenêtre est restée ouverte le temps de deux rounds, sur un système à compte unique. Ce raisonnement a une date de péremption : il cesse d'être vrai au premier partage de dossier. |

## La décision qui s'est révélée mal cadrée

| # | Décision | Ce qui a été manqué |
|---|---|---|
| 8 | **Classer au backlog les demandes d'éditeur enrichi et de vues multiples**, au motif qu'elles n'affectaient pas le lot en cours. | Vrai pour le schéma de données, faux pour la sécurité. L'entrée de backlog rédigée à ce moment-là énonce le modèle de menace exact — « un contributeur en écriture pourrait injecter du script exécuté dans la session du propriétaire » — et le pose comme contrainte pour un lot **futur**. Le trou existait déjà trois fois dans le code en cours de livraison. L'attaquant a été identifié, puis la défense différée. Trouvé par la revue finale, corrigé. |

## Pourquoi cinq revues ont laissé passer l'injection de script

Chaque consigne de revue a été rédigée autour de l'**autorisation** — qui peut atteindre
quoi. La question « reste-t-il une requête non filtrée » a été posée cinq fois ; la question
« que se passe-t-il si le contenu lui-même est hostile » ne l'a jamais été avant la revue
finale, la première à recevoir une question ouverte sur l'ensemble du travail.

À retenir pour les lots suivants : une revue ne trouve que ce qu'on lui demande de chercher.

## Décisions prises après L1

| # | Décision | Coût si c'est une erreur |
|---|---|---|
| 19 | **Une pièce peut illustrer plusieurs évènements, et `Document` porte son `dossier` en propre.** Renverse D3, qui déduisait les droits de `document.event.dossier` — impossible dès qu'une pièce a plusieurs évènements. | Si l'on revenait à une pièce par évènement, la migration `0005` est irréversible : il faudrait restaurer une sauvegarde. |
| 20 | **Déduplication par empreinte SHA-256, bornée au dossier.** Le même fichier sur deux évènements d'une affaire n'est stocké qu'une fois ; dans deux affaires, ce sont deux pièces distinctes. | Deux affaires ne partagent jamais d'octets : c'est ce qui garantit qu'une suppression dans l'une ne peut rien atteindre dans l'autre. Mutualiser le stockage entre dossiers casserait l'isolation. |
| 21 | **Le titre du premier dépôt l'emporte** si le même contenu est téléversé sous deux noms. | Un nom de fichier perdu. Stocker un titre par lien corrigerait, au prix d'un modèle plus lourd. |
| 22 | **Le retrait d'une pièce supprime le fichier du disque quand le dernier lien tombe.** Django ne le fait pas seul. | Irréversible sur des preuves. Les sauvegardes horodatées restent le dernier filet. |
| 23 | **Les PDF et images sont servis `inline`, le reste en `attachment`.** Tout servir en pièce jointe fermait la faille mais obligeait à télécharger chaque devis pour le lire. | Ce qui est dangereux en `inline`, ce sont les formats que le navigateur exécute sur l'origine : HTML et SVG, absents des deux listes et refusés au téléversement. |
| 24 | **`X-Frame-Options` passe à `SAMEORIGIN` pour la seule vue document**, afin que l'aperçu dans la page fonctionne. Le reste du site garde `DENY`. | Un test garde chacun des deux côtés. |
| 25 | **Les doublons de sélection sont écartés côté navigateur, avec un message.** Signature : nom + taille + date de modification. | Écarter en silence aurait été pire que le doublon : on croirait la pièce jointe. |

## Défaut introduit puis corrigé

En alignant `test_admin.py` sur le nouveau modèle, j'ai remplacé une chaîne de
nom de fichier par une vraie création de pièce — dans une classe de tests
dépourvue de `MEDIA_ROOT` temporaire. **Deux fichiers d'essai se sont retrouvés
dans le répertoire de preuves réel.** Retirés, et la cause corrigée : les deux
classes ont désormais leur répertoire temporaire, et la suite complète laisse
`media/` à l'identique.
