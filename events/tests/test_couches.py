"""Chaque couche d'un transfert devient un évènement.

Une mise en demeure reçue le 28/09, transférée à l'avocate le 07/10, puis ce
transfert envoyé à l'alias du dossier : deux évènements (la mise en demeure,
le transfert à l'avocate), le second relié au premier. L'enveloppe envoyée à
l'alias n'en est pas un — sauf si elle s'adresse aussi à quelqu'un d'autre.
"""

import datetime
from pathlib import Path

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from events.courriels import analyser, analyser_en_couches
from events.import_courriels import importer, importer_couches
from events.models import CourrielImporte, Event, Journal
from events.tests.factories import make_dossier, make_user
from events.tests.test_courriels import (
    ALIAS, BRUXELLES, EXEMPLES, MediaMixin, courriel, original, transferer_dans_le_texte,
    transferer_en_piece_jointe,
)

AVOCATE = "a.dubois@avocat.example"
EXEMPLE_AVOCATE = EXEMPLES / (
    "TR_ Mise en demeure – paiement des travaux réalisés et indemnisation du préjudice subi "
    "à la suite de l’arrêt du chantier.eml"
)


def transfert_a_l_avocate(cc_alias=False):
    """Un « Transférer » ordinaire de la mise en demeure à l'avocate, avec un mot."""
    texte = (
        "Bonjour Maître,\n\nVoici la mise en demeure reçue lundi.\n\nJean\n\n"
        "________________________________\n"
        "De : ACME Bat <contact@acme-bat.example>\n"
        "Envoyé : lundi 28 septembre 2026 20:29\n"
        "À : Jean <jdupont@exemple.be>\n"
        "Objet : Mise en demeure\n\n"
        "Madame, Monsieur,\n\nNous vous mettons en demeure.\n"
    )
    m = courriel("TR: Mise en demeure", texte, de="Jean <jdupont@exemple.be>",
                 a=AVOCATE, recu="Wed, 7 Oct 2026 16:38:44 +0000",
                 message_id="<avocate@exemple.be>",
                 pieces=[("mise-en-demeure.pdf", b"%PDF med", "application", "pdf")])
    if cc_alias:
        m["Cc"] = ALIAS
    return m.as_bytes()


