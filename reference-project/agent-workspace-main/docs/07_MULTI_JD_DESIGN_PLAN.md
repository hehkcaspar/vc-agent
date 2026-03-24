# Multi-Position JD Matching Design Plan

> Design document for enabling resume screener to match candidates against all available positions, not just the first one.

---

## Executive Summary

**Current Gap**: The resume screener only evaluates candidates against the first position in `positions.json` (hardcoded `positions[0]`), ignoring other open roles.

**Proposed Solution**: Parallel multi-position screening that evaluates candidates against all available positions concurrently, then recommends the best match.

**Scope**: Shell-level changes only (`apps/resume_screener/`). Core `agent_workspace` remains unchanged.

---

## 1. Current State Analysis

### 1.1 The Problem

```python
# screener.py:screen() - line ~506-518
async def screen(self, resume_id: str, resume_path: Path, position_id: Optional[str] = None):
    if position_id:
        position = self.jd_store.get_position(position_id)
    else:
        positions = self.jd_store.list_positions()
        position = positions[0] if positions else None  # ← ALWAYS first position!
```

### 1.2 Current Data Flow

```
Resume Detected
    ↓
Single Workspace Created (1 position's JD as instructions)
    ↓
Single Agent Run
    ↓
Single ScreeningResult (one position_id)
    ↓
Single Conclusion Display
```

### 1.3 Sample Data Reality

The `positions.json` contains **3 distinct positions**:
- `software-engineer` - Technical/Engineering role
- `growth-design-lead` - Design/Growth role  
- `office-assistant` - Administrative/Operations role

These roles have **non-overlapping requirements** - a designer shouldn't be evaluated against engineering criteria.

---

## 2. Design Philosophy

### 2.1 Core Principles

1. **Shell Responsibility**: Multi-position matching is domain-specific logic that belongs in the shell, not the core
2. **Parallel over Sequential**: Use concurrent evaluation for faster response times
3. **Best-Match First**: Present the strongest match prominently, with alternatives accessible
4. **Graceful Degradation**: Partial failures don't break the entire evaluation

### 2.2 Design Boundaries

| Layer | Responsibility | Changes Needed |
|-------|---------------|----------------|
| `agent_workspace` (core) | Domain-agnostic ReAct agent | None |
| `apps/resume_screener` (shell) | Position matching, result aggregation | Yes |
| `positions.json` | JD definitions | None |

---

## 3. Proposed Design: Parallel Multi-Position Screening

### 3.1 High-Level Flow

```
Resume Detected
    ↓
┌─────────────────────────────────────────────────────────────┐
│  Parallel Position Evaluation (asyncio.gather)              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐       │
│  │ JD 1: Engineer│  │ JD 2: Design │  │ JD 3: Admin  │       │
│  │ - Workspace 1 │  │ - Workspace 2│  │ - Workspace 3│       │
│  │ - Agent Run 1 │  │ - Agent Run 2│  │ - Agent Run 3│       │
│  │ - Result 1    │  │ - Result 2   │  │ - Result 3   │       │
│  └──────────────┘  └──────────────┘  └──────────────┘       │
└─────────────────────────────────────────────────────────────┘
    ↓
Best Match Selection Algorithm
    ↓
MultiPositionResult (aggregated)
    ↓
Rich Conclusion Display (best match + alternatives)
```

### 3.2 Data Model Changes

#### 3.2.1 New: MultiPositionResult

```python
@dataclass
class MultiPositionResult:
    """Aggregated screening result across all positions."""
    id: str  # eval_{resume_id}_multi
    resume_id: str
    candidate_name: Optional[str]
    
    # Overall recommendation
    overall_verdict: str  # "invite", "waitlist", "reject"
    overall_confidence: str  # "high", "medium", "low"
    
    # Best match identification
    best_match_position_id: Optional[str]
    best_match_score: int  # 0-100
    
    # All evaluations
    position_evaluations: List[ScreeningResult]  # One per position
    
    # Cross-position analysis
    cross_position_analysis: str  # Agent or rule-based reasoning
    candidate_summary: str  # Position-agnostic candidate summary
    
    # Metadata
    total_positions_evaluated: int
    successful_evaluations: int
    failed_evaluations: int
    
    processing_time_seconds: float
    evaluated_at: str
```

#### 3.2.2 Modified: ScreeningResult

