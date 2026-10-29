"""Import de courriels : un courriel transféré à l'alias d'un dossier en devient un évènement."""

import datetime
import shutil
import tempfile
import urllib.parse
from email.message import EmailMessage
from email.utils import parsedate_to_datetime
from io import StringIO
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from django.core import mail
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from events.courriels import analyser, date_en_toutes_lettres
from events.import_courriels import dossier_destinataire, importer
from events.models import CourrielImporte, DossierAccess, Event, Journal
from events.tests.factories import grant, make_dossier, make_user

BRUXELLES = ZoneInfo("Europe/Brussels")
ALIAS = "dossier-sdb-k7f3@exemple.be"


def courriel(sujet, texte, *, de="a@exemple.be", a=ALIAS, recu="Wed, 9 Sep 2026 10:00:00 +0000",
             html=None, pieces=(), joint=None, message_id="<transfert@exemple.be>"):
    m = EmailMessage()
    m["Received"] = "from mx.exemple.be by boite.exemple.be; %s" % recu
    m["From"], m["To"], m["Subject"], m["Message-ID"] = de, a, sujet, message_id
    m["Date"] = recu
    m.set_content(texte)
    if html:
        m.add_alternative(html, subtype="html")
    for nom, contenu, maintype, soustype in pieces:
        m.add_attachment(contenu, maintype=maintype, subtype=soustype, filename=nom)
    if joint is not None:
        m.add_attachment(joint)
    return m


def original():
    o = EmailMessage()
    o["Received"] = "from smtp.entreprise.be by mx.exemple.be; Tue, 25 Aug 2026 12:32:10 +0000"
    o["Received"] = "from poste by smtp.entreprise.be; Tue, 25 Aug 2026 12:32:05 +0000"
    o["From"], o["To"] = "ACME Bat <contact@acme-bat.example>", "jdupont@exemple.be"
    o["Subject"], o["Message-ID"] = "Réponse 2e acompte", "<original-42@acme-bat.example>"
    o["Date"] = "Tue, 25 Aug 2026 14:31:00 +0200"
    o.set_content("Bonjour,\n\nNous contestons.\n\nCordialement")
    o.add_attachment(b"%PDF facture", maintype="application", subtype="pdf", filename="facture.pdf")
    return o