class CouchesTests(TestCase):
    def verifier_les_deux_couches(self, couches):
        self.assertEqual(len(couches), 2)
        mise_en_demeure, transfert = couches
        self.assertEqual(mise_en_demeure.titre, "Mise en demeure")
        self.assertIn("contact@acme-bat.example", mise_en_demeure.expediteur)
        self.assertEqual(mise_en_demeure.moment, datetime.datetime(2026, 9, 28, 20, 29, tzinfo=BRUXELLES))
        self.assertIn("Nous vous mettons en demeure", mise_en_demeure.texte)
        self.assertIn("jdupont@exemple.be", transfert.expediteur)
        self.assertIn(AVOCATE, transfert.destinataires)
        self.assertEqual(transfert.moment.date(), datetime.date(2026, 10, 7))
        self.assertIn("Voici la mise en demeure", transfert.texte)
        self.assertNotIn("Nous vous mettons en demeure", transfert.texte)
        self.assertTrue(transfert.transfert)
        self.assertFalse(mise_en_demeure.transfert)
        return mise_en_demeure, transfert

    def test_le_transfert_a_l_avocate_seul_donne_deux_couches(self):
        """Le courriel envoyé à l'avocate, déposé tel quel : il s'adresse à
        quelqu'un d'autre que l'alias, c'est donc lui-même une couche."""
        self.verifier_les_deux_couches(analyser_en_couches(transfert_a_l_avocate(), ignorer=[ALIAS]))

    def test_transfere_dans_le_texte_a_l_alias(self):
        """Le cas du 2026-10-09 : l'enveloppe adressée à l'alias seul ne compte pas."""
        couches = analyser_en_couches(transferer_dans_le_texte(transfert_a_l_avocate()), ignorer=[ALIAS])
        mise_en_demeure, transfert = self.verifier_les_deux_couches(couches)
        self.assertEqual(transfert.moment, datetime.datetime(2026, 10, 7, 18, 38, tzinfo=BRUXELLES))
        # Le mot écrit en envoyant à l'alias reste gardé, sur la couche du dessus.
        self.assertIn("Pour le dossier.", transfert.commentaire)

    def test_transfere_en_piece_jointe_a_l_alias(self):
        couches = analyser_en_couches(transferer_en_piece_jointe(transfert_a_l_avocate()), ignorer=[ALIAS])
        mise_en_demeure, transfert = self.verifier_les_deux_couches(couches)
        self.assertEqual(transfert.message_id, "<avocate@exemple.be>")
        self.assertEqual(transfert.titre, "TR: Mise en demeure")

    def test_l_alias_en_copie_du_vrai_courriel(self):
        """« Transférer » à l'avocate avec l'alias en Cc : ce courriel-là est le transfert."""
        couches = analyser_en_couches(transfert_a_l_avocate(cc_alias=True), ignorer=[ALIAS])
        self.verifier_les_deux_couches(couches)

    def test_les_pieces_vont_au_courriel_qui_les_porte(self):
        mise_en_demeure, transfert = analyser_en_couches(transfert_a_l_avocate(), ignorer=[ALIAS])
        # Dans un transfert ordinaire, la pièce jointe est celle du courriel d'origine.
        self.assertEqual([p.nom for p in mise_en_demeure.pieces], ["mise-en-demeure.pdf"])
        self.assertEqual(transfert.pieces, [])

    def test_un_envoi_direct_reste_une_seule_couche(self):
        couches = analyser_en_couches(courriel("Devis", "Voici le devis").as_bytes(), ignorer=[ALIAS])
        self.assertEqual(len(couches), 1)
        self.assertFalse(couches[0].transfert)

    def test_une_reponse_reste_une_seule_couche(self):
        texte = "Je conteste.\n\nDe : ACME Bat <contact@acme-bat.example>\nEnvoyé : lundi 28 septembre 2026 20:29\nObjet : Devis\n\nTexte cité"
        couches = analyser_en_couches(courriel("RE: Devis", texte).as_bytes(), ignorer=[ALIAS])
        self.assertEqual(len(couches), 1)

    def test_un_transfert_simple_a_l_alias_reste_une_seule_couche(self):
        couches = analyser_en_couches(
            courriel("TR: Réponse 2e acompte", "Pour le dossier", joint=original()).as_bytes(), ignorer=[ALIAS])
        self.assertEqual(len(couches), 1)
        self.assertEqual(couches[0].titre, "Réponse 2e acompte")

    def test_les_couches_ont_des_cles_distinctes(self):
        from events.import_courriels import cle_du_courriel

        couches = analyser_en_couches(transfert_a_l_avocate(), ignorer=[ALIAS])
        self.assertEqual(len({cle_du_courriel(c) for c in couches}), 2)

    def test_analyser_rend_toujours_le_courriel_d_origine(self):
        """L'ancienne lecture (une seule `Analyse`) ne change pas."""
        a = analyser(transferer_dans_le_texte(transfert_a_l_avocate()))
        self.assertEqual(a.titre, "Mise en demeure")
        self.assertTrue(a.commentaire.startswith("Pour le dossier."))
        self.assertIn("Voici la mise en demeure", a.commentaire)


class VraiTransfertALAvocateTests(TestCase):
    """Le vrai courriel envoyé à l'avocate le 07/10 (non versionné)."""

    def setUp(self):
        if not EXEMPLE_AVOCATE.is_file():
            self.skipTest("exemple absent de exemples_courriels/")
        self.octets = EXEMPLE_AVOCATE.read_bytes()

    def verifier(self, couches):
        self.assertEqual(len(couches), 2)
        mise_en_demeure, transfert = couches
        self.assertIn("acme-bat@outlook.com", mise_en_demeure.expediteur)
        self.assertEqual(mise_en_demeure.moment, datetime.datetime(2026, 9, 28, 20, 29, tzinfo=BRUXELLES))
        self.assertIn("jdupont@perso.example", transfert.expediteur)
        self.assertIn(AVOCATE, transfert.destinataires)
        self.assertEqual(transfert.moment.date(), datetime.date(2026, 10, 7))

    def test_tel_quel(self):
        self.verifier(analyser_en_couches(self.octets, ignorer=[ALIAS]))

    def test_transfere_dans_le_texte(self):
        self.verifier(analyser_en_couches(transferer_dans_le_texte(self.octets), ignorer=[ALIAS]))

    def test_transfere_en_piece_jointe(self):
        self.verifier(analyser_en_couches(transferer_en_piece_jointe(self.octets), ignorer=[ALIAS]))