```python
@dataclass
class ScreeningResult:
    """Single position evaluation result (existing, with additions)."""
    id: str
    resume_id: str
    position_id: str
    position_title: str  # NEW: for display without JD lookup
    
    candidate_name: Optional[str]
    verdict: str  # "invite", "waitlist", "reject"
    confidence: str  # "high", "medium", "low"
    match_score: int  # NEW: 0-100 computed score
    
    summary: str
    strengths: List[str]
    gaps: List[str]
    experience_years: Optional[float]
    skills_match: Dict[str, Any]
    ai_competency: Dict[str, Any]
    reasoning: str
    
    evaluated_at: str
    processing_time_seconds: float
    raw_output: str
```

#### 3.2.3 Modified: ResumeFile

```python
@dataclass
class ResumeFile:
    """Resume file in the system (existing, with additions)."""
    id: str
    original_name: str
    file_path: Path
    file_size: int
    created_at: datetime
    status: str = "pending"  # pending, processing, completed, error
    
    # Evaluation tracking
    evaluation_id: Optional[str] = None  # MultiPositionResult ID
    position_evaluation_ids: List[str] = field(default_factory=list)  # NEW: Individual results
```

### 3.3 Screener Logic Changes

#### 3.3.1 New Method: `screen_all_positions()`

```python
async def screen_all_positions(
    self, 
    resume_id: str, 
    resume_path: Path
) -> MultiPositionResult:
    """
    Screen a resume against all available positions in parallel.
    
    Args:
        resume_id: Unique identifier for the resume
        resume_path: Path to the resume file
        
    Returns:
        MultiPositionResult with aggregated evaluations and best match
    """
    start_time = datetime.now()
    positions = self.jd_store.list_positions()
    
    if not positions:
        raise ValueError("No positions configured")
    
    # Create evaluation tasks for all positions
    evaluation_tasks = [
        self._evaluate_single_position(resume_id, resume_path, position)
        for position in positions
    ]
    
    # Execute all evaluations in parallel
    results = await asyncio.gather(*evaluation_tasks, return_exceptions=True)
    
    # Process results: separate successes from failures
    successful_evaluations = []
    failed_count = 0
    
    for result in results:
        if isinstance(result, ScreeningResult):
            successful_evaluations.append(result)
        else:
            failed_count += 1
            logger.error(f"Position evaluation failed: {result}")
    
    # Determine best match from successful evaluations
    best_match = self._determine_best_match(successful_evaluations)
    
    # Generate cross-position analysis
    cross_analysis = self._generate_cross_position_analysis(
        successful_evaluations, best_match
    )
    
    # Extract candidate summary (position-agnostic)
    candidate_summary = self._extract_candidate_summary(successful_evaluations)
    
    # Compute overall verdict
    overall_verdict = self._compute_overall_verdict(
        successful_evaluations, best_match
    )
    
    processing_time = (datetime.now() - start_time).total_seconds()
    
    return MultiPositionResult(
        id=f"eval_{resume_id}_multi",
        resume_id=resume_id,
        candidate_name=best_match.candidate_name if best_match else None,
        overall_verdict=overall_verdict,
        overall_confidence=best_match.confidence if best_match else "low",
        best_match_position_id=best_match.position_id if best_match else None,
        best_match_score=best_match.match_score if best_match else 0,
        position_evaluations=successful_evaluations,
        cross_position_analysis=cross_analysis,
        candidate_summary=candidate_summary,
        total_positions_evaluated=len(positions),
        successful_evaluations=len(successful_evaluations),
        failed_evaluations=failed_count,
        processing_time_seconds=processing_time,
        evaluated_at=datetime.now().isoformat(),
    )
```

#### 3.3.2 New Method: `_determine_best_match()`

```python
def _determine_best_match(
    self, 
    evaluations: List[ScreeningResult]
) -> Optional[ScreeningResult]:
    """
    Determine the best position match from all evaluations.
    
    Scoring Algorithm:
    - Base score from verdict: invite=100, waitlist=50, reject=0
    - Confidence multiplier: high=1.0, medium=0.8, low=0.6
    - AI competency bonus: +10 if uses_ai_tools
    - Experience match bonus: +5 if experience within range
    
    Returns:
        Best matching ScreeningResult, or None if no evaluations
    """
    if not evaluations:
        return None
    
    # Compute match score for each evaluation
    for eval in evaluations:
        score = self._compute_match_score(eval)
        eval.match_score = score
    
    # Sort by match score descending
    sorted_evals = sorted(
        evaluations, 
        key=lambda e: e.match_score, 
        reverse=True
    )
    
    return sorted_evals[0]

def _compute_match_score(self, evaluation: ScreeningResult) -> int:
    """Compute a 0-100 match score for an evaluation."""
    # Base verdict score
    verdict_scores = {"invite": 100, "waitlist": 50, "reject": 0}
    base_score = verdict_scores.get(evaluation.verdict, 0)
    
    # Confidence multiplier
    confidence_mult = {"high": 1.0, "medium": 0.8, "low": 0.6}
    mult = confidence_mult.get(evaluation.confidence, 0.6)
    
    score = int(base_score * mult)
    
    # Bonuses
    if evaluation.ai_competency.get("uses_ai_tools"):
        score += 10
    if evaluation.ai_competency.get("has_projects"):
        score += 5
    if evaluation.ai_competency.get("ownership_mindset"):
        score += 5
    
    return min(score, 100)  # Cap at 100
```

