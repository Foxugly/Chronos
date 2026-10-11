"""Recherche, filtres, export de la sélection, doublons, journal des imports.

Le lot du 2026-10-09 : ce qu'on voit (filtré) est ce qu'on exporte ; la
recherche ignore accents et majuscules et lit le texte des `.md` ; la fusion
de doublons ne perd rien et le journal garde l'absorbé.
"""

import io
import os
import subprocess
import sys
import zipfile

from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from events.doublons import doublons_possibles, fusionner
from events.filtres import lire_filtres, normaliser
from events.models import (
    CourrielImporte, Document, DossierAccess, Event, Expediteur, Journal, PaireIgnoree, ShareLink,
)
from events.tests.factories import grant, make_document, make_dossier, make_user
from events.tests.test_documents import DocumentFixtureMixin


class RechercheFixtureMixin(DocumentFixtureMixin):
    """Le jeu de `DocumentFixtureMixin` (« Devis signé », 02/06/2026, devis.pdf),
    plus un courriel importé avec sa version `.md` et un expéditeur."""

    def setUp(self):
        super().setUp()
        self.writer = make_user("writer")
        grant(self.dossier, self.writer, DossierAccess.Role.WRITE)
        self.expediteur = Expediteur.objects.create(
            dossier=self.dossier, adresse="ev@exemple.be", nom="ACME Bat", couleur="#2563eb")
        self.courriel = Event.objects.create(
            dossier=self.dossier, title="Mise en demeure", date="2026-09-27",
            heure="16:13", expediteur=self.expediteur,
            description="<p>L&#x27;entrepreneur conteste.</p>")
        make_document(self.courriel, title="courriel.md",
                      content="Pose de la natte Mapeguard sans étanchéité.".encode())
        CourrielImporte.objects.create(dossier=self.dossier, message_id="<m1>", evenement=self.courriel)
        self.base = reverse("timeline", args=[self.dossier.pk])

    def liste(self, **parametres):
        self.client.force_login(self.owner)
        return self.client.get(self.base, {"vue": "liste", **parametres})


class RechercheTests(RechercheFixtureMixin, TestCase):
    def test_normaliser_ignore_accents_et_casse(self):
        self.assertEqual(normaliser("  Évènement   ÉTANCHÉITÉ "), "evenement etancheite")

    def test_recherche_dans_le_texte_d_un_md_sans_accents(self):
        reponse = self.liste(q="ETANCHEITE mapeguard")
        self.assertEqual(reponse.context["nb_retenus"], 1)
        self.assertContains(reponse, "Mise en demeure")
        # La version texte n'est jamais nommée : c'est « le texte du courriel ».
        self.assertContains(reponse, "texte du courriel")
        self.assertNotContains(reponse, "courriel.md")
        self.assertNotContains(reponse, "Devis signé</a>")

    def test_tous_les_mots_sont_requis(self):
        self.assertEqual(self.liste(q="mapeguard devis").context["nb_retenus"], 0)

    def test_recherche_dans_la_description_assainie(self):
        # nh3 stocke l'apostrophe en entité : la recherche doit la trouver quand même.
        self.assertEqual(self.liste(q="l'entrepreneur").context["nb_retenus"], 1)

    def test_recherche_dans_le_nom_d_une_piece(self):
        self.assertEqual(self.liste(q="devis.pdf").context["nb_retenus"], 1)

    def test_filtre_par_expediteur_et_saisis_a_la_main(self):
        self.assertEqual(self.liste(expediteur=self.expediteur.pk).context["nb_retenus"], 1)
        self.assertEqual(self.liste(expediteur="aucun").context["nb_retenus"], 1)

    def test_expediteur_d_un_autre_dossier_est_ignore(self):
        autre = Expediteur.objects.create(dossier=make_dossier(self.owner, "Autre"), adresse="x@y.be")
        filtres = lire_filtres({"expediteur": str(autre.pk)}, self.dossier)
        self.assertEqual(filtres.expediteur, "")

    def test_filtre_par_periode(self):
        self.assertEqual(self.liste(du="2026-09", au="2026-09").context["nb_retenus"], 1)
        self.assertEqual(self.liste(au="2026-06").context["nb_retenus"], 1)

    def test_un_intervalle_qui_touche_la_periode_est_retenu(self):
        Event.objects.create(dossier=self.dossier, title="Chantier", date="2026-08-18", date_fin="2026-10-02")
        titres = [e.title for g in self.liste(du="2026-09", au="2026-09").context["groupes"]
                  for e in g["evenements"]]
        self.assertIn("Chantier", titres)

    def test_parametres_invalides_ignores(self):
        reponse = self.liste(du="n'importe", au="2026-13", expediteur="abc")
        self.assertEqual(reponse.status_code, 200)
        self.assertFalse(reponse.context["filtres"].actifs)
        self.assertEqual(reponse.context["nb_retenus"], 2)

    def test_les_onglets_gardent_les_filtres(self):
        reponse = self.liste(q="natte")
        self.assertContains(reponse, "?vue=chronologie&amp;q=natte")

    def test_toutes_les_vues_filtrent(self):
        self.client.force_login(self.owner)
        for vue in ("chronologie", "calendrier-mois", "calendrier-timeline", "mois", "semaine", "liste"):
            reponse = self.client.get(self.base, {"vue": vue, "q": "mapeguard"})
            self.assertEqual(reponse.status_code, 200, vue)
            self.assertEqual(reponse.context["nb_retenus"], 1, vue)

    def test_filtres_par_lien_de_partage(self):
        lien = ShareLink.objects.create(dossier=self.dossier, created_by=self.owner,
                                        role=DossierAccess.Role.READ)
        reponse = self.client.get(reverse("share_timeline", args=[lien.token]),
                                  {"vue": "liste", "q": "mapeguard"})
        self.assertEqual(reponse.context["nb_retenus"], 1)


