import contextlib
import importlib
import shutil
import tempfile

import django.urls
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

import config.urls
from events.models import Document, DossierAccess, Event
from events.tests.factories import grant, make_document, make_dossier, make_user


class DocumentFixtureMixin:
    """Jeu d'essai commun, dans un `MEDIA_ROOT` propre à chaque classe.

    Le répertoire temporaire est créé par classe, et non une fois pour le
    module : partagé, la première classe à terminer le supprimerait sous les
    pieds de la seconde, et l'échec dépendrait alors de l'ordre d'exécution.
    `enterClassContext` referme l'`override_settings` même si `setUpClass`
    échoue en cours de route.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.media_root = tempfile.mkdtemp()
        cls.enterClassContext(override_settings(MEDIA_ROOT=cls.media_root))

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(cls.media_root, ignore_errors=True)

    def setUp(self):
        self.owner = make_user("owner")
        self.reader = make_user("reader")
        self.stranger = make_user("stranger")
        self.dossier = make_dossier(self.owner)
        grant(self.dossier, self.reader, DossierAccess.Role.READ)
        self.event = Event.objects.create(
            dossier=self.dossier, title="Devis signé", date="2026-06-02"
        )
        self.document = make_document(self.event)


@contextlib.contextmanager
def urlconf_reloaded_in_debug():
    """Rejoue la lecture de `DEBUG` que `config/urls.py` fait à l'import.

    Le montage fautif — `static(MEDIA_URL, document_root=MEDIA_ROOT)` —
    vivait sous `if settings.DEBUG`. La suite tourne en `DEBUG=False`, et
    `override_settings` seul ne rejoue pas l'import : les tests `/media/`
    étaient donc incapables de virer au rouge, ce que leur docstring
    reconnaissait. Recharger le module sous `DEBUG=True`, caches d'URLconf
    vidés, reproduit exactement la condition d'origine.

    L'état du module est restauré en sortie, `DEBUG` revenu à sa valeur
    réelle : les tests suivants retrouvent l'URLconf du projet.
    """
    try:
        with override_settings(DEBUG=True):
            django.urls.clear_url_caches()
            importlib.reload(config.urls)
            yield
    finally:
        django.urls.clear_url_caches()
        importlib.reload(config.urls)


class DocumentDownloadTests(DocumentFixtureMixin, TestCase):
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

    def test_a_missing_file_on_disk_is_a_404_not_a_500(self):
        """La base compte 53 fichiers pour 49 lignes : les deux inventaires
        ont déjà divergé. Une ligne dont le fichier a disparu doit répondre
        « introuvable », pas transformer une pièce manquante en incident
        serveur avec sa trace d'exception."""
        orphan = make_document(self.event, "disparu.pdf", b"x")
        orphan.file.storage.delete(orphan.file.name)

        self.client.force_login(self.owner)
        response = self.client.get(reverse("document_download", args=[orphan.pk]))
        self.assertEqual(response.status_code, 404)

    def test_media_url_is_not_served_anymore(self):
        """La faille d'origine : MEDIA_ROOT était monté en statique."""
        with urlconf_reloaded_in_debug():
            response = self.client.get(f"/media/{self.document.file.name}")
            self.assertEqual(response.status_code, 404)

    def test_media_url_is_not_served_to_a_logged_in_stranger(self):
        """Anonyme, la requête était déjà interceptée par
        `LoginRequiredMiddleware`, ce qui masquait le problème ; connecté,
        n'importe quel compte téléchargeait le document de n'importe quel
        dossier en devinant un nom de fichier.

        Exécuté sous URLconf rechargé en `DEBUG=True` : remonter un jour
        `static(MEDIA_URL, ...)` fait désormais virer ce test au rouge.
        """
        with urlconf_reloaded_in_debug():
            self.client.force_login(self.stranger)
            response = self.client.get(f"/media/{self.document.file.name}")
            self.assertEqual(response.status_code, 404)

    def test_the_debug_harness_would_see_a_media_mount(self):
        """Preuve que les deux tests ci-dessus peuvent virer au rouge.

        On remonte volontairement `static(MEDIA_URL, ...)` sur l'URLconf
        rechargé : si le harnais était inerte, le fichier resterait
        introuvable et cette assertion échouerait. Le montage disparaît avec
        le rechargement de sortie.
        """
        from django.conf import settings
        from django.conf.urls.static import static

        with urlconf_reloaded_in_debug():
            config.urls.urlpatterns += static(
                settings.MEDIA_URL, document_root=settings.MEDIA_ROOT
            )
            django.urls.clear_url_caches()
            self.client.force_login(self.stranger)
            response = self.client.get(f"/media/{self.document.file.name}")
            self.assertEqual(response.status_code, 200)

        # Hors du harnais, la porte est refermée.
        self.client.force_login(self.stranger)
        self.assertEqual(
            self.client.get(f"/media/{self.document.file.name}").status_code, 404
        )

    def test_event_detail_exposes_the_protected_url_not_the_media_path(self):
        self.client.force_login(self.reader)
        html = self.client.get(
            reverse("event_detail", args=[self.event.pk]),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        ).content.decode()
        self.assertIn(reverse("document_download", args=[self.document.pk]), html)
        self.assertNotIn("/media/", html)


