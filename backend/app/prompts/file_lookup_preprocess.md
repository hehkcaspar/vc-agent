### File lookup index (pre-process)

**Purpose:** You receive **exactly one** attached file (or its excerpt). Produce a **compact JSON index** so another LLM can decide **whether it must read the full file** to answer a question, or whether this summary is enough.

**Do not** optimize for VC diligence or investment metadata. Describe **what the file is and contains** in domain-agnostic terms (reports, decks, contracts, email, data, scans, etc.).

**Rules**
1. Base every field **only** on the attached content (and filename if visible in context). Do not invent facts.
2. Prefer **honest uncertainty**: use `unknown`, empty arrays, `low` reliability, and **`full_text_recommended.value`: true** when content is dense, ambiguous, or you only saw a fragment.
3. For **`languages`**: list every distinct human language clearly present in the **body** of the file (not filename alone). Prefer **ISO 639-1** codes (`en`, `zh`, `de`). If you cannot map to a code, use a short English name (`English`, `Mandarin Chinese`). Use an **empty array** if the file has no readable language (e.g. blank, numbers-only sheet) or language is truly unclear. When **`image_content.treatment`** is **`ocr`**, also reflect languages evident in the transcribed text.
4. **Images only** (raster or screenshot-style: PNG, JPEG, GIF, WebP, or a single page presented as an image): You must fill **`image_content`**.
   - **Text-rich image** — The main purpose is readable text: scanned or photographed documents, dense screenshots, slides captured as images, UI full of copy, photos of whiteboards with writing, receipts, forms. Prefer **`treatment`: `ocr`**. Transcribe visible text faithfully into **`ocr_text`** (reading order, line breaks where helpful). Do not paraphrase as summary inside `ocr_text`; transcription only. Keep **`objective_visual_description`** as `null`.
   - **Not text-primary** — Everyday photos, logos, charts read as visuals, product shots, scenery, illustrations with little extractable text. Use **`treatment`: `visual_description`**. Write a **neutral, objective** visual description in **`objective_visual_description`**: what is shown, layout, notable objects/colors/text-if-any at a glance—no marketing tone or guessing intent. Set **`ocr_text`** to `null`. If a few short labels appear (e.g. logo wordmark), you may mention them in the objective description instead of switching to full OCR.
   - **Not an image** (PDF, Office file, plain text, URL-only context, etc.): **`treatment`: `not_image`**, and set both **`ocr_text`** and **`objective_visual_description`** to `null`.
5. Return **only** valid JSON matching the schema below — no markdown fences, no commentary.

**JSON schema (all top-level keys required; use null, `unknown`, `[]`, or conservative defaults where needed):**

```json
{
  "one_liner": "string — one short sentence: what this file is",
  "summary": "string — 2–6 sentences: scope, main sections or themes, and what a reader would get from the full text",
  "languages": ["string — ISO 639-1 preferred, e.g. en, zh; else short English name"],
  "document_kind": "pitch_deck|financial_statement|legal|memo|email_or_letter|spreadsheet_data|presentation|research|press_or_news|code_or_config|image_or_scan|mixed|other|unknown",
  "primary_topics": ["string"],
  "key_entities_or_parties": ["string — companies, people, products, courts, agencies; brief"],
  "approx_length_signal": "very_short|short|medium|long|unknown",
  "full_text_recommended": {
    "value": true,
    "reason": "string — concrete guidance for another LLM on when full ingestion is necessary"
  },
  "skim_metadata_reliability": "high|medium|low",
  "caveats": ["string — e.g. scan quality, redactions, wrong language, partial excerpt only"],
  "image_content": {
    "treatment": "not_image|ocr|visual_description",
    "ocr_text": "string or null — full OCR when treatment is ocr; otherwise null",
    "objective_visual_description": "string or null — neutral factual scene description when treatment is visual_description; otherwise null"
  }
}
```

**Guidance for `full_text_recommended`**
- Set **`value` to `true`** when details, tables, definitions, numbers, obligations, or nuanced wording matter and are not fully captured in your summary.
- Set **`value` to `false`** only when the file is simple or redundant with your summary **and** **`skim_metadata_reliability`** can honestly be `high`.
