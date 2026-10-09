"""Assainissement du HTML saisi dans les descriptions d'évènement.

**C'est le seul rempart.** L'éditeur côté navigateur est un confort : un POST
direct, un lien de partage en écriture ou un client HTTP quelconque le
contournent en une ligne. Ce module s'exécute à l'enregistrement, sur le
serveur, et sa liste blanche fait autorité.

Le modèle de menace est connu et documenté depuis L1 : un dossier est partagé
entre un propriétaire et des contributeurs en écriture — voire, depuis L4,
avec des porteurs de lien anonymes. Du script injecté dans une description
s'exécuterait dans la session du propriétaire, sur son origine, avec son
cookie et son jeton CSRF. C'est une élévation de privilège à l'intérieur du
dossier, sur des pièces de litige.

La liste autorise la mise en forme éditoriale et rien d'autre : pas de
`script`, pas de `style`, pas d'`iframe`, pas d'`img` — une image distante
suffirait à faire fuiter l'adresse IP et le navigateur du lecteur vers un
hôte choisi par l'auteur de la description.
"""

import nh3

#: Balises de mise en forme autorisées.
BALISES = {
    "p",
    "br",
    "strong",
    "b",
    "em",
    "i",
    "u",
    "s",
    "ul",
    "ol",
    "li",
    "blockquote",
    "code",
    "pre",
    "h3",
    "h4",
    "a",
}

#: Attributs autorisés, par balise. Rien d'autre ne passe — en particulier
#: aucun `on*`, aucun `style`, aucune `class`.
ATTRIBUTS = {"a": {"href", "title"}}

#: Schémas d'URL autorisés. `javascript:` et `data:` sont absents : le
#: premier exécute, le second permet d'embarquer un document entier.
SCHEMAS = {"http", "https", "mailto"}


def nettoyer(html):
    """Rend le HTML sûr à insérer dans une page.

    Les liens reçoivent `rel="noopener noreferrer nofollow"` : sans
    `noreferrer`, suivre un lien depuis un dossier partagé transmettrait
    l'URL de la page — jeton de partage compris — au site visé.
    """
    if not html:
        return ""
    return nh3.clean(
        html,
        tags=BALISES,
        attributes=ATTRIBUTS,
        url_schemes=SCHEMAS,
        link_rel="noopener noreferrer nofollow",
    )


def est_vide(html):
    """Vrai si le HTML ne porte aucun texte.

    Un éditeur riche laissé vide renvoie rarement une chaîne vide : il
    produit `<p><br></p>` ou équivalent. Sans ce test, une description
    « vide » occuperait une ligne dans l'affichage.
    """
    return not nh3.clean(html or "", tags=set()).strip()
