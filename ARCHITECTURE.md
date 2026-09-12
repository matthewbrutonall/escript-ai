# Escript AI — Concept & Architecture

**Project:** Escript AI — independent open-source work **derived from** eScriptorium (not an official eScriptorium release).
**Status:** Implemented in alpha; this document is the design it was built from · **Date:** 2026-09-04 (heading updated 2026-09-11)
**Author:** Matthew Bruton
**Purpose:** Design for colour-keyed VLM transcription alongside kraken. Grounded in eScriptorium (`develop`, 2026-09-02) and kraken (2026-09-04) clones in this repo, with file:line references.

---

## 1. Vision

**Escript AI** is an independent project **derived from** eScriptorium. An **AI backend — a large multimodal model (VLM), remote via API (Gemini/Claude/GPT) or local (Ollama/vLLM) — does the heavy lifting** of transcription, then can feed the archive's own trained kraken models. In every other respect the tool behaves like eScriptorium. The classic kraken workflow stays fully intact; AI is added *alongside* it. This is not an official eScriptorium release.

**Target user:** an archive that wants to process its own historical material in-house with nothing more than an API key (or a local GPU), instead of shipping images to a transcription vendor.

**Two workflows we design for:**

- **The headline (the normal workflow): AI reads, you tweak, you export.** The AI transcribes, the archivist skims and fixes minor errors, and exports. This is what the tool is *for* and what most users do most of the time. Everything in the UX optimises this path — fast review, easy correction, clean export.
- **The power path (when AI needs help, or the collection is large/homogeneous):** the archivist corrects more heavily and trains a kraken model on those corrections for the rest of the collection, then exports.

**The headline is workflow 1.** Training is a genuine, first-class capability — the AI trains the user's own models (an original project goal) — but it is the *power path*, not the everyday one. The doc must not let the flywheel's technical depth (§9) overstate its everyday weight: most users read, tweak, export, and never train.

**Primary goal: a genuinely useful open-source project (MIT-forkable).** Hosting/implementation revenue and reputation follow from that.

---

## 2. Guiding design principles

1. **AI is a new transcription *source*, not a new data model.** eScriptorium already stores multiple named transcription *layers* per document and stamps every line with a `version_source`. AI output becomes another layer with `version_source = "gemini-2.5-pro"`. This is why Escript AI can be additive (§4).
2. **Play to each engine's strengths.** Kraken is strong at segmentation (precise baselines/polygons); VLMs are weak at pixel coordinates but strong at reading text in context. So: **kraken segments, AI transcribes** — and the AI is told *which* line it's reading (§5), never asked to invent coordinates.
3. **Additive, self-contained code.** The AI layer lives in its own Django app (`ai/`) with its own Celery tasks and models, mirroring the `core.tasks.transcribe` *contract* — never by adding a backend `if` inside `DocumentPart.transcribe()`. Keeps the tree legible and ready to absorb a GPU kraken build cleanly.
4. **Cost & trust are first-class.** Every AI job estimates spend before running (at a fixed max image dimension — §11), enforces caps, logs actual spend, and stamps provenance. Every AI-produced line carries a triage signal (§9).
5. **Never silently poison training data.** The train-from-AI flywheel always passes a quality gate: dual-engine disagreement triage + non-skippable sample review + held-out `ketos test` CER. Systematic AI errors — including "helpful" editorial ones — must not get baked into the archive's own model unseen (§9).

---

## 3. The existing architecture (what we build on)

Verified against the cloned source.

### 3.1 Data model (`app/apps/core/models.py`)

```
Project → Document → DocumentPart (one page/image) → Line (baseline + mask polygon)
Document → Transcription (a NAMED layer, e.g. "manual")
Line + Transcription → LineTranscription (the text of one line in one layer)
```

