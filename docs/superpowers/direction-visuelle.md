# Chronos — design system (proposed redesign direction)

## Product context
- **What it is:** a private web tool that holds the *case file* (« dossier ») of a real construction
  dispute: a chronology of events (quotes, invoices, site photos, an architect's opinion, formal demand
  letters, emails) each backed by **evidence documents** (« pièces »). The database is the only copy.
- **Who uses it:** the owner (a private individual, not a lawyer) every day; occasionally a lawyer or an
  expert through a read-only share link. Belgian context, interface in French (also Dutch, English).
- **Jobs to be done:** see what happened when; find a document fast; add an event (often a forwarded
  email); hand the lawyer a clean, verifiable chronology.
- **Key screens:** dossier list; **dossier view** (chronology / calendar / list / documents, with search
  and filters); event detail (modal); event form (modal); journal (audit log); sharing; settings;
  possible duplicates.
- **Constraints:** Django server-rendered pages, Bootstrap 5.3 underneath, no build step. The list and
  documents views must work without JavaScript and print well. Every external resource needs an SRI hash,
  so fonts will be self-hosted. Must work at 400 px wide.

## Visual direction: « the exhibit file »
The vernacular is the lawyer's binder: a chronology with **numbered exhibits** (Belgian practice numbers
every *pièce* — « pièce 12 » — so numbering here is real information, not decoration), stamped dates,
clean typed text. Calm, credible, precise. It must look like something you can show an expert.

### Colour (cool, inky — not cream, not neon)
| Token | Hex | Use |
|---|---|---|
| `canvas` | `#EDF0F4` | page background (cool grey) |
| `surface` | `#FFFFFF` | cards, table, modal |
| `ink` | `#16233A` | text, headings, primary button |
| `muted` | `#5B6577` | secondary text, metadata |
| `rule` | `#D5DBE3` | borders, the chronology spine |
| `accent` | `#2F4BD8` | links, focus ring, active view, « Nouvel évènement » |
| `caution` | `#B45309` | duplicates, partial-export warnings |
| `danger` | `#B42318` | delete only |

Event colours stay user data (sender colours: #2563eb #dc2626 #16a34a #d97706 #7c3aed #0891b2 …):
they appear only as a dot on the spine and a thin left edge, never as large fills.

### Type
- **Source Serif 4** — dossier title and event titles only (the « typed document » voice). 600 weight.
- **Public Sans** — everything else (institutional, very legible UI sans). 400 / 600.
- Scale (1.25): 12.8 / 14 (UI base) / 16 / 20 / 25 / 31. Body text min 14 px; no 11 px greys.
- Dates in tabular figures. Sentence case everywhere; no all-caps labels, no letter-spaced eyebrows.

### Layout
- Dossier view, desktop: header row (serif title + one line of counts, primary action on the right,
  secondary actions in one « Dossier » menu), then a **view switcher with 4 views** (Chronologie,
  Calendrier, Liste, Documents), then a slim filter bar, then content. Left-aligned throughout.
- **List view = a vertical chronology spine**: month headers, a date column on the left, a line with a
  dot per event (sender colour), the event card to the right; documents as compact exhibit chips
  « 12 · devis-signé.pdf » with a file-type icon; image thumbnails when there are photos.
- Long email text collapsed to ~4 lines with « Afficher tout ».
- Radius 6 px on cards, 4 px on chips and inputs; one shadow level only for the modal.

### Principles
1. One memorable element: the chronology spine with numbered exhibits. Everything else stays quiet.
2. Hierarchy by type, not boxes: fewer borders, more whitespace.
3. Actions named by what they do (« Exporter pour l'avocat », « Ajouter un évènement »).
4. Visible keyboard focus (accent ring), 44 px touch targets on mobile, WCAG AA contrast.
5. Prints cleanly (list and documents views).
