# Bureau of Applied Science — Field Recorder

**Brand and Product Doctrine — Revision 1**
Applies to: naming, interface language, data architecture, and roadmap scope.
Status: authoritative. Supersedes naming and terminology in the implementation roadmap.

---

## 1. Purpose of this document

Field Recorder is the first instrument issued under the Bureau's name. It is therefore the reference implementation of what a Bureau instrument is — not only in what it does, but in how it is named, worded, structured, and shipped.

This document exists so that decisions made once are not relitigated, and so that implementation work (including work delegated to subagents) inherits the brand without requiring the brand to be re-explained.

Read this before writing UI strings, schema fields, directory paths, or documentation.

---

## 2. Thesis

**The product does not produce recordings. It produces records.**

Audio is capture. Transcription is intermediate. The artifact that matters is a **record**: a speaker-attributed, timestamped, annotated, machine-addressable account of spoken work — legible to a person and ingestible by a model.

Everything downstream of this sentence follows from it. If a decision is unclear, decide in favor of the record.

---

## 3. Naming and endorsement

### 3.1 The mark

```
BUREAU OF APPLIED SCIENCE
Field Recorder
Model 1
```

The Bureau is always in the endorsement position. The instrument does not carry independent brand equity and does not receive its own logo, wordmark, or color. It borrows the institution's authority; that is the mechanism.

**Full form:** Bureau of Applied Science — Field Recorder
**Short form:** BAS Field Recorder
**In-product form:** Field Recorder
**Never:** FieldRecorder, FR, Field Recorder™, Field Recorder by BAS, fieldrecorder.app

### 3.2 Model designation

Public versioning is by **Model number**, not semantic version. `Field Recorder — Model 1`. Semantic versioning remains internal (releases, changelogs, `version.json`). Instruments have models; software has versions; the user is holding an instrument.

A Model number increments only for a substantive change in what the instrument is. Bug fixes and feature additions do not.

### 3.3 The suite

Do not name the suite. Ship one instrument.

```
Bureau of Applied Science
└── [suite — unnamed until instrument two exists]
    └── Field Recorder — Model 1
```

Premature suite branding is the signature of a product that has not shipped. If a second instrument arrives, the collective term is decided then. If it never arrives, nothing has been lost.

Future instruments, should they exist, are named by function in the same register — what they take in, what they file. All emit the same artifact. That is the point of the suite, and the only justification for one.

### 3.4 On the word "Field"

Recorded rationale, so this is settled once:

*Field recorder* is an established term of art for portable capture of real-world audio. The Bureau's brand is defined by "field-tested, not trend-chasing" and "applied intelligence over theory alone." The field is where the work happens, as opposed to the lab. A call is fieldwork. The objection that a desk is not a field misreads both the term and the brand.

The name holds. It does not need defending again.

### 3.5 Descriptor line

Beneath the mark, a functional descriptor — not a tagline:

> Captures calls and meetings as structured records.

It states what the instrument takes in and what it puts out. No adjectives. No promise. Alternates, if context demands brevity: *An instrument for capturing spoken work.*

Proscribed: anything with "effortless," "powerful," "AI-powered," "supercharge," "never miss," or an exclamation point.

---

## 4. Lexicon

Terminology is not cosmetic. The words define the data model and the user's mental model of the artifact. Use the right column everywhere: UI strings, documentation, schema field names, commit messages, directory names.

| Do not use | Use |
|---|---|
| Recordings (as the collection) | **Records** |
| Recording (as the artifact) | **Record** — the audio is *within* the record |
| Recordings window | **Records** |
| Name this recording | **Name this record** |
| Name speakers | **Identify speakers** |
| Keep forever / ★ Keep | **Retention hold** |
| Auto-delete after N days | **Retention policy** |
| Workspace | **Records store** (`records/`) |
| Transcription server | **Transcription service** |
| Job | **Job** (acceptable — a bureau has jobs) |
| Cloud transcription | **Off-device transcription** |
| Local transcription | **On-device transcription** |
| DiarizedTranscriber | **Field Recorder** (see §7 — rename required) |

Notes:

- *Identify speakers* is what the operation actually is. Diarization detects; the operator identifies.
- *Retention hold* is the correct term for exempting a record from a retention policy, and it is what an institution calls it. The control is a hold, not a favorite. Do not use a star.
- *Off-device* / *on-device* is more precise than *cloud* / *local* and states the fact that matters: whether the audio leaves the machine.

---

## 5. Interface tone

The brand doc's rule applies without modification: *technical but readable; engineered rather than decorated; quiet confidence, not performance.*

Operating rules for strings:

1. **State the fact, then the consequence.** "Cancelling a running transcription discards all progress. The recording is not affected." This is already correct in the roadmap. It is the register for everything.
2. **No exclamation points. No emoji in strings.** The roadmap's toolbar uses 👤 📋 🗑 📁 ★ — replace with text labels or a consistent line-geometry icon set drawn from the BAS mark. Emoji are decoration without functional purpose.
3. **No congratulation.** The instrument does not say "Done!" or "Great — your recording is ready." It says what happened. "Record saved."
4. **No anthropomorphism.** The instrument does not think, listen, understand, or get to work. It captures, transcribes, files, and reports.
5. **Errors state cause and remedy.** "Could not start the transcription service. Port 7777 through 7780 are in use. Set a port in Settings → Service."
6. **Numbers are exact.** Durations as `MM:SS`. Sizes in the unit the operator can act on. "Approximately 3–5 GB" is acceptable when the figure is genuinely uncertain; false precision is not.
7. **Progress is reported, not narrated.** "Installing packages — 38%." Not "Setting things up…"

The setup wizard is the first thing an operator sees and is therefore the brand's first impression. It should read as an installation procedure, not a welcome.

---

