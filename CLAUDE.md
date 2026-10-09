# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

**Chronos** (named SDBTimeline until 2026-10-09 — the older design docs and the
first migrations still say so) is a single-app Django 6.1 site that holds the chronology of a real bathroom-renovation
legal dispute: quotes, invoices, photographs of defective work, an architect's opinion, consumer-body
correspondence, formal demand letters. The database contains the **only copy** of that evidence —
24 events and 49 documents. Treat data-touching operations accordingly: back up `db.sqlite3` and
`media/` before any migration, and prefer failing loudly over guessing when an operation cannot
positively identify what it is about to modify.

The interface is **French, Dutch and English** (since 2026-10-08). French is the *source*
language: every msgid is the French text, written in the code as before. See *Architecture:
translations* before adding or changing any user-visible string.

## Commands

The project is Windows-first locally. `.venv` was built with the interpreter at
`C:\Users\Renaud\AppData\Local\Python\pythoncore-3.14-64\python.exe` (Python 3.14.4) — a previous
`.venv` pointed at a path from another machine and was unusable, so always invoke the venv's own
Python explicitly rather than relying on a `python` on PATH.

**`DJANGO_DEBUG=True` is now required for every local command.** `DEBUG` no longer defaults
to `True` (it used to fail open), and without `DEBUG` the settings refuse to start on the
development `SECRET_KEY`. Set it once per shell:

```powershell
$env:DJANGO_DEBUG="True"
.\.venv\Scripts\python.exe manage.py runserver
.\.venv\Scripts\python.exe manage.py test events          # whole suite
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run   # must say "No changes detected"
```

Without it you get `ImproperlyConfigured: DJANGO_SECRET_KEY doit être défini…` — that is the
intended fail-closed behaviour, not a broken checkout. Setting `DJANGO_SECRET_KEY` instead also
works, and is what production must do.

