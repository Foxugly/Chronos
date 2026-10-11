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

    def test_le_destinataire_cite_est_retenu_a_l_import(self):
        trace = CourrielImporte.objects.get(evenement=importer(
            make_dossier(make_user("autre"), name="Autre"), analyser(reponse_a_la_mise_en_demeure(5))))
        self.assertEqual(trace.cite_a, "Jean <jdupont@exemple.be>")

    def test_appliquer_complete_le_destinataire_sans_rien_recalculer(self):
        """Sans --recalculer, une citation déjà notée garde ses champs ; seul le
        destinataire, nouveau, est complété."""
        CourrielImporte.objects.filter(evenement=self.reponse).update(
            cite_de="garde", cite_le="2026-09-28T18:29Z", cite_objet="garde", cite_a="")
        self.lancer("--appliquer")
        trace = CourrielImporte.objects.get(evenement=self.reponse)
        self.assertEqual((trace.cite_de, trace.cite_objet), ("garde", "garde"))
        self.assertEqual(trace.cite_a, "Jean <jdupont@exemple.be>")

    def test_appliquer_ecrit_le_lien_et_la_citation(self):
        self.lancer("--appliquer")
        self.reponse.refresh_from_db()
        self.assertEqual(self.reponse.reponse_a, self.med)
        self.assertTrue(CourrielImporte.objects.filter(evenement=self.reponse).exclude(cite_le=None).exists())
        # Rejouer ne refait rien.
        self.assertIn("0 lien(s)", self.lancer("--appliquer"))


def reponse_coupee_par_exchange():
    """Le n° 12 du dossier réel : le corps s'arrête sur « ci-dessous », la citation
    est dans une pièce `ATT00004.txt` qu'Exchange a détachée du texte."""
    m = courriel("RE: Mise en demeure", "Veuillez trouver ci-dessous ma réponse.\n\nJean",
                 de=MOI, a=ACME_BAT, recu="Tue, 29 Sep 2026 07:10:00 +0000", message_id="<coupe@x>")
    m.add_attachment("Veuillez trouver ci-dessous ma réponse.", subtype="plain", filename="ATT00001.txt")
    m.add_attachment("<p>De : ACME Bat</p>", subtype="html", filename="ATT00003.htm")
    m.add_attachment(
        "De : ACME Bat <contact@acme-bat.example>\nEnvoyé : lundi 28 septembre 2026 20:29\n"
        "À : Jean <jdupont@exemple.be>\nObjet : Mise en demeure\n\nNous vous mettons en demeure.",
        subtype="plain", filename="ATT00004.txt")
    return m.as_bytes()


def reponse_citant_deux_messages(premier_cite_present=False):
    """Le n° 18 du dossier réel : il cite d'abord un courriel absent du dossier, puis un présent."""
    texte = ("D'accord.\n\n"
             "De : Paul Durand <info@mediation.example>\nEnvoyé : mardi 29 septembre 2026 09:13\n"
             "Objet : RE: Mise en demeure\n\nNous regardons.\n\n"
             "De : ACME Bat <contact@acme-bat.example>\nEnvoyé : lundi 28 septembre 2026 20:29\n"
             "Objet : Mise en demeure\n\nNous vous mettons en demeure.")
    return courriel("RE: Mise en demeure", texte, de=MOI, a="info@mediation.example, contact@acme-bat.example",
                    recu="Tue, 29 Sep 2026 08:00:00 +0000", message_id="<deux@x>").as_bytes()


def reponse_de_paul():
    return courriel("RE: Mise en demeure", "Nous regardons.", de="Paul Durand <info@mediation.example>",
                    a=MOI, recu="Tue, 29 Sep 2026 07:13:00 +0000", message_id="<paul@x>").as_bytes()


