# Testing Documentation

## Test Suite Overview

```
tests/
├── test_phase1.py           # Phase 1: Walking Skeleton tests
├── test_all.py              # Comprehensive tests (Phases 1-4)
└── e2e/
    ├── test_set_1/          # Investment monitoring: Initial files
    ├── test_set_2/          # Investment monitoring: Update files
    ├── test_set_3/          # Resume screening: JD + 8 resumes
    ├── test_set_4/          # Job platform: 129 operational docs
    └── workspaces/          # E2E test outputs
        ├── investment_monitoring/
        ├── resume_screening/
        └── job_platform/
```

## Running Tests

### Phase 1 Tests (Basic Functionality)

```bash
cd agent_workspace
python -m tests.test_phase1
```

**Coverage**:
1. Workspace initialization
2. Scan empty resources
3. Scan with files
4. Content extraction
5. Artifact writing
6. Artifact reading
7. Diff detection
8. Artifacts and memory commands

**Expected Output**:
```
============================================================
PHASE 1 AUTOMATIC TEST SUITE
============================================================
...
Passed: 8/8
Failed: 0/8

[OK] ALL TESTS PASSED - Phase 1 is ready!
```

### Comprehensive Tests (Phases 1-4)

```bash
python -m tests.test_all
```

**Coverage**:
- Phase 1: Walking skeleton
- Phase 2: Persistence (diff, memory, traces)
- Phase 3: Templates
- Phase 4: Excel extraction, search

### Unit Tests (Individual Components)

```bash
# Test workspace scanning
python -c "from agent_workspace.workspace import Workspace; ws = Workspace('.'); print(ws.scan())"

# Test extraction
python -c "from agent_workspace.extractor import extract_file; from pathlib import Path; print(extract_file(Path('test.txt'), 'text', None))"

# Test config loading
python -c "from agent_workspace.config import load_workspace_config; print(load_workspace_config('.'))"
```

## End-to-End Tests

### Test 1: Investment Monitoring (WeBox)

**Scenario**: User monitors an invested company over time

**Files**:
- `test_set_1/`: Q1-Q4 2025 CEO Letters (4 PDFs)
- `test_set_2/`: Q1 2026 Letter + Pitch Deck (2 PDFs)

**Execution**:
```bash
# Setup
python -m agent_workspace init --dir test_workspace
cp tests/e2e/test_set_1/*.pdf test_workspace/resources/

# Initial analysis
python -m agent_workspace run --workspace test_workspace \
  --task "This company is invested by the USER, monitor updates over time"

# Add updates
cp tests/e2e/test_set_2/*.pdf test_workspace/resources/

# Monitor changes
python -m agent_workspace run --workspace test_workspace \
  --task "Review new Q1 2026 updates and update monitoring report"

# Verify artifacts
python -m agent_workspace artifacts --workspace test_workspace
python -m agent_workspace diff --workspace test_workspace
```

**Expected Artifacts**:
- `artifacts/reports/WeBox_Quarterly_Monitoring_Report.md`
- `artifacts/memory/WeBox_Monitoring_Observations.md`
- `artifacts/memory/WeBox_Q1_2026_Update.md`
- `artifacts/traces/trace_*.json`

### Test 2: Resume Screening (Causally)

**Scenario**: Hiring team evaluates candidates for AI startup

**Files**:
- `test_set_3/JD.txt`: Job descriptions (3 positions)
- `test_set_3/*.pdf`: 8 candidate resumes

**Execution**:
```bash
# Setup
python -m agent_workspace init --dir hiring_workspace
cp tests/e2e/test_set_3/JD.txt hiring_workspace/resources/
cp tests/e2e/test_set_3/*.pdf hiring_workspace/resources/

# Evaluate all candidates
python -m agent_workspace run --workspace hiring_workspace \
  --task "Evaluate all candidates against JD and rank top 3"
```

**Expected Artifacts**:
- `artifacts/reports/candidate_evaluation_report.md`
- `artifacts/reports/candidate_evaluation_round2.md`
- `artifacts/memory/candidate_evaluation_observations.md`

### Test 3: Job Platform Assessment

**Scenario**: Government evaluates employment service station