class ExportSelectionTests(RechercheFixtureMixin, TestCase):
    def exporter(self, **donnees):
        self.client.force_login(self.owner)
        reponse = self.client.post(reverse("dossier_export", args=[self.dossier.pk]), donnees)
        self.assertEqual(reponse.status_code, 200)
        return zipfile.ZipFile(io.BytesIO(b"".join(reponse.streaming_content)))

    def test_export_complet_sans_filtre(self):
        noms = self.exporter().namelist()
        self.assertEqual(len([n for n in noms if n.startswith("pieces/")]), 2)
        self.assertNotIn("journal.pdf", noms)

    def test_export_de_la_selection_ne_contient_que_ses_pieces(self):
        archive = self.exporter(q="mapeguard")
        pieces = [n for n in archive.namelist() if n.startswith("pieces/")]
        self.assertEqual(pieces, ["pieces/002-courriel.md"])
        self.assertIn("Export partiel", archive.read("LISEZMOI.txt").decode("utf-8"))
        self.assertIn(b"Export partiel", archive.read("chronologie.pdf"))
        entree = Journal.objects.get(action=Journal.Action.EXPORT)
        self.assertEqual(entree.details["evenements"], 1)
        self.assertIn("mapeguard", entree.details["selection"])

    def test_export_avec_le_journal(self):
        Journal.objects.create(dossier=self.dossier, action=Journal.Action.EXPORT, objet="ancien")
        archive = self.exporter(journal="1")
        self.assertIn("journal.pdf", archive.namelist())
        self.assertTrue(Journal.objects.get(details__journal=True))

    def test_le_journal_exporte_par_un_lecteur_omet_le_partage(self):
        Journal.objects.create(dossier=self.dossier, action=Journal.Action.LIEN_CREE, objet="SECRETLIEN")
        self.client.force_login(self.reader)
        reponse = self.client.post(reverse("dossier_export", args=[self.dossier.pk]), {"journal": "1"})
        archive = zipfile.ZipFile(io.BytesIO(b"".join(reponse.streaming_content)))
        self.assertNotIn(b"SECRETLIEN", archive.read("journal.pdf"))

    def test_jamais_de_journal_par_un_lien(self):
        lien = ShareLink.objects.create(dossier=self.dossier, created_by=self.owner,
                                        role=DossierAccess.Role.READ)
        reponse = self.client.post(reverse("share_dossier_export", args=[lien.token]),
                                   {"journal": "1", "q": "mapeguard"})
        archive = zipfile.ZipFile(io.BytesIO(b"".join(reponse.streaming_content)))
        self.assertNotIn("journal.pdf", archive.namelist())
        self.assertEqual([n for n in archive.namelist() if n.startswith("pieces/")], ["pieces/002-courriel.md"])

    def test_le_bouton_dit_selection(self):
        self.assertContains(self.liste(q="mapeguard"), "Exporter la sélection (1)")