class AnalyseTests(TestCase):
    def test_transfert_en_piece_jointe(self):
        a = analyser(courriel("TR: Réponse 2e acompte", "Pour le dossier", joint=original()).as_bytes())
        self.assertEqual(a.forme, "joint")
        self.assertEqual(a.titre, "Réponse 2e acompte")
        # Le Received le plus récent de l'original : 12:32:10 UTC = 14:32:10 à Bruxelles.
        self.assertEqual(a.moment, datetime.datetime(2026, 8, 25, 14, 32, 10, tzinfo=BRUXELLES))
        self.assertEqual(a.expediteur, "ACME Bat <contact@acme-bat.example>")
        self.assertIn("Nous contestons.", a.texte)
        self.assertEqual(a.commentaire, "Pour le dossier")
        self.assertEqual([p.nom for p in a.pieces], ["facture.pdf"])
        self.assertEqual(a.message_id, "<original-42@acme-bat.example>")
        self.assertIn(b"original-42@acme-bat.example", a.eml)

    def test_transfert_dans_le_texte_par_outlook(self):
        blocs = {
            "fr": "De : ACME Bat <contact@acme-bat.example>\nEnvoyé : mardi 25 août 2026 14:31\nÀ : Jean\nObjet : Réponse 2e acompte",
            "en": "From: ACME Bat <contact@acme-bat.example>\nSent: Tuesday, August 25, 2026 2:31 PM\nTo: Jean\nSubject: Réponse 2e acompte",
            "nl": "Van: ACME Bat <contact@acme-bat.example>\nVerzonden: dinsdag 25 augustus 2026 14:31\nAan: Jean\nOnderwerp: Réponse 2e acompte",
            "gmail": "---------- Forwarded message ---------\nDe : ACME Bat <contact@acme-bat.example>\nDate: mar. 25 août 2026 à 14:31\nSubject: Réponse 2e acompte\nTo: Jean",
        }
        for langue, bloc in blocs.items():
            with self.subTest(langue=langue):
                texte = "À verser au dossier.\n\n" + bloc + "\n\nBonjour,\nNous contestons."
                a = analyser(courriel("TR: Réponse 2e acompte", texte).as_bytes())
                self.assertEqual(a.forme, "texte")
                self.assertEqual(a.titre, "Réponse 2e acompte")
                self.assertEqual(a.moment, datetime.datetime(2026, 8, 25, 14, 31, tzinfo=BRUXELLES))
                self.assertFalse(a.date_approchee)
                self.assertIn("contact@acme-bat.example", a.expediteur)
                self.assertEqual(a.texte, "Bonjour,\nNous contestons.")
                self.assertEqual(a.commentaire, "À verser au dossier.")

    def test_transfert_dans_le_texte_par_samsung_email(self):
        # Samsung Email (Android) aplatit son texte brut : plus aucun retour à la
        # ligne entre les champs du bloc. Seul le HTML garde une ligne par champ
        # (transfert réel du 27/10, lu comme un courriel direct de Jean).
        texte = ("À verser au dossier.\r\n-------- Message d'origine --------De : ACME Bat "
                 "<contact@acme-bat.example> Date : 25/08/26  14:31  (GMT+01:00) À : Jean "
                 "Objet : Réponse 2e acompte Bonjour,\xa0Nous contestons.")
        html = ("<div>À verser au dossier.</div><div>-------- Message d'origine --------</div>"
                "<div>De : ACME Bat &lt;contact@acme-bat.example&gt; </div>"
                "<div>Date : 25/08/26  14:31  (GMT+01:00) </div><div>À : Jean </div>"
                "<div>Objet : Réponse 2e acompte </div><div><br></div>"
                "<div>Bonjour,</div><div>Nous contestons.</div>")
        a = analyser(courriel("FW: Réponse 2e acompte", texte, html=html).as_bytes())
        self.assertEqual(a.forme, "texte")
        self.assertEqual(a.titre, "Réponse 2e acompte")
        self.assertEqual(a.moment, datetime.datetime(2026, 8, 25, 14, 31, tzinfo=BRUXELLES))
        self.assertFalse(a.date_approchee)
        self.assertIn("contact@acme-bat.example", a.expediteur)
        self.assertEqual(a.texte, "Bonjour,\nNous contestons.")
        self.assertEqual(a.commentaire, "À verser au dossier.")

    def test_une_date_a_deux_chiffres_d_annee(self):
        # « 27/10/26  11:25  (GMT+01:00) » : Samsung Email n'écrit que deux chiffres.
        self.assertEqual(date_en_toutes_lettres("27/10/26  11:25  (GMT+01:00)"),
                         datetime.datetime(2026, 10, 27, 11, 25))

    def test_un_courriel_direct_garde_sa_date_de_reception(self):
        a = analyser(courriel("Devis", "Voici le devis.").as_bytes())
        self.assertEqual(a.forme, "direct")
        self.assertEqual(a.moment, datetime.datetime(2026, 9, 9, 12, 0, tzinfo=BRUXELLES))

    def test_un_transfert_sans_date_lisible_est_signale(self):
        texte = "De : X\nEnvoyé : un jour\nObjet : Devis\n\nTexte"
        a = analyser(courriel("TR: Devis", texte).as_bytes())
        self.assertTrue(a.date_approchee)
        self.assertEqual(a.moment, datetime.datetime(2026, 9, 9, 12, 0, tzinfo=BRUXELLES))

    def test_le_html_seul_devient_du_texte(self):
        m = EmailMessage()
        m["Received"] = "from a by b; Wed, 9 Sep 2026 10:00:00 +0000"
        m["From"], m["To"], m["Subject"] = "a@exemple.be", ALIAS, "HTML"
        m.set_content("<p>Premier&nbsp;paragraphe</p><p>J&#x27;ai <b>dit</b></p><script>x()</script>", subtype="html")
        a = analyser(m.as_bytes())
        self.assertEqual(a.texte, "Premier paragraphe\nJ'ai dit")

    def test_les_images_de_signature_sont_ignorees(self):
        m = courriel("Devis", "Voici.", pieces=[("devis.pdf", b"%PDF", "application", "pdf")])
        m.add_attachment(b"\x89PNG logo", maintype="image", subtype="png", disposition="inline", cid="<logo@x>")
        a = analyser(m.as_bytes())
        self.assertEqual([p.nom for p in a.pieces], ["devis.pdf"])


