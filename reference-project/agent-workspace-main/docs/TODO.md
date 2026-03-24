# Project TODOs

> Tracking known gaps, design plans, and future work for the Agentic Workspace Processor.

---

## Resume Screener Shell (`apps/resume_screener/`)

### 🔴 High Priority

| # | Gap | Impact | Design Doc | Status |
|---|-----|--------|------------|--------|
| 1 | **Single Position Matching** - Only evaluates against first position in `positions.json` | Candidates not matched to all open roles; may miss better fits for other positions | [07_MULTI_JD_DESIGN_PLAN.md](07_MULTI_JD_DESIGN_PLAN.md) | Design Complete |
| 2 | **Data Organization** - `sample_data/` mixes inputs, outputs, runtime data, and temp data | Risk of git committing generated files; unbounded data growth; confusion about data lifecycle | [08_DATA_ORGANIZATION_DESIGN_PLAN.md](08_DATA_ORGANIZATION_DESIGN_PLAN.md) | Design Complete |

### 🟡 Medium Priority

| # | Item | Description |
|---|------|-------------|
| 3 | Batch Processing | Process multiple resumes concurrently |
| 4 | Export Functionality | Export evaluations to CSV/Excel |
| 5 | Webhook Integration | Notify external systems on completion |
| 6 | Resume Parsing | Pre-fill candidate info from resume structure |

### 🟢 Low Priority / Ideas

| # | Item | Description |
|---|------|-------------|
| 7 | Comparison View | Side-by-side comparison of waitlisted candidates |
| 8 | Position Prioritization | Mark urgent positions for preferential matching |
| 9 | Candidate Self-Selection | Allow candidates to indicate target position via filename |
| 10 | Smart Pre-Filtering | Use keyword matching to reduce LLM calls for unlikely matches |

---

## Core (`agent_workspace/`)

No known gaps at this time. Core is stable and feature-complete for MVP scope.

---

## Future Shells

Potential new shell applications built on the core:

| Shell | Use Case | Complexity |
|-------|----------|------------|
| Document Analyzer | Generic document analysis with customizable criteria | Low |
| Meeting Minutes Processor | Extract action items and decisions from meeting transcripts | Medium |
| Contract Reviewer | Legal contract analysis against checklists | High |
| Research Synthesizer | Literature review and gap analysis | Medium |

---

## Design Document Index

| Doc | Title | Description |
|-----|-------|-------------|
| [01_BUILD_PLAN.md](01_BUILD_PLAN.md) | Original Build Plan | Phase-by-phase construction plan |
| [02_MVP_DESIGN.md](02_MVP_DESIGN.md) | MVP Design | Architecture and design decisions |
| [03_USER_GUIDE.md](03_USER_GUIDE.md) | User Guide | Comprehensive usage documentation |
| [04_ARCHITECTURE.md](04_ARCHITECTURE.md) | Architecture | System architecture details |
| [05_TESTING.md](05_TESTING.md) | Testing | Testing strategy and procedures |
| [06_E2E_RESULTS.md](06_E2E_RESULTS.md) | E2E Results | End-to-end test results |
| [07_MULTI_JD_DESIGN_PLAN.md](07_MULTI_JD_DESIGN_PLAN.md) | Multi-Position JD Matching | Design for matching candidates to all positions |
| [08_DATA_ORGANIZATION_DESIGN_PLAN.md](08_DATA_ORGANIZATION_DESIGN_PLAN.md) | Data Organization | Design for proper directory structure |

---

## Implementation Priority

### Phase 1: Critical Fixes
1. ✅ Design multi-position matching (DONE)
2. ✅ Design data organization (DONE)
3. ⬜ Implement multi-position matching
4. ⬜ Implement data organization refactor

### Phase 2: Enhancements
5. ⬜ Batch processing
6. ⬜ Export functionality
7. ⬜ Webhook integration

### Phase 3: Advanced Features
8. ⬜ Resume parsing
9. ⬜ Comparison view
10. ⬜ Smart pre-filtering

---

*Last Updated: 2026-03-12*
