"""Liens et doublons entre courriels (`events/liens.py`), sur des cas tirés du dossier réel."""

import datetime
from io import StringIO

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from events.courriels import BRUXELLES, analyser, analyser_en_couches
from events.doublons import doublons_possibles
from events.import_courriels import importer, importer_couches
from events.liens import adresse_de, empreinte, objet_normalise
from events.models import CourrielImporte, Event, Journal
from events.tests.factories import make_dossier, make_user
from events.tests.test_courriels import ALIAS, MediaMixin, courriel

ACME_BAT = "ACME Bat <contact@acme-bat.example>"
MOI = "Jean <jdupont@exemple.be>"


def mise_en_demeure_directe():
    """Reçue le 28/09 à 20:31 (heure de réception : deux minutes après l'envoi)."""
    return courriel("Mise en demeure", "Madame, Monsieur,\n\nNous vous mettons en demeure.",
                    de=ACME_BAT, a=MOI, recu="Mon, 28 Sep 2026 18:31:00 +0000",
                    message_id="<med@acme-bat.example>").as_bytes()


def reponse_a_la_mise_en_demeure(minutes=0):
    texte = ("Je conteste formellement.\n\nJean\n\n"
             "De : ACME Bat <contact@acme-bat.example>\n"
             "Envoyé : lundi 28 septembre 2026 20:29\n"
             "À : Jean <jdupont@exemple.be>\n"
             "Objet : Mise en demeure\n\n"
             "Madame, Monsieur,\n\nNous vous mettons en demeure.\n")
    return courriel("RE: Mise en demeure", texte, de=MOI, a=ACME_BAT,
                    recu="Tue, 29 Sep 2026 07:%02d:00 +0000" % (10 + minutes),
                    message_id="<rep-%d@exemple.be>" % minutes).as_bytes()


def transfert_texte_de_la_mise_en_demeure():
    """La mise en demeure recopiée dans un transfert : pas de Message-ID à elle."""
    texte = ("Pour le dossier.\n\n________________________________\n"
             "De : ACME Bat <contact@acme-bat.example>\nEnvoyé : lundi 28 septembre 2026 20:29\n"
             "À : Jean <jdupont@exemple.be>\nObjet : Mise en demeure\n\n"
             "Madame, Monsieur,\n\nNous vous mettons en demeure.\n")
    return courriel("TR: Mise en demeure", texte, de=MOI, recu="Fri, 9 Oct 2026 08:00:00 +0000",
                    message_id="<enveloppe@exemple.be>").as_bytes()


class EmpreinteTests(SimpleTestCase):
    def test_les_adresses_mal_formees_d_outlook_se_lisent(self):
        self.assertEqual(adresse_de("jdupont@perso.example <jdupont@perso.example>"), "jdupont@perso.example")
        self.assertEqual(adresse_de("Paul Durand [mailto:info@mediation.example]"), "info@mediation.example")
        self.assertEqual(adresse_de("Sophie LAMBERT / Cabinet Lambert <s.lambert@cabinet.example>"),
                         "s.lambert@cabinet.example")
        self.assertEqual(adresse_de("Lea Simon"), "")

    def test_l_objet_perd_ses_prefixes_et_ses_accents(self):
        self.assertEqual(objet_normalise("RE: TR:  Facture d’acompte"), objet_normalise("facture d’acompte"))
        self.assertEqual(objet_normalise("Re: étanchéité SDB"), "etancheite sdb")

    def test_vingt_minutes_d_ecart_au_plus(self):
        base = datetime.datetime(2026, 9, 28, 20, 29, tzinfo=BRUXELLES)
        a = empreinte(ACME_BAT, base, "Mise en demeure")
        self.assertTrue(a.correspond(empreinte("contact@acme-bat.example", base + datetime.timedelta(minutes=13), "TR: Mise en demeure")))
        self.assertFalse(a.correspond(empreinte(ACME_BAT, base + datetime.timedelta(minutes=30), "Mise en demeure")))
        self.assertFalse(a.correspond(empreinte(MOI, base, "Mise en demeure")))