class MediaMixin:
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.media_root = tempfile.mkdtemp()
        cls.enterClassContext(override_settings(MEDIA_ROOT=cls.media_root))

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(cls.media_root, ignore_errors=True)


class ImportTests(MediaMixin, TestCase):
    def setUp(self):
        self.owner = make_user("owner")
        self.dossier = make_dossier(self.owner)
        self.dossier.alias_courriel = ALIAS
        self.dossier.save()

    def test_l_import_cree_l_evenement_ses_pieces_et_le_journal(self):
        m = courriel("TR: Réponse 2e acompte", "Pour le dossier", joint=original(),
                     pieces=[("virus.exe", b"MZ", "application", "octet-stream")])
        evenement = importer(self.dossier, analyser(m.as_bytes()))

        self.assertEqual(evenement.title, "Réponse 2e acompte")
        self.assertEqual((evenement.date, evenement.heure), (datetime.date(2026, 8, 25), datetime.time(14, 32, 10)))
        self.assertIn("Nous contestons.", evenement.description)
        noms = sorted(d.title for d in evenement.documents.all())
        self.assertEqual(noms, ["courriel-20260825-1432.eml", "courriel-20260825-1432.md", "facture.pdf"])
        md = evenement.documents.get(title__endswith=".md").file.read().decode()
        self.assertIn("contact@acme-bat.example", md)
        self.assertIn("virus.exe", md)  # pièce refusée, mais nommée
        entree = Journal.objects.get(action=Journal.Action.COURRIEL_IMPORTE)
        self.assertEqual(entree.details["refusees"][0][0], "virus.exe")
        self.assertEqual(Journal.objects.filter(action=Journal.Action.PIECE_AJOUTEE).count(), 3)

    def test_le_meme_courriel_n_est_importe_qu_une_fois(self):
        octets = courriel("TR: x", "y", joint=original()).as_bytes()
        self.assertIsNotNone(importer(self.dossier, analyser(octets)))
        self.assertIsNone(importer(self.dossier, analyser(octets)))
        self.assertEqual(Event.objects.count(), 1)
        self.assertEqual(CourrielImporte.objects.count(), 1)

    def test_le_texte_d_un_courriel_ne_devient_jamais_du_balisage(self):
        m = courriel("Devis", "<script>window.pwned=1</script>\n<img src=x onerror=alert(1)>")
        evenement = importer(self.dossier, analyser(m.as_bytes()))
        self.assertNotIn("<script", evenement.description)
        self.assertNotIn("<img", evenement.description)

    def test_le_dossier_se_trouve_par_son_alias(self):
        self.assertEqual(dossier_destinataire(["autre@x.be", ALIAS.upper()]), self.dossier)
        self.assertIsNone(dossier_destinataire(["autre@x.be"]))


class FausseBoite:
    def __init__(self, messages):
        self.messages = messages  # {id: octets}
        self.ranges = {}
        self.depuis = None
        self.categories = {}  # {id: [catégories Outlook]}

    def messages_recus(self, depuis):
        self.depuis = depuis
        return [{"id": i, "subject": "x", "receivedDateTime": "2026-10-14T13:14:00Z",
                 "from": {"emailAddress": {"name": "Avocate", "address": "avocate@exemple.be"}},
                 "categories": list(self.categories.get(i, []))}
                for i in list(self.messages)]

    def categoriser(self, identifiant, categories):
        self.categories[identifiant] = list(categories)

    def mime(self, identifiant):
        return self.messages[identifiant]

    def dossier(self, nom):
        return nom

    def deplacer(self, identifiant, dossier):
        self.ranges[identifiant] = dossier