class DocumentContentTypeTests(DocumentFixtureMixin, TestCase):
    """C3 : un fichier déposé ne doit jamais revenir exécutable.

    Un collaborateur en écriture déposait `piece.html` ; la vue le servait
    en `text/html` *inline*, donc exécuté sur l'origine du site, dans la
    session du propriétaire, avec son cookie et son jeton CSRF. Les 49
    pièces devenaient lisibles et supprimables de l'intérieur.
    """

    def _download(self, title, content):
        document = make_document(self.event, title, content)
        self.client.force_login(self.owner)
        return self.client.get(reverse("document_download", args=[document.pk]))

    def test_an_html_file_is_never_inline_html(self):
        response = self._download("piece.html", b"<script>window.pwned=1</script>")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/octet-stream")
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertNotIn("inline", response["Content-Disposition"])

    def test_no_executable_document_is_ever_served_inline(self):
        """Ce qui est dangereux en `inline`, c'est ce que le navigateur
        exécute sur l'origine du site : HTML et SVG. Ces formats restent en
        `attachment` et en type neutre, extension connue ou non."""
        for title in ("piece.html", "piece.svg", "piece.xhtml", "sans-extension"):
            with self.subTest(title=title):
                # Un contenu distinct par titre : la déduplication par
                # empreinte refuserait deux pièces identiques dans un même
                # dossier, et le test porterait alors sur une seule.
                response = self._download(
                    title, b"<svg onload=alert(1)> " + title.encode()
                )
                self.assertEqual(response["Content-Type"], "application/octet-stream")
                self.assertNotIn("inline", response["Content-Disposition"])

    def test_a_pdf_opens_in_a_tab_with_its_real_type(self):
        """Un PDF est rendu par une visionneuse isolée : l'afficher ne donne
        accès ni au DOM, ni aux cookies, ni au stockage de l'origine. Tout
        servir en `attachment` fermait la faille mais obligeait à enregistrer
        chaque devis pour le lire."""
        response = self._download("rapport.pdf", b"%PDF-1.4 rapport")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("inline", response["Content-Disposition"])
        self.assertNotIn("attachment", response["Content-Disposition"])
        self.assertEqual(b"".join(response.streaming_content), b"%PDF-1.4 rapport")

    def test_an_image_opens_in_a_tab_as_an_image(self):
        """Les images sont inertes : elles s'affichent, et le panneau de
        détails comme glightbox continuent de les rendre."""
        response = self._download("photo.jpg", b"\xff\xd8\xff\xe0octets-jpeg")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/jpeg")
        self.assertIn("inline", response["Content-Disposition"])
        self.assertEqual(
            b"".join(response.streaming_content), b"\xff\xd8\xff\xe0octets-jpeg"
        )

    def test_a_previewable_document_can_be_framed_by_our_own_pages(self):
        """L'aperçu dans la page encadre la pièce. Django pose
        `X-Frame-Options: DENY` sur tout le site, et le navigateur refusait
        donc d'afficher le cadre : « connexion refusée ». La vue lève cette
        interdiction pour elle seule, en SAMEORIGIN — nos pages peuvent
        encadrer, un site tiers non."""
        response = self._download("rapport.pdf", b"%PDF-1.4 rapport")
        self.assertEqual(response["X-Frame-Options"], "SAMEORIGIN")

    def test_the_rest_of_the_site_still_refuses_to_be_framed(self):
        """La levée doit rester locale à la vue document : partout ailleurs
        le refus d'encadrement protège contre le détournement de clic."""
        response = self.client.get(reverse("timeline", args=[self.dossier.pk]))
        self.assertEqual(response["X-Frame-Options"], "DENY")

    def test_office_documents_are_downloaded_not_displayed(self):
        """Le navigateur les télécharge de toute façon ; l'affirmer évite
        qu'un futur élargissement de la liste inline les y fasse entrer."""
        for title in ("devis.docx", "releve.xlsx", "courrier.doc", "compte.xls"):
            with self.subTest(title=title):
                # Contenu distinct par titre, cf. la déduplication par empreinte.
                response = self._download(
                    title, b"contenu bureautique " + title.encode()
                )
                self.assertIn("attachment", response["Content-Disposition"])
                self.assertNotIn("inline", response["Content-Disposition"])

    def test_the_inline_list_never_contains_an_executable_format(self):
        """Garde structurelle : si quelqu'un ajoute svg, html ou xhtml à
        INLINE_DOCUMENT_EXTENSIONS, ce test tombe avant toute mise en ligne."""
        from events.views.pieces import INLINE_DOCUMENT_EXTENSIONS

        for dangereux in ("svg", "html", "xhtml", "htm", "xml"):
            self.assertNotIn(dangereux, INLINE_DOCUMENT_EXTENSIONS)