class ImportReliéTests(MediaMixin, TestCase):
    def setUp(self):
        self.dossier = make_dossier(make_user("owner"), name="ACMEBAT")
        self.dossier.alias_courriel = ALIAS
        self.dossier.save()

    def importer(self, octets):
        return importer_couches(self.dossier, analyser_en_couches(octets, ignorer=[ALIAS]))

    def test_le_meme_courriel_par_deux_chemins_n_est_importe_qu_une_fois(self):
        """Le doublon n° 1 / n° 28 du dossier réel : en direct, puis recopié dans un transfert."""
        self.importer(mise_en_demeure_directe())
        crees = self.importer(transfert_texte_de_la_mise_en_demeure())
        self.assertEqual(crees, [])
        self.assertEqual(Event.objects.count(), 1)
        self.assertEqual(CourrielImporte.objects.count(), 2)  # la seconde clé est retenue

    def test_une_reponse_se_relie_au_courriel_cite_deja_present(self):
        self.importer(mise_en_demeure_directe())
        (reponse,) = self.importer(reponse_a_la_mise_en_demeure())
        self.assertEqual(reponse.reponse_a.title, "Mise en demeure")
        self.assertTrue(Journal.objects.filter(action=Journal.Action.EVENEMENT_MODIFIE,
                                               evenement=reponse).exists())

    def test_le_courriel_cite_arrive_apres_sa_reponse(self):
        """En aval : la réponse était là, le courriel qu'elle cite arrive ensuite."""
        (reponse,) = self.importer(reponse_a_la_mise_en_demeure())
        (mise_en_demeure,) = self.importer(mise_en_demeure_directe())
        reponse.refresh_from_db()
        self.assertEqual(reponse.reponse_a, mise_en_demeure)

    def test_un_courriel_et_sa_reponse_ne_sont_jamais_le_meme(self):
        """n° 4/5 du dossier réel : même expéditeur, même fil, dix minutes d'écart."""
        premier = courriel("RE: Litige", "Voici les pièces.", de=MOI, a=ACME_BAT,
                           recu="Mon, 5 Oct 2026 21:53:00 +0000", message_id="<l1@x>").as_bytes()
        second = courriel(
            "RE: Litige",
            "J'oubliais.\n\nDe : Jean <jdupont@exemple.be>\nEnvoyé : lundi 5 octobre 2026 23:53\n"
            "Objet : RE: Litige\n\nVoici les pièces.",
            de=MOI, a=ACME_BAT, recu="Mon, 5 Oct 2026 22:03:00 +0000", message_id="<l2@x>").as_bytes()
        self.importer(premier)
        (reponse,) = self.importer(second)
        self.assertEqual(Event.objects.count(), 2)
        self.assertEqual(reponse.reponse_a.title, "RE: Litige")
        self.assertEqual(doublons_possibles(self.dossier), [])

    def test_l_expediteur_mal_forme_est_quand_meme_lu(self):
        octets = courriel("Devis", "x", de="jdupont@perso.example <jdupont@perso.example>",
                          recu="Wed, 9 Sep 2026 10:00:00 +0000").as_bytes()
        (evenement,) = self.importer(octets)
        self.assertEqual(evenement.expediteur.adresse, "jdupont@perso.example")

    def test_la_page_doublons_propose_le_meme_courriel_et_garde_le_plus_riche(self):
        """Deux évènements importés avant la reconnaissance par empreinte."""
        pauvre = importer(self.dossier, analyser(transfert_texte_de_la_mise_en_demeure()))
        riche_octets = courriel("Mise en demeure", "Nous vous mettons en demeure.", de=ACME_BAT, a=MOI,
                                recu="Mon, 28 Sep 2026 18:31:00 +0000", message_id="<med2@acme-bat.example>",
                                pieces=[("med.pdf", b"%PDF", "application", "pdf")]).as_bytes()
        riche = importer(self.dossier, analyser(riche_octets))
        (paire,) = doublons_possibles(self.dossier)
        self.assertEqual(paire["garder"], riche)
        self.assertNotEqual(pauvre, riche)
        self.assertIn("deux chemins", " ".join(str(r) for r in paire["raisons"]))

    def test_le_detail_affiche_la_reponse_dans_les_deux_sens(self):
        (med,) = self.importer(mise_en_demeure_directe())
        (reponse,) = self.importer(reponse_a_la_mise_en_demeure())
        self.client.force_login(self.dossier.owner)
        page = self.client.get(reverse("event_detail", args=[reponse.pk])).content.decode()
        self.assertIn("En réponse à :", page)
        self.assertIn(reverse("event_detail", args=[med.pk]), page)
        page = self.client.get(reverse("event_detail", args=[med.pk])).content.decode()
        self.assertIn("Réponse :", page)


class CommandeRelierTests(MediaMixin, TestCase):
    """Le rattrapage de l'existant : des évènements importés avant les liens."""

    def setUp(self):
        self.dossier = make_dossier(make_user("owner"), name="ACMEBAT")
        self.dossier.alias_courriel = ALIAS
        self.dossier.save()
        # L'ancienne façon : chaque courriel à part, sans lien ni citation retenue.
        self.med = importer(self.dossier, analyser(mise_en_demeure_directe()))
        self.reponse = importer(self.dossier, analyser(reponse_a_la_mise_en_demeure()))
        CourrielImporte.objects.update(cite_de="", cite_le=None, cite_objet="")
        Event.objects.update(reponse_a=None)

    def lancer(self, *args):
        sortie = StringIO()
        call_command("relier_courriels", *args, stdout=sortie, stderr=StringIO())
        return sortie.getvalue()

    def test_l_essai_montre_le_lien_sans_l_ecrire(self):
        sortie = self.lancer()
        self.assertIn("en réponse à n°%d" % self.med.pk, sortie)
        self.assertIn("Essai", sortie)
        self.reponse.refresh_from_db()
        self.assertIsNone(self.reponse.reponse_a)
        self.assertFalse(CourrielImporte.objects.exclude(cite_le=None).exists())

    def test_appliquer_ecrit_le_lien_et_la_citation(self):
        self.lancer("--appliquer")
        self.reponse.refresh_from_db()
        self.assertEqual(self.reponse.reponse_a, self.med)
        self.assertTrue(CourrielImporte.objects.filter(evenement=self.reponse).exclude(cite_le=None).exists())
        # Rejouer ne refait rien.
        self.assertIn("0 lien(s)", self.lancer("--appliquer"))