class DoublonsFixtureMixin(RechercheFixtureMixin):
    def setUp(self):
        super().setUp()
        # La même mise en demeure, saisie à la main le même jour, avec la même pièce.
        self.manuel = Event.objects.create(
            dossier=self.dossier, title="RV mise en demeure", date="2026-09-27",
            description="<p>Ma note perso</p>", couleur="#dc2626")
        self.piece = make_document(self.manuel, title="mise_en_demeure.pdf", content=b"MED")
        self.piece.events.add(self.courriel)
        self.url = reverse("dossier_doublons", args=[self.dossier.pk])


class DoublonsTests(DoublonsFixtureMixin, TestCase):
    def test_detecte_la_paire_et_suggere_de_garder_l_importe(self):
        paires = doublons_possibles(self.dossier)
        self.assertEqual(len(paires), 1)
        self.assertEqual({paires[0]["a"], paires[0]["b"]}, {self.courriel, self.manuel})
        self.assertEqual(paires[0]["garder"], self.courriel)

    def test_une_photo_reprise_des_semaines_plus_tard_n_est_pas_un_doublon(self):
        self.piece.events.remove(self.manuel)
        loin = Event.objects.create(dossier=self.dossier, title="Photos", date="2026-08-01")
        self.piece.events.add(loin)
        Event.objects.filter(pk=self.manuel.pk).update(title="Sans rapport")
        self.assertEqual(doublons_possibles(self.dossier), [])

    def test_deux_courriels_importes_ne_sont_jamais_des_doublons(self):
        CourrielImporte.objects.create(dossier=self.dossier, message_id="<m2>", evenement=self.manuel)
        self.assertEqual(doublons_possibles(self.dossier), [])

    def test_meme_jour_et_titres_proches_sans_piece(self):
        Event.objects.create(dossier=self.dossier, title="Facture acompte", date="2026-06-02")
        Event.objects.create(dossier=self.dossier, title="facture d'acompte reçue", date="2026-06-02")
        titres = [{p["a"].title, p["b"].title} for p in doublons_possibles(self.dossier)]
        self.assertIn({"Facture acompte", "facture d'acompte reçue"}, titres)

    def test_page_reservee_a_qui_ecrit(self):
        for qui, attendu in ((self.writer, 200), (self.reader, 404), (self.stranger, 404)):
            self.client.force_login(qui)
            self.assertEqual(self.client.get(self.url).status_code, attendu)

    def test_bouton_dans_l_en_tete_pour_qui_ecrit(self):
        self.assertContains(self.liste(), self.url)
        self.client.force_login(self.reader)
        self.assertNotContains(self.client.get(self.base, {"vue": "liste"}), self.url)

    def test_fusion_ne_perd_rien(self):
        with self.captureOnCommitCallbacks(execute=True):
            fichier = self.piece.file.path
        self.client.force_login(self.writer)
        reponse = self.client.post(reverse("doublons_fusionner", args=[self.dossier.pk]),
                                   {"a": self.manuel.pk, "b": self.courriel.pk, "garder": self.courriel.pk})
        self.assertRedirects(reponse, self.url)
        self.assertFalse(Event.objects.filter(pk=self.manuel.pk).exists())
        garde = Event.objects.get(pk=self.courriel.pk)
        self.assertIn("Ma note perso", garde.description)
        self.assertIn("RV mise en demeure", garde.description)
        self.assertIn("entrepreneur conteste", garde.description)
        self.assertEqual(garde.couleur, "#dc2626")
        self.assertEqual(garde.expediteur, self.expediteur)
        self.assertIn(self.piece, garde.documents.all())
        self.assertTrue(os.path.exists(fichier))
        self.assertEqual(Document.objects.filter(dossier=self.dossier).count(), 3)
        self.assertEqual(garde.version, 2)
        supprime = Journal.objects.get(action=Journal.Action.EVENEMENT_SUPPRIME)
        self.assertEqual(supprime.details["valeurs"]["title"], "RV mise en demeure")
        self.assertEqual(supprime.details["fusionne_dans"], "Mise en demeure")
        self.assertTrue(Journal.objects.filter(action=Journal.Action.EVENEMENTS_FUSIONNES).exists())

    def test_fusion_dans_le_manuel_reporte_la_memoire_d_import(self):
        manuel, courriel = Event.objects.get(pk=self.manuel.pk), Event.objects.get(pk=self.courriel.pk)
        fusionner(manuel, courriel, request=type("R", (), {"user": self.owner})())
        self.assertEqual(CourrielImporte.objects.get(message_id="<m1>").evenement, self.manuel)
        self.manuel.refresh_from_db()
        self.assertEqual(self.manuel.expediteur, self.expediteur)
        self.assertEqual(self.manuel.documents.count(), 2)

    def test_fusion_refusee_au_lecteur_et_hors_dossier(self):
        self.client.force_login(self.reader)
        url = reverse("doublons_fusionner", args=[self.dossier.pk])
        donnees = {"a": self.manuel.pk, "b": self.courriel.pk, "garder": self.courriel.pk}
        self.assertEqual(self.client.post(url, donnees).status_code, 404)
        ailleurs = Event.objects.create(dossier=make_dossier(self.owner, "Autre"), title="X", date="2026-09-27")
        self.client.force_login(self.owner)
        self.assertEqual(self.client.post(url, {"a": self.manuel.pk, "b": ailleurs.pk,
                                                "garder": self.manuel.pk}).status_code, 404)
        self.assertEqual(self.client.post(url, {"a": self.manuel.pk, "b": self.manuel.pk,
                                                "garder": self.manuel.pk}).status_code, 404)
        self.assertEqual(Event.objects.filter(dossier=self.dossier).count(), 3)

    def test_ecarter_une_paire(self):
        self.client.force_login(self.writer)
        self.client.post(reverse("doublons_ecarter", args=[self.dossier.pk]),
                         {"a": self.courriel.pk, "b": self.manuel.pk})
        self.assertEqual(PaireIgnoree.objects.count(), 1)
        self.assertEqual(doublons_possibles(self.dossier), [])
        self.assertTrue(Journal.objects.filter(action=Journal.Action.DOUBLON_ECARTE).exists())
        self.assertEqual(Event.objects.filter(dossier=self.dossier).count(), 3)


