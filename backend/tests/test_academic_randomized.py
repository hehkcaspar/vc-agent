"""
Randomized E2E test for Academic Tracking v2 deep evaluation pipeline.

Selects 4 random scholars from the test pool of 20, varies URL input strategies,
and validates the full pipeline via the v2 scholar-centric API:
  Phase 1: Identity extraction (enriched profile.json with identity.google_scholar etc.)
  Phase 2: Semantic Scholar papers + author position (papers.json)
  Phase 3-4: Computed metrics + AI evaluation (dimension-based evaluations/*.json)
  Phase 5: Enhanced report generation (reports/*.md)

Run from backend/:
    ../venv/bin/python tests/test_academic_randomized.py

Reproducible runs:
    RANDOM_SEED=42 ../venv/bin/python tests/test_academic_randomized.py

Control sample size:
    SCHOLAR_COUNT=2 ../venv/bin/python tests/test_academic_randomized.py
"""

import json
import logging
import os
import random
import shutil
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger("test_academic_randomized")

# ── Config ────────────────────────────────────────────────────

TEST_DIR = os.path.join(tempfile.gettempdir(), "academic_rand_v2_test")
TEST_DB = os.path.join(TEST_DIR, "test.db")
TEST_ACADEMIC_DB = os.path.join(TEST_DIR, "academic.db")
TEST_SCHOLARS_DIR = os.path.join(TEST_DIR, "scholars")
TEST_CONFIG_DIR = os.path.join(TEST_DIR, "config")

os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{TEST_DB}"
os.environ["ACADEMIC_DATABASE_URL"] = f"sqlite+aiosqlite:///{TEST_ACADEMIC_DB}"
os.environ["ACADEMIC_SCHOLARS_DIR"] = TEST_SCHOLARS_DIR
os.environ["ACADEMIC_CONFIG_DIR"] = TEST_CONFIG_DIR

SEED = int(os.environ.get("RANDOM_SEED", str(random.randint(0, 999999))))
SCHOLAR_COUNT = int(os.environ.get("SCHOLAR_COUNT", "4"))
PIPELINE_TIMEOUT = int(os.environ.get("PIPELINE_TIMEOUT", "480"))  # seconds
PORT = 8879
BASE = f"http://127.0.0.1:{PORT}"

# Report sections expected in the generated markdown
EXPECTED_REPORT_SECTIONS = [
    "executive summary",
    "research profile",
    "impact analysis",
    "authorship analysis",
    "key publications",
    "career trajectory",
    "commercialization potential",
    "collaboration network",
    "evaluation scores",
    "summary",
    "vc recommendation",
]

URL_STRATEGIES = ["single-homepage", "single-gs", "partial", "all"]


# ── Result tracking ──────────────────────────────────────────

@dataclass
class PhaseResult:
    passed: bool = True
    fails: list[str] = field(default_factory=list)
    warns: list[str] = field(default_factory=list)
    info: dict[str, Any] = field(default_factory=dict)


@dataclass
class ScholarResult:
    name: str
    field_: str
    career_stage: str
    url_strategy: str
    elapsed: int = 0
    pipeline_status: str = "unknown"
    phase1: PhaseResult = field(default_factory=PhaseResult)
    phase2: PhaseResult = field(default_factory=PhaseResult)
    phase3_4: PhaseResult = field(default_factory=PhaseResult)  # metrics + eval
    phase5: PhaseResult = field(default_factory=PhaseResult)    # report

    @property
    def overall_pass(self) -> bool:
        return all(p.passed for p in [self.phase1, self.phase2, self.phase3_4, self.phase5])

    @property
    def total_warns(self) -> int:
        return sum(len(p.warns) for p in [self.phase1, self.phase2, self.phase3_4, self.phase5])


# ── Server ────────────────────────────────────────────────────

