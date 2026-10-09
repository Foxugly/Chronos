"""Le détail d'un évènement : une page, et le même fragment dans un modal.

Un clic sur un évènement, dans n'importe laquelle des quatre vues, ouvre son
détail. Le serveur rend un seul fragment : la page complète l'enveloppe (repli
sans JavaScript), le modal l'injecte tel quel. Deux rendus divergeraient, et
c'est celui qu'on regarde le moins qui garderait le défaut.
"""

import hashlib
import re
import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from events.models import Document, DossierAccess, Event, ShareLink
from events.tests.factories import grant, make_dossier, make_user

AJAX = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}


class DetailFixtureMixin:
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
        self.dossier = make_dossier(self.owner, name="Litige SDB")
        grant(self.dossier, self.reader, DossierAccess.Role.READ)
        self.event = Event.objects.create(
            dossier=self.dossier,
            title="Constat de l'architecte",
            date="2026-09-03",
            description="<p>Pente <strong>insuffisante</strong></p>",
        )
        self.photo = self.piece("douche.jpg", b"\xff\xd8 photo")
        self.rapport = self.piece("rapport.pdf", b"%PDF rapport")
        self.devis = self.piece("devis.docx", b"PK devis")
        self.url = reverse("event_detail", args=[self.event.pk])

    def piece(self, titre, contenu):
        document = Document.objects.create(
            dossier=self.dossier,
            title=titre,
            file=SimpleUploadedFile(titre, contenu),
            content_hash=hashlib.sha256(contenu).hexdigest(),
        )
        document.events.add(self.event)
        return document


class DetailPageTests(DetailFixtureMixin, TestCase):
    def test_the_page_shows_the_event_without_javascript(self):
        self.client.force_login(self.reader)
        reponse = self.client.get(self.url)

        self.assertTemplateUsed(reponse, "events/detail_evenement.html")
        self.assertContains(reponse, "Constat de l&#x27;architecte")
        self.assertContains(reponse, "03/09/2026")
        self.assertContains(reponse, "<strong>insuffisante</strong>", html=False)
        # Une page complète, avec la navigation et un chemin de retour.
        self.assertContains(reponse, "<nav")
        self.assertContains(reponse, reverse("timeline", args=[self.dossier.pk]))

    def test_ajax_gets_the_fragment_alone(self):
        self.client.force_login(self.reader)
        reponse = self.client.get(self.url, **AJAX)

        self.assertTemplateUsed(reponse, "events/_detail_evenement.html")
        self.assertTemplateNotUsed(reponse, "events/detail_evenement.html")
        self.assertNotContains(reponse, "<nav")
        self.assertNotContains(reponse, "<script")
        self.assertContains(reponse, "Constat de l&#x27;architecte")

    def test_a_stranger_gets_a_404_page_and_fragment(self):
        self.client.force_login(make_user("stranger"))
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.client.get(self.url, **AJAX).status_code, 404)

    def test_the_title_is_text_never_markup(self):
        """Le titre vient d'un collaborateur : Django l'échappe, et rien dans
        le gabarit ne doit le marquer `safe`."""
        self.event.title = "<script>window.pwned=1</script>"
        self.event.save()
        self.client.force_login(self.owner)

        reponse = self.client.get(self.url, **AJAX)

        self.assertNotContains(reponse, "<script>window.pwned")
        self.assertContains(reponse, "&lt;script&gt;window.pwned=1&lt;/script&gt;")

    def test_a_document_title_is_text_never_markup(self):
        self.piece('"><img src=x onerror=alert(1)>.pdf', b"%PDF hostile")
        self.client.force_login(self.owner)

        reponse = self.client.get(self.url, **AJAX)

        self.assertNotContains(reponse, "<img src=x")
        self.assertNotContains(reponse, '"><img')

    def test_each_kind_of_document_gets_its_own_rendering(self):
        self.client.force_login(self.reader)
        html = self.client.get(self.url, **AJAX).content.decode()
        url = lambda d: reverse("document_download", args=[d.pk])  # noqa: E731

        # Une image s'ouvre dans la visionneuse — déclarée image, l'URL n'ayant
        # pas d'extension pour que glightbox la devine.
        self.assertRegex(
            html,
            r'<a [^>]*class="glightbox"[^>]*href="%s"' % re.escape(url(self.photo)),
        )
        self.assertIn('data-glightbox="type: image"', html)
        # Un PDF propose un aperçu dans la page.
        self.assertRegex(html, r'<a [^>]*href="%s"[^>]*data-apercu' % re.escape(url(self.rapport)))
        # Le reste se télécharge.
        self.assertIn(url(self.devis), html)
        self.assertNotIn("/media/", html)

    def test_a_writer_is_offered_to_edit(self):
        self.client.force_login(self.owner)
        reponse = self.client.get(self.url, **AJAX)
        self.assertRegex(
            reponse.content.decode(),
            r'<a [^>]*href="%s(?:\?retour=[^"]*)?"[^>]*data-modifier-evenement'
            % re.escape(reverse("event_edit", args=[self.event.pk])),
        )

    def test_a_reader_is_not_offered_what_would_be_refused(self):
        self.client.force_login(self.reader)
        reponse = self.client.get(self.url, **AJAX)
        self.assertNotContains(reponse, reverse("event_edit", args=[self.event.pk]))
        self.assertNotContains(reponse, "Modifier")