class CommandeTests(MediaMixin, TestCase):
    def setUp(self):
        self.dossier = make_dossier(make_user("owner"), name="Litige SDB")
        self.dossier.alias_courriel = ALIAS
        self.dossier.save()

    def lancer(self, boite, *args):
        sortie = StringIO()
        with mock.patch("events.import_courriels.ClientGraph", return_value=boite):
            call_command("importer_courriels", *args, stdout=sortie, stderr=StringIO())
        return sortie.getvalue()

    def test_importe_et_range_dans_le_dossier_du_meme_nom(self):
        boite = FausseBoite({
            "1": courriel("TR: x", "y", joint=original()).as_bytes(),
            "2": courriel("Pub", "rien", a="inconnu@exemple.be", message_id="<pub@x>").as_bytes(),
        })
        sortie = self.lancer(boite)
        self.assertEqual(boite.ranges, {"1": "Litige SDB"})
        self.assertEqual(Event.objects.count(), 1)
        self.assertIn("1 importé(s), 0 déjà importé(s), 1 sans alias, 0 en échec", sortie)

    def test_un_courriel_sans_alias_n_est_ni_deplace_ni_cite(self):
        """La boîte peut servir à autre chose : ce qui ne vise aucun alias ne
        bouge pas, et son objet n'apparaît pas dans les journaux."""
        boite = FausseBoite({
            "2": courriel("Facture privée", "rien", a="inconnu@exemple.be", message_id="<pub@x>").as_bytes(),
        })
        sortie = self.lancer(boite)
        self.assertEqual(boite.ranges, {})
        self.assertNotIn("Facture privée", sortie)

    def test_ne_parcourt_que_les_dernieres_24_heures(self):
        boite = FausseBoite({})
        avant = timezone.now()
        self.lancer(boite)
        self.assertLessEqual(boite.depuis, avant - datetime.timedelta(hours=24) + datetime.timedelta(seconds=5))
        self.assertGreaterEqual(boite.depuis, avant - datetime.timedelta(hours=24) - datetime.timedelta(seconds=5))

    def test_l_essai_ne_cree_ni_ne_range_rien(self):
        boite = FausseBoite({"1": courriel("TR: x", "y", joint=original()).as_bytes()})
        self.lancer(boite, "--essai")
        self.assertEqual(boite.ranges, {})
        self.assertFalse(Event.objects.exists())

    def test_un_courriel_deja_importe_est_range_sans_doublon(self):
        octets = courriel("TR: x", "y", joint=original()).as_bytes()
        importer(self.dossier, analyser(octets))
        boite = FausseBoite({"1": octets})
        self.lancer(boite)
        self.assertEqual(boite.ranges, {"1": "Litige SDB"})
        self.assertEqual(Event.objects.count(), 1)

    def test_un_courriel_en_echec_reste_dans_la_boite_de_reception(self):
        boite = FausseBoite({"1": courriel("TR: x", "y", joint=original()).as_bytes()})
        with mock.patch("events.import_courriels.importer",
                        side_effect=ValueError("illisible")), \
                self.assertLogs("events.import_courriels", level="ERROR") as journaux:
            sortie = self.lancer(boite)
        self.assertEqual(boite.ranges, {})
        # Journalisé en ERROR avec la trace : Sentry le reçoit (rien ne remontait le 17/10).
        self.assertIn("illisible", "\n".join(journaux.output))
        self.assertIn("0 importé(s), 0 déjà importé(s), 0 sans alias, 1 en échec", sortie)

    def echouer(self, boite, *args):
        with mock.patch("events.import_courriels.importer", side_effect=ValueError("illisible")), \
                self.assertLogs("events.import_courriels", level="ERROR"):
            return self.lancer(boite, *args)

    def test_un_echec_previent_le_proprietaire_une_seule_fois(self):
        from events.alertes import CATEGORIE_ECHEC
        boite = FausseBoite({"1": courriel("TR: x", "y", joint=original()).as_bytes()})
        self.echouer(boite)
        (alerte,) = mail.outbox
        self.assertEqual(alerte.to, ["owner@example.test"])
        self.assertIn("Litige SDB", alerte.subject)
        self.assertIn("Avocate <avocate@exemple.be>", alerte.body)
        self.assertIn("ValueError", alerte.body)
        self.assertNotIn("illisible", alerte.body)  # le type, pas le message de l'exception
        self.assertEqual(boite.categories["1"], [CATEGORIE_ECHEC])
        self.echouer(boite)  # relève suivante : la catégorie retient que c'est signalé
        self.assertEqual(len(mail.outbox), 1)

    def test_retirer_la_categorie_redemande_une_alerte(self):
        boite = FausseBoite({"1": courriel("TR: x", "y", joint=original()).as_bytes()})
        self.echouer(boite)
        boite.categories["1"] = ["Rouge"]  # retirée à la main ; une autre catégorie reste
        self.echouer(boite)
        self.assertEqual(len(mail.outbox), 2)
        self.assertIn("Rouge", boite.categories["1"])

    def test_une_alerte_non_partie_n_est_pas_retenue(self):
        boite = FausseBoite({"1": courriel("TR: x", "y", joint=original()).as_bytes()})
        with mock.patch("events.alertes.send_mail", side_effect=OSError("smtp")), \
                self.assertLogs("events.alertes", level="ERROR"):
            self.echouer(boite)
        self.assertNotIn("1", boite.categories)  # réessayée à la relève suivante

    def test_l_essai_ne_previent_personne(self):
        boite = FausseBoite({"1": courriel("TR: x", "y", joint=original()).as_bytes()})
        # En essai rien n'est importé : l'échec doit venir de l'analyse.
        with mock.patch("events.import_courriels.analyser_en_couches", side_effect=ValueError("illisible")), \
                self.assertLogs("events.import_courriels", level="ERROR"):
            self.lancer(boite, "--essai")
        self.assertEqual(mail.outbox, [])
        self.assertEqual(boite.categories, {})

    def test_importe_apres_coup_le_courriel_perd_la_categorie(self):
        from events.alertes import CATEGORIE_ECHEC
        boite = FausseBoite({"1": courriel("TR: x", "y", joint=original()).as_bytes()})
        boite.categories["1"] = [CATEGORIE_ECHEC, "Rouge"]
        self.lancer(boite)
        self.assertEqual(boite.categories["1"], ["Rouge"])
        self.assertEqual(boite.ranges, {"1": "Litige SDB"})
        self.assertEqual(mail.outbox, [])

    def lancer_avec_erreurs(self, boite):
        sortie, erreurs = StringIO(), StringIO()
        with mock.patch("events.import_courriels.ClientGraph", return_value=boite), \
                self.assertLogs("events.import_courriels", level="ERROR") as journaux:
            call_command("importer_courriels", stdout=sortie, stderr=erreurs)
        return sortie.getvalue(), erreurs.getvalue(), "\n".join(journaux.output)

    def test_un_courriel_illisible_avant_le_tri_ne_bloque_pas_la_releve(self):
        """Audit du 2026-10-18 (M3) : un échec avant le tri (lecture Graph,
        en-tête tordu) arrêtait toute la relève, à chaque passage pendant 24 h."""
        boite = FausseBoite({
            "0": courriel("Facture privée", "rien", a="inconnu@exemple.be", message_id="<pub@x>").as_bytes(),
            "1": courriel("TR: x", "y", joint=original()).as_bytes(),
        })
        mime = boite.mime

        def mime_qui_echoue(identifiant):
            if identifiant == "0":
                raise ValueError("Facture privée de inconnu@exemple.be")
            return mime(identifiant)

        boite.mime = mime_qui_echoue
        sortie, erreurs, journaux = self.lancer_avec_erreurs(boite)
        self.assertEqual(boite.ranges, {"1": "Litige SDB"})
        self.assertEqual(Event.objects.count(), 1)
        self.assertIn("1 importé(s), 0 déjà importé(s), 0 sans alias, 1 en échec", sortie)
        # Ni objet ni adresse d'un courriel qui ne vise peut-être aucun dossier :
        # l'identifiant Graph (opaque) et le type de l'erreur seulement.
        for texte in (sortie, erreurs, journaux.split("Traceback")[0]):
            self.assertNotIn("Facture privée", texte)
            self.assertNotIn("inconnu@exemple.be", texte)
        self.assertIn("ValueError", erreurs)
        self.assertIn("Graph 0", erreurs)
        self.assertEqual(mail.outbox, [])  # aucun dossier connu : personne à prévenir

    def test_un_en_tete_illisible_ne_bloque_pas_la_releve(self):
        from events import import_courriels

        boite = FausseBoite({
            "0": courriel("TR: x", "y", joint=original(), message_id="<a@x>").as_bytes(),
            "1": courriel("TR: x", "y", joint=original()).as_bytes(),
        })
        adresses = import_courriels.adresses
        appels = []

        def adresses_qui_echouent(*args, **kwargs):
            appels.append(1)
            if len(appels) == 1:
                raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "en-tête tordu")
            return adresses(*args, **kwargs)

        with mock.patch("events.import_courriels.adresses", side_effect=adresses_qui_echouent):
            sortie, _, _ = self.lancer_avec_erreurs(boite)
        self.assertIn("1 en échec", sortie)
        self.assertEqual(list(boite.ranges), ["1"])

    def test_depuis_un_fichier_eml(self):
        chemin = Path(self.media_root) / "exemple.eml"
        chemin.write_bytes(courriel("TR: x", "y", joint=original()).as_bytes())
        sortie = StringIO()
        call_command("importer_courriels", "--fichier", str(chemin), "--dossier", str(self.dossier.pk),
                     stdout=sortie)
        self.assertEqual(Event.objects.get().title, "Réponse 2e acompte")

    def test_sans_configuration_la_commande_refuse(self):
        from django.core.management.base import CommandError

        with override_settings(GRAPH_TENANT_ID="", GRAPH_CLIENT_ID="", GRAPH_CLIENT_SECRET="", COURRIEL_BOITE=""):
            with self.assertRaisesMessage(CommandError, "Configuration incomplète"):
                call_command("importer_courriels", stdout=StringIO())