**Files**:
- `test_set_4/`: 129 files (docs, spreadsheets, images)

**Execution**:
```bash
# Setup
python -m agent_workspace init --dir platform_workspace
cp -r tests/e2e/test_set_4/* platform_workspace/resources/

# Comprehensive assessment
python -m agent_workspace run --workspace platform_workspace \
  --task "Analyze job platform operations, staffing, metrics, and provide assessment"
```

**Expected Artifacts**:
- `artifacts/reports/就业平台综合评估报告.md`
- `artifacts/memory/就业驿站运营关键观察记录.md`

## Test Data Summary

| Test Set | Type | Files | Size | Language |
|----------|------|-------|------|----------|
| test_set_1 | CEO Letters | 4 PDFs | ~7 MB | English |
| test_set_2 | Updates | 2 PDFs | ~6 MB | English |
| test_set_3 | Resumes | 8 PDFs + 1 TXT | ~8 MB | Chinese |
| test_set_4 | Operational | 129 mixed | ~100 MB | Chinese |

## Validation Checklist

### Phase 1 - Walking Skeleton
- [ ] `init` creates correct folder structure
- [ ] `scan` detects all file types
- [ ] `run` executes without errors
- [ ] `write_artifact` creates files
- [ ] `read_artifact` reads files
- [ ] `diff` shows changes

### Phase 2 - Persistence
- [ ] Snapshot saved after run
- [ ] Diff detects added/modified/removed
- [ ] Memory loads from artifacts/memory/
- [ ] Trace saved to artifacts/traces/
- [ ] Multi-run context awareness

### Phase 3 - Conversation
- [ ] Template resolution works
- [ ] Variable substitution correct
- [ ] Error handling for missing templates

### Phase 4 - Rich Extraction
- [ ] PDF extraction works
- [ ] DOCX extraction works
- [ ] Excel extraction works
- [ ] Image extraction (base64) works
- [ ] Search across files works

### Phase 5 - Polish
- [ ] Progress output shows tool calls
- [ ] `--quiet` flag suppresses output
- [ ] Retry logic handles 429/5xx errors
- [ ] Error messages are user-friendly
- [ ] Keyboard interrupt handled gracefully

## E2E Test Results Summary

### Test Run: 2025-03-11

| Test | Status | Files | Time | Output |
|------|--------|-------|------|--------|
| Investment Monitoring | ✅ Pass | 6 | ~3 min | 3 reports, 2 memory files |
| Resume Screening | ✅ Pass | 9 | ~5 min | 2 reports, 2 memory files |
| Job Platform | ✅ Pass | 129 | ~2 min | 1 report, 1 memory file |

**Total Artifacts Generated**: 11 reports, 5 memory files, 11 traces

## Performance Benchmarks

### Extraction Speed (approximate)

| File Type | Size | Time |
|-----------|------|------|
| Text | 10 KB | <10ms |
| PDF | 1 MB | ~200ms |
| DOCX | 500 KB | ~100ms |
| Excel | 200 KB | ~150ms |

### Agent Execution

| Task Complexity | Files | Iterations | Time |
|-----------------|-------|------------|------|
| Simple scan | 5 | 2-3 | 10-20s |
| Multi-file analysis | 10 | 5-7 | 30-60s |
| Large batch (100+) | 129 | 6-8 | 60-120s |

## Known Limitations

1. **Unicode filenames**: Display may be garbled on Windows, but content extracts correctly
2. **Large files**: >15K chars truncated per extraction limits
3. **Images**: Not processed by LLM unless multimodal model used
4. **Excel**: Only first 5 sheets, 100 rows extracted

## Debugging Failed Tests

### Enable Verbose Logging

```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

### Check Trace Files

```bash
# Pretty print trace
python -c "import json; print(json.dumps(json.load(open('artifacts/traces/trace_*.json')), indent=2))"
```

### Verify Workspace Structure

```bash
python -m agent_workspace scan --workspace <path>
python -m agent_workspace artifacts --workspace <path>
```

### Test Individual Tools

```python
from agent_workspace.tools.scan_resources import scan_resources
print(scan_resources.invoke({"workspace_root": "/path/to/workspace"}))
```

---

*Last updated: 2025-03-11*