#### 3.3.3 New Method: `_generate_cross_position_analysis()`

```python
def _generate_cross_position_analysis(
    self,
    evaluations: List[ScreeningResult],
    best_match: Optional[ScreeningResult]
) -> str:
    """
    Generate a cross-position analysis summarizing the candidate's fit
    across all evaluated positions.
    """
    if not evaluations:
        return "无法生成跨职位分析：没有成功的评估结果。"
    
    lines = []
    
    # Summary of results by position
    lines.append("### 各职位匹配情况")
    for eval in sorted(evaluations, key=lambda e: e.match_score, reverse=True):
        status_icon = {"invite": "✅", "waitlist": "⏸️", "reject": "❌"}.get(
            eval.verdict, "❓"
        )
        lines.append(f"- {status_icon} **{eval.position_title}**: "
                    f"匹配度 {eval.match_score}/100 ({eval.confidence} confidence)")
    
    # Best match explanation
    if best_match:
        lines.append(f"\n### 最佳匹配")
        lines.append(f"**{best_match.position_title}** 是最佳匹配。")
        lines.append(f"{best_match.summary}")
    
    # Comparison note if multiple high matches
    high_matches = [e for e in evaluations if e.verdict == "invite"]
    if len(high_matches) > 1:
        lines.append(f"\n⚠️ **注意**：候选人在 {len(high_matches)} 个职位上均表现良好，"
                    "建议人工复核以确定最合适的岗位。")
    
    # No strong match case
    if not best_match or best_match.verdict == "reject":
        lines.append("\n❌ **结论**：候选人暂不适合任何开放职位。")
    
    return "\n".join(lines)
```

### 3.4 Backend API Changes

#### 3.4.1 New Response Models

```python
class PositionMatchDetail(BaseModel):
    """Detailed match information for a single position."""
    position_id: str
    position_title: str
    department: str
    verdict: str
    confidence: str
    match_score: int
    is_best_match: bool
    summary: str
    # Condensed fields for list view
    top_strengths: List[str]
    top_gaps: List[str]


class ConclusionResponse(BaseModel):
    """Enhanced screening conclusion with multi-position support."""
    
    # Overall result
    verdict: str
    verdict_display: str
    verdict_color: str
    
    # Candidate info
    candidate_name: Optional[str]
    candidate_summary: str  # NEW: Position-agnostic summary
    
    # Best match (primary display)
    position_title: str
    position_id: str
    confidence: str
    summary: str
    strengths: List[str]
    gaps: List[str]
    experience_years: Optional[float]
    ai_competency: Optional[Dict[str, Any]]
    reasoning: str
    
    # Multi-position context (NEW)
    total_positions_evaluated: int
    all_position_matches: List[PositionMatchDetail]
    cross_position_analysis: str
    has_multiple_good_matches: bool
    
    # Metadata
    processing_time_seconds: float
    evaluated_at: str
    id: str
```

#### 3.4.2 Modified: `_process_loop()`

```python
async def _process_loop() -> None:
    """Background task to process pending resumes."""
    while True:
        try:
            resume = await queue.get_pending()
            
            if resume:
                await queue.mark_processing(resume.id)
                await _broadcast_event("processing", resume.to_dict())
                
                try:
                    # Use new multi-position screening
                    result = await screener.screen_all_positions(
                        resume_id=resume.id,
                        resume_path=resume.file_path,
                    )
                    
                    await queue.mark_completed(resume.id, result.id)
                    
                    # Convert to frontend-friendly format
                    conclusion_data = _get_conclusion_response(result)
                    await _broadcast_event("completed", conclusion_data.model_dump())
                    
                except Exception as e:
                    logger.error(f"Screening failed: {e}", exc_info=True)
                    await queue.mark_error(resume.id, str(e))
                    await _broadcast_event("error", {"resume_id": resume.id, "error": str(e)})
            
            await asyncio.sleep(0.5)
            
        except Exception as e:
            logger.error(f"Process loop error: {e}")
            await asyncio.sleep(1)
```