class SuiteExchangeTests(SimpleTestCase):
    def test_la_suite_detachee_par_exchange_revient_dans_le_texte(self):
        from events.courriels import corps_en_texte, lire, pieces_jointes

        message = lire(reponse_coupee_par_exchange())
        texte = corps_en_texte(message)
        self.assertIn("Nous vous mettons en demeure", texte)
        self.assertEqual(texte.count("Veuillez trouver ci-dessous ma réponse."), 1)  # pas de doublon
        self.assertEqual(pieces_jointes(message), [])  # ce n'étaient pas des pièces

    def test_la_forme_gmail_a_ecrit_est_une_citation(self):
        from events.courriels import analyser_en_couches
        from events.liens import citations

        texte = ("Je conteste.\n\nLe lun. 28 sept. 2026 à 20:29, ACME Bat <contact@acme-bat.example> a écrit :\n"
                 "> Nous vous mettons en demeure.")
        (couche,) = analyser_en_couches(courriel("Re: Mise en demeure", texte, de=MOI, a=ACME_BAT).as_bytes(),
                                        ignorer=[ALIAS])
        (cite,) = citations(couche)
        self.assertEqual(cite.expediteur[0], "contact@acme-bat.example")
        self.assertEqual(cite.moment, datetime.datetime(2026, 9, 28, 20, 29, tzinfo=BRUXELLES))


class ReponsesMieuxRelieesTests(MediaMixin, TestCase):
    def setUp(self):
        self.dossier = make_dossier(make_user("owner"), name="ACMEBAT")
        self.dossier.alias_courriel = ALIAS
        self.dossier.save()

    def importer(self, octets):
        return importer_couches(self.dossier, analyser_en_couches(octets, ignorer=[ALIAS]))

    def test_la_citation_dans_la_suite_exchange_relie_la_reponse(self):
        (med,) = self.importer(mise_en_demeure_directe())
        (reponse,) = self.importer(reponse_coupee_par_exchange())
        self.assertEqual(reponse.reponse_a, med)

    def test_le_cite_direct_absent_on_relie_au_plus_proche_ancetre_present(self):
        (med,) = self.importer(mise_en_demeure_directe())
        (reponse,) = self.importer(reponse_citant_deux_messages())
        self.assertEqual(reponse.reponse_a, med)

    def test_le_cite_direct_arrive_ensuite_et_le_lien_se_recentre(self):
        self.importer(mise_en_demeure_directe())
        (reponse,) = self.importer(reponse_citant_deux_messages())
        (paul,) = self.importer(reponse_de_paul())
        reponse.refresh_from_db()
        self.assertEqual(reponse.reponse_a, paul)

    def test_les_destinataires_sont_gardes_et_affiches(self):
        (reponse,) = self.importer(reponse_citant_deux_messages())
        self.assertIn("info@mediation.example", reponse.destinataires)
        self.assertIn("contact@acme-bat.example", reponse.destinataires)
        self.client.force_login(self.dossier.owner)
        page = self.client.get(reverse("event_detail", args=[reponse.pk])).content.decode()
        self.assertIn("info@mediation.example", page)
        self.assertIn("Destinataires", page)


class CommandeRecalculerTests(MediaMixin, TestCase):
    def test_recalculer_relit_la_suite_exchange_et_relie(self):
        dossier = make_dossier(make_user("owner"), name="ACMEBAT")
        dossier.alias_courriel = ALIAS
        dossier.save()
        importer(dossier, analyser(mise_en_demeure_directe()))
        reponse = importer(dossier, analyser(reponse_coupee_par_exchange()))
        # Comme avant la lecture de la suite Exchange : ni citation, ni lien, ni destinataires.
        CourrielImporte.objects.filter(evenement=reponse).update(cite_de="x", cite_le="2020-01-01T00:00Z", cite_objet="x")
        Event.objects.filter(pk=reponse.pk).update(reponse_a=None, destinataires="")
        call_command("relier_courriels", "--appliquer", "--recalculer", stdout=StringIO(), stderr=StringIO())
        reponse.refresh_from_db()
        self.assertEqual(reponse.reponse_a.title, "Mise en demeure")
        self.assertIn("contact@acme-bat.example", reponse.destinataires)