class AliasTests(TestCase):
    def setUp(self):
        self.owner = make_user("owner")
        self.dossier = make_dossier(self.owner)
        self.url = reverse("dossier_alias", args=[self.dossier.pk])

    def test_le_proprietaire_regle_l_alias_et_c_est_journalise(self):
        self.client.force_login(self.owner)
        self.client.post(self.url, {"alias_courriel": " Dossier-SDB@Exemple.be "})
        self.dossier.refresh_from_db()
        self.assertEqual(self.dossier.alias_courriel, "dossier-sdb@exemple.be")
        entree = Journal.objects.get()
        self.assertEqual(entree.details["changements"]["alias_courriel"], ["", "dossier-sdb@exemple.be"])

    def test_un_alias_deja_pris_est_refuse(self):
        autre = make_dossier(self.owner, name="Autre")
        autre.alias_courriel = "pris@exemple.be"
        autre.save()
        self.client.force_login(self.owner)
        self.client.post(self.url, {"alias_courriel": "pris@exemple.be"})
        self.dossier.refresh_from_db()
        self.assertIsNone(self.dossier.alias_courriel)

    def test_seul_le_proprietaire(self):
        ecrivain = make_user("ecrivain")
        grant(self.dossier, ecrivain, DossierAccess.Role.WRITE)
        self.client.force_login(ecrivain)
        self.assertEqual(self.client.post(self.url, {"alias_courriel": "x@exemple.be"}).status_code, 404)