### 3.5 Frontend Changes

#### 3.5.1 Progress View Updates

Modify step 3 label from "匹配职位要求" to "匹配所有开放职位 (1/N)":

```javascript
// Update processing steps dynamically
const updateProcessingStep = (currentPosition, totalPositions) => {
    const step3Label = document.querySelector('.step[data-step="3"] .step-label');
    if (step3Label && totalPositions > 1) {
        step3Label.textContent = `匹配所有开放职位 (${currentPosition}/${totalPositions})`;
    }
};
```

#### 3.5.2 Conclusion View Enhancements

Add "其他职位匹配" expandable section:

```html
<!-- New section in conclusion view -->
<div class="other-matches-section" id="otherMatchesSection">
    <h3 class="section-title">
        其他职位匹配情况 
        <span class="badge">${allMatches.length - 1}</span>
    </h3>
    <div class="position-match-cards" id="positionMatchCards">
        <!-- Dynamically populated -->
    </div>
</div>
```

Position match card component:

```javascript
const renderPositionMatchCard = (match) => {
    const isBest = match.is_best_match;
    const verdictClass = getVerdictColor(match.verdict);
    
    return `
        <div class="position-card ${isBest ? 'best-match' : ''}">
            <div class="position-card-header">
                <span class="position-title">${match.position_title}</span>
                <span class="match-score">${match.match_score}/100</span>
            </div>
            <div class="position-card-meta">
                <span class="department">${match.department}</span>
                <span class="verdict-badge ${verdictClass}">
                    ${getVerdictDisplay(match.verdict)}
                </span>
            </div>
            <div class="position-card-summary">
                ${match.summary}
            </div>
            ${!isBest ? `
                <button class="btn-link" onclick="viewPositionDetails('${match.position_id}')">
                    查看详情
                </button>
            ` : '<span class="best-match-label">最佳匹配</span>'}
        </div>
    `;
};
```

#### 3.5.3 Report Modal Updates

Add cross-position analysis section:

```javascript
const renderCrossPositionSection = (data) => {
    if (!data.all_position_matches || data.all_position_matches.length <= 1) {
        return '';  // Single position, don't show
    }
    
    return `
        <div class="report-section">
            <div class="report-section-title">跨职位匹配分析</div>
            <div class="position-comparison-table">
                <table>
                    <thead>
                        <tr>
                            <th>职位</th>
                            <th>匹配度</th>
                            <th>结论</th>
                            <th>置信度</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${data.all_position_matches.map(m => `
                            <tr class="${m.is_best_match ? 'best-match-row' : ''}">
                                <td>${m.position_title}</td>
                                <td>
                                    <div class="score-bar">
                                        <div class="score-fill" style="width: ${m.match_score}%"></div>
                                        <span>${m.match_score}</span>
                                    </div>
                                </td>
                                <td>${getVerdictDisplay(m.verdict)}</td>
                                <td>${m.confidence}</td>
                            </tr>
                        `).join('')}
                    </tbody>
                </table>
            </div>
            <div class="cross-analysis-text">
                ${renderMarkdown(data.cross_position_analysis)}
            </div>
        </div>
    `;
};
```

### 3.6 Configuration Changes

#### 3.6.1 New Config Options

```python
@dataclass
class ScreenerConfig:
    """Configuration for the resume screener."""
    
    # Existing fields
    incoming_dir: str
    processed_dir: str
    evaluations_dir: str
    jds_file: str
    poll_interval: float
    supported_extensions: tuple
    
    # NEW: Multi-position screening config
    multi_position_screening: bool = True  # Enable/disable feature
    max_concurrent_evaluations: int = 5  # Limit parallel LLM calls
    matching_strategy: str = "evaluate_all"  # Future: "smart_filter"
    
    # NEW: Scoring weights (customizable)
    scoring_weights: Dict[str, int] = field(default_factory=lambda: {
        "invite_base": 100,
        "waitlist_base": 50,
        "reject_base": 0,
        "ai_tools_bonus": 10,
        "projects_bonus": 5,
        "ownership_bonus": 5,
    })
```

---

## 4. Implementation Phases

### Phase 1: Core Multi-Position Logic
**Files**: `screener.py`