class EncodageExchangeTests(SimpleTestCase):
    def test_une_suite_utf8_declaree_ascii_se_lit_correctement(self):
        """n° 12 et 20 du dossier réel : Exchange déclare `us-ascii` des octets UTF-8 ;
        lus tels quels, « De : » et « Envoyé : » deviennent illisibles."""
        from email.mime.application import MIMEApplication  # noqa: F401
        from events.courriels import corps_en_texte, lire

        m = courriel("RE: sdb", "Voir ci-dessous.", de=MOI, a=ACME_BAT)
        m.add_attachment(b"x", maintype="text", subtype="plain", filename="ATT00004.txt")
        brut = m.as_bytes()
        suite = ("De : ACME Bat <contact@acme-bat.example>\nEnvoyé : lundi 28 septembre 2026 20:29\n"
                 "Objet : Mise en demeure\n\nNous vous mettons en demeure.").encode("utf-8")
        import base64
        brut = brut.replace(b"Content-Type: text/plain; charset=\"utf-8\"; name=\"ATT00004.txt\"",
                            b"Content-Type: text/plain; charset=\"us-ascii\"; name=\"ATT00004.txt\"")
        brut = brut.replace(base64.b64encode(b"x"), base64.b64encode(suite))
        texte = corps_en_texte(lire(brut))
        self.assertIn("Envoyé : lundi 28 septembre 2026 20:29", texte)
        self.assertNotIn("�", texte)


class BlocAvecLignesVidesTests(SimpleTestCase):
    def test_une_ligne_vide_entre_de_et_envoye_ne_coupe_pas_le_bloc(self):
        """n° 12 du dossier réel : « De : … », ligne vide, « Envoyé : … »."""
        from events.liens import citations

        texte = ("Voir ci-dessous.\n\nDe : ACME Bat <contact@acme-bat.example>\n\n"
                 "Envoyé : lundi 28 septembre 2026 20:29\nÀ : Jean <jdupont@exemple.be>\n"
                 "Objet : Mise en demeure\n\nNous vous mettons en demeure.")
        (couche,) = analyser_en_couches(courriel("RE: Mise en demeure", texte, de=MOI, a=ACME_BAT).as_bytes(),
                                        ignorer=[ALIAS])
        (cite,) = citations(couche)
        self.assertEqual(cite.moment, datetime.datetime(2026, 9, 28, 20, 29, tzinfo=BRUXELLES))

    def test_un_bloc_suivi_d_un_paragraphe_reste_un_bloc_court(self):
        from events.courriels import _bloc_de_transfert

        texte = ("De : A <a@x.be>\nEnvoyé : lundi 28 septembre 2026 20:29\nObjet : X\n\n"
                 "Date : le paragraphe suivant n'est pas un en-tête de ce bloc.")
        _commentaire, champs, suite = _bloc_de_transfert(texte)
        self.assertEqual(champs["objet"], "X")


class TransfertSansEnTeteTests(MediaMixin, TestCase):
    """n° 12 du dossier réel : « FW: RE: … » dont le texte transféré a perdu l'en-tête
    « De : … » du courriel de l'architecte ; seul son contenu reste."""

    def setUp(self):
        self.dossier = make_dossier(make_user("owner"), name="ACMEBAT")
        self.dossier.alias_courriel = ALIAS
        self.dossier.save()

    AVIS = ("Après examen des photographies, les jonctions entre le sol et les murs ne respectent "
            "pas les règles de l'art : le joint souple manque, la pente est insuffisante et "
            "l'étanchéité sous carrelage n'a pas été remontée sur les parois comme prévu au cahier.")

    def envoyer_le_transfert(self):
        """Ce qui arrive à l'alias : « TR: FW: RE: sdb », le bloc du transfert à
        l'entrepreneur, le mot, l'avis de l'architecte SANS son en-tête, puis la
        citation du courriel d'origine envoyé à l'architecte."""
        texte = "\n".join([
            "Pour le dossier.", "",
            "De : Jean <jdupont@exemple.be>", "Envoyé : jeudi 27 août 2026 14:13",
            "À : ACME Bat <contact@acme-bat.example>", "Objet : FW: RE: sdb", "",
            "Veuillez trouver ci-dessous l'avis de mon architecte.", "",
            self.AVIS, "",
            "De : Jean <jdupont@exemple.be>", "Envoyé : jeudi 27 août 2026 10:52",
            "À : Architecte <archi@exemple.be>", "Objet : sdb", "",
            "Que pensez-vous des jonctions ?",
        ])
        octets = courriel("TR: FW: RE: sdb", texte, de=MOI, recu="Thu, 27 Aug 2026 13:00:00 +0000",
                          message_id="<env@x>").as_bytes()
        return importer_couches(self.dossier, analyser_en_couches(octets, ignorer=[ALIAS]))

    def importer_l_avis(self):
        return importer(self.dossier, analyser(courriel(
            "RE: sdb", self.AVIS, de="Architecte <archi@exemple.be>", a=MOI,
            recu="Thu, 27 Aug 2026 11:52:00 +0000", message_id="<avis@x>").as_bytes()))

    def test_a_l_import_le_transfert_se_relie_a_l_avis_et_non_a_l_origine(self):
        avis = self.importer_l_avis()
        crees = self.envoyer_le_transfert()
        transfert = [e for e in crees if e.title.startswith("FW")][0]
        transfert.refresh_from_db()
        self.assertEqual(transfert.transfert_de, avis)

    def test_le_rattrapage_relie_un_transfert_existant_par_son_contenu(self):
        avis = self.importer_l_avis()
        transfert = [e for e in self.envoyer_le_transfert() if e.title.startswith("FW")][0]
        Event.objects.filter(pk=transfert.pk).update(transfert_de=None)
        call_command("relier_courriels", "--appliquer", stdout=StringIO(), stderr=StringIO())
        transfert.refresh_from_db()
        self.assertEqual(transfert.transfert_de, avis)

    def test_un_texte_court_ne_suffit_pas_a_prouver(self):
        from events.import_courriels import transfere_par_contenu

        court = importer(self.dossier, analyser(courriel(
            "Devis", "Merci, bien reçu.", de=ACME_BAT, a=MOI,
            recu="Thu, 27 Aug 2026 09:00:00 +0000", message_id="<court@x>").as_bytes()))
        transfert = importer(self.dossier, analyser(courriel(
            "FW: Devis", "Merci, bien reçu.", de=MOI, a=ACME_BAT,
            recu="Thu, 27 Aug 2026 12:00:00 +0000", message_id="<fw2@x>").as_bytes()))
        self.assertIsNone(transfere_par_contenu(transfert, "Merci, bien reçu."))
        self.assertNotEqual(court, transfert)