class ImportDesCouchesTests(MediaMixin, TestCase):
    def setUp(self):
        self.dossier = make_dossier(make_user("owner"), name="ACMEBAT")
        self.dossier.alias_courriel = ALIAS
        self.dossier.save()

    def couches(self):
        return analyser_en_couches(transferer_dans_le_texte(transfert_a_l_avocate()), ignorer=[ALIAS])

    def test_deux_evenements_relies(self):
        crees = importer_couches(self.dossier, self.couches())
        self.assertEqual(len(crees), 2)
        mise_en_demeure, transfert = Event.objects.order_by("date")
        self.assertEqual(transfert.transfert_de, mise_en_demeure)
        self.assertEqual(list(mise_en_demeure.transferts.all()), [transfert])
        self.assertEqual(CourrielImporte.objects.count(), 2)
        self.assertEqual(Journal.objects.filter(action=Journal.Action.COURRIEL_IMPORTE).count(), 2)

    def test_le_eml_est_stocke_une_fois_et_rattache_aux_deux(self):
        # En pièce jointe : le PDF voyage avec (le transfert dans le texte des tests ne garde que le texte).
        importer_couches(self.dossier, analyser_en_couches(
            transferer_en_piece_jointe(transfert_a_l_avocate()), ignorer=[ALIAS]))
        mise_en_demeure, transfert = Event.objects.order_by("date")
        eml_1 = {d.pk for d in mise_en_demeure.documents.all() if d.file.name.endswith(".eml")}
        eml_2 = {d.pk for d in transfert.documents.all() if d.file.name.endswith(".eml")}
        self.assertEqual(eml_1, eml_2)
        self.assertTrue(any(d.title.endswith(".pdf") or d.file.name.endswith(".pdf")
                            for d in mise_en_demeure.documents.all()))

    def test_rejouer_ne_cree_rien(self):
        importer_couches(self.dossier, self.couches())
        self.assertEqual(importer_couches(self.dossier, self.couches()), [])
        self.assertEqual(Event.objects.count(), 2)

    def test_une_mise_en_demeure_deja_importee_n_est_pas_recreee(self):
        """Le courriel importé le 2026-10-09 avant ce changement : seul le
        transfert s'ajoute, relié à l'évènement qui existait."""
        octets = transferer_dans_le_texte(transfert_a_l_avocate())
        existant = importer(self.dossier, analyser(octets))  # l'ancienne façon
        crees = importer_couches(self.dossier, analyser_en_couches(octets, ignorer=[ALIAS]))
        self.assertEqual(len(crees), 1)
        self.assertEqual(crees[0].transfert_de, existant)
        self.assertEqual(Event.objects.count(), 2)

    def test_un_lien_ne_traverse_pas_les_dossiers(self):
        autre = make_dossier(self.dossier.owner, name="Autre")
        ailleurs = Event.objects.create(dossier=autre, title="x", date="2026-09-28")
        e = Event(dossier=self.dossier, title="y", date="2026-10-07", transfert_de=ailleurs)
        with self.assertRaises(ValidationError):
            e.full_clean()

    def test_le_detail_montre_les_deux_liens(self):
        importer_couches(self.dossier, self.couches())
        mise_en_demeure, transfert = Event.objects.order_by("date")
        self.client.force_login(self.dossier.owner)
        page = self.client.get(reverse("event_detail", args=[transfert.pk])).content.decode()
        self.assertIn(reverse("event_detail", args=[mise_en_demeure.pk]), page)
        page = self.client.get(reverse("event_detail", args=[mise_en_demeure.pk])).content.decode()
        self.assertIn(reverse("event_detail", args=[transfert.pk]), page)
