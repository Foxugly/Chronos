import json

from django.test import TestCase
from django.urls import reverse
from django.utils.html import escape

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
        """Le nom du dossier adverse est volontairement sans apostrophe.

        Django échappe le gabarit : « Dossier d'un autre » y apparaîtrait
        en `Dossier d&#x27;un autre`, et un `assertNotContains` sur la chaîne
        brute passerait même si la vue fuitait. On compare donc à la forme
        échappée, et le nom reste sans caractère à échapper.
        """
        other = make_dossier(make_user("elsewhere"), name="Dossier adverse")
        self.client.force_login(self.reader)
        response = self.client.get(reverse("dossier_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Litige SDB")
        self.assertNotContains(response, escape(other.name))

    def test_dossier_list_counts_each_event_once(self):
        """`accessible_by` joint déjà `accesses` : sans `distinct=True`, le
        `COUNT` compte le produit des deux jointures et chaque accès
        supplémentaire gonfle le nombre d'évènements affiché. Ici le dossier
        a deux accès (lecteur, rédacteur) et deux évènements : le bug
        afficherait 4.
        """
        Event.objects.create(
            dossier=self.dossier, title="Constat huissier", date="2026-06-20"
        )
        self.client.force_login(self.owner)
        response = self.client.get(reverse("dossier_list"))

        counts = {c["dossier"].pk: c["dossier"].event_count for c in response.context["cartes"]}
        self.assertEqual(counts[self.dossier.pk], 2)
        self.assertContains(response, "2 évènements")

    def test_timeline_readable_by_reader(self):
        self.client.force_login(self.reader)
        url = reverse("timeline", args=[self.dossier.pk])
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_timeline_hidden_from_stranger(self):
        self.client.force_login(self.stranger)
        url = reverse("timeline", args=[self.dossier.pk])
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_timeline_payload_contains_only_the_events_of_this_dossier(self):
        """La propriété que les tests de garde ne couvrent pas.

        Les tests 404 vérifient qui peut ouvrir la page ; celui-ci vérifie ce
        que la page contient. Sans lui, revenir à `Event.objects.all()` dans
        `timeline` — le bug même que ce lot corrige — laisse toute la suite au
        vert.

        Le propriétaire possède les deux dossiers : même un utilisateur qui a
        accès à A *et* à B ne doit pas voir les évènements de B sur la
        chronologie de A. La portée est celle du dossier, pas celle des droits.

        Les titres sont volontairement en ASCII : la sérialisation JSON
        non-ASCII (« Devis signé » devient `Devis sign\\u00e9` dans le HTML),
        donc un `assertNotContains` sur un titre accentué passerait tout seul.
        L'assertion qui compte porte de toute façon sur la liste du contexte.
        """
        other_dossier = make_dossier(self.owner, name="Second dossier")
        other_event = Event.objects.create(
            dossier=other_dossier, title="Expertise contradictoire", date="2026-07-15"
        )
        mine = Event.objects.create(
            dossier=self.dossier, title="Constat huissier", date="2026-06-20"
        )

        self.client.force_login(self.owner)
        response = self.client.get(reverse("timeline", args=[self.dossier.pk]))
        self.assertEqual(response.status_code, 200)

        # `timeline_items` est désormais la liste elle-même : c'est le
        # gabarit qui la sérialise, via `json_script`.
        items = response.context["timeline_items"]
        ids = {item["id"] for item in items}
        titles = {item["content"] for item in items}

        self.assertIn(mine.title, titles)
        self.assertIn(mine.id, ids)
        self.assertNotIn(other_event.title, titles)
        self.assertNotIn(other_event.id, ids)
        self.assertEqual(ids, {self.event.id, mine.id})

        # Le titre est ASCII : il apparaîtrait tel quel dans le corps s'il fuitait.
        self.assertNotContains(response, other_event.title)

    def test_event_detail_readable_by_reader(self):
        self.client.force_login(self.reader)
        response = self.client.get(reverse("event_detail", args=[self.event.pk]))
        self.assertContains(response, "Devis signé")

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


class MixedRolesAcrossDossiersTests(TestCase):
    """Un droit sur un dossier ne doit jamais déborder sur un autre.

    Les autres tests ne donnent à chaque utilisateur qu'un seul rôle sur un
    seul dossier. Dès que le partage existe, la forme réaliste est celle-ci :
    un même compte est rédacteur ici et simple lecteur — ou étranger — là.
    Correct par construction via `writable_by` / `accessible_by`, mais non
    couvert jusqu'ici.
    """

    def setUp(self):
        self.owner = make_user("owner-of-both")
        self.user = make_user("mixed-roles")
        self.dossier_a = make_dossier(self.owner, name="Dossier A")
        self.dossier_b = make_dossier(self.owner, name="Dossier B")
        self.event_b = Event.objects.create(
            dossier=self.dossier_b, title="Piece du dossier B", date="2026-08-01"
        )

    def test_write_on_a_does_not_grant_write_on_b(self):
        grant(self.dossier_a, self.user, DossierAccess.Role.WRITE)
        grant(self.dossier_b, self.user, DossierAccess.Role.READ)
        self.client.force_login(self.user)

        # Lecture de B : permise, l'accès en lecture existe.
        self.assertEqual(
            self.client.get(reverse("timeline", args=[self.dossier_b.pk])).status_code,
            200,
        )
        # Écriture sur un évènement de B : refusée malgré le WRITE sur A.
        self.assertEqual(
            self.client.get(reverse("event_edit", args=[self.event_b.pk])).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(
                reverse("event_add", args=[self.dossier_b.pk])
            ).status_code,
            404,
        )
        # Et le POST ne modifie rien.
        self.client.post(
            reverse("event_edit", args=[self.event_b.pk]),
            {"title": "Titre injecte", "date": "2026-08-01", "description": ""},
        )
        self.event_b.refresh_from_db()
        self.assertEqual(self.event_b.title, "Piece du dossier B")

    def test_read_on_a_does_not_grant_read_on_b(self):
        grant(self.dossier_a, self.user, DossierAccess.Role.READ)
        self.client.force_login(self.user)

        self.assertEqual(
            self.client.get(reverse("timeline", args=[self.dossier_a.pk])).status_code,
            200,
        )
        self.assertEqual(
            self.client.get(reverse("timeline", args=[self.dossier_b.pk])).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(
                reverse("event_detail", args=[self.event_b.pk])
            ).status_code,
            404,
        )
        # Le dossier B n'apparaît pas non plus dans la liste.
        response = self.client.get(reverse("dossier_list"))
        self.assertContains(response, escape(self.dossier_a.name))
        self.assertNotContains(response, escape(self.dossier_b.name))


class StoredXssTests(TestCase):
    """L'attaque que le partage rend possible, et que ce lot doit fermer.

    `Role.WRITE` donne à un tiers le droit d'écrire dans le dossier qui
    contient l'unique copie des preuves. Si ce qu'il écrit redevient du
    balisage chez le propriétaire, il exécute du script dans la session de
    celui-ci : il lit les 49 pièces et les supprime avec le jeton CSRF du
    propriétaire — contournant par l'intérieur les contrôles d'accès que ce
    lot a posés à l'extérieur.
    """

    def setUp(self):
        self.owner = make_user("owner")
        self.collaborator = make_user("collaborator")
        self.dossier = make_dossier(self.owner, name="Litige SDB")
        grant(self.dossier, self.collaborator, DossierAccess.Role.WRITE)

    def test_a_title_cannot_close_the_timeline_script_tag(self):
        """C1 : `json.dumps` n'échappe ni `<`, ni `>`, ni `/`, et le gabarit
        injectait son résultat avec `|safe` à l'intérieur d'un `<script>`."""
        payload = "</script><script>window.pwned=1</script>"

        self.client.force_login(self.collaborator)
        created = self.client.post(
            reverse("event_add", args=[self.dossier.pk]),
            {"title": payload, "date": "2026-09-27", "description": ""},
        )
        self.assertEqual(created.status_code, 302)

        self.client.force_login(self.owner)
        response = self.client.get(reverse("timeline", args=[self.dossier.pk]))
        body = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(payload, body)
        self.assertNotIn("</script><script>", body)
        # `window.pwned=1` subsiste bien quelque part dans le corps, mais
        # entre guillemets, dans une chaîne JSON : c'est du texte, pas une
        # instruction. Ce qui comptait, c'est qu'aucune balise ne se ferme.
        self.assertNotIn("<script>window.pwned", body)

        # Le titre circule toujours — mais comme donnée : `json_script` le
        # porte intact sous forme d'échappements JSON, et c'est `JSON.parse`
        # qui le lit, jamais l'analyseur HTML.
        marker = '<script id="timeline-items" type="application/json">'
        start = body.index(marker) + len(marker)
        end = body.index("</script>", start)
        items = json.loads(body[start:end])
        self.assertIn(payload, [item["content"] for item in items])

    def test_a_hostile_description_is_stripped_before_it_reaches_the_panel(self):
        """La description est devenue du HTML : elle porte une mise en forme
        et atteint donc le panneau **en tant que balisage**.

        Ce qui la rend sûre n'est plus l'absence de balisage, c'est
        l'assainissement côté serveur — à l'enregistrement et à la sortie.
        Ce test garde cette propriété-là : du script déposé dans la base par
        un autre chemin que le formulaire, l'admin par exemple, ne doit pas
        ressortir.
        """

        hostile = (
            '<p>Texte <strong>légitime</strong></p>'
            '<img src=x onerror=alert(1)>'
            '<script>window.pwned=1</script>'
            '<a href="javascript:alert(2)">piège</a>'
        )
        event = Event.objects.create(
            dossier=self.dossier,
            title="Devis",
            date="2026-06-02",
            description=hostile,
        )

        self.client.force_login(self.owner)
        # Le fragment du modal : la page complète porte, elle, ses propres
        # balises <script>, qui rendraient la vérification aveugle.
        servi = self.client.get(
            reverse("event_detail", args=[event.pk]),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        ).content.decode()

        for interdit in ("<script", "onerror", "javascript:", "<img"):
            self.assertNotIn(interdit, servi, f"{interdit} a survécu")

        # La mise en forme légitime, elle, doit passer : un assainisseur qui
        # vide tout serait « sûr » et inutile.
        self.assertIn("<strong>légitime</strong>", servi)

    def test_the_sanitiser_is_the_one_that_decides(self):
        """L'éditeur du navigateur est un confort : un POST direct le
        contourne. La propriété se vérifie donc sur la vue, pas sur la page."""
        reponse = self.client_post_description(
            '<p>ok</p><script>window.pwned=1</script>'
        )
        self.assertEqual(reponse.status_code, 302)
        cree = Event.objects.get(title="Par POST direct")
        self.assertNotIn("<script", cree.description)
        self.assertIn("<p>ok</p>", cree.description)

    def client_post_description(self, description):
        self.client.force_login(self.owner)
        return self.client.post(
            reverse("event_add", args=[self.dossier.pk]),
            {
                "title": "Par POST direct",
                "date": "2026-06-02",
                "description": description,
            },
        )

    def test_the_panel_builds_everything_else_node_by_node(self):
        """Tout ce qui n'est pas la description reste construit nœud par
        nœud. Réintroduire une concaténation ailleurs fait virer ce test."""
        import re

        self.client.force_login(self.owner)
        page = self.client.get(
            reverse("timeline", args=[self.dossier.pk])
        ).content.decode()

        for puits in (
            ".outerHTML =",
            ".insertAdjacentHTML(",
            "createContextualFragment(",
        ):
            self.assertNotIn(puits, page, f"{puits} reintroduit un puits de balisage")

        # Un seul emploi légitime de `innerHTML` : `corpsModal`, qui reçoit
        # un fragment rendu par Django — formulaire ou détail d'évènement.
        AUTORISEES = {"corpsModal"}
        affectations = set(re.findall(r"(\w+)\.innerHTML\s*=", page))
        self.assertTrue(
            affectations <= AUTORISEES,
            f"affectation innerHTML inattendue : {affectations - AUTORISEES}",
        )
        self.assertIn("textContent", page)

    def test_a_document_title_is_not_concatenated_into_the_panel(self):
        """Même propriété pour `doc.title` : il alimentait le texte de la
        carte, l'`alt` de l'image et le libellé du lien, tous construits par
        concaténation de chaînes."""
        Event.objects.create(dossier=self.dossier, title="Devis", date="2026-06-02")
        self.client.force_login(self.owner)
        page = self.client.get(
            reverse("timeline", args=[self.dossier.pk])
        ).content.decode()

        self.assertIn("document.createElement", page)
        self.assertNotIn("${doc.title}", page)
        self.assertNotIn("${doc.url}", page)
        self.assertNotIn("${data.title}", page)
        self.assertNotIn("${data.description}", page)


class TimelineFetchRobustnessTests(TestCase):
    """I9 : le `fetch` n'avait ni contrôle de `response.ok` ni `.catch()`.

    Sur un 404, l'analyse JSON échouait sans bruit et le panneau gardait
    affichés le titre, la description et les pièces de l'évènement
    *précédent* — un lecteur dont l'accès vient d'être retiré continuait donc
    de lire des preuves sur son écran.
    """

    def setUp(self):
        self.owner = make_user("owner")
        self.dossier = make_dossier(self.owner, name="Litige SDB")

    def test_the_page_checks_the_response_and_shows_a_french_error(self):
        self.client.force_login(self.owner)
        page = self.client.get(
            reverse("timeline", args=[self.dossier.pk])
        ).content.decode()

        self.assertIn("reponse.ok", page)
        self.assertIn(".catch(", page)
        self.assertIn("Impossible de charger cet", page)

    def test_the_detail_url_comes_from_the_url_pattern(self):
        """L'URL n'est plus écrite en dur dans le JavaScript : la renommer
        dans `events/urls.py` ne laisse plus la page appeler l'ancienne."""
        self.client.force_login(self.owner)
        page = self.client.get(
            reverse("timeline", args=[self.dossier.pk])
        ).content.decode()

        self.assertIn(reverse("event_detail", args=[999999999]), page)
        self.assertNotIn("/events/${eventId}/", page)


class TimelineItemTemplateTests(TestCase):
    """Le titre d'un évènement est saisi par un collaborateur en écriture et
    atterrit dans vis-timeline, qui a deux chemins de rendu : une chaîne part
    dans `innerHTML` — filtrée par sa js-xss embarquée, qui laisse passer
    `<a>`, `<img>`, `<b>` — tandis qu'un Element part dans `appendChild`.

    Ces assertions portent sur le code servi, faute de moteur JavaScript dans
    la suite Django ; la preuve directe a été faite au navigateur, avec un
    titre hostile réel : zéro `<img>` et zéro `<a>` injectés dans le DOM, et
    le balisage affiché en texte littéral.
    """

    def setUp(self):
        self.owner = make_user("owner")
        self.dossier = make_dossier(self.owner, name="Dossier adverse")
        Event.objects.create(
            dossier=self.dossier, title="Devis signe", date="2026-06-02"
        )
        self.client.force_login(self.owner)

    def _page(self):
        return self.client.get(
            reverse("timeline", args=[self.dossier.pk])
        ).content.decode()

    def test_the_timeline_builds_items_through_a_template(self):
        page = self._page()
        self.assertIn("template: itemTemplate", page)
        self.assertIn("function itemTemplate(item)", page)

    def test_the_template_sets_text_and_never_markup(self):
        page = self._page()
        self.assertIn("span.textContent", page)
        self.assertNotIn("span.innerHTML =", page)

    def test_the_timeline_is_not_built_with_default_options(self):
        """Garde contre un retour en arriere : `{}` rendait les titres en
        chaine, donc en `innerHTML`."""
        page = self._page()
        self.assertNotIn("items,\r\n        {}", page)
        self.assertNotIn("items,\n        {}", page)


class ImageCardLightboxTypeTests(TestCase):
    """R2 : `document_download` répond désormais en `attachment` (une pièce
    `.html` téléversée ne doit pas pouvoir s'exécuter sur l'origine du site).
    Mais `/documents/<id>/` n'a pas d'extension, et GLightbox 3.3.1 choisit
    le type d'une diapositive par un sniff d'URL -- sans extension, il
    retombe sur `"external"` et affiche une iframe vide au lieu de l'image ;
    cliquer sur la vignette déclenche un téléchargement à la place d'ouvrir
    la visionneuse.

    Il n'y a pas de moteur JavaScript dans la suite de tests Django : ce
    test n'exécute pas GLightbox, il vérifie seulement que le détail servi
    porte la déclaration de type explicite (`data-glightbox="type: image"`)
    qui lui évite ce sniff. C'est un indicateur de ce que
    fera le navigateur, pas une preuve directe du comportement du clic.
    """

    def setUp(self):
        self.owner = make_user("owner")
        self.dossier = make_dossier(self.owner, name="Litige SDB")

    def test_image_card_declares_its_glightbox_type_explicitly(self):
        import tempfile

        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.test import override_settings

        from events.models import Document

        event = Event.objects.create(
            dossier=self.dossier, title="Photo", date="2026-06-02"
        )
        with tempfile.TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media):
            photo = Document.objects.create(
                dossier=self.dossier,
                title="douche.jpg",
                file=SimpleUploadedFile("douche.jpg", b"\xff\xd8"),
                content_hash="0" * 64,
            )
            photo.events.add(event)
            self.client.force_login(self.owner)
            html = self.client.get(
                reverse("event_detail", args=[event.pk]),
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            ).content.decode()

        self.assertIn('data-glightbox="type: image"', html)