**Migration check that actually catches things.** Two commonly-used checks are blind to an
unapplied migration, and the combination has already cost this project a broken login page
(`no such table: account_emailaddress`, because allauth's nine migrations were never applied):

- `makemigrations --check` only detects migrations that have not been **generated**. It says
  "No changes detected" while nine generated migrations sit unapplied.
- `showmigrations events` and `migrate events` are scoped to one app. A third-party app added
  to `INSTALLED_APPS` — allauth here — brings its own migrations that an app-scoped command
  never touches.
- The test suite cannot catch it either: it builds a fresh database and applies everything.

After adding any app to `INSTALLED_APPS`, run **`manage.py migrate`** with no app label, and
verify with **`manage.py showmigrations`** with no app label — the count of `[ ]` lines must be
zero.

Run one module, class, or single test:

```powershell
.\.venv\Scripts\python.exe manage.py test events.tests.test_views
.\.venv\Scripts\python.exe manage.py test events.tests.test_views.ViewAccessTests
.\.venv\Scripts\python.exe manage.py test events.tests.test_views.ViewAccessTests.test_timeline_hidden_from_stranger -v 2
```

Tests use the MD5 password hasher (`config/settings.py`, only when `sys.argv[1] == "test"`): PBKDF2
was 97 % of the suite's time (14 min → 13 s). A test checks it does not leak outside `manage.py test`.

There is no linter, formatter, or front-end build configured. `requirements.txt` lists only the
direct dependencies; everything else is transitive.

### Backups

**Before any migration, run a backup** — it replaced the hand-made `*.backup-*` copies, deleted on
2026-10-08 after checking that every file they held was still in `media/`:

```powershell
.\scripts\sauvegarde.ps1                       # or: manage.py sauvegarder [--destination D] [--garder N]
```

`manage.py sauvegarder` writes `sauvegardes/AAAAMMJJ-HHMMSS/` (SQLite backup API snapshot +
full copy of `media/` + `manifeste.json`), **re-reads it** (`PRAGMA integrity_check`, SHA-256 of
every file against the source) and only then renames it from `.partiel`. Rotation keeps 14
(`SAUVEGARDE_GARDER`) and only ever counts or deletes directories named exactly
`\d{8}-\d{6}` — never `.partiel`, never anything else in the folder.
The Windows task **"Chronos - sauvegarde"** runs the script daily at 20:00 (catches up at
next boot); results append to `sauvegardes/sauvegarde.log`.

Its tests use `TransactionTestCase`, not `TestCase`, and must stay that way: SQLite's backup API
waits forever on a connection with an uncommitted write, which is exactly what `TestCase`'s
per-test transaction is. The command refuses to run inside an atomic block for the same reason.

## Architecture: the authorization model

This is the part that needs several files to understand, and the part where a mistake is expensive.

**Data shape.** `Dossier` (a case file) → `Event` → `Document`. A `Dossier` has an `owner`
(`on_delete=PROTECT` — deleting the owner of an evidence file must be refused, not cascade) and any
number of `DossierAccess` rows granting another user `Role.READ` or `Role.WRITE`
(`on_delete=CASCADE`, unique per `(dossier, user)`).

**Four invariants. Violating any of them is how a confidentiality leak gets introduced:**

1. **The owner has NO `DossierAccess` row.** Their rights come from `Dossier.owner`. Two sources of
   truth would drift, so there is only one. The consequence is that every rights query must read
   "owner **or** access", never access alone.
2. **Never query `DossierAccess` directly** from a view, helper or template. Go through
   `Dossier.objects.accessible_by(user)` or `Dossier.objects.writable_by(user)`
   (`DossierQuerySet` in `events/models.py`), which already encode invariant 1.
3. **Refusals are `Http404`, never `PermissionDenied`.** A 403 would confirm that a dossier exists.
   This applies to read refusals, write refusals, and unknown ids alike — a reader attempting a
   write-protected view gets 404, not 403.
4. **`Document` carries its own `dossier` FK, and rights derive from it — never from an event.**
   A document may illustrate several events (`Document.events` is many-to-many), so "*the*"
   event's dossier no longer exists; picking one at random would open a leak the moment a
   document were linked across two cases. This **reverses spec decision D3**, which was correct
   only while a document had exactly one event.

   A document is also stored **once per dossier**: `UniqueConstraint(dossier, content_hash)`.
   Uploading the same bytes onto a second event of the same dossier reuses the stored file and
   adds a link. The same bytes in a **different** dossier are *not* a duplicate — a separate
   row and a separate file. Two cases never share bytes, so a deletion in one can never reach
   into the other.

   Removing a document from an event is `document_detach`, not a delete: the link goes, and the
   row and file go only when the last link does. The event comes from the URL, never inferred.
   **Django does not delete files when a row disappears** — the view removes the stored file
   explicitly. (Four unreferenced `.jpg` files from before this work still sit in `media/`.)

   Deleting an event (`event_delete`, `share_event_delete`, both through `supprimer_evenement`
   in `events/views.py`) applies the same rule to each of its documents: kept if another event
   still uses it, deleted with its file otherwise. Files are removed in `transaction.on_commit`,
   never before the rows are gone for good. GET only shows the confirmation (it lists which
   documents go and which stay); the deletion is POST. The journal keeps the whole event
   (`instantane`) and each document's name and hash. Its `CourrielImporte` row goes too, so a
   mail imported by mistake can be imported again.

5. **Only the owner shares.** Granting access, inviting an address and minting a share link all
   go through `get_dossier_for_owner`, never `get_dossier_for_write`. A `Role.WRITE` collaborator
   can add evidence; they cannot widen the circle that reads it. The owner also chooses a share
   link's role and its validity (unlimited is allowed) — `events/share_views.py` is a separate
   path that never reads `request.user` and bounds every query to `lien.dossier`.

**`events/permissions.py` is the single gate.** Six helpers — `get_dossier_for_read` /
`_for_write`, `get_event_for_read` / `_for_write`, `get_document_for_read` / `_for_write` — each
returning the object or raising `Http404`. Views call these; views do not build rights queries
themselves. When adding a view, route it through one of these six or extend that module; do not
inline a filter.

Each helper makes **one** rights-filtered query, and this is not cosmetic. Loading the object and
then checking rights reports `No Document matches…` for an id that does not exist and
`No Event matches…` for one that is merely forbidden — two messages distinguishable under `DEBUG`,
which is an existence oracle on an evidence set. A single filtered query makes both cases
identical. Never write `get_object_or_404(Model…)` followed by a guard whose return value you
discard.

## Architecture: authentication

`django-allauth >= 65, < 66` — the floor is **not** negotiable. Version 65.0.0 added support for
Django's `LoginRequiredMiddleware`, decorating allauth's own views with `login_not_required`. On an
older allauth the login page would redirect to itself.

- `LoginRequiredMiddleware` sits immediately after `AuthenticationMiddleware`, so **everything is
  private by default** and exemptions are explicit. `AccountMiddleware` must stay last.
- `AUTHENTICATION_BACKENDS` contains **only** allauth's backend. Do not add `ModelBackend` for admin
  username login: allauth's docs warn against it because the admin login view is not rate-limited.
- **Signup is open, and invitations exist alongside it** (the owner chose both).
  `events/adapters.py` returns `True` from `is_open_for_signup`. Anyone may create an account;
  that account sees **nothing** until a dossier owner grants it access, so an open signup is not
  an access decision. Two other routes in: an `Invitation` (an emailed token that grants
  `READ` or `WRITE` on acceptance, with an expiry) and a `ShareLink` (an anonymous token, served
  by `events/share_views.py`). **Only the owner may create either one**, and write access never
  carries the right to share — see invariant 5 below.
- **`/accounts/email/` is shadowed to 404** in `config/urls.py`, registered *before*
  `include("allauth.urls")`. That view would otherwise let any authenticated user attach arbitrary
  unconfirmed email addresses to their account. **This protection depends on URL declaration order** —
  if you reorder `config/urls.py` and move the allauth include above the shadow, the hole reopens.
  A test in `events/tests/test_auth.py` guards it; keep it.
- **`/admin/login/` is shadowed too**, by `admin_login_redirect` in `config/urls.py`, registered
  *before* `admin.site.urls`. Django decorates its admin login view with `@login_not_required`
  (`django/contrib/admin/sites.py`), so it was anonymously reachable *and* unthrottled, while
  `/accounts/login/` is rate-limited by allauth — two doors onto the same passwords, only one of
  which counts failures. The shadow redirects to `/accounts/login/?next=/admin/`. Like
  `/accounts/email/`, **it depends on declaration order**: move `admin.site.urls` above it and the
  door reopens. Tests in `events/tests/test_auth.py` guard it.
- allauth does **not** use `templates/base.html` — its templates go through its self-contained
  `allauth/layouts/*` chain. Changing the project base will not restyle the login pages.

## Front end

No npm, no bundler, no build step. Bootstrap 5.3.8, Bootstrap Icons, vis-timeline, FullCalendar,
Font Awesome, glightbox, Quill and flatpickr all load from CDNs. No jQuery.

**Four tabs per dossier** (owner's decision, 2026-10-09; `onglets` / `ONGLET_DE_LA_VUE` in
`events/views.py`, rendered by `_entete_dossier.html`): *Chronologie* (vis-timeline, `timeline.html`),
*Calendrier* (FullCalendar, `calendrier.html` — month / week / list / timeline are chosen in **its own
toolbar**, and `history.replaceState` keeps `?vue=calendrier-…` in the URL), *Liste* (server-rendered,
`vue_liste.html`) and *Documents*. The server-rendered month and week views (`?vue=mois`, `?vue=semaine`)
are no longer tabs but are still served: they are the calendar's no-JavaScript fallback (`<noscript>`
links), link to each other, and light the *Calendrier* tab. The list is the printable view — keep it and
the documents tab working without any script. All four use the same frame (`container-xxl`) so the header,
tabs and filters never move between tabs; `base.html` reserves the scrollbar gutter for the same reason.