def photos_sans_objet():
    """Un courriel envoyé sans objet : l'import l'intitule « (sans objet) »."""
    return courriel("", "Voici les photos du carrelage.", de=MOI, a=ACME_BAT,
                    recu="Wed, 07 Oct 2026 08:02:00 +0000", message_id="<photos@exemple.be>").as_bytes()


def reponse_aux_photos_sans_objet():
    """Sa réponse : le bloc cité a une ligne « Objet : » vide."""
    texte = ("Bien reçu, je passe demain.\n\n"
             "De : Jean <jdupont@exemple.be>\n"
             "Envoyé : mercredi 7 octobre 2026 10:00\n"
             "À : ACME Bat <contact@acme-bat.example>\n"
             "Objet : \n\n"
             "Voici les photos du carrelage.\n")
    return courriel("RE: ", texte, de=ACME_BAT, a=MOI,
                    recu="Wed, 07 Oct 2026 12:30:00 +0000", message_id="<rep-photos@acme-bat.example>").as_bytes()


class CourrielSansObjetTests(MediaMixin, TestCase):
    """« (sans objet) » est le titre que l'import donne à un objet vide : pour les
    comparer, les deux sont le même objet — vide."""

    def setUp(self):
        self.dossier = make_dossier(make_user("owner"), name="ACMEBAT")
        self.dossier.alias_courriel = ALIAS
        self.dossier.save()

    def importer(self, octets):
        return importer_couches(self.dossier, analyser_en_couches(octets, ignorer=[ALIAS]))

    def test_objet_normalise(self):
        self.assertEqual(objet_normalise("(sans objet)"), "")
        self.assertEqual(objet_normalise("RE: (sans objet)"), "")

    def test_la_reponse_se_relie_au_courriel_sans_objet(self):
        (photos,) = self.importer(photos_sans_objet())
        self.assertEqual(photos.title, "(sans objet)")
        (reponse,) = self.importer(reponse_aux_photos_sans_objet())
        self.assertEqual(reponse.reponse_a, photos)

    def test_dans_l_autre_ordre_aussi(self):
        (reponse,) = self.importer(reponse_aux_photos_sans_objet())
        (photos,) = self.importer(photos_sans_objet())
        reponse.refresh_from_db()
        self.assertEqual(reponse.reponse_a, photos)

    def test_il_ne_manque_pas(self):
        from events.fils import courriels_manquants
        self.importer(photos_sans_objet())
        self.importer(reponse_aux_photos_sans_objet())
        self.assertEqual(courriels_manquants(self.dossier), [])
