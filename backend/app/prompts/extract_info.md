### Extract structured metadata (portfolio preset)

**Workspace context:** The system message names this entity (**{{entity_name}}**, website: **{{entity_website}}**). You will receive attached **resources** (files, URLs, text) and **artifact excerpts** selected for this run.

**Your task:** Infer structured investment metadata from those materials only (plus Google Search when enabled). Return **only** valid JSON matching the schema below—no markdown, no commentary.

**JSON schema (all top-level keys required; use null or [] where unknown):**

```json
{
  "company_name": { "value": "string or null", "confidence": "high|medium|low" },
  "founders": [
    {
      "name": "string",
      "title": "string or null",
      "background": "brief or null",
      "linkedin_url": "url or null",
      "confidence": "high|medium|low"
    }
  ],
  "industry_tags": ["string"],
  "investment_stage": { "value": "seed|pre_seed|series_a|series_b|angel|growth|unknown", "confidence": "high|medium|low" },
  "company_description": { "value": "2-3 sentences or null", "confidence": "high|medium|low" },
  "company_website": "https or null",
  "funding_ask": { "amount": "string or null", "currency": "USD|CNY|other|null", "confidence": "high|medium|low" } | null,
  "referral_source": "string or null",
  "priority_indicators": ["string"],
  "red_flags": ["string"],
  "competitors_mentioned": ["string"]
}
```

**Rules**
1. Set **confidence** to `high` only when the fact is explicit in the corpus; use `low` for inference.
2. Prefer **{{entity_name}}** as `company_name.value` if materials do not name a different operating company.
3. If a URL resource is listed, you may use search (when available) to supplement **public** facts; still mark uncertain fields with lower confidence.
4. Do not invent founders, funding figures, or traction not supported by sources.
5. Output **one JSON object** only—no wrapping array unless the schema explicitly allowed it (it does not).