**Attachments field: one selection, and it is what gets sent.** `equiperChampFichiers`
(`_event_form_js.html`) keeps a single `DataTransfer` and copies it into the native
`<input name="documents" multiple>` on every change; the list on screen is built from that same
selection. Each file is checked on its own (extension, size, duplicate) and every refusal is
announced — a refused file never takes the valid ones with it. Extensions and size limit come from
the server (`contexte_des_pieces()` → `data-extensions`, `data-taille-max-ko`, `accept`), so they
cannot drift from `_save_documents`. A file dropped *outside* the drop zone is swallowed: the
browser would otherwise open it in place of the form. Without JavaScript the native multiple input
remains. **This replaced bootstrap-fileinput (and with it jQuery) on 2026-10-08**: it kept its own
file list beside the input's, and its « clear » button and its refusal of a `.exe` emptied the
input — *no* file was sent, not even valid ones — while the list still showed them. Verify such
behaviour in a browser with the POST intercepted (Playwright `route`), never against the live
database.

**FullCalendar 6.1.21, and its licence.** The page loads the *premium* bundle
(`fullcalendar-scheduler`), because the *Timeline* tab is a premium view. It runs under
`schedulerLicenseKey: "CC-Attribution-NonCommercial-NoDerivatives"`, which is valid for
**non-commercial use only** — the owner's decision on 2026-10-08, this being a private dispute.
Commercial use would need a paid licence or the removal of that tab; month, week and list are MIT.
The theme is `themeSystem: "bootstrap5"` (plugin `@fullcalendar/bootstrap5`), which needs Bootstrap
Icons. 7.x exists but was two days old when this was written; moving to it changes the theme API.

**Coming back to where you were.** Links to the form and to the detail page carry
`?retour=<current view>`; `adresse_de_retour()` (`events/views.py`) follows it only if it is a URL
of this site (`url_has_allowed_host_and_scheme`) and, on a share link, only under that link's own
prefix. The form keeps it in a hidden `retour` field; Cancel and the redirect after saving use it.

**One modal for every view** (`_modal_evenement.html` + `_detail_evenement_js.html`). Clicking an
event anywhere opens its detail; links carry `data-voir-evenement` and point at a real page
(`event_detail`), so a ctrl-click or a browser without JavaScript gets the full page instead. The
form opens in the same modal (`_formulaire_modal_js.html`, writers only): the chronology updates its
items in place after a save, the calendar reloads. Its width follows its content (`elargir` in `_detail_evenement_js.html`):
`modal-lg` for a short event, `modal-xl` (up to 90 vw / 1 500 px above 1 400 px) for an event with
documents or long text, a PDF preview, or the form; full screen below `lg`. The edit button is a `btn-outline-primary` link
with an inline SVG pencil and an `aria-label`, not a text button.

**Every external resource must carry an `integrity` hash and `crossorigin="anonymous"`.** This site
serves legal evidence; a CDN compromise injecting script is in scope. The current hashes have been
verified against the bytes the CDNs actually serve. When adding a resource, compute its hash rather
than omitting the attribute:

```bash
curl -s -o f https://cdn.example/lib.js && openssl dgst -sha384 -binary f | openssl base64 -A
```

`templates/base.html` is the shared project base and is deliberately minimal — the navigation and
visual design belong to a later lot (see the backlog). `events/templates/events/` holds the
dossier list, the timeline, and the event form.

## Architecture: dates and the current language

**Every date shown to a user follows the current language — never a format written in the code.**
The site serves French, Dutch and English (`fr`, `nl`, `en-gb`), so nothing that
touches dates may assume French. Concretely:

- **Python:** `django.utils.formats.date_format` / `time_format` with a *named* format
  (`SHORT_DATE_FORMAT`, `TIME_FORMAT`, `YEAR_MONTH_FORMAT`…), never `strftime("%d/%m/%Y")`. Month
  and day names come from `date_format(…, "F")` / `"l"` (`events/calendrier.py`), never a list.
  Phrases around dates (`periode_affichee`: « du … au … ») go through `gettext`, French msgids.