class ReponseTests(TestCase):
    def test_une_reponse_qui_cite_le_message_precedent_n_est_pas_un_transfert(self):
        """Une réponse cite le message précédent sous le même bloc « De : …
        Envoyé : … » qu'un transfert. La prendre pour un transfert donnait à
        l'évènement la date, l'auteur et le texte du message *cité*."""
        texte = ("Voici ma réponse.\n\nDe : ACME Bat <contact@acme-bat.example>\n"
                 "Envoyé : mardi 25 août 2026 14:31\nÀ : Jean\nObjet : Devis\n\nMessage cité.")
        a = analyser(courriel("RE: Devis", texte, de="Jean <jdupont@exemple.be>").as_bytes())
        self.assertEqual(a.forme, "direct")
        self.assertEqual(a.titre, "RE: Devis")
        self.assertEqual(a.expediteur, "Jean <jdupont@exemple.be>")
        self.assertTrue(a.texte.startswith("Voici ma réponse."))

    def test_un_transfert_annonce_dans_le_texte_reste_un_transfert(self):
        texte = ("---------- Forwarded message ---------\nDe : X <x@x.be>\n"
                 "Date: mar. 25 août 2026 à 14:31\nSubject: Devis\nTo: Y\n\nTexte d'origine.")
        a = analyser(courriel("Devis", texte).as_bytes())
        self.assertEqual(a.forme, "texte")