- **`Transcription`** (`models.py:2068`) — a named layer, `unique_together = ["name", "document"]`, default `"manual"` (`:2079`), carries `avg_confidence` (`:2076`). Documents routinely hold several layers; the UI diffs them and lets you pick which is truth. **AI output slots in here with no core schema change.**
- **`LineTranscription`** (`models.py:2103`) — the atom. `content` (text, `max_length=2048` — `:2111`); `graphs` = JSONB per-character `{c, poly, confidence}` (`:2113-2144`); `avg_confidence` (`:2147`); it is `Versioned` (`:2104`).
- **`Line`** (`models.py:1983`) — `baseline` + `mask` polygons. Segmentation output. Lines without a baseline are already skipped by recognition (`:1616`).
- **`OcrModel`** (`models.py:2186`) — a *kraken file on disk*: `FileField` with `.mlmodel`/`.safetensors` validator, `file_size`, `architecture` (filled by `qualify_model`), `job ∈ {SEGMENT, RECOGNIZE}` (`:2198`), `parent` fine-tuning lineage (`:2228`), `clone_for_training` that copies the file. **This is the kraken model the archive fine-tunes and keeps. We do not touch it.**

### 3.2 Versioning & provenance (`app/apps/versioning/models.py`)

- `version_source` (`:46`, `max_length=128`) and `version_author` (`:49`) — our provenance seam. Kraken sets `version_source = 'kraken:' + model.name` (`core/models.py:1640`), truncating to 128 (`:1641`).
- **Landmine:** `new_version()` (`versioning/models.py:97`) raises `NoChangeException` if `data + author + source` are unchanged, and defaults `source_ = source or self.version_author` (`:100`) — i.e. source silently falls back to *author*, not to the prior source. **AI writes must set `version_source` explicitly every time**, as kraken does, and must handle `NoChangeException` on identical re-runs. Rich run metadata (prompt version, token counts, cost) does **not** fit in 128 chars — it goes on an `AIJob` row (§8), not in `version_source`.

### 3.3 Task layer (`app/apps/core/tasks.py`) — Celery `@shared_task`