class JournalImportTests(RechercheFixtureMixin, TestCase):
    def test_entree_sans_auteur_sur_un_evenement_importe_est_attribuee_a_l_import(self):
        Journal.objects.create(dossier=self.dossier, action=Journal.Action.PIECE_AJOUTEE,
                               objet="courriel.md", evenement=self.courriel)
        Journal.objects.create(dossier=self.dossier, action=Journal.Action.PIECE_AJOUTEE,
                               objet="autre", evenement=self.event)
        self.client.force_login(self.owner)
        reponse = self.client.get(reverse("dossier_journal", args=[self.dossier.pk]))
        lignes = {l["entree"].objet: l["par_import"] for l in reponse.context["lignes"]}
        self.assertEqual(lignes, {"courriel.md": True, "autre": False})
        self.assertContains(reponse, "par l'import de courriels")


class HachageDesTestsTests(TestCase):
    def test_hachage_rapide_seulement_sous_manage_py_test(self):
        self.assertEqual(settings.PASSWORD_HASHERS, ["django.contrib.auth.hashers.MD5PasswordHasher"])
        code = ("import sys; sys.argv=['manage.py','runserver']; "
                "import config.settings as s; print(hasattr(s, 'PASSWORD_HASHERS'))")
        sortie = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                                env={**os.environ, "DJANGO_DEBUG": "True"}, cwd=settings.BASE_DIR)
        self.assertEqual(sortie.stdout.strip(), "False", sortie.stderr)