def start_server():
    import uvicorn
    from contextlib import asynccontextmanager

    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware

    from app.academic_database import init_academic_db
    from app.database import init_db
    from app.routers.academic import router as academic_router

    @asynccontextmanager
    async def lifespan(app):
        await init_db()
        await init_academic_db()
        yield

    app = FastAPI(lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(academic_router)

    @app.get("/health")
    async def health():
        return {"status": "healthy"}

    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")


def wait_for_server():
    import httpx

    for _ in range(30):
        try:
            if httpx.get(f"{BASE}/health", timeout=2).status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


# ── URL strategy ──────────────────────────────────────────────

def choose_url_strategy(urls: list[str]) -> tuple[str, list[str]]:
    """Pick a URL strategy and return (strategy_name, urls_list) for v2 API."""
    if len(urls) == 1:
        return "single-only", urls

    gs_urls = [u for u in urls if "scholar.google" in u.lower()]
    non_gs_urls = [u for u in urls if "scholar.google" not in u.lower()]

    strategy = random.choice(URL_STRATEGIES)

    if strategy == "single-homepage":
        if non_gs_urls:
            return strategy, [non_gs_urls[0]]
        return strategy, [urls[0]]

    elif strategy == "single-gs":
        if gs_urls:
            return strategy, [gs_urls[0]]
        # Fallback: no GS URL, use first URL
        return "single-homepage", [urls[0]]

    elif strategy == "partial":
        remaining = urls[1:]
        k = random.randint(1, max(1, len(remaining) - 1))
        subset = [urls[0]] + random.sample(remaining, k)
        return strategy, subset

    else:  # all
        return strategy, urls


def gs_url_was_provided(strategy: str, urls_list: list[str]) -> bool:
    """Check if a Google Scholar URL was included in the input."""
    return any("scholar.google" in u.lower() for u in urls_list)


# ── Validation functions ──────────────────────────────────────

def validate_phase1(scholar: dict, expected: dict, had_gs_url: bool) -> PhaseResult:
    """Validate identity extraction results from enriched scholar response."""
    r = PhaseResult()

    name = scholar.get("name", "")
    if not name:
        r.fails.append("name is empty")
        r.passed = False
    r.info["name"] = name

    # Google Scholar ID — in v2 it lives in identity.google_scholar.id
    identity = scholar.get("identity") or {}
    gs_identity = identity.get("google_scholar", {}) or {}
    gs_id = gs_identity.get("id")
    expected_gs_id = expected.get("google_scholar_id")
    r.info["google_scholar_id"] = gs_id
    if had_gs_url and expected_gs_id:
        if gs_id != expected_gs_id:
            r.fails.append(f"google_scholar_id mismatch: got {gs_id}, expected {expected_gs_id}")
            r.passed = False
    elif not gs_id and not had_gs_url:
        r.warns.append("google_scholar_id missing (no GS URL provided)")

    # H-Index — enriched from profile.json metrics
    h = scholar.get("h_index")
    min_h = expected.get("min_h_index", 0)
    r.info["h_index"] = h
    if h is not None:
        threshold = min_h * 0.5
        if h < threshold:
            r.warns.append(f"h_index={h} below threshold ({threshold}, min_expected={min_h})")
    elif had_gs_url:
        r.fails.append("h_index is null despite GS URL being provided")
        r.passed = False
    else:
        r.warns.append("h_index is null")

    # Affiliation — enriched from profile.json
    r.info["affiliation"] = scholar.get("affiliation")
    if not scholar.get("affiliation"):
        r.warns.append("affiliation is null")

    # i10-index
    r.info["i10_index"] = scholar.get("i10_index")
    if scholar.get("i10_index") is None:
        r.warns.append("i10_index is null")

    # Total citations
    r.info["total_citations"] = scholar.get("total_citations")
    if not scholar.get("total_citations"):
        r.warns.append("total_citations is null or 0")

    # Research areas
    areas = scholar.get("research_areas", [])
    r.info["research_areas"] = areas
    if not areas:
        r.warns.append("research_areas is empty")

    # Identity sub-profiles discovered
    identity_keys = list(identity.keys()) if isinstance(identity, dict) else []
    r.info["identity_keys"] = identity_keys
    if not identity_keys:
        r.warns.append("identity is empty (no profiles discovered)")

    return r


def validate_phase2(scholar: dict, papers_response: dict, expected: dict) -> PhaseResult:
    """Validate Semantic Scholar resolution, papers, and author positions.

    papers_response is the v2 PapersResponse: {papers: [], summary: {}, total: int}
    """
    r = PhaseResult()

    papers = papers_response.get("papers", [])
    total = papers_response.get("total", 0)
    summary = papers_response.get("summary", {})
    min_papers = expected.get("min_papers", 0)
    r.info["paper_count"] = total
    r.info["papers_summary"] = summary

    if total == 0:
        r.warns.append("no papers stored (SS author may not have been resolved)")
    else:
        threshold = min_papers * 0.3
        if total < threshold:
            r.warns.append(
                f"paper_count={total} below threshold ({threshold}, min_expected={min_papers})"
            )

    # Check author_position coverage on papers
    if papers:
        positions = [p.get("author_position") for p in papers]
        has_position = [p for p in positions if p is not None]
        position_pct = len(has_position) / len(positions) * 100 if positions else 0
        r.info["author_position_coverage"] = f"{len(has_position)}/{len(positions)} ({position_pct:.0f}%)"

        if position_pct < 30:
            r.warns.append(
                f"author_position coverage low: {position_pct:.0f}% "
                f"({len(has_position)}/{len(positions)} papers)"
            )

        # Distribution of positions
        from collections import Counter
        pos_counts = Counter(p for p in positions if p is not None)
        r.info["position_distribution"] = dict(pos_counts)

        # At least some first or last author papers expected for any active scholar
        if position_pct > 50 and not pos_counts.get("first") and not pos_counts.get("last"):
            r.warns.append("no first or last author papers found despite good coverage")

        # Check paper fields
        sample = papers[:5]
        has_venue = sum(1 for p in sample if p.get("venue"))
        has_inf_cit = sum(1 for p in sample if p.get("influential_citations", 0) > 0)
        r.info["sample_venue_count"] = has_venue
        r.info["sample_influential_count"] = has_inf_cit

        # Log top 3 papers
        top = sorted(papers, key=lambda p: p.get("citations", 0), reverse=True)[:3]
        r.info["top_papers"] = [
            {
                "title": p.get("title", "?")[:80],
                "year": p.get("year"),
                "citations": p.get("citations", 0),
                "author_position": p.get("author_position"),
            }
            for p in top
        ]
    else:
        r.info["author_position_coverage"] = "N/A (no papers)"

    return r


def validate_phase3_4(evaluations_response: dict, scholar_name: str) -> PhaseResult:
    """Validate metrics computation (Phase 3) and AI evaluation (Phase 4).

    evaluations_response is the v2 EvaluationListResponse: {evaluations: [...]}
    Each evaluation has dimensions dict with {score, explanation, evidence} per dimension,
    plus computed_metrics, field_context, commercialization_signals.
    """
    r = PhaseResult()

    evaluations = evaluations_response.get("evaluations", [])
    if not evaluations:
        r.warns.append("no evaluation record found")
        r.info["has_evaluation"] = False
        return r

    # Use the most recent evaluation
    evaluation = evaluations[0]
    r.info["has_evaluation"] = True
    r.info["evaluation_id"] = evaluation.get("id")
    r.info["evaluation_type"] = evaluation.get("type")
    r.info["evaluation_trigger"] = evaluation.get("trigger")

    # ── Phase 3: Computed metrics ──
    computed = evaluation.get("computed_metrics", {})
    r.info["first_author_papers"] = computed.get("first_author_papers", 0)
    r.info["last_author_papers"] = computed.get("last_author_papers", 0)
    r.info["sole_author_papers"] = computed.get("sole_author_papers", 0)
    r.info["first_author_citation_pct"] = computed.get("first_author_citation_pct")

    career_years = computed.get("career_years")
    r.info["career_years"] = career_years
    if career_years is not None and career_years <= 0:
        r.warns.append(f"career_years={career_years} seems wrong")

    r.info["papers_per_year_avg"] = computed.get("papers_per_year_avg")
    r.info["papers_per_year_recent"] = computed.get("papers_per_year_recent")
    r.info["citation_growth_rate"] = computed.get("citation_growth_rate")
    r.info["unique_coauthors"] = computed.get("unique_coauthors")
    r.info["influential_paper_count"] = computed.get("influential_paper_count", 0)
    r.info["top_venue_papers"] = computed.get("top_venue_papers", 0)
    r.info["recent_5yr_papers"] = computed.get("recent_5yr_papers", 0)

    # ── Phase 4: AI evaluation — dimension-based scores ──
    dimensions = evaluation.get("dimensions", {})
    r.info["dimensions_present"] = list(dimensions.keys())

    if not dimensions:
        r.warns.append("no dimensions in evaluation (scoring may have failed)")
    else:
        # Validate each dimension has score, explanation, evidence
        null_scores = []
        for dim_name, dim_data in dimensions.items():
            score = dim_data.get("score") if isinstance(dim_data, dict) else None
            explanation = dim_data.get("explanation", "") if isinstance(dim_data, dict) else ""
            evidence = dim_data.get("evidence", []) if isinstance(dim_data, dict) else []

            r.info[f"dim_{dim_name}_score"] = score
            if score is None:
                null_scores.append(dim_name)
            elif not (0 <= score <= 100):
                r.warns.append(f"dimension {dim_name} score={score} out of range [0,100]")

            if not explanation:
                r.warns.append(f"dimension {dim_name} has no explanation")

        if null_scores:
            r.warns.append(f"null dimension scores: {', '.join(null_scores)}")

    # Commercialization signals (structured data)
    comm = evaluation.get("commercialization_signals", {})
    r.info["has_commercialization_signals"] = bool(comm)
    if isinstance(comm, dict):
        r.info["patents_found"] = len(comm.get("patents", []))
        r.info["startups_found"] = len(comm.get("startups", []))
        r.info["industry_collabs_found"] = len(comm.get("industry_collabs", []))
    elif isinstance(comm, list):
        r.info["commercialization_items"] = len(comm)

    # Field context
    fc = evaluation.get("field_context", {})
    r.info["has_field_context"] = bool(fc)
    if fc:
        r.info["primary_field"] = fc.get("primary_field")
        r.info["percentile_estimate"] = fc.get("percentile_estimate")

    # Delta (if this is a re-evaluation)
    delta = evaluation.get("delta")
    r.info["has_delta"] = delta is not None

    return r


def validate_phase5(client, scholar_id: str, reports_response: dict, scholar_name: str) -> PhaseResult:
    """Validate enhanced report generation.

    reports_response is the v2 ReportListResponse: {reports: [{id, filename, report_type, created_at}]}
    Content requires a second fetch via GET /academic/scholars/{id}/reports/{report_id}.
    """
    r = PhaseResult()

    reports = reports_response.get("reports", [])
    if not reports:
        r.fails.append("no reports generated")
        r.passed = False
        return r

    # Fetch the content of the first (most recent) report
    report_meta = reports[0]
    report_id = report_meta.get("id")
    r.info["report_id"] = report_id
    r.info["report_type"] = report_meta.get("report_type")
    r.info["report_filename"] = report_meta.get("filename")

    resp = client.get(f"/academic/scholars/{scholar_id}/reports/{report_id}")
    if resp.status_code != 200:
        r.fails.append(f"failed to fetch report content: HTTP {resp.status_code}")
        r.passed = False
        return r

    report = resp.json()
    content = report.get("content", "") or ""
    r.info["content_length"] = len(content)

    if len(content) < 200:
        r.fails.append(f"report too short ({len(content)} chars)")
        r.passed = False
        return r

    # Scholar name should appear in report
    if scholar_name.lower() not in content.lower():
        # Try partial match (first name or last name)
        name_parts = scholar_name.lower().split()
        if not any(part in content.lower() for part in name_parts if len(part) > 2):
            r.warns.append("scholar name not found in report content")

    # Count section headers found (case-insensitive)
    content_lower = content.lower()
    found_sections = [s for s in EXPECTED_REPORT_SECTIONS if s in content_lower]
    r.info["sections_found"] = len(found_sections)
    r.info["sections_matched"] = found_sections

    # With the enhanced pipeline, we expect at least 4 of the known sections
    if len(found_sections) < 4:
        r.warns.append(
            f"only {len(found_sections)}/{len(EXPECTED_REPORT_SECTIONS)} expected sections found: "
            f"{found_sections}"
        )

    return r


# ── Test execution ────────────────────────────────────────────

def run_scholar_test(client, scholar_entry: dict) -> ScholarResult:
    """Run the full pipeline for one scholar and validate all phases."""
    name = scholar_entry["name"]
    urls = scholar_entry["urls"]
    expected = scholar_entry["expected"]

    strategy, urls_list = choose_url_strategy(urls)
    had_gs_url = gs_url_was_provided(strategy, urls_list)

    result = ScholarResult(
        name=name,
        field_=scholar_entry["field"],
        career_stage=scholar_entry["career_stage"],
        url_strategy=strategy,
    )

    logger.info(
        "Testing %s [%s] strategy=%s urls=%s",
        name, scholar_entry["field"], strategy, urls_list,
    )

    # Create scholar via v2 API
    payload: dict[str, Any] = {
        "name": name,
        "urls": urls_list,
        "tracking_priority": "high",
        "tags": [scholar_entry["field"], scholar_entry["career_stage"]],
    }

    r = client.post("/academic/scholars", json=payload)
    if r.status_code != 200:
        result.phase1.fails.append(f"scholar creation failed: HTTP {r.status_code} — {r.text[:200]}")
        result.phase1.passed = False
        return result

    scholar_id = r.json()["id"]

    # Execute evaluation pipeline
    r = client.post(f"/academic/scholars/{scholar_id}/evaluate")
    if r.status_code != 200:
        result.phase1.fails.append(f"evaluate failed: HTTP {r.status_code} — {r.text[:200]}")
        result.phase1.passed = False
        return result

    # Poll until completion — status is a string in v2
    start = time.time()
    status = "evaluating"
    while time.time() - start < PIPELINE_TIMEOUT:
        r = client.get(f"/academic/scholars/{scholar_id}")
        status = r.json()["status"]
        if status != "evaluating":
            break
        elapsed = int(time.time() - start)
        if elapsed % 15 == 0:
            logger.info("  [%s] %ds — still evaluating...", name, elapsed)
        time.sleep(5)

    result.elapsed = int(time.time() - start)
    result.pipeline_status = status

    if status != "active":
        if result.elapsed >= PIPELINE_TIMEOUT:
            result.phase1.fails.append(f"pipeline timed out after {PIPELINE_TIMEOUT}s")
        else:
            result.phase1.fails.append(f"pipeline ended with status={status}")
        result.phase1.passed = False
        return result

    # ── Fetch data for validation ──

    # Scholar record (single scholar, enriched with profile.json data)
    r = client.get(f"/academic/scholars/{scholar_id}")
    if r.status_code != 200:
        result.phase1.fails.append(f"GET scholar failed: HTTP {r.status_code}")
        result.phase1.passed = False
        return result

    scholar = r.json()

    # Papers — v2 returns PapersResponse {papers: [], summary: {}, total: int}
    r = client.get(f"/academic/scholars/{scholar_id}/papers?limit=100")
    papers_response = r.json() if r.status_code == 200 else {"papers": [], "summary": {}, "total": 0}

    # Evaluations — v2 returns {evaluations: [...]} with dimension-based format
    r = client.get(f"/academic/scholars/{scholar_id}/evaluations")
    evaluations_response = r.json() if r.status_code == 200 else {"evaluations": []}

    # Reports — v2 returns {reports: [{id, filename, report_type, created_at}]}
    r = client.get(f"/academic/scholars/{scholar_id}/reports")
    reports_response = r.json() if r.status_code == 200 else {"reports": []}

    # ── Validate each phase ──
    result.phase1 = validate_phase1(scholar, expected, had_gs_url)
    result.phase2 = validate_phase2(scholar, papers_response, expected)
    result.phase3_4 = validate_phase3_4(evaluations_response, name)
    result.phase5 = validate_phase5(client, scholar_id, reports_response, name)

    return result


# ── Output formatting ─────────────────────────────────────────

def print_result(result: ScholarResult):
    status_str = "PASS" if result.overall_pass else "FAIL"
    warn_str = f" ({result.total_warns} warns)" if result.total_warns else ""

    print(f"\n{'='*70}")
    print(f"SCHOLAR: {result.name}  [{result.career_stage}]  [{result.field_}]")
    print(f"URL Strategy: {result.url_strategy}")
    print(f"Pipeline: status={result.pipeline_status} in {result.elapsed}s  [{status_str}{warn_str}]")
    print(f"{'-'*70}")

    # Phase 1
    p1 = result.phase1
    print(f"\nPhase 1 — Identity:")
    print(f"  Name: {p1.info.get('name', '?')}")
    print(f"  Affiliation: {p1.info.get('affiliation', '?')}")
    gs_id = p1.info.get('google_scholar_id')
    print(f"  Google Scholar ID: {gs_id or 'None'}")
    print(f"  H-Index: {p1.info.get('h_index', '?')}")
    print(f"  Citations: {p1.info.get('total_citations', '?')}")
    areas = p1.info.get('research_areas', [])
    print(f"  Research Areas: {areas[:5]}")
    identity_keys = p1.info.get('identity_keys', [])
    print(f"  Identity Profiles: {identity_keys}")
    for f in p1.fails:
        print(f"  [FAIL] {f}")
    for w in p1.warns:
        print(f"  [WARN] {w}")

    # Phase 2
    p2 = result.phase2
    print(f"\nPhase 2 — Papers + Author Position:")
    print(f"  Papers stored: {p2.info.get('paper_count', 0)}")
    print(f"  Author position coverage: {p2.info.get('author_position_coverage', '?')}")
    summary = p2.info.get('papers_summary', {})
    if summary:
        print(f"  Papers summary: {summary}")
    pos_dist = p2.info.get('position_distribution', {})
    if pos_dist:
        print(f"  Position distribution: {pos_dist}")
    top_papers = p2.info.get('top_papers', [])
    if top_papers:
        print(f"  Top papers:")
        for p in top_papers:
            pos = f" [{p.get('author_position', '?')}]" if p.get('author_position') else ""
            print(f"    - {p['title']} ({p.get('year', '?')}) — {p['citations']} cit.{pos}")
    for f in p2.fails:
        print(f"  [FAIL] {f}")
    for w in p2.warns:
        print(f"  [WARN] {w}")

    # Phase 3-4
    p34 = result.phase3_4
    print(f"\nPhase 3-4 — Metrics + AI Evaluation:")
    if p34.info.get("has_evaluation"):
        eval_id = p34.info.get("evaluation_id", "?")
        eval_type = p34.info.get("evaluation_type", "?")
        print(f"  Evaluation: {eval_id} (type={eval_type})")

        # Dimension scores
        dims = p34.info.get("dimensions_present", [])
        if dims:
            print(f"  Dimensions ({len(dims)}):")
            for dim_name in dims:
                score = p34.info.get(f"dim_{dim_name}_score")
                label = dim_name.replace("_", " ").title()
                print(f"    {label}: {score if score is not None else '?'}/100")
        else:
            print(f"  No dimensions found")

        print(f"  Career: {p34.info.get('career_years', '?')} yrs | "
              f"papers/yr avg={p34.info.get('papers_per_year_avg', '?')} "
              f"recent={p34.info.get('papers_per_year_recent', '?')}")
        print(f"  Citation growth: {p34.info.get('citation_growth_rate', '?')}%")
        print(f"  Authorship: first={p34.info.get('first_author_papers', 0)} "
              f"last={p34.info.get('last_author_papers', 0)} "
              f"sole={p34.info.get('sole_author_papers', 0)} "
              f"1st-author-cit-pct={p34.info.get('first_author_citation_pct', '?')}%")
        print(f"  Influential papers: {p34.info.get('influential_paper_count', 0)} | "
              f"Top venue: {p34.info.get('top_venue_papers', 0)}")
        print(f"  Coauthors: {p34.info.get('unique_coauthors', '?')}")
        if p34.info.get("has_field_context"):
            print(f"  Field: {p34.info.get('primary_field', '?')} "
                  f"~{p34.info.get('percentile_estimate', '?')}th percentile")
        if p34.info.get("has_commercialization_signals"):
            print(f"  Commercialization: patents={p34.info.get('patents_found', 0)} "
                  f"startups={p34.info.get('startups_found', 0)} "
                  f"industry={p34.info.get('industry_collabs_found', 0)}")
        if p34.info.get("has_delta"):
            print(f"  Delta: present (re-evaluation)")
    else:
        print(f"  No evaluation record found")
    for f in p34.fails:
        print(f"  [FAIL] {f}")
    for w in p34.warns:
        print(f"  [WARN] {w}")

    # Phase 5
    p5 = result.phase5
    print(f"\nPhase 5 — Enhanced Report:")
    print(f"  Report ID: {p5.info.get('report_id', 'N/A')}")
    print(f"  Report type: {p5.info.get('report_type', 'N/A')}")
    print(f"  Content length: {p5.info.get('content_length', 0)} chars")
    print(f"  Sections found: {p5.info.get('sections_found', 0)}/{len(EXPECTED_REPORT_SECTIONS)}")
    matched = p5.info.get('sections_matched', [])
    if matched:
        print(f"  Matched: {matched}")
    for f in p5.fails:
        print(f"  [FAIL] {f}")
    for w in p5.warns:
        print(f"  [WARN] {w}")

    print(f"{'='*70}")


def print_summary(results: list[ScholarResult], seed: int, total_elapsed: int):
    passed = sum(1 for r in results if r.overall_pass)
    warned = sum(1 for r in results if r.overall_pass and r.total_warns > 0)
    failed = sum(1 for r in results if not r.overall_pass)

    strategies_used = [r.url_strategy for r in results]
    stages = [r.career_stage for r in results]

    print(f"\n{'#'*70}")
    print(f"SUMMARY (seed={seed})")
    print(f"{'#'*70}")
    print(f"Scholars tested: {len(results)}")
    print(f"  PASSED: {passed} ({warned} with warnings)")
    print(f"  FAILED: {failed}")
    print(f"Total elapsed: {total_elapsed}s")
    print(f"URL strategies: {', '.join(strategies_used)}")
    print(f"Career stages: {', '.join(stages)}")

    if failed:
        print(f"\nFailed scholars:")
        for r in results:
            if not r.overall_pass:
                all_fails = (
                    r.phase1.fails + r.phase2.fails +
                    r.phase3_4.fails + r.phase5.fails
                )
                print(f"  - {r.name}: {'; '.join(all_fails)}")

    # Aggregate evaluation quality — v2 uses dimension scores, no overall_score
    eval_scholars = [r for r in results if r.phase3_4.info.get("has_evaluation")]
    if eval_scholars:
        # Collect all dimension scores across scholars
        all_dim_scores: dict[str, list[int]] = {}
        for r in eval_scholars:
            for dim in r.phase3_4.info.get("dimensions_present", []):
                score = r.phase3_4.info.get(f"dim_{dim}_score")
                if score is not None:
                    all_dim_scores.setdefault(dim, []).append(score)

        if all_dim_scores:
            print(f"\nDimension scores across tested scholars:")
            for dim_name, scores in sorted(all_dim_scores.items()):
                label = dim_name.replace("_", " ").title()
                print(f"  {label}: min={min(scores)} max={max(scores)} avg={sum(scores)/len(scores):.0f}")

    pos_scholars = [r for r in results if r.phase2.info.get("position_distribution")]
    if pos_scholars:
        print(f"\nAuthor position coverage:")
        for r in pos_scholars:
            print(f"  {r.name}: {r.phase2.info.get('position_distribution', {})}")

    print(f"{'#'*70}")

    return failed == 0


# ── Main ──────────────────────────────────────────────────────

def main():
    import httpx

    # Clean up old test dir
    if os.path.exists(TEST_DIR):
        shutil.rmtree(TEST_DIR)
    os.makedirs(TEST_DIR, exist_ok=True)

    # Load test scholars
    scholars_file = os.path.join(
        os.path.dirname(__file__), "support", "academic_test_scholars.json"
    )
    with open(scholars_file) as f:
        data = json.load(f)
    all_scholars = data["scholars"]

    # Random selection
    random.seed(SEED)
    count = min(SCHOLAR_COUNT, len(all_scholars))
    selected = random.sample(all_scholars, count)

    print(f"Random seed: {SEED}")
    print(f"Test dir: {TEST_DIR}")
    print(f"Selected {count} scholars: {[s['name'] for s in selected]}")

    # Start server
    print("\nStarting isolated test server...")
    threading.Thread(target=start_server, daemon=True).start()
    assert wait_for_server(), "Server failed to start"
    print("Server ready.\n")

    client = httpx.Client(base_url=BASE, timeout=httpx.Timeout(30.0))
    results: list[ScholarResult] = []
    total_start = time.time()

    for scholar_entry in selected:
        result = run_scholar_test(client, scholar_entry)
        results.append(result)
        print_result(result)

    total_elapsed = int(time.time() - total_start)
    client.close()

    success = print_summary(results, SEED, total_elapsed)

    # Cleanup
    if os.path.exists(TEST_DIR):
        shutil.rmtree(TEST_DIR)

    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()