## 6. Visual system

Inherits the BAS system without deviation. The instrument does not get its own aesthetic.

- Dark mode primary. Warm charcoal ground, warm off-white text, controlled orange used structurally and sparingly.
- Libre Franklin. Strong hierarchy through size, weight, and spacing.
- Strict geometry; horizontal alignment; rules, bars, and dividers doing the branding work.
- Flat. No gradients, no glow, no glassmorphism.
- Motion, if any: measured reveals, lines extending from 90-degree bends. Nothing bounces.

**Tray icons.** Programmatic generation is correct (roadmap §1.8), but circles are the wrong vocabulary. Use the BAS three-line geometry as the base and signal state through the accent color and line configuration. State must be readable at 16 px, in the tray, in both Windows themes. The instrument is identifiable at a glance as a Bureau instrument.

**Transcript rendering.** This is the artifact made visible. It should read as a technical document: aligned columns in timestamp mode, clean paragraphs in reading mode, speaker attribution unambiguous. This screen is the product. Treat it as the homepage.

---

## 7. Consequences for the roadmap

Ordered by cost of delay. Items 1 and 2 are expensive after Phase 1 ships, because they orphan user data.

### 7.1 Data root and store layout — **before Phase 1 ships**

`DiarizedTranscriber` is a working title and must not reach a user. The records store belongs to the Bureau, not the instrument, so that a second instrument can file into it without a second data root.

```
%APPDATA%\BureauOfAppliedScience\
├── records/                    # the store — every instrument files here
├── instruments/
│   └── field-recorder/
│       ├── backend/            # embedded Python, venv, service
│       ├── models/             # Whisper + pyannote weights
│       └── settings.json       # instrument settings
├── tmp/                        # in-progress capture (crash recovery)
├── settings.json               # bureau-level: retention, ingestion
└── deletions.log
```

macOS: `~/Library/Application Support/Bureau of Applied Science/`
Linux: `~/.local/share/bureau-of-applied-science/`

`recorder/user_data.py` remains the single source of truth for path resolution. No path is constructed inline anywhere else.

Affected: `user_data.py`, PyInstaller spec, `build.ps1`, tray tooltip, window titles, the setup wizard, and every string containing "Diarized Transcriber."

### 7.2 Record schema — **before Phase 2 writes JSON in earnest**

The current schema is built for a human reader. For ingestion it must answer *what happened, with whom, when, and from where* without re-deriving anything from a filename. Add:

| Field | Type | Notes |
|---|---|---|
| `record_id` | UUID | Stable identity. Not the filename. Filenames are not identity. |
| `format_revision` | int | The Record Format revision this file conforms to. |
| `source` | object | What produced this: application, meeting title, call direction, counterparty. Best-effort; nulls are honest. |
| `duration_seconds` | float | Read from the WAV header at mixdown. |
| `participants` | array | Real identities. Distinct from `speaker_names`, which is the map from diarization labels to those identities. |

`speaker_names` remains the label map. `participants` is who was in the room. They are different things and conflating them will cost more later than separating them now.

GUI-owned fields remain GUI-owned; `transcribe.py` is still unchanged and still writes only `backend`, `audio_file`, `speakers_detected`, and `segments`.

### 7.3 Off-device transcription is a deviation, not a peer

The instrument's position is that audio stays on the machine. On-device is the default and the identity. Off-device is available, clearly labeled, and consented to once — the existing consent dialog does this well and should keep its plain statement that audio leaves the device.

Do not present the two as an even choice in Settings. The default is a position, and the position is part of what is being sold.

### 7.4 Phase 5 — Ingestion — **scope now, build last**

The roadmap ends at cross-platform support with no ingestion surface. If ingestion is the endpoint, the roadmap is missing its final phase and its only differentiated one. Phases 1–3 are table stakes; the ingestion surface is what makes this a Bureau instrument rather than another transcription app.

Deliverable: an MCP server over `records/` — search records, fetch a record, fetch a transcript, list participants, filter by date and speaker.

It is scoped now because it constrains §7.2 and §7.1. It is built last because it is worthless without records to serve.

---

## 8. Record Format, Revision 1

A short public document defining the JSON schema every Bureau instrument emits. Published on the BAS site.

This is not a marketing gesture. A standards body publishing a standard is the most on-brand act available, it costs a weekend, and it does what the case study doctrine requires: it sells through demonstrated quality rather than sales language. It also forces the schema question early, which §7.2 requires regardless.

Contents: purpose; the artifact defined; field-by-field specification with types and nullability; revision policy; a complete worked example. Voice: specification, not explanation. No CTA.

Write it after §7.2 is settled and before Phase 5 is built.

---

## 9. Decisions on record

| # | Decision | Rationale |
|---|---|---|
| 1 | Endorsed naming; no sub-brand | The Bureau's authority is the mechanism. |
| 2 | "Field Recorder" | Term of art; consistent with field-tested brand position. §3.4. |
| 3 | Model numbers publicly, semver internally | Instruments have models. |
| 4 | Suite unnamed until instrument two | Premature suite branding is a tell. |
| 5 | The artifact is the record | Determines lexicon, schema, and store layout. §2. |
| 6 | Records store at bureau level, not instrument level | Multi-instrument ingestion into one store. §7.1. |
| 7 | On-device is default and identity | The position is part of the product. §7.3. |
| 8 | Record Format published as a public spec | Credibility artifact; forces the schema. §8. |

## 10. Open

- Icon geometry: three-line mark reduced to 16 px, four states, both Windows themes. Needs a pass.
- `source` population: what is actually detectable per platform without heroics. Determines whether the field is useful or decorative.
- Whether records are distributable between machines, and whether that implies the record is a single file rather than three siblings.
- Licensing and distribution posture. Not a branding question yet, but it becomes one at first release.