1. Add `MultiPositionResult` dataclass
2. Add `_evaluate_single_position()` helper (extract from existing `screen()`)
3. Implement `screen_all_positions()` method
4. Implement `_determine_best_match()` and scoring logic
5. Implement `_generate_cross_position_analysis()`
6. Modify `screen()` to call `screen_all_positions()` internally
7. Update `_save_evaluation()` to save both aggregate and individual results

**Success Criteria**: Backend can evaluate against all positions and return aggregated results

### Phase 2: API & Backend Integration
**Files**: `main.py`, `watcher.py`

1. Update `ConclusionResponse` model with new fields
2. Modify `_get_conclusion_response()` to convert `MultiPositionResult`
3. Update `_process_loop()` to use new screening method
4. Update `ResumeFile` model with new fields
5. Test WebSocket events with new payload

**Success Criteria**: API returns enhanced responses, frontend receives correct data

### Phase 3: Frontend Enhancement
**Files**: `app.js`, `index.html`, `styles.css`

1. Update progress step text for multi-position context
2. Add "Other Position Matches" section to conclusion view
3. Add position match card components
4. Update report modal with cross-position analysis table
5. Add styles for new components

**Success Criteria**: UI shows best match prominently, alternatives accessible

### Phase 4: Testing & Optimization
**Files**: Test scripts, documentation

1. Add test cases for multi-position matching
2. Test edge cases (all reject, multiple invites, partial failures)
3. Performance testing with varying position counts
4. Update README with new features

**Success Criteria**: All tests pass, performance acceptable

---

## 5. Edge Cases & Handling

| Edge Case | Handling Strategy |
|-----------|-------------------|
| **All positions reject** | Show best of rejects with "no strong match" message; suggest keeping resume for future |
| **Multiple equal invites** | Flag for human decision; show top 2-3 with "consider for multiple roles" |
| **Evaluation fails for one position** | Continue with others; include error info in `failed_evaluations` count |
| **Single position configured** | Gracefully degrade to current behavior (no UI changes) |
| **Empty positions.json** | Return clear error message; show "no open positions" in UI |
| **Candidate name mismatch** across positions | Use most frequently extracted name; note discrepancy in analysis |

---

## 6. Cost & Performance Analysis

### 6.1 Cost Impact

| Scenario | Current Cost | New Cost | Increase |
|----------|-------------|----------|----------|
| 1 position | 1 LLM call | 1 LLM call | 0% |
| 3 positions | 1 LLM call | 3 LLM calls | 200% |
| 5 positions | 1 LLM call | 5 LLM calls | 400% |

**Mitigation**:
- Feature is opt-out via config (`multi_position_screening: false`)
- Future: Implement "smart_filter" strategy to pre-filter positions

### 6.2 Performance Impact

| Scenario | Sequential Time | Parallel Time | Improvement |
|----------|----------------|---------------|-------------|
| 3 positions, 5s each | 15s | ~5s | 67% faster |
| 5 positions, 5s each | 25s | ~6s | 76% faster |

*Assumes concurrent LLM calls; actual time = slowest single evaluation + overhead*

---

## 7. Future Enhancements

### 7.1 Smart Pre-Filtering (Phase 5)

Before full agent evaluation, use lightweight keyword/semantic matching to identify potentially relevant positions:

```python
async def _pre_filter_positions(
    self, 
    resume_path: Path, 
    positions: List[Position]
) -> List[Position]:
    """
    Quick pre-filter to identify potentially matching positions.
    Uses keyword extraction + simple matching to reduce LLM calls.
    """
    # Extract keywords from resume
    resume_keywords = await self._extract_resume_keywords(resume_path)
    
    # Score each position by keyword overlap
    scored_positions = []
    for pos in positions:
        score = self._compute_keyword_match(resume_keywords, pos)
        if score > threshold:
            scored_positions.append((pos, score))
    
    # Sort and return top N
    scored_positions.sort(key=lambda x: x[1], reverse=True)
    return [p for p, _ in scored_positions[:max_positions]]
```

### 7.2 Candidate Self-Selection

Allow candidates to indicate target position (via filename pattern or UI):

```python
# Filename pattern: "candidate_name_[position_id].pdf"
# Example: "zhangsan_software-engineer.pdf"

def _extract_target_position(self, filename: str) -> Optional[str]:
    """Extract target position from filename."""
    for pos in self.jd_store.list_positions():
        if pos.id in filename or pos.id.replace('-', '_') in filename:
            return pos.id
    return None
```

### 7.3 Position-Specific Workflows