class EveryViewOpensTheDetailTests(DetailFixtureMixin, TestCase):
    """Le même geste partout : cliquer sur un évènement montre son détail."""

    def lien_detail(self, html, url):
        return re.search(
            r'<a [^>]*href="%s(?:\?retour=[^"]*)?"[^>]*data-voir-evenement' % re.escape(url), html
        )

    def test_month_week_and_list_link_to_the_detail(self):
        self.client.force_login(self.reader)
        base = reverse("timeline", args=[self.dossier.pk])
        for vue, periode in (("mois", "2026-09"), ("semaine", "2026-08-31"), ("liste", None)):
            with self.subTest(vue=vue):
                params = {"vue": vue}
                if periode:
                    params["periode"] = periode
                html = self.client.get(base, params).content.decode()
                self.assertTrue(self.lien_detail(html, self.url))

    def test_every_view_has_the_modal_even_for_a_reader(self):
        self.client.force_login(self.reader)
        base = reverse("timeline", args=[self.dossier.pk])
        for vue in (
            "chronologie",
            "calendrier-mois",
            "calendrier-semaine",
            "calendrier-liste",
            "calendrier-timeline",
            "mois",
            "semaine",
            "liste",
        ):
            with self.subTest(vue=vue):
                html = self.client.get(base, {"vue": vue}).content.decode()
                self.assertIn('id="modal-evenement"', html)

    def test_the_old_panel_under_the_chronology_is_gone(self):
        self.client.force_login(self.owner)
        html = self.client.get(reverse("timeline", args=[self.dossier.pk])).content.decode()
        self.assertNotIn('id="event-details"', html)


class SharedLinkDetailTests(DetailFixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.lecture = ShareLink.objects.create(
            dossier=self.dossier, created_by=self.owner, role=DossierAccess.Role.READ
        )
        self.ecriture = ShareLink.objects.create(
            dossier=self.dossier, created_by=self.owner, role=DossierAccess.Role.WRITE
        )

    def detail(self, lien, **extra):
        return self.client.get(
            reverse("share_event_detail", args=[lien.token, self.event.pk]), **extra
        )

    def test_an_anonymous_visitor_reads_the_detail_page(self):
        reponse = self.detail(self.lecture)
        self.assertContains(reponse, "Constat de l&#x27;architecte")
        self.assertContains(reponse, reverse("share_timeline", args=[self.lecture.token]))
        self.assertEqual(reponse["Referrer-Policy"], "no-referrer")

    def test_documents_go_through_the_link_not_the_application(self):
        html = self.detail(self.lecture, **AJAX).content.decode()
        self.assertIn(
            reverse("share_document_download", args=[self.lecture.token, self.photo.pk]),
            html,
        )
        # Préfixé de `href="` : l'URL de partage contient `/documents/<id>/`.
        self.assertNotIn(
            'href="%s"' % reverse("document_download", args=[self.photo.pk]), html
        )

    def test_a_read_link_offers_no_edit(self):
        self.assertNotContains(self.detail(self.lecture, **AJAX), "Modifier")

    def test_a_write_link_edits_through_the_link(self):
        self.assertContains(
            self.detail(self.ecriture, **AJAX),
            reverse("share_event_edit", args=[self.ecriture.token, self.event.pk]),
        )

    def test_the_views_link_to_the_shared_detail(self):
        html = self.client.get(
            reverse("share_timeline", args=[self.lecture.token]), {"vue": "liste"}
        ).content.decode()
        self.assertRegex(
            html,
            r'<a [^>]*href="%s(?:\?retour=[^"]*)?"[^>]*data-voir-evenement'
            % re.escape(
                reverse("share_event_detail", args=[self.lecture.token, self.event.pk])
            ),
        )


class PleinePageTests(DetailFixtureMixin, TestCase):
    """L'œil de la popup ouvre l'évènement en pleine page ; la page a un bouton retour."""

    def oeil(self, html):
        return re.search(r'<a [^>]*data-pleine-page[^>]*>', html)

    def test_la_popup_offre_l_oeil_meme_a_un_lecteur(self):
        retour = reverse("timeline", args=[self.dossier.pk]) + "?vue=liste"
        self.client.force_login(self.reader)
        html = self.client.get(self.url, {"retour": retour}, **AJAX).content.decode()
        lien = self.oeil(html)
        self.assertIsNotNone(lien)
        self.assertIn('href="%s?retour=' % self.url, lien.group(0))
        self.assertIn("btn-outline-success", lien.group(0))
        # Un vrai lien : pas `data-voir-evenement`, qui le rouvrirait dans la popup.
        self.assertNotIn("data-voir-evenement", lien.group(0))

    def test_la_pleine_page_n_a_pas_d_oeil_mais_un_bouton_retour(self):
        retour = reverse("timeline", args=[self.dossier.pk]) + "?vue=liste"
        self.client.force_login(self.owner)
        html = self.client.get(self.url, {"retour": retour}).content.decode()
        self.assertIsNone(self.oeil(html))
        self.assertRegex(html, r'<a class="btn btn-outline-secondary[^"]*" href="%s"[^>]*data-retour-dossier'
                         % re.escape(retour.replace("&", "&amp;")))

    def test_par_un_lien_de_partage_l_oeil_reste_dans_le_partage(self):
        lien = ShareLink.objects.create(dossier=self.dossier, created_by=self.owner,
                                        role=DossierAccess.Role.READ)
        url = reverse("share_event_detail", args=[lien.token, self.event.pk])
        html = self.client.get(url, **AJAX).content.decode()
        self.assertIn('href="%s?retour=' % url, self.oeil(html).group(0))