EXEMPLES = Path(__file__).resolve().parents[2] / "exemples_courriels"


class VraisExemplesTests(TestCase):
    """Les vrais courriels déposés dans `exemples_courriels/` (non versionnés) :
    ignorés s'il n'y en a pas, lus un à un s'il y en a."""

    def test_chaque_exemple_se_lit_sans_erreur_et_sans_contresens(self):
        fichiers = sorted(EXEMPLES.glob("*.eml")) if EXEMPLES.is_dir() else []
        if not fichiers:
            self.skipTest("aucun exemple dans exemples_courriels/")
        for fichier in fichiers:
            with self.subTest(fichier=fichier.name):
                a = analyser(fichier.read_bytes())
                self.assertTrue(a.titre)
                self.assertIsNotNone(a.moment.tzinfo)
                self.assertIn("@", a.expediteur)
                if a.titre.lower().startswith(("re:", "re :")):
                    # Une réponse est le courriel lui-même, jamais le message qu'elle cite.
                    self.assertEqual(a.forme, "direct")


JOURS_FR = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
MOIS_FR = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
           "septembre", "octobre", "novembre", "décembre")


def transferer_en_piece_jointe(octets):
    """Ce qu'Outlook envoie à la boîte avec « Transférer en tant que pièce jointe »."""
    from email import message_from_bytes, policy

    enveloppe = courriel("TR: transfert", "Pour le dossier.", de="moi@exemple.be",
                         recu="Fri, 9 Oct 2026 08:00:00 +0000", message_id="<enveloppe@x>")
    enveloppe.add_attachment(message_from_bytes(octets, policy=policy.default))
    return enveloppe.as_bytes()


def transferer_dans_le_texte(octets):
    """Ce qu'Outlook (français) envoie avec un « Transférer » ordinaire : un bloc
    « De : … Envoyé : … » au-dessus du texte d'origine."""
    from events.courriels import corps_en_texte, lire

    o = lire(octets)
    envoye = parsedate_to_datetime(str(o["Date"])).astimezone(BRUXELLES)
    date = "%s %d %s %d %02d:%02d" % (JOURS_FR[envoye.weekday()], envoye.day,
                                      MOIS_FR[envoye.month - 1], envoye.year, envoye.hour, envoye.minute)
    texte = ("Pour le dossier.\n\n________________________________\n"
             "De : %s\nEnvoyé : %s\nÀ : %s\nObjet : %s\n\n%s"
             % (o["From"], date, o["To"], o["Subject"], corps_en_texte(o)))
    return courriel("TR: %s" % o["Subject"], texte, de="moi@exemple.be",
                    recu="Fri, 9 Oct 2026 08:00:00 +0000", message_id="<enveloppe2@x>").as_bytes()