- **Templates:** `|date:"SHORT_DATE_FORMAT"`, `|time:"TIME_FORMAT"` — named formats only.
- **JavaScript libraries** take their language from `{{ langue|json_script:"langue-courante" }}`
  in `base.html`, fed by `events.context_processors.langue` (`code`, `base`, `format_date`):
  FullCalendar (`locale: langue.code`, `locales-all` bundle), vis-timeline (`locale: langue.base`,
  otherwise it follows the *browser*) and flatpickr (per-language l10n file chosen in
  `_formulaire_js.html`).
- **`strftime` stays legitimate for wire formats only**: ISO in URL parameters, JSON for the
  calendars, backup directory names.

**Form fields: the value is ISO, the display is localised.** The HTML spec requires
`<input type="date">` to carry `2026-08-05` whatever the language; `EventForm`'s widgets force
`FORMAT_DATE_HTML` / `FORMAT_HEURE_HTML`. Without that, `fr-be` (the language until 2026-10-08) rendered `value="05.08.2026"`, the
browser rejected it, and editing an event showed an **empty** date. What the user sees is
flatpickr's `altInput`, in `SHORT_DATE_FORMAT` (`05/08/2026` in fr, `5-8-2026` in nl). A native
date input cannot do this: it displays in the *browser's* language. If flatpickr fails to load,
the native input remains — correct, in the browser's format. `disableMobile` keeps flatpickr on
phones, whose native picker would follow the device's language.

**Language codes.** `fr`, not `fr-be` (whose short format is `05.08.2026` — the owner chose
`05/08/2026` on 2026-10-08); `en-gb`, not `en` (`05/08/2026`, not the American `08/05/2026`,
ambiguous in a Belgian case file); `nl` (Django has no `nl_BE` formats). `500.html` is the one page
with `lang="fr"` hard-coded: Django renders it without a request.

## Architecture: translations