class DocumentUploadTests(DocumentFixtureMixin, TestCase):
    def test_several_files_in_one_post_all_become_documents(self):
        """Le champ est `multiple` et la vue lit `getlist`. Verifie par le
        navigateur : trois pieces choisies en trois fois arrivent bien en un
        seul POST et donnent trois `Document`."""
        self.client.force_login(self.owner)
        response = self.client.post(
            reverse("event_add", args=[self.dossier.pk]),
            {
                "title": "Mise en demeure et annexes",
                "date": "2026-09-27",
                "description": "",
                "documents": [
                    SimpleUploadedFile("piece-un.pdf", b"%PDF-1.4 un"),
                    SimpleUploadedFile("piece-deux.pdf", b"%PDF-1.4 deux"),
                    SimpleUploadedFile("piece-trois.pdf", b"%PDF-1.4 trois"),
                ],
            },
        )
        self.assertEqual(response.status_code, 302)
        cree = Event.objects.get(title="Mise en demeure et annexes")
        self.assertEqual(
            sorted(d.title for d in cree.documents.all()),
            ["piece-deux.pdf", "piece-trois.pdf", "piece-un.pdf"],
        )

    def test_the_form_accumulates_selections_instead_of_replacing_them(self):
        """Choisir des pieces en plusieurs fois doit les cumuler : sans
        accumulateur, chaque nouvelle selection remplace la precedente et les
        premieres pieces disparaissent sans un mot.

        Assertions sur le code servi, faute de moteur JavaScript dans la
        suite ; la preuve directe a ete faite au navigateur, trois tours de
        selection suivis d'une soumission reelle donnant trois `Document`.
        """
        self.client.force_login(self.owner)
        page = self.client.get(
            reverse("event_add", args=[self.dossier.pk])
        ).content.decode()
        self.assertIn("new DataTransfer()", page)
        self.assertIn("selection.items.add(file)", page)
        self.assertIn("champ.files = selection.files", page)

    def test_the_form_refuses_to_queue_the_same_file_twice(self):
        """Choisir deux fois la même pièce la dupliquait dans le dossier.
        La signature retenue est nom + taille + date de modification : lire
        le contenu demanderait une lecture asynchrone, disproportionnée ici.

        Vérifié au navigateur : `piece-un` puis `piece-un` à nouveau laisse
        deux fichiers et non trois, et une sélection mixte n'ajoute que la
        pièce nouvelle.
        """
        self.client.force_login(self.owner)
        page = self.client.get(
            reverse("event_add", args=[self.dossier.pk])
        ).content.decode()
        self.assertIn("function signatureFichier(file)", page)
        self.assertIn("dejaLa.has(signatureFichier(file))", page)

    def test_an_ignored_duplicate_is_announced_not_dropped_in_silence(self):
        """Écarter une pièce sans le dire serait pire que le doublon : on
        croirait l'avoir jointe. C'est la perte silencieuse que ce projet a
        déjà connue avec `documents[]`."""
        self.client.force_login(self.owner)
        page = self.client.get(
            reverse("event_add", args=[self.dossier.pk])
        ).content.decode()
        self.assertIn("data-pending-note", page)
        self.assertIn("déjà dans la liste, ignoré", page)

    def test_a_file_posted_to_event_add_really_becomes_a_document(self):
        """Le gabarit nommait le champ `documents[]` alors que la vue lit
        `documents` : chaque pièce jointe était reçue puis abandonnée sans
        un mot. Sur un dossier de preuves, c'est la perte silencieuse d'une
        pièce que personne ne remarque avant d'en avoir besoin."""
        self.client.force_login(self.owner)
        response = self.client.post(
            reverse("event_add", args=[self.dossier.pk]),
            {
                "title": "Constat d'huissier",
                "date": "2026-09-27",
                "description": "",
                "documents": SimpleUploadedFile("constat.pdf", b"%PDF-1.4 constat"),
            },
        )
        self.assertEqual(response.status_code, 302)
        created = Event.objects.get(title="Constat d'huissier")
        self.assertEqual(
            [document.title for document in created.documents.all()], ["constat.pdf"]
        )

    def test_an_unsupported_extension_is_refused_server_side(self):
        """Le contrôle d'extension du formulaire est un confort, côté
        client : un POST direct le contourne entièrement."""
        self.client.force_login(self.owner)
        response = self.client.post(
            reverse("event_add", args=[self.dossier.pk]),
            {
                "title": "Pièce douteuse",
                "date": "2026-09-27",
                "description": "",
                "documents": SimpleUploadedFile(
                    "piece.html", b"<script>window.pwned=1</script>"
                ),
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Document.objects.filter(title="piece.html").exists())

    def test_a_refused_upload_is_reported_and_leaves_no_half_saved_event(self):
        """Le refus ne doit pas être silencieux, et l'évènement ne doit pas
        rester en base sans ses pièces : les deux s'enregistrent ensemble."""
        self.client.force_login(self.owner)
        response = self.client.post(
            reverse("event_add", args=[self.dossier.pk]),
            {
                "title": "Pièce douteuse",
                "date": "2026-09-27",
                "description": "",
                "documents": SimpleUploadedFile("piece.exe", b"MZ"),
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "piece.exe")
        self.assertFalse(Event.objects.filter(title="Pièce douteuse").exists())


class DocumentDetachTests(DocumentFixtureMixin, TestCase):
    """Retirer une pièce d'un évènement n'est plus la détruire.

    Une pièce peut illustrer plusieurs évènements sans être stockée deux
    fois : le retrait défait un lien, et ne supprime qu'au dernier.
    """

    def _url(self, event=None, document=None):
        return reverse(
            "document_detach",
            args=[
                (event or self.event).pk,
                (document or self.document).pk,
            ],
        )

    def test_stranger_cannot_detach(self):
        """La faille destructrice d'origine : aucun contrôle de droits avant
        `.delete()`. Un compte sans le moindre lien avec le dossier obtenait
        `200 {"success": true}` et la pièce disparaissait. Les identifiants
        étant séquentiels, une boucle suffisait à vider le dossier."""
        self.client.force_login(self.stranger)
        response = self.client.post(self._url())
        self.assertEqual(response.status_code, 404)
        self.assertTrue(Document.objects.filter(pk=self.document.pk).exists())

    def test_reader_cannot_detach(self):
        self.client.force_login(self.reader)
        response = self.client.post(self._url())
        self.assertEqual(response.status_code, 404)
        self.assertTrue(Document.objects.filter(pk=self.document.pk).exists())

    def test_get_is_rejected(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(self._url()).status_code, 405)

    def test_detaching_the_last_link_removes_the_document_and_its_file(self):
        """Django ne touche pas au stockage quand une ligne disparaît : la
        suppression du fichier doit être explicite. Ce projet traîne déjà
        quatre fichiers que plus aucune ligne ne référence."""
        self.client.force_login(self.owner)
        stockage, chemin = self.document.file.storage, self.document.file.name
        self.assertTrue(stockage.exists(chemin))

        response = self.client.post(self._url())

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["supprime"])
        self.assertFalse(Document.objects.filter(pk=self.document.pk).exists())
        self.assertFalse(stockage.exists(chemin))

    def test_detaching_one_of_two_links_keeps_the_document_and_its_file(self):
        """Le cas qui justifie tout le modèle : la pièce sert ailleurs."""
        autre = Event.objects.create(
            dossier=self.dossier, title="Second évènement", date="2026-09-28"
        )
        self.document.events.add(autre)
        stockage, chemin = self.document.file.storage, self.document.file.name

        self.client.force_login(self.owner)
        response = self.client.post(self._url())

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["supprime"])
        self.document.refresh_from_db()
        self.assertEqual(list(self.document.events.all()), [autre])
        self.assertTrue(stockage.exists(chemin))

    def test_detaching_both_links_in_turn_ends_with_a_physical_deletion(self):
        """Première fois logique, seconde fois physique."""
        autre = Event.objects.create(
            dossier=self.dossier, title="Second évènement", date="2026-09-28"
        )
        self.document.events.add(autre)
        stockage, chemin = self.document.file.storage, self.document.file.name

        self.client.force_login(self.owner)
        premier = self.client.post(self._url())
        self.assertFalse(premier.json()["supprime"])
        self.assertTrue(stockage.exists(chemin))

        second = self.client.post(self._url(event=autre))
        self.assertTrue(second.json()["supprime"])
        self.assertFalse(Document.objects.filter(pk=self.document.pk).exists())
        self.assertFalse(stockage.exists(chemin))

    def test_a_document_cannot_be_detached_through_a_foreign_event(self):
        """L'évènement vient de l'URL : rien ne garantit qu'il partage le
        dossier de la pièce, et sans ce contrôle un rédacteur pourrait agir
        sur une pièce d'une autre affaire."""
        autre_dossier = make_dossier(self.owner, name="Autre affaire")
        evenement_ailleurs = Event.objects.create(
            dossier=autre_dossier, title="Ailleurs", date="2026-09-28"
        )
        self.client.force_login(self.owner)
        response = self.client.post(self._url(event=evenement_ailleurs))
        self.assertEqual(response.status_code, 404)
        self.assertTrue(Document.objects.filter(pk=self.document.pk).exists())

    def test_an_unknown_id_is_refused_exactly_like_a_forbidden_one(self):
        """L'oracle d'existence que la lecture-puis-contrôle laissait ouvert :
        un identifiant inconnu répondait « No Document matches… » et un
        identifiant interdit « No Event matches… », deux messages
        distinguables sous `DEBUG`."""
        self.client.force_login(self.stranger)
        interdit = self.client.post(self._url())
        inconnu = self.client.post(
            reverse("document_detach", args=[self.event.pk, 999999])
        )
        self.assertEqual(interdit.status_code, 404)
        self.assertEqual(inconnu.status_code, 404)