class VraisExemplesTransferesTests(TestCase):
    """Les vrais exemples, transférés des deux façons à la boîte : l'enveloppe
    du transfert ne doit jamais l'emporter sur le courriel d'origine."""

    def setUp(self):
        self.fichiers = sorted(EXEMPLES.glob("*.eml")) if EXEMPLES.is_dir() else []
        if not self.fichiers:
            self.skipTest("aucun exemple dans exemples_courriels/")

    def test_transfere_en_piece_jointe(self):
        for fichier in self.fichiers:
            with self.subTest(fichier=fichier.name):
                direct = analyser(fichier.read_bytes())
                transfere = analyser(transferer_en_piece_jointe(fichier.read_bytes()))
                # Un exemple déjà transféré une fois reste lu à travers sa couche de texte.
                self.assertEqual(transfere.forme, "joint" if direct.forme == "direct" else direct.forme)
                self.assertEqual(transfere.titre, direct.titre)
                self.assertEqual(transfere.moment, direct.moment)  # la réception d'origine
                self.assertEqual(transfere.expediteur, direct.expediteur)
                self.assertEqual(sorted(p.nom for p in transfere.pieces), sorted(p.nom for p in direct.pieces))
                self.assertTrue(transfere.commentaire.startswith("Pour le dossier."))

    def test_transfere_dans_le_texte(self):
        for fichier in self.fichiers:
            with self.subTest(fichier=fichier.name):
                o = analyser(fichier.read_bytes())
                envoye = parsedate_to_datetime(str(lire_entete(fichier, "Date"))).astimezone(BRUXELLES)
                transfere = analyser(transferer_dans_le_texte(fichier.read_bytes()))
                self.assertEqual(transfere.forme, "texte")
                self.assertEqual(transfere.titre, o.titre)
                # Un transfert ordinaire ne porte que l'heure d'envoi, à la minute ; un
                # exemple déjà transféré se lit jusqu'à son origine, comme en direct.
                attendu = o.moment if o.forme == "texte" else envoye.replace(second=0, microsecond=0)
                self.assertEqual(transfere.moment, attendu)
                self.assertIn(o.expediteur.split("<")[-1].rstrip(">").lower(), transfere.expediteur.lower())
                self.assertTrue(transfere.commentaire.startswith("Pour le dossier."))
                self.assertTrue(transfere.texte.startswith(o.texte[:40].strip()))


def lire_entete(fichier, nom):
    from events.courriels import lire

    return lire(fichier.read_bytes())[nom]


class TransfertDeTransfertTests(TestCase):
    """Le courriel d'origine, même enfoui sous deux transferts."""

    def test_en_piece_jointe_deux_fois(self):
        milieu = courriel("TR: Réponse", "Pour l'avocate", joint=original(), message_id="<milieu@x>")
        a = analyser(courriel("TR: TR: Réponse", "Pour le dossier", joint=milieu).as_bytes())
        self.assertEqual(a.titre, "Réponse 2e acompte")
        self.assertEqual(a.moment, datetime.datetime(2026, 8, 25, 14, 32, 10, tzinfo=BRUXELLES))
        self.assertEqual(a.commentaire, "Pour le dossier\n\nPour l'avocate")
        self.assertEqual([p.nom for p in a.pieces], ["facture.pdf"])

    def test_dans_le_texte_deux_fois(self):
        texte = ("Pour le dossier.\n\nDe : Moi <moi@x.be>\nEnvoyé : mercredi 7 octobre 2026 18:38\n"
                 "À : Avocate\nObjet : TR: Mise en demeure\n\nBonjour Maître.\n\n"
                 "De : ACME Bat <contact@acme-bat.example>\nEnvoyé : lundi 28 septembre 2026 20:29\n"
                 "À : Moi\nObjet : Mise en demeure\n\nMonsieur Dupont,")
        a = analyser(courriel("TR: TR: Mise en demeure", texte).as_bytes())
        self.assertEqual(a.titre, "Mise en demeure")
        self.assertEqual(a.moment, datetime.datetime(2026, 9, 28, 20, 29, tzinfo=BRUXELLES))
        self.assertIn("contact@acme-bat.example", a.expediteur)
        self.assertEqual(a.texte, "Monsieur Dupont,")
        self.assertEqual(a.commentaire, "Pour le dossier.\n\nBonjour Maître.")


class ClientGraphBoiteTests(TestCase):
    def test_ne_demande_que_les_courriels_recus_depuis_la_date_donnee(self):
        from events.graph import ClientGraph

        client = ClientGraph("t", "c", "s", "hello@exemple.be")
        depuis = datetime.datetime(2026, 10, 8, 12, 30, tzinfo=datetime.timezone.utc)
        with mock.patch.object(ClientGraph, "_appel", return_value={"value": []}) as appel:
            list(client.messages_recus(depuis))
        url = urllib.parse.unquote_plus(appel.call_args.args[1])
        self.assertIn("$filter=receivedDateTime ge 2026-10-08T12:30:00Z", url)
        self.assertIn("/mailFolders/inbox/messages", url)