- `segment(instance_pks, model_pk, ...)` (`:471`).
- **`transcribe(instance_pks, model_pk, transcription_pk, user_pk, ...)`** (`:555`) — the contract we mirror. Loops parts → `part.transcribe(model, transcription, user)` (`:592`), emits websocket workflow events (`:603`, `:622`), quota-checks (`:577`).
- `train(transcription_pk, model_pk, ...)` (`:786`), `segtrain(...)` (`:307`), `*_from_collection` (`:1075`, `:1191`). `train_` (`:664`) trains on `content` + `baseline`/`mask`/`image` only — **graphs are irrelevant to training; the flywheel's GT is the raw `content` string.**
- `forced_align` (`:856`) and `align` (`:921`) — see §5 for what they actually do (and don't).

### 3.4 The inference call site — `DocumentPart.transcribe()` (`models.py:1592`)

Hardcoded to `RecognitionTaskModel.load_model(model.file.path)` (`:1593`), per-line kraken predict (`:1631`), writes `LineTranscription` with `content` + CTC `graphs` + `avg_confidence` + `version_source='kraken:'+name` (`:1640`). **We do not inject into this.** The AI path is a sibling — `ai.tasks` + `DocumentPart.ai_transcribe` (or kept off the model entirely) — producing the same `LineTranscription` rows with `version_source='gemini-...'`.

### 3.5 API trigger layer (`app/apps/api/views.py`)

DRF `@action`s: `segment` (`:653`), `transcribe` (`:665`), `train` (`:657`), `segtrain` (`:661`), `align`/`forced_align` (`:669`/`:673`), each via serializer + `.delay()`. We add `ai_transcribe`, `ai_correct` the same way. **Security policy (§13) is enforced here, at dispatch — not in the UI.**

### 3.6 Quota & cost (existing)

`DISABLE_QUOTAS`, `QUOTA_CPU_MINUTES`, `QUOTA_GPU_MINUTES`, `QUOTA_DISK_STORAGE` (`settings.py:504-516`), weekly, checked via `user.has_free_cpu_minutes()` (`tasks.py:577`). Reporting has `cpu_cost`/`gpu_cost` only, computed from process time (`reporting/models.py:65,124,146`). **We copy the `has_free_*` *pattern* but do not overload CPU-minutes to mean money** — AI spend gets its own ledger (§11).

### 3.7 Licences

eScriptorium = **MIT**, kraken = **Apache 2.0**. Full freedom to fork, host commercially, follow our own path. No copyleft.

---

## 4. Core integration model: "AI as a transcription layer"

Every AI transcription run:
1. targets `DocumentPart`s **already segmented by kraken**,
2. writes into a named `Transcription` layer (auto-named e.g. `"AI — gemini-2.5-pro — 2026-09-04"`),
3. sets each `LineTranscription.version_source` to the backend id, `version_author` to the user,
4. records a per-line triage signal (§9).

Falls out for free from the existing design: **diff** AI vs kraken vs manual in the existing comparison UI; correct *on top* via existing versioning; **train on whichever layer is blessed** — `core.tasks.train(transcription_pk=...)` already takes a layer as GT. No new training path for the flywheel.

*Scope note:* only transcription **output** needs no core schema change. AI backends, the spend ledger, privacy policy, the few-shot example store, and quality-gate state are all **new tables in `ai/`**.

---

## 5. Transcription strategies — the numbered-line spine

The central tension: eScriptorium is line-based; VLMs are best with page context. Do not send a full page of prose and try to align it onto baselines. **Existing alignment code does not do that job:**

- `forced_align` (`tasks.py:856`) loads each `LineTranscription`'s *already-known* `content`, runs a **kraken** model, and writes character `graphs` (`:886-917`). It aligns known line-text to pixels; it never allocates page text to lines.
- `align` (`tasks.py:921` → `Document.align`, `models.py:925`) is **Passim** collation of a `TextualWitness` file against an existing OCR layer (`n_gram`, `beam_size`, `threshold`). Built for "snap a diplomatic edition onto this OCR," not "put this VLM dump on these polygons." Its `merge=True` branch (`models.py:1039`) copies text from the *original* transcription for unmatched lines — which would **silently mask** allocation failures. Do not use it to hide gaps.

So: **flowing-page-text → line GT is a new problem.** We avoid creating it.

### 5.A Keyed region mode — the quality path (Phase 1)
Render kraken's segmentation with each line **visually keyed** on the overlay, send **one region/block crop** (a column/paragraph, `Block` granularity, `models.py:1895`) — or a whole page where density allows — and demand **JSON keyed by line key**: `{"red": "...", "blue": "..."}`. Zip onto `Line` rows by key.
- **Pros:** full page/region context (best CER, natural abbreviation handling); one call per region (cheap); **the VLM assigns text to lines, so no alignment step exists to get wrong**; wrong key count, missing/duplicate keys, or merged lines are **detectable quality events**.
- **Cons:** the keying can collide with the content; per-line confidence still must be derived (§9). See the keying scheme below.
- **Best when:** the everyday quality path for most material. This is the "wow."

**Keying scheme — colour primary, number fallback.** The obvious key is a painted line *number*, but on dense manuscript pages a stamped "37" either **occludes the text underneath** or gets **misread as part of the transcription** (marginal "17" vs the digit we painted). Numbers compete with the content for the same visual channel. **Colour does not:** tint each line's baseline/region a distinct translucent colour and the text stays fully legible underneath, with no glyph collision and no "is that number part of the line?" ambiguity. The VLM returns text keyed by colour name.

The limit is palette size — a VLM reliably distinguishes only ~6-10 nameable colours before teal/cyan-type confusion. This **reinforces the region-not-whole-page decision** (already taken above for glyph legibility): region/block crops keep most images under ~10 lines, right in colour's comfort zone. So:
- **Colour is the primary line key**, one distinct colour per line within a crop.
- Where a crop genuinely exceeds the palette, fall back to **colour-band + number** (e.g. "red-3").
- Region granularity is therefore not just about legible glyphs — it is what makes **colour keying viable at all**.

**Measured evidence (`phase0-spike/real_page_overlay.py`).** Rendering the overlay on real kraken geometry (`kraken/tests/resources/page/cPAS-2000.xml`: 97 lines, 2 columns, median line height 54px, min inter-line gap 0px) confirms this empirically: an 8-colour palette recycles **12×** down the page, and at whole-page scale the key legend collides into an unreadable smear while a recycled plain colour becomes ambiguous. Whole-page keying is *measured* to fail. **Operating constraint: crop to ≤~8 lines (a column-slice or paragraph `Block`)** so pure colour keys uniquely and the legend stays legible.

**Live VLM evidence (`phase0-spike/real_page_gemini_test.py`).**
- *Neat hand, tight PAGE polygons* (≤6 lines/crop) → **0.5% CER vs human GT, 25/31 exact.** This is the one real accuracy number.
- *Hard hand, box-only ALTO* (Dublin names/addresses, HTRflow output): the "CER" here is **disagreement with HTRflow's machine text, not error.** Qualitatively Gemini out-read HTRflow (*Stella Maris* vs "Stolia Maria", *Clontarf* vs "Clonterf") — the VLM prior recovering place-names CTC can't. But it also produced empty/merged/bled keys — because HTRflow's ALTO **shattered wrapped name+address entries into sub-line fragments that are not reading units.** Colour-keying asks "which colour is this string?" when the boxes don't correspond to lines: an ill-posed question. This was a *segmentation* stress test accidentally scored as HTR.

**Hard prerequisite (empirical):** colour-keying requires line masks with **pairwise overlap below a small threshold** — *overlap is the gate, not the box-vs-polygon format.* The hard page's HTRflow ALTO actually *has* polygons (45 of them), yet they overlap **25–100% per crop** (one spans a whole 1716px region), and colour-keying fails on them just as on boxes. Bounding-box ALTO (`HPOS/VPOS/WIDTH/HEIGHT`) is one common cause of excessive overlap, but shattered/region polygons are another — the pre-flight tests the overlap directly, so it catches both. Measured by `crop_overlap_frac()` in the test harness.

**Correct fallback ladder** (supersedes the earlier "retry per-line on overlap", which is wrong — per-line on fragment boxes just sends the VLM `Wi` and `lliam Humphrey…`, a well-posed question against broken geometry):
1. **Pre-flight, before spending the call:** if the source is box-only ALTO, or any pair of masks in the crop overlaps beyond a few px → **do not colour-key.** Queue `segment` (kraken `blla`) on that part, then key the fresh polygons.
2. **Post-call:** empty / unexpected / duplicate keys → flag the crop, **do not write those lines**, do not per-line-retry on the same masks.
3. **Only if polygons are already tight** and the VLM still drops one key → per-line retry on that single mask is reasonable.

Note this is *already* "kraken segments, AI transcribes" (§6) — this page is the empirical proof, and it does **not** depend on the GPU kraken fork: CPU `blla` emits the tight non-overlapping masks that made the scored Victorian PAGE work; a GPU kraken build is throughput/training, not the prerequisite for this path.

**Hard-hand confirmation (`_hard_seg.xml` — masks only; page image not in this repo).** Ornate flowing 19th-c. Spencerian cursive segmented by CPU `blla` (14 lines, **0% crop overlap**) → Gemini 2.5 Flash read the 12 body lines near-perfectly **by eye** ($0.00086). *This is an eyeball read, not a scored CER — the segmentation carried no reference text; do not rank it with the scored Victorian PAGE's 0.5% vs human GT.* Still, it isolates the result: the earlier "hard page" failed on *geometry*, not difficulty — with tight polygons a genuinely harder hand reads cleanly. Promising for harder hands, given good segmentation (unscored). (The pre-flight *rule* would skip overlapping crops, but the spike harness still spent on this run for evidence — enforcement is a Phase-1 item.) Two failures, both segmentation edge-cases: (a) the decorative display header ("2ᴺᴰ Meeting" split into ornate-capital lines, misread); (b) a degenerate 44px bottom sliver where blla mis-segmented — and there Gemini **hallucinated** plausible boilerplate ("With this deed Hand and Seal…") rather than returning empty.

**New failure mode — hallucination on degenerate crops.** A VLM cannot be trusted to stay silent on garbage input; it invents plausible text. Mitigations: (1) extend the pre-flight to reject degenerate line polygons (absurd aspect ratio / height below a threshold) before spending; (2) rely on the §9 dual-engine agreement + confidence gate — a hallucinated line will disagree hard with a cheap kraken pass. Still open: faded/crabbed hands, heavy abbreviation, marginalia, glossed Latin, and the local-VLM path (evidence so far is remote Gemini).

Overlay rendering is a small lift — kraken contrib already has `segmentation_overlay.py` / `baselineset_overlay.py`, which already draw per-line colour.

### 5.B Per-line mode — GT-harvest & fallback (Phase 0 spike + fallback)
Crop each segmented line, send individually, write straight to its `LineTranscription`.
- **Pros:** perfect data-model fit; output is immediately valid kraken GT; trivial mapping; the safe write-path spike.
- **Cons:** ~30-40 calls/page (cost/latency); no page context — the VLM's *weakest* setting for isolated handwriting/abbreviations/catchwords. Ships first only because the write path is obvious, **not** because it's the quality path. Do not ship per-line-only to a real archivist — they will compare it to whole-page tools and bounce.
- **Best when:** the Phase-0 spike; harvesting clean line-level GT for training; and the fallback for pages where numbered assignment fails or line count is unreliable (heavy marginalia, interlinear glosses, tables).

### 5.C Passim fallback (optional, later)
If ever needed: run a **cheap kraken recognition first**, treat the VLM page text as a temporary `TextualWitness`, and use `align` (`tasks.py:921`) for the job it actually does — witness→OCR collation. Note this means full-page is **not** "skip kraken recognition." Unmatched/low-score lines are left **empty and flagged**, never `merge`d from kraken silently.

**Backend interface takes a *list* of line crops / a region descriptor** even if the first implementation loops internally, so provider-side batching is possible later without an API change.

---

## 6. Segmentation: kraken segments, AI reads; AI reviews (later)

VLMs return unreliable pixel coordinates, so Escript AI never asks AI to draw baselines. Kraken's `blla` segments as today; the AI is *told* the lines (§5).

Later, an **AI segmentation-review** pass can flag missed/spurious lines, wrong reading order, and region/line typology (heading vs body vs marginnote) — judgement tasks VLMs are good at, expressed as structured *suggestions* on existing `Line`/`Block` objects, never as overwritten masks. Deferred; architecture leaves room.

---

## 7. Provider abstraction

```
class AIBackend(Protocol):
    def transcribe(self, images: list[LineCrop] | RegionImage, prompt: str,
                   context: TranscriptionContext) -> AIResult: ...
    # AIResult: text keyed by line id (5.A) or per-crop (5.B),
    #           optional self-scores, token usage, raw response

class TranscriptionContext:
    # diplomatic conventions (abbreviations, long-s, u/v, punctuation,
    #   hyphenation-at-line-break), script/period hints,
    #   few-shot gold examples (§10.1), glossary
```

Implementations, priority order:
1. **Gemini** (first — best quality/price today; cost guards are mandatory, §11).
2. **Claude**, **GPT** — same interface.
3. **OpenAI-compatible local** (Ollama/vLLM) — any on-prem GPU. **The fully-private, no-API-spend path; first-class, not an afterthought** (§13). "Local = free" only of API spend — GPU time, admin, and hardware are real costs.

Config precedence: **instance-level** default (self-host) *and* **per-user/per-team** keys (multi-tenant), §12. Keys encrypted at rest, never logged, redacted in tracebacks.

---

## 8. The "AI backend" as a config object (not an `OcrModel`)

**A new `AIModel`/`AIBackendConfig` in `ai/`; do not extend `OcrModel`.** `OcrModel` is a file on disk with a `.mlmodel`/`.safetensors` validator, `file_size`, kraken `architecture`, `clone_for_training` (copies the file), and an inference-queue keyed on architecture (`TranscribeSerializer`, `serializers.py`). An AI config is provider + model id + prompt template + few-shot set + params — no file. A discriminator flag would force `if backend` into every one of those paths, and the GPU kraken fork makes that worse.

- New object holds the config; a separate **`AIJob`** row holds per-run metadata (prompt version, token counts, est/actual cost, task id) that can't live in `version_source`.
- `ai_transcribe` takes `ai_model_pk`, not `model_pk`.
- The **UI unions** AI backends and kraken models in the Transcribe modal (which already groups Your/Shared/Public model lists) — presentation only, no union table.
- **Terminology:** call the AI side an **"AI backend/source,"** never an "AI model." Reserve "model" for the kraken artifact the archive trains and keeps.

---

## 9. The training flywheel + quality gate (the power path)

*Not the headline — see §1. Most users read/tweak/export and never reach here. This section matters for large homogeneous fonds and for material the AI reads poorly, where training the archive's own kraken model pays off.*


**AI transcribes a lot → archivist reviews a little → train the archive's own kraken model → apply to the rest.** The gate stops systematic AI errors — including "helpful" editorial ones — being baked in.

**Why confidence alone fails:** a VLM's dangerous errors are the ones it's *confident* about — silently expanding `q̃`/`ꝑ`/`⁊`, modernising spelling, consistently misreading a glyph. Sorting by confidence surfaces *uncertain* lines, not these. And VLM self-scores are miscalibrated; token logprobs aren't comparable to kraken's CTC softmax (`graphs` confidence, `models.py:1647`).

**The gate:**
1. **Dual-engine disagreement triage (primary).** Run a **cheap kraken pass** (CATMuS or whatever's on the instance) on the same segmented lines. Per-line signal = normalised edit distance / CER between AI and kraken. Sort review by disagreement; use any VLM self-score only as a weak prior.
2. **Non-skippable sample review.** A random sample **and** the disagreement tail, e.g. `max(50 lines, 2% of lines)`. Skipping requires a typed acknowledgement and **still cannot feed `core.tasks.train`**.
3. **Held-out CER via `ketos test`** on pages the archivist actually corrected — keep 10-20% of *reviewed* pages out. **Do not** hold out unreviewed AI text and call it evaluation, and **do not** read `OcrModel.training_accuracy` (a training-set metric, default `0.0`) as held-out CER.
4. **Layer state machine:** `raw → sampled → training-eligible`. Training's `transcription_pk` picker **omits** non-eligible layers by default.
5. **Diplomatic conventions are GT-critical** (§10.1): kraken learns to emit whatever string sits on the glyph. If the VLM wrote `que` for q-with-tilde, kraken learns `que`. Whether that's right (expanded) or a disaster (diplomatic/TEI/house rules) is the archive's call — so conventions must be pinned *before* harvesting GT.
6. **Lineage:** the resulting kraken model records `parent` and which AI layer/version + convention set it trained from.

The output is a **normal kraken model** — runs offline, zero AI dependency, produces real CTC `graphs` for ALTO.

---

## 10. The "send back to AI" loop

- **10.1 Style priming (core, not novelty).** Corrected (image, diplomatic-text) pairs become few-shot examples in `TranscriptionContext`. This is **how diplomatic conventions are enforced** and how house style propagates — the difference between usable and contaminated GT. Selection: **user-pinned gold → same document / block / script (recency) → visual similarity (embedding) only if the first two don't fill the cap.** Never pull examples across documents. Cap hard (4-8 gold pairs beat 30 mediocre; image few-shot is expensive). **Example store isolated per document/project** so restricted material can't leak into another document's prompt (a privacy control, §13).
- **10.2 Interactive "fix this."** Per-line/page: re-read using the archivist's partial correction as a guide. Good for stubborn lines; secondary to 10.1 for archival work.

Both are pure applications of §7 + the example store. Neither touches core.

---

## 11. Cost & quota control (first-class)

Informed by the €150 Google-Translate incident (global memory):
- **Pre-flight estimate at a fixed max image dimension.** VLM cost is dominated by *image tokens*, not the prompt — an unresized 400 DPI TIFF is how you recreate that bill. Downscale to a configured max edge before counting/sending. Show estimated cost; require confirmation above a threshold; never auto-spend.
- **Caps.** Per-job and per-month currency ceilings; hard stop. Copy the `has_free_*` assertion style (`tasks.py:577`) → `has_free_ai_budget()`.
- **Dedicated `AIUsageLedger`** (provider, model id, tokens in/out, est cost, actual cost, currency, user, document, task id). Link to `TaskReport`; **do not** repurpose `gpu_cost` (`reporting/models.py:65`).
- **Local backend** consumes GPU-minutes only (already metered); no currency meter.

---

## 12. Deployment: self-host AND multi-tenant

Self-host first (the OSS product): instance-level key + model in settings; quotas optional. Multi-tenant: per-user/team keys *or* operator-supplied keys with per-tenant caps + the §11 ledger. eScriptorium is already multi-user with per-user quotas, so the config-precedence mechanism in §7 covers both.

---

## 13. Security & privacy

- **Data egress is the headline archival concern.** Sending images to a third-party API may be unacceptable for restricted collections. The **local backend (§7.3) is the mitigation — first-class and documented.** A per-document **"never send off-site"** flag (`ai.AIDocumentPolicy`, additive — not a `core.Document` column) disables remote backends for that material, **enforced in `ai.tasks.assert_dispatch_allowed`** so a background job or raw `.delay()` cannot bypass it — never only in the UI. Missing policy row = allowed (self-host default).
- **Example-store isolation** per document/project (§10.1) so restricted text can't leak into another prompt.
- **Keys** encrypted at rest, never logged, redacted in error paths, scoped per tenant.
- **Prompt-injection** from text embedded in document images: transcription prompts must resist it; outputs are always reviewable, provenance-stamped, never auto-exported without a review state.
- **Provider ToS/training:** default to providers/settings that don't train on submitted data; document per backend.

---

## 14. Upstream relationship

Independent project derived from eScriptorium (not positioned as an eScriptorium fork, and not rebasable on upstream’s product roadmap). We restructure freely, **but** keep the AI layer as a self-contained `ai/` app — for legibility, not merge theatre. Still **track upstream eScriptorium security fixes** selectively (cherry-pick), since we inherit their attack surface.

---

## 15. Phased roadmap (for discussion)

- **Phase 0 — spike:** one line crop → one `LineTranscription` with `version_source="gemini..."`. Proves the write path. No UI.
- **Phase 1 — keyed region MVP (§5.A) + per-line fallback (§5.B):** `ai/` app; `AIBackend` (Gemini + local); `AIModel` config + `AIJob` (§8); `ai_transcribe` mirroring the `core.tasks.transcribe` contract; colour-keyed overlay renderer + JSON-by-key parsing; minimal UI (pick AI backend → run on selected parts/regions → new layer); cost pre-flight + `AIUsageLedger` (§11); dispatch-level egress policy (§13).
- **Phase 2 — the loops + gate:** dual-engine disagreement triage, non-skippable sample, layer state machine, `ketos test` CER (§9); style priming + conventions object (§10.1). *In tree now:* conventions toggles on the prompt; CER vs an existing comparison layer, or a random AI-line sample if none; `AILayerGate` (`raw → sampled → training-eligible`); admin acknowledge + `TrainSerializer`/`core.tasks.train` refusal of raw/sampled AI layers. *Not yet:* few-shot image priming, `ketos test` hook.
- **Phase 3 — hardening:** interactive "fix this" (§10.2); Passim fallback (§5.C); robustness on numbered-assignment failure modes.
- **Phase 4 — AI segmentation review (§6), multi-tenant hardening (§12), more providers.**