class OngletDocumentsTests(RechercheFixtureMixin, TestCase):
    """L'onglet « Documents » : les pièces, cherchées et filtrées."""

    def setUp(self):
        super().setUp()
        # Comme un vrai import : le courriel (.eml) à côté de sa version texte
        # (.md), qui n'est jamais une ligne de l'onglet.
        make_document(self.courriel, title="courriel.eml", content=b"From: ev@exemple.be\r\n\r\nvoir texte")

    def documents(self, **parametres):
        return self.liste(vue="documents", **parametres)

    def noms(self, reponse):
        return [l["document"].title for l in reponse.context["pieces"]]

    def test_toutes_les_pieces_avec_leurs_evenements(self):
        reponse = self.documents()
        self.assertEqual(reponse.status_code, 200)
        self.assertEqual(self.noms(reponse), ["devis.pdf", "courriel.eml"])
        self.assertContains(reponse, "Mise en demeure")
        self.assertContains(reponse, reverse("document_download", args=[self.document.pk]))

    def test_recherche_dans_le_nom_et_le_texte_de_la_piece(self):
        self.assertEqual(self.noms(self.documents(q="devis")), ["devis.pdf"])
        # Le mot n'est que dans la version texte : c'est le courriel qui sort.
        reponse = self.documents(q="ETANCHEITE")
        self.assertEqual(self.noms(reponse), ["courriel.eml"])
        self.assertEqual(reponse.context["pieces"][0]["trouve_dans"], ["texte"])
        self.assertContains(reponse, "1 pièce sur 2")

    def test_la_recherche_ne_porte_pas_sur_l_evenement(self):
        # « conteste » est dans la description de l'évènement, pas dans ses pièces.
        self.assertEqual(self.noms(self.documents(q="conteste")), [])

    def test_filtre_par_type(self):
        self.assertEqual(self.noms(self.documents(type="courriel")), ["courriel.eml"])
        self.assertEqual(self.noms(self.documents(type="pdf")), ["devis.pdf"])
        self.assertEqual(len(self.noms(self.documents(type="nimporte"))), 2)

    def test_filtre_par_expediteur_et_periode(self):
        self.assertEqual(self.noms(self.documents(expediteur=self.expediteur.pk)), ["courriel.eml"])
        self.assertEqual(self.noms(self.documents(au="2026-06")), ["devis.pdf"])

    def test_une_piece_sans_evenement_est_montree_sans_filtre(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        Document.objects.create(dossier=self.dossier, title="orpheline.pdf",
                                file=SimpleUploadedFile("orpheline.pdf", b"o"), content_hash="0" * 64)
        self.assertIn("orpheline.pdf", self.noms(self.documents()))
        self.assertNotIn("orpheline.pdf", self.noms(self.documents(au="2026-06")))

    def test_les_pieces_d_un_autre_dossier_n_apparaissent_jamais(self):
        ailleurs = Event.objects.create(dossier=make_dossier(self.stranger, "Autre"), title="X", date="2026-06-02")
        make_document(ailleurs, title="secret.pdf", content=b"secret")
        self.assertNotIn("secret.pdf", self.noms(self.documents()))

    def test_par_lien_de_partage(self):
        lien = ShareLink.objects.create(dossier=self.dossier, created_by=self.owner,
                                        role=DossierAccess.Role.READ)
        reponse = self.client.get(reverse("share_timeline", args=[lien.token]), {"vue": "documents"})
        self.assertEqual(len(reponse.context["pieces"]), 2)
        self.assertContains(reponse, reverse("share_document_download", args=[lien.token, self.document.pk]))
        self.assertNotContains(reponse, 'href="%s"' % reverse("document_download", args=[self.document.pk]))

    def test_refuse_a_un_inconnu(self):
        self.client.force_login(self.stranger)
        self.assertEqual(self.client.get(self.base, {"vue": "documents"}).status_code, 404)