- **Choosing the language:** the switcher in the navbar (`templates/_selecteur_langue.html`, modelled
  on QuizOnline's) posts to `set_language`, which sets the `django_language` cookie; otherwise
  `LocaleMiddleware` reads `Accept-Language`; otherwise `fr`. `set_language` is wrapped in
  `login_not_required` in `config/urls.py` — the language is chosen before signing in and on share
  links. It validates `next` itself (no open redirect) and only acts on POST with a CSRF token.
- **The switcher is absent from `404.html`** (`{% block selecteur_langue %}` emptied): its `next`
  field echoes the requested path, and the refusal page for a forbidden dossier must stay
  byte-identical to the one for an id that does not exist (invariant 3). A test guards it.
- **Catalogues:** `locale/nl/` and `locale/en_GB/`, both `.po` (the source to edit) and `.mo`
  (compiled, committed — there is no build step). `fr` has no catalogue. A test fails if an entry is
  empty or fuzzy.
- **Workflow** (gettext is installed in `C:\Program Files\gettext-iconv\bin`, add it to `PATH`):

  ```powershell
  .\.venv\Scripts\python.exe manage.py makemessages -l nl -l en_GB --ignore=.venv --ignore=sauvegardes --ignore=media --ignore="*.backup-*" --ignore=docs --no-obsolete
  # translate the new entries in both .po files, then:
  .\.venv\Scripts\python.exe manage.py compilemessages -l nl -l en_GB --ignore=.venv
  ```
  Without `--ignore=.venv`, `compilemessages` also recompiles every catalogue inside the venv.

**Three pitfalls, each already hit once:**

1. **`makemessages` only recognises known function names.** `gettext_lazy as _lazy` silently
   dropped 66 strings from the catalogues. Use `_` (= `gettext`) inside functions and the full
   name `gettext_lazy` at module/class level (models, forms, admin).
2. **`{{ _("…%(x)s…") }}` doubles the `%`** in a template, so `%(x)s` is never substituted
   (« Aperçu de %rapport.pdf »). A message with a placeholder goes through
   `{% translate "…" as var %}` then `"{{ var|escapejs }}"`; the `.po` stores it with `%%`. A test
   rejects any `%%(` in rendered pages.
3. **JS strings inside templates** are `"{{ _("texte")|escapejs }}"` (no placeholder) — `escapejs`
   is mandatory. Runtime values are substituted in JS and still reach the DOM through
   `textContent` only.

Translations were written in one pass on 2026-10-08 (Belgian Dutch vocabulary: *stukken*,
*gebeurtenis*, *afmelden*); a native review of `nl` is still worth doing. 
## Architecture: the journal (history of changes)

`Journal` (`events/models.py`, written through `events/journal.py`) records who did what to a
dossier, and when: events created, edited (field by field, before → after) and deleted, documents added,
detached, edited and deleted (with their SHA-256), access granted, changed and removed,
invitations, share links, dossier renames and owner changes, and exports. Everything done in the
**admin** is recorded too, flagged `via_admin` (`events/admin.py`: `save_model`, `save_related`,
`save_formset`, `delete_model`, `delete_queryset`). It starts on 2026-10-08.

- **Append-only, enforced in the model:** `save()` on an existing row, `delete()`, and queryset
  `update()`/`delete()` raise `PermissionError`; the admin is read-only. `dossier` is `PROTECT`;
  author, link and event are `SET_NULL`, and `objet` keeps the label as it was.
- **Same transaction as the change:** `consigner` is called inside the view's `atomic()` block, so a
  refused save (version conflict, rejected file) leaves no entry, and no change goes unrecorded.
- **The "before" of an edit is captured before the form:** `ModelForm.is_valid()` already copies the
  posted values onto the instance; `instantane(event)` must be taken right after loading it.
  The admin re-reads the row from the database for the same reason.
- **Visibility:** `/dossiers/<id>/journal/` for anyone who can read the dossier, but sharing entries
  (`Journal.ACTIONS_DE_PARTAGE`) only for the owner — invariant 5. Not reachable via share links.
- **A file replaced in the admin keeps its old `content_hash`** (read-only there): the export then
  flags it as different. Deliberate — a replaced piece of evidence must show, and the journal says
  when and by whom. A document deleted in the admin leaves its file on disk (Django never deletes
  files); the journal keeps its name and hash.
- An anonymous share-link writer appears as the link (its label), never as a user.

## Architecture: export

`POST /dossiers/<id>/export/`, and `POST /partage/<token>/export/` for a share link (read or write
— the link already opens everything the archive contains; journaled under the link's label), both
built by `events/export.py`, return a zip: `chronologie.pdf` (reportlab,
standard Helvetica fonts so it renders identically on Windows and Linux; characters outside cp1252
become « ? »), `pieces/NNN-name.ext` (each document once, **ASCII file names** — accented names were
read inconsistently by unzip tools and broke `sha256sum -c`), `empreintes.sha256` and
`LISEZMOI.txt`. Every file's SHA-256 is **recomputed** and compared with `content_hash`; a mismatch
is flagged at the top of the PDF, never hidden. POST only, because each export is journaled and a
prefetched GET would log exports nobody asked for. Descriptions are converted to text with
`html.unescape(strip_tags(…))` — nh3 stores apostrophes as `&#x27;`.

## Architecture: import by email

A dossier can have an **email alias** (`Dossier.alias_courriel`, set by the owner on the dossier's
**settings page** `/dossiers/<id>/parametres/` — gear button in the header, owner only, 404 for anyone
else; the same page renames the dossier, journaled). `AliasCourrielForm` normalises (strip, lower)
*before* checking uniqueness, and refuses the import mailbox's own address (`COURRIEL_BOITE`); the
page suggests a hard-to-guess alias on that mailbox's domain and shows the `Set-Mailbox` command.
Mail forwarded to that alias lands in an Exchange Online mailbox; `manage.py importer_courriels`
(scheduled every 10 min by `scripts/importer_courriels.ps1`) reads it through **Microsoft Graph**
(`events/graph.py`, client credentials, stdlib only — Exchange Online refuses password IMAP) and turns
each mail into an event. Set-up on the Microsoft side: `docs/superpowers/import-courriels.md`.

- **Anyone who knows the alias can add an event** — the owner's decision (2026-10-09). Mitigations:
  aliases hard to guess, changeable by the owner (journaled), every import journaled with its sender.
- `events/courriels.py` is pure (bytes in, `Analyse` out) and handles three shapes: **forwarded as
  attachment** (`message/rfc822`: the original's topmost `Received` gives the true *receipt* time),
  **forwarded inline** (Outlook FR/EN/NL or Gmail header block: only the original *sent* time, read as
  Brussels time; the forwarder's own note is kept apart) and **sent directly**. **A reply quotes the
  previous message under the same « De : … Envoyé : … » block**: the block counts as a forward only if
  the subject is a forward (TR:/FW:/Fwd:/WG:/Doorst:) or a « message transféré » line announces it
  (`est_un_transfert`) — found on the owner's real samples, where three replies were read as forwards.
- **The forward's own envelope never counts, and forwards nest**: `analyser` is recursive. A mail
  forwarded to a lawyer and then to the import mailbox is unwrapped layer by layer (attachment or
  inline) down to the original; each layer's note is kept, newest first, in `commentaire` (`.md`).
  On the owner's samples, forwarding as attachment gives the *receipt* time, inline the *sent* time —
  2 to 13 minutes apart. `VraisExemplesTransferesTests` replays both on `exemples_courriels/`. Month names are matched
  next to the day and year — Gmail writes « mar. » for *mardi*, which is also *Mar*ch.
- `events/import_courriels.py` creates, in **one transaction**, the event (instant, Brussels time), the
  original `.eml`, a `.md` text version (for search / AI), the attachments that pass `_verifier_fichier`
  (refused ones are named in the journal and the `.md`, the rest is still imported), a
  `CourrielImporte` row and the journal. Signature images (`Content-ID` / inline) are skipped.
- **Never twice:** `CourrielImporte` is unique per `(dossier, message_id)` of the *original* mail.
- The mailbox is never emptied: imported mail goes to the Outlook folder « Importés », mail that
  matches no alias or fails to parse to « Non importés ». Re-running is safe.
- `--essai` analyses without creating or moving anything; `--fichier x.eml --dossier N` imports a saved
  mail without touching the mailbox. Real sample mails go in `exemples_courriels/` (git-ignored).
- `eml` and `md` were added to `ALLOWED_DOCUMENT_EXTENSIONS`; both are served as downloads.

## Architecture: search, filters, duplicates

- **One set of filters for every view and for the export** (`events/filtres.py`): `q` (all words
  required, accent- and case-insensitive, over title, description, document names and the text of
  `.md` documents), `expediteur` (a sender **of this dossier**, or `aucun`), `du` / `au` (months
  `AAAA-MM`; an interval touching the period is kept). Invalid parameters are ignored, never an
  error. Matching is done in Python, not SQL — SQLite cannot fold accents, and `.md` text is in files.
  `vue_du_dossier` (`events/views.py`) applies them for both the account and share-link paths; tab and
  navigation links carry them (`suffixe_filtres`).
- **The export exports what is shown**: the header's export button posts the current filters
  (`_bouton_export.html`); a filtered export contains only those events and their documents, and says
  so at the top of the PDF and in `LISEZMOI.txt` — a partial archive must not pass for the whole file.
  « Avec le journal » adds `journal.pdf`, filtered like the journal page (sharing entries for the owner
  only); never through a share link.
- **Documents tab** (`?vue=documents`, `vue_documents.html`, no JavaScript needed): every document
  of the dossier with its events, size, upload date and hash. There the sender and period filters choose
  the *events* (hence their documents), but `q` searches the **document itself** (name, `.md` text), and
  `type` (pdf / image / courriel / bureautique) applies to this tab only — it is not carried to the others.
  Documents linked to no event are listed when no event filter is active.
- **Duplicates** (`events/doublons.py`, `/dossiers/<id>/doublons/`, writers only): a pair sharing a
  document at most one day apart, or same day with similar titles. Sharing a document alone is *not*
  enough (an imported mail re-attaches photos filed weeks earlier); two imported mails are never
  duplicates. Merging keeps one event and pours the other in (documents, description appended under
  its original title and date, sender, colour, `CourrielImporte`), bumps `version`, and journals both
  the changes and the absorbed event in full. `PaireIgnoree` remembers pairs declared distinct.
- Journal entries written by the import (no author) read « par l'import de courriels »: marked
  `source: "courriel"` since 2026-10-09, recognised before that by their imported event.

## Architecture: exhibit numbers and tags

- **Every document has a permanent number** (`Document.numero`, « pièce 12 »), assigned in
  `Document.save()` on first save — by every path (form, email import, admin) — from a per-dossier
  counter `Dossier.dernier_numero_piece` incremented with one conditional `UPDATE … SET n = n + 1`.
  A counter, not `max()+1`: deleting the last document must not hand its number to the next one, since
  that number may already be cited in a lawyer's letter. Numbers are never changed or reused; gaps
  are expected. `UniqueConstraint(dossier, numero)`. Migration `0017` numbered the existing 77 in
  chronological order. The export uses these numbers (`pieces/012-…`), so a partial export has gaps.
- **Tags** (`Etiquette`, per dossier, many-to-many with `Event`, `events/etiquettes.py`): ticked or
  typed in the event form (case-insensitive reuse), filter `etiquette` in every view, shown in list,
  detail and PDF export, renamed/deleted by the owner in Paramètres. The form posts a marker
  `etiquettes_presentes`; without it (direct POST) an event's tags are left untouched. Tags are part of
  `instantane()`, so tag changes are journaled like any field. Merging duplicates unions them.

## Architecture: colours

An event's colour is **derived, never copied**: `Event.couleur_affichee` returns the colour of its
`expediteur` (an imported mail's sender, `Expediteur`, one per dossier and address) if any, else the
event's own `couleur` (chosen from `couleurs.PALETTE` in the form), else none. Changing a sender's colour
on `/dossiers/<id>/expediteurs/` (writers; readers see it as a legend) therefore recolours all their
events at once. A new sender gets the first free palette colour.

- Colours end up in `style` attributes and in the calendars' JSON: **only `#rrggbb`** is accepted
  (`valider_couleur` on both models, palette-only choice in the form, regex in the senders view).
- Text on a coloured background is black or white by WCAG luminance (`couleur_du_texte`).
- Lists must `select_related("expediteur")` — otherwise each event costs a query (a test counts them).
- Events without a colour produce exactly the same payload as before (no colour keys).

## Architecture: output encoding

The authorization model answers *who may read a dossier*. Output encoding answers the other half:
**what a collaborator who may legitimately write into it can make the owner's browser do.** A
`Role.WRITE` grant lets a second person put arbitrary text into the only copy of the evidence; if
that text comes back as markup in the owner's session, the collaborator reads all 49 documents and
deletes them with the owner's own CSRF token — defeating from the inside the controls the
permission model enforces from the outside. Three rules, all currently enforced by tests:

1. **The timeline payload goes through `json_script`, never `json.dumps` + `|safe`.**
   `events/views.py` puts the *list* in the context; `timeline.html` renders
   `{{ timeline_items|json_script:"timeline-items" }}` and the page reads it with
   `JSON.parse(…textContent)`. `json.dumps` escapes neither `<`, `>` nor `/`, so a title containing
   `</script>` closed the tag and opened its own.
2. **An event's detail is rendered by Django, never assembled in JavaScript.**
   `rendre_detail` (`events/views.py`, shared with `share_views.py`) renders
   `_detail_evenement.html`: the page for a plain request, the fragment alone for an
   `X-Requested-With: XMLHttpRequest` one. Autoescape covers the title, each document's title and
   the image `alt`; the only `|safe` is the description, re-sanitised by `nettoyer` at output. The
   fragment contains no `<script>`. The single `innerHTML` sink in the pages is `corpsModal`, which
   receives that server-rendered fragment (or the form's) — a test enumerates the sinks. Anything
   the scripts build themselves (messages, PDF preview) is built with `createElement` and
   `textContent`.
3. **`document_download` always answers `as_attachment=True`**, and forces
   `content_type="application/octet-stream"` for any extension outside
   `pdf jpg jpeg png doc docx xls xlsx`. The same allow-list is enforced server-side in
   `_save_documents`: the form's `accept` attribute and its pre-upload check are *client*
   comfort, and a direct POST walks straight past them. Images still render — `Content-Disposition:
   attachment` governs top-level navigation, not `<img src>`.

The rich-text editor in the backlog will need **server-side HTML sanitisation** on top of these;
rules 1 and 2 are what make the plain-text case safe today.

## Architecture: how an event sits in time

An `Event` can be **a date** ("devis signé le 02/06"), **an instant** ("mail envoyé le 21/08 à
14h32") or **an interval** ("chantier du 18 août au 28 septembre").

**There is no stored type field, and adding one would be a regression.** The kind is derived by
`Event.precision` from three optional columns — `heure`, `date_fin`, `heure_fin`:

| `heure` | `date_fin` | `precision`  |
|---------|------------|--------------|
| —       | —          | `date`       |
| set     | —          | `instant`    |
| any     | set        | `intervalle` |

A stored type could contradict the dates (an event marked "intervalle" with no end date, or the
reverse) and you would then have to decide which of the two is right. Derived, it cannot lie.
`date_fin >= date` is held by a `CheckConstraint` named `fin_apres_debut` *and* by `Event.clean()`:
the constraint guards the admin and any script writing straight to the database, `clean()` is what
produces a message the form can show.

Consequences that are easy to break:

- **`EventForm.precision` is a UI mode, not data.** It is `required=False`; when absent,
  `_mode_deduit()` reads the kind off the submitted fields, so a direct POST or an HTTP client that
  sends only dates still works. When present, it **erases** the fields that don't belong to the
  chosen mode — a leftover hour from a change of mind must not silently make the event an instant.
- **`calendrier._grouper_par_date` indexes an event on every day of `jours_couverts`.** A six-week
  renovation shown only on its first day would vanish behind one calendar cell. The list view
  deliberately does not repeat it — a chronology is not a calendar.
- **`Event.pour_calendrier()` is FullCalendar's equivalent**, and differs on purpose: no hour at
  all makes the event `allDay`, but an hour on *either* side makes it timed — the recorded hour is
  part of the evidence and must not vanish into an all-day cell.
- **`Event.pour_chronologie()` adds a day to a date-only `end`.** vis-timeline treats `end` as
  exclusive, so without it a bar stops the day before its end date.
- **`_jour()` in `models.py` normalises string dates.** `Event.objects.create(date="2026-10-05")`
  leaves the string on the in-memory object, so `jours_couverts` would break on objects that never
  made a database round trip. `calendrier._jour_de()` exists for the same reason.

## Architecture: production (AWS fleet, chronos.foxugly.com)

Target: the Foxugly EC2 fleet (Ubuntu 24.04, Python 3.12, PostgreSQL 16, port **8010**,
`/var/www/django_websites/Chronos`). Runbook: **`deploy/README.md`**; fleet rules: `OPERATIONS.md`.

- **Settings fail closed outside DEBUG**: `SECRET_KEY` (bare fleet name; `DJANGO_SECRET_KEY` still
  read locally), `ALLOWED_HOSTS` (never `*`), and `DB_ENGINE=postgresql` are required — no silent
  fallback to a SQLite file. HTTPS/HSTS/secure cookies, Sentry (`SENTRY_DSN`, no PII), `/health/`
  (`config/sante.py`, real `SELECT 1`, 503 when down, public).
- **Evidence is never served publicly.** No `/media/` location (nginx answers 404). `reponse_de_piece`
  (`events/views.py`, both paths) checks rights, then — when `PIECES_X_ACCEL_PREFIX` is set — answers
  `X-Accel-Redirect` to an nginx `location internal;` whose `types {}` only keep pdf/jpeg/png and send
  everything else as `application/octet-stream`. The first X-Accel use in the fleet.
- **CSP with nonces** (Django's built-in `SECURE_CSP`): every inline `<script>` carries
  `nonce="{{ csp_nonce }}"`; scripts only from self and the two CDNs; styles may be inline
  (FullCalendar, vis, Quill inject them). Adding an inline script without the nonce breaks it in every
  browser — a test checks every page's scripts against the header.
- **Mail**: `events/courrier_graph.py` (Graph `/sendMail` through `events/graph.py`) when
  `GRAPH_SENDER` is set; console otherwise.
- **Exchange aliases**: `events/exchange.py` + `scripts/exchange/manage_alias.ps1` (copied verbatim
  from PushIT). Unlike PushIT, failure is not swallowed: add in Exchange first, save only on success.
  Certificate: PFX in SSM (`EXCHANGE_CERT_PFX_B64`), decoded by the env-fetch script into
  `/run/chronos/exchange.pfx` — never on disk in the app tree.
- **Backups**: `manage.py sauvegarder` handles PostgreSQL too (`pg_dump --format=custom`, read back with
  `pg_restore --list`, password via `PGPASSWORD` env only), run by `chronos-sauvegarde.timer`.
  Off-box copy (S3) is not set up — the fleet has no convention yet.
- **CI runs on PostgreSQL 16 / Python 3.12** with production settings (`.github/workflows/ci.yml`);
  deploy is the fleet's OIDC → SSM model (`deploy.yml`), units and nginx installed from the committed
  git blob. `.gitattributes` forces LF for everything under `deploy/` and `.github/`.
- `manage.py verifier_integrite`: counts + SHA-256 of every document; run on both sides of a transfer.
- Local PostgreSQL 18 exists on the dev PC but its credentials are unknown; the PostgreSQL branch of
  the backup is tested with mocked `pg_dump`/`pg_restore`. The real check is CI and the server.

## Conventions and gotchas

- **Git repository since 2026-10-09** (default branch `main`). The `.gitignore` must keep
  excluding `db.sqlite3`, `media/`, `sauvegardes/`, `*.backup-*` (all hold copies of the
  evidence files), `exemples_courriels/` (real mails), `.venv/` and certificates. Before any
  `git add -A`, check that `git status` lists none of them.
- **Line endings: pre-existing files use CRLF, newer ones LF.** Preserve whatever a file already
  has; a heredoc that silently rewrites a whole file to LF turns a two-line fix into an unreadable
  diff.
- **`MAILERS` in `config/settings.py` is a legitimate Django 6.1 setting**, not a mistake — it
  replaced `EMAIL_BACKEND`. It is currently on the console backend, so password-reset links print
  to the terminal.
- `SECRET_KEY`, `DEBUG` and `ALLOWED_HOSTS` read from `DJANGO_SECRET_KEY`, `DJANGO_DEBUG` and
  `DJANGO_ALLOWED_HOSTS`, and **both fail closed**. `DEBUG` is true only when `DJANGO_DEBUG` is
  exactly `"True"`, so a deployment that forgets the variable gets the safe setting rather than a
  full Django error page with its settings in it. The development `SECRET_KEY` is usable **only**
  under `DEBUG`: outside it, an unset `DJANGO_SECRET_KEY` raises `ImproperlyConfigured` instead of
  signing sessions and CSRF tokens with a key published in this repository.
- **`events/migrations/0003_event_dossier.py` is a hand-written data migration** that attached the
  pre-existing events to an initial dossier. Its forward direction is idempotent (`get_or_create`);
  it has **no reverse at all**, and that is deliberate — `migrate events 0002` stops on
  `IrreversibleError`. There is no honest reverse: dropping the column would hit *every* dossier,
  not just the one this migration created, and `CASCADE` would take with it `DossierAccess` grants
  made long afterwards. Worse, a reverse-then-forward cycle would re-attach every event in the
  database to the single initial dossier, silently merging separate cases into one chronology.
  Preserve the irreversibility; going back is a decision to take by hand, with a backup.
- Deployment is out of scope so far; this app is not on the EC2 fleet described in the user-level
  `CLAUDE.md`. When it eventually ships, document serving should move from Django's `FileResponse`
  to an nginx `X-Accel-Redirect`.

## Work in progress

`docs/superpowers/` carries the design record: `specs/` holds the numbered decisions (referenced in
code comments as "spec D2", "spec D5"), `plans/` holds the task-by-task plan, `decisions-l1.md` the
rulings taken during execution, and `backlog.md` what has been requested, deferred or deliberately
left out — read `backlog.md` first, it is the most current of the four.

**Lots L1 to L6 are done, plus the temporal-precision work and the robustness + backup lot of
2026-10-08.** The suite is at **592 tests**, green, with no pending migrations (latest:
`0017_numeros_et_etiquettes`). Delivered: a shared `base.html` with navbar and logout,
`404.html` / `500.html`, the event form as a modal (page version kept as the no-JavaScript
fallback), a Quill rich-text editor sanitised server-side by nh3 (`events/sanitize.py`), four views
(chronology, month, week, list — the last three render without JavaScript), content-hash
deduplication of documents per dossier, invitations, anonymous share links, the
date / instant / interval model described above, the event-detail modal and the FullCalendar tabs
described in *Front end*, and on 2026-10-08:

- **Optimistic locking on `Event`.** `Event.version` is sent back by the form as a hidden field;
  `EventForm.save` does `UPDATE … WHERE version = expected` in **one** query and raises
  `ValidationError` on a mismatch, which the views already turn into a form error inside their
  transaction (so the same POST's attachments are not stored either). Read-then-write would
  reopen the race. A POST with no `version` is *not* checked, like `precision`; the admin keeps
  last-write-wins but bumps the version.
- **Upload size limit** `DOCUMENT_TAILLE_MAX` (50 MB) in `_save_documents`, which now validates
  **every** file before writing any: the views' transaction rolls back rows, not files.
- `hotes_autorises()` for `ALLOWED_HOSTS`; `DossierQuerySet` returns `none()` for an anonymous
  user (`accessible_by`, `writable_by`, and `owned_by`, which `get_dossier_for_owner` now uses).

What is deliberately **not** done, and recorded in `backlog.md` (section « Ouvert »):

- Backups land on the same disk by default. `--destination` points elsewhere; choosing Dropbox
  sends the evidence to a third party, which is the owner's call, not the code's.
- Four unreferenced `.jpg` files sit in `media/`, from before this work. They are left untouched
  on purpose: on an evidence set, deleting a file nobody can currently explain is the worse error.
  (Documents removed *through the application* do have their file deleted — see invariant 4.)
- Going live: the code side is done (see *Architecture: production*); what remains is the
  out-of-repo setup in `deploy/README.md` (GitHub repo, IAM role, PostgreSQL, SSM, machine,
  Microsoft 365, data transfer).