class DocumentDeduplicationTests(DocumentFixtureMixin, TestCase):
    """Le même fichier sur deux évènements d'un dossier n'est stocké qu'une
    fois ; le même fichier dans deux affaires reste deux pièces distinctes."""

    def _poster(self, titre, contenu, nom="piece.pdf", dossier=None):
        return self.client.post(
            reverse("event_add", args=[(dossier or self.dossier).pk]),
            {
                "title": titre,
                "date": "2026-09-27",
                "description": "",
                "documents": SimpleUploadedFile(nom, contenu),
            },
        )

    def test_the_same_file_twice_in_one_dossier_is_stored_once(self):
        self.client.force_login(self.owner)
        avant = Document.objects.filter(dossier=self.dossier).count()

        self._poster("Premier", b"%PDF-1.4 identique")
        self._poster("Second", b"%PDF-1.4 identique")

        apres = Document.objects.filter(dossier=self.dossier).count()
        self.assertEqual(apres - avant, 1, "une seule pièce doit être stockée")

        piece = Document.objects.filter(dossier=self.dossier).latest("pk")
        self.assertEqual(
            sorted(e.title for e in piece.events.all()), ["Premier", "Second"]
        )

    def test_the_same_file_in_another_dossier_is_not_a_duplicate(self):
        """Deux affaires ne partagent jamais d'octets : supprimer une pièce
        dans l'une ne doit rien pouvoir atteindre dans l'autre."""
        autre = make_dossier(self.owner, name="Autre affaire")
        self.client.force_login(self.owner)

        self._poster("Ici", b"%PDF-1.4 identique")
        self._poster("Ailleurs", b"%PDF-1.4 identique", dossier=autre)

        ici = Document.objects.filter(dossier=self.dossier).latest("pk")
        ailleurs = Document.objects.filter(dossier=autre).latest("pk")
        self.assertNotEqual(ici.pk, ailleurs.pk)
        self.assertEqual(ici.content_hash, ailleurs.content_hash)
        self.assertNotEqual(ici.file.name, ailleurs.file.name)

    def test_a_different_file_is_stored_separately(self):
        self.client.force_login(self.owner)
        avant = Document.objects.filter(dossier=self.dossier).count()
        self._poster("Premier", b"%PDF-1.4 un")
        self._poster("Second", b"%PDF-1.4 deux")
        self.assertEqual(
            Document.objects.filter(dossier=self.dossier).count() - avant, 2
        )


