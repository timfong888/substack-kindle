# Design exploration — reading experience across Kindle, iOS and laptop

> **Status:** Exploration (2026-10-04). Items marked **verify** are unconfirmed.
> Primary user: a Substack reader who wants a better reading experience. Primary output: EPUB → Send to Kindle.

## Key point: one delivery, every screen

An EPUB sent to the customer's Send-to-Kindle address lands in their Kindle library. It does not go only to the
e-ink device. The same document opens in:

- Kindle e-ink devices (Paperwhite, Scribe, …)
- the Kindle app on iPhone/iPad and Android
- the Kindle app on Mac/PC (and Kindle for web — **verify** personal-document support)

Amazon syncs **last page read, highlights, notes and bookmarks** for personal documents that Send to Kindle has
converted to Kindle format (Whispersync). So a reader can start an issue on the phone and finish it on the Kindle.

**Implication:** we don't need separate iOS or laptop delivery. Kindle is the delivery channel. Our job is to make the
EPUB work well on *all* of those surfaces, and in particular on colour, backlit, dark-mode screens, not only on e-ink.

## What each surface adds, and what we must do to unlock it

| Capability | Where it shines | What the EPUB must do | Today |
|---|---|---|---|
| Navigable TOC, per-article chapters | All | EPUB3 nav + NCX, one spine item per article | Done (Reqs 14, 18) |
| **Footnote pop-ups** | Kindle (all), Apple Books | Convert Substack footnotes to `epub:type="noteref"` → `<aside epub:type="footnote">` | Not done. Substack writers use footnotes heavily; this is a visible improvement over the web. |
| **Dark mode / themes, reader's font & size** | iOS, Mac (backlit) | No hard-coded text/background colours, font families or absolute sizes. Use relative units. | **Gap:** `job_epub.py` CSS hard-codes `th{background-color:#f2f2f2}` and `color:#555`. In dark mode, white text on a light table header is unreadable. |
| Images & charts in colour | iOS, Mac, colour Kindles | Embed images (within size budget), keep `alt` text, scale to width | Partial (`epub_builder.py` embeds within budget) |
| **Open original / comments** | iOS, Mac (one tap to browser) | Per-article "Read on Substack · Comments" link to the canonical post URL | Not done. This is what the reader gives up by leaving Substack. Adding it also supplies the canonical URL needed for cross-channel dedup. |
| Highlights, notes, sync | All (Whispersync) | Nothing beyond being a converted personal document | Free |
| Highlight export (Notebook / Readwise) | Laptop | — | **Verify** whether personal-document highlights appear in Kindle Notebook (`read.amazon.com/notebook`) and so in Readwise. If they don't, this gap is a product opportunity. |
| Dictionary, Wikipedia, translate | All | Correct `dc:language` | Check language metadata |
| Read aloud | iOS (Speak Screen), Apple Books | Clean semantic HTML (real headings, no layout tables) | Mostly free |
| Library recognition | All | Clear title (`Substack Digest — Oct 4`), author = publication names, date, cover | Title done; cover noted as unreliable on Kindle; **verify** collection/series metadata |
| "Time left in chapter" | Kindle | One chapter per article (not one giant chapter) | Done |

Not available to personal documents: X-Ray, Popular Highlights, Word Wise (Kindle Store books only).

## Apple Books (secondary)

Apple Books on iPhone/iPad/Mac reads EPUB natively, with themes, footnote pop-ups and iCloud sync. There is no
"email to Books" address, so delivery needs the customer to open an attachment or a link. Option: also email the EPUB
(or a download link) to the customer's own address. This is low effort and serves readers without a Kindle. It is
not a priority while Kindle is the product.

## PDF (secondary)

PDF doesn't reflow on phones or e-ink, so it fails the "better reading experience" goal for the primary user.
It is useful for printing, archiving, or iPad annotation with a pencil. Defer.

## Positioning (blind spot)

The space is crowded: InboxToKindle, Inkwell ($4/mo), Readbetter.io (free), Newsletters to Kindle, RSS to Kindle.
Several already do "paste a Substack URL → EPUB on Kindle". Delivery alone isn't a differentiator.
Candidate differentiators, all cheap given the deterministic pipeline:

1. Reading quality: footnote pop-ups, correct tables, dark-mode-safe styling, clean Substack chrome removal.
2. A daily/weekly digest as one book with a real TOC, rather than one file per post cluttering the library.
3. Paid Substacks handled (private feed) — **verify** what competitors support.
4. Easy return to the conversation: per-article link to comments.

## Proposed next steps (priority order)

1. Dark-mode-safe CSS (small; fixes a real bug on iOS/Mac).
2. "Read on Substack · Comments" link per article (small; also provides the canonical URL for dedup).
3. Footnote pop-ups (medium; needs a Substack footnote-markup mapping and tests).
4. Verify: Kindle Notebook / Readwise export of personal-document highlights; Kindle-for-web personal docs; collection/series metadata.
5. Competitive check of the five services above (paid support, digest format, price).