Different positions may need different evaluation criteria or prompts:

```python
class Position:
    # ... existing fields
    evaluation_template: str = "default"  # Reference to prompt template
    custom_criteria: Optional[Dict] = None  # Position-specific scoring
```

---

## 8. Backward Compatibility

### 8.1 API Compatibility

- Existing `screen(resume_id, resume_path, position_id)` signature preserved
- If `position_id` provided, use single-position mode (current behavior)
- If `position_id` is None, use multi-position mode (new behavior)

### 8.2 Data Compatibility

- Existing evaluation JSON files remain valid
- New fields added as optional with defaults
- `MultiPositionResult` stored separately from individual `ScreeningResult`s

### 8.3 Frontend Compatibility

- Single-position results display same as before
- Multi-position results show enhanced UI
- Feature flag allows disabling without code changes

---

## 9. Success Metrics

| Metric | Current | Target | Measurement |
|--------|---------|--------|-------------|
| Positions evaluated per resume | 1 | All configured | Backend logs |
| Time to show first result | N/A | < 10s for 3 positions | Frontend timing |
| Candidate-position match accuracy | N/A | > 80% HR approval | Manual review |
| False positives (wrong position invite) | N/A | < 10% | Manual review |
| User satisfaction (HR) | N/A | > 4/5 | Survey |

---

## 10. Open Questions

1. **Should we show all position evaluations to candidates, or only the best match?**
   - Recommendation: Show best match prominently, others in expandable section

2. **How to handle candidates who match multiple positions equally well?**
   - Recommendation: Flag for human decision, show "consider for multiple roles"

3. **Should we allow position prioritization (e.g., urgent hires get preference)?**
   - Future enhancement: Add `priority` field to positions

4. **How to handle position requirements that change mid-evaluation?**
   - Current: Use snapshot of positions at evaluation start
   - Future: Version positions, re-evaluate if version mismatch

---

## 11. Appendix: Sample Output

### 11.1 MultiPositionResult (JSON)

```json
{
  "id": "eval_abc123_multi",
  "resume_id": "abc123",
  "candidate_name": "张三",
  "overall_verdict": "invite",
  "overall_confidence": "high",
  "best_match_position_id": "software-engineer",
  "best_match_score": 95,
  "position_evaluations": [
    {
      "id": "eval_abc123_software-engineer",
      "position_id": "software-engineer",
      "position_title": "软件工程师（架构/全栈方向）",
      "verdict": "invite",
      "confidence": "high",
      "match_score": 95,
      "summary": "候选人具备扎实的全栈开发经验...",
      "strengths": [...],
      "gaps": [...],
      ...
    },
    {
      "id": "eval_abc123_growth-design-lead",
      "position_id": "growth-design-lead",
      "position_title": "传播与体验负责人",
      "verdict": "waitlist",
      "confidence": "medium",
      "match_score": 55,
      "summary": "候选人有设计背景但缺乏增长经验...",
      ...
    },
    {
      "id": "eval_abc123_office-assistant",
      "position_id": "office-assistant",
      "position_title": "办公室助理",
      "verdict": "reject",
      "confidence": "high",
      "match_score": 15,
      "summary": "技能严重不匹配...",
      ...
    }
  ],
  "cross_position_analysis": "候选人最适合软件工程师职位...",
  "candidate_summary": "5年经验的全栈工程师，AI工具深度用户...",
  "total_positions_evaluated": 3,
  "successful_evaluations": 3,
  "failed_evaluations": 0,
  "processing_time_seconds": 6.8,
  "evaluated_at": "2026-03-12T10:30:00"
}
```

### 11.2 Frontend Display Mockup

```
┌─────────────────────────────────────────────────────────────┐
│  ✅ 决策                                                      │
│  张三                                                         │
│                                                             │
│  哇塞！到我们后台聊聊                                         │
│  软件工程师（架构/全栈方向）                                   │
│                                                             │
├─────────────────────────────────────────────────────────────┤
│  其他职位匹配情况 [2]                                        │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ ⏸️ 传播与体验负责人          匹配度 55/100          │   │
│  │ 候选人有设计背景但缺乏增长经验...                    │   │
│  │ [查看详情]                                           │   │
│  └─────────────────────────────────────────────────────┘   │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ ❌ 办公室助理                匹配度 15/100          │   │
│  │ 技能严重不匹配...                                    │   │
│  │ [查看详情]                                           │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

---

*Document Version: 1.0*  
*Created: 2026-03-12*  
*Status: Design Complete, Awaiting Implementation*