class ChampDePiecesTests(TestCase):
    """Le champ de pièces : une seule sélection, réglée par le serveur.

    bootstrap-fileinput gérait sa propre liste à côté de celle du champ ; son
    bouton « vider » et le refus d'un `.exe` vidaient le champ — aucune pièce
    ne partait, pas même les valides — pendant que la liste affichait encore
    les fichiers. Le comportement se vérifie dans un navigateur ; ces tests
    gardent ce qui se lit dans la page servie.
    """

    def setUp(self):
        from events.tests.factories import make_dossier, make_user

        self.owner = make_user("owner")
        self.dossier = make_dossier(self.owner)
        self.client.force_login(self.owner)

    def page(self):
        return self.client.get(reverse("event_add", args=[self.dossier.pk])).content.decode()

    def test_les_extensions_viennent_du_serveur(self):
        from events.views.pieces import ALLOWED_DOCUMENT_EXTENSIONS

        page = self.page()
        attendues = sorted(ALLOWED_DOCUMENT_EXTENSIONS)
        self.assertIn('data-extensions="%s"' % ",".join(attendues), page)
        self.assertIn('accept="%s"' % ",".join("." + e for e in attendues), page)

    def test_plus_de_bootstrap_fileinput_ni_de_jquery(self):
        page = self.page()
        self.assertNotIn("fileinput.min.js", page)
        self.assertNotIn(".fileinput(", page)
        self.assertNotIn("jquery", page.lower())

    def test_la_selection_affichee_est_celle_qui_part(self):
        page = self.page()
        # Le champ natif est la seule sélection, recopiée à chaque changement.
        self.assertIn("champ.files = selection.files;", page)
        # Chaque pièce se retire seule, et un refus ne vide pas la sélection.
        self.assertIn("function retirerFichier(index)", page)
        self.assertIn("type non accepté", page)
        self.assertIn("trop lourd", page)

    def test_un_depot_hors_zone_ne_remplace_pas_la_page(self):
        """Un PDF lâché à côté de la zone serait ouvert par le navigateur à la
        place du formulaire, perdu avec tout ce qui était saisi."""
        page = self.page()
        self.assertIn("if (!zone.contains(e.target))", page)
