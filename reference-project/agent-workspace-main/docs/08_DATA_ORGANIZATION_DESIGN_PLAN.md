# Data Organization Design Plan

> Design document for properly organizing inputs, outputs, and generated data in the resume_screener shell.

---

## Executive Summary

**Current Gap**: The `sample_data/` folder mixes sample inputs, runtime data, generated outputs, and temporary workspaces without clear separation of concerns. This creates confusion about what data is persistent vs. temporary, and pollutes the "sample" concept with production runtime data.

**Proposed Solution**: Establish a clear three-tier directory structure:
1. **`sample_data/`** - Sample/reference data only (read-only templates)
2. **`data/`** - Runtime data (inputs, queue, archive)
3. **`output/`** - Generated results (evaluations, reports)

**Scope**: Shell-level changes (`apps/resume_screener/`). Core `agent_workspace` remains unchanged.

---

## 1. Current State Analysis

### 1.1 Current Directory Structure

```
apps/resume_screener/
└── sample_data/                    ← PROBLEM: Mixed purposes
    ├── incoming_candidate/         ← Runtime input queue (should be in data/)
    ├── jds/                        ← Reference data (OK here, but could be config/)
    │   └── positions.json
    ├── processed/                  ← Generated archive (should be in data/)
    ├── evaluations/                ← Generated results (should be in output/)
    └── workspaces/                 ← Temporary data (should be temp/ or cleaned up)
```

### 1.2 The Problems

| Issue | Impact | Example |
|-------|--------|---------|
| **Naming confusion** | `sample_data` implies read-only samples, but contains runtime data | Users may accidentally commit processed files to git |
| **Mixed lifecycles** | Sample data should be versioned; generated data should not | evaluations/ grows unbounded |
| **No data retention** | Old evaluations and processed files accumulate forever | 100+ MB of old data over time |
| **Temporary data pollution** | Workspaces should be cleaned up but sometimes persist for debugging | workspaces/ contains old debug data |
| **Git pollution risk** | Generated data in sample_data/ may be accidentally committed | processed/ contains binary PDFs |

### 1.3 Data Classification

| Data Type | Current Location | Proper Location | Lifecycle |
|-----------|-----------------|-----------------|-----------|
| Job descriptions | `sample_data/jds/` | `config/jds/` or `sample_data/jds/` | Version controlled |
| Sample resumes | N/A (missing) | `sample_data/resumes/` | Version controlled |
| Incoming queue | `sample_data/incoming_candidate/` | `data/incoming/` | Ephemeral (moved after processing) |
| Processed archive | `sample_data/processed/` | `data/processed/` | Configurable retention (e.g., 30 days) |
| Evaluation results | `sample_data/evaluations/` | `output/evaluations/` | Persistent (until deleted) |
| Full reports | N/A (missing) | `output/reports/` | Persistent |
| Temporary workspaces | `sample_data/workspaces/` | `temp/workspaces/` | Auto-cleaned after success |
| Debug workspaces | N/A | `temp/debug/` | Manual cleanup |

---

## 2. Design Philosophy

### 2.1 Core Principles

1. **Separation of Concerns**: Sample data ≠ Runtime data ≠ Generated outputs
2. **Clear Lifecycles**: Each data type has explicit retention and cleanup policies
3. **Git-Friendly**: Only sample/config data in version control; data/ and output/ in `.gitignore`
4. **Debuggability**: Temporary data accessible when needed, cleaned up by default

### 2.2 Directory Conventions

```
apps/resume_screener/
├── sample_data/           ← Read-only reference data (version controlled)
│   ├── jds/              ← Job descriptions
│   └── resumes/          ← Sample resumes for testing
│
├── config/               ← Configuration files (version controlled)
│   └── jds/              ← Alternative: JDs as config
│
├── data/                 ← Runtime data (.gitignored)
│   ├── incoming/         ← Input queue (monitored by watcher)
│   └── processed/        ← Archive of processed resumes
│
├── output/               ← Generated results (.gitignored)
│   ├── evaluations/      ← JSON evaluation files
│   └── reports/          ← Human-readable reports (markdown)
│
└── temp/                 ← Temporary data (.gitignored, auto-cleaned)
    ├── workspaces/       ← Agent workspaces (auto-cleaned on success)
    └── debug/            ← Debug workspaces (manual cleanup)
```

---

## 3. Proposed Design

### 3.1 New Directory Structure

```
apps/resume_screener/
│
├── sample_data/                    # Reference/sample data (version controlled)
│   ├── jds/
│   │   └── positions.json         # Job descriptions
│   └── resumes/                   # Sample resumes for testing
│       ├── sample_engineer.pdf
│       └── sample_designer.png
│
├── config/                        # Configuration (version controlled)
│   └── screener_config.yaml      # Shell-specific config
│
├── data/                          # Runtime data (.gitignored)
│   ├── incoming/                  # Input queue
│   ├── processed/                 # Archive (with retention policy)
│   └── logs/                      # Application logs
│
├── output/                        # Generated results (.gitignored)
│   ├── evaluations/               # JSON evaluation results
│   │   └── YYYY-MM/              # Organized by month
│   │       └── eval_{id}.json
│   └── reports/                   # Markdown reports
│       └── YYYY-MM/
│           └── {candidate_name}_{date}.md
│
└── temp/                          # Temporary data (.gitignored)
    ├── workspaces/                # Auto-cleaned after success
    └── debug/                     # For debugging (manual cleanup)
```

### 3.2 Configuration Changes

#### 3.2.1 New `ScreenerConfig` with Proper Paths

```python
@dataclass
class ScreenerConfig:
    """Configuration for the resume screener with proper data organization."""
    
    # Reference Data (read-only, version controlled)
    sample_resumes_dir: str = "sample_data/resumes"
    jds_file: str = "sample_data/jds/positions.json"
    
    # Runtime Data (ephemeral, .gitignored)
    incoming_dir: str = "data/incoming"
    processed_dir: str = "data/processed"
    logs_dir: str = "data/logs"
    
    # Output Data (persistent results, .gitignored)
    evaluations_dir: str = "output/evaluations"
    reports_dir: str = "output/reports"
    
    # Temporary Data (auto-cleaned, .gitignored)
    temp_dir: str = "temp/workspaces"
    debug_dir: str = "temp/debug"
    
    # Data Retention Policy
    processed_retention_days: int = 30  # Auto-delete old processed files
    evaluation_organization: str = "monthly"  # YYYY-MM subdirectories
    
    # Polling interval in seconds (minimum 3.0)
    poll_interval: float = 5.0
    
    # Processing
    max_file_size_mb: int = 50
    supported_extensions: tuple = (".pdf", ".docx", ".doc", ".png", ".jpg", ".jpeg", ".tiff", ".bmp")
    
    # Debug mode - keep workspaces for inspection
    debug_mode: bool = False
    
    def __post_init__(self):
        """Ensure paths are resolved relative to app root and directories exist."""
        app_root = Path(__file__).parent.parent
        
        # Resolve all paths
        for field_name in [
            'sample_resumes_dir', 'jds_file',
            'incoming_dir', 'processed_dir', 'logs_dir',
            'evaluations_dir', 'reports_dir',
            'temp_dir', 'debug_dir'
        ]:
            value = getattr(self, field_name)
            resolved = app_root / value
            setattr(self, field_name, str(resolved))
            
            # Create directories (except for file paths)
            if not field_name.endswith('_file'):
                Path(resolved).mkdir(parents=True, exist_ok=True)
    
    def get_evaluation_path(self, evaluation_id: str, timestamp: Optional[datetime] = None) -> Path:
        """Get organized path for evaluation file."""
        if timestamp is None:
            timestamp = datetime.now()
        
        if self.evaluation_organization == "monthly":
            subdir = timestamp.strftime("%Y-%m")
            dir_path = Path(self.evaluations_dir) / subdir
        else:
            dir_path = Path(self.evaluations_dir)
        
        dir_path.mkdir(parents=True, exist_ok=True)
        return dir_path / f"{evaluation_id}.json"
    
    def get_report_path(self, candidate_name: str, timestamp: Optional[datetime] = None) -> Path:
        """Get organized path for report file."""
        if timestamp is None:
            timestamp = datetime.now()
        
        subdir = timestamp.strftime("%Y-%m")
        dir_path = Path(self.reports_dir) / subdir
        dir_path.mkdir(parents=True, exist_ok=True)
        
        safe_name = "".join(c for c in candidate_name if c.isalnum() or c in (' ', '-', '_')).strip()
        return dir_path / f"{safe_name}_{timestamp.strftime('%Y%m%d')}.md"
    
    def get_workspace_path(self, resume_id: str, position_id: str, debug: bool = False) -> Path:
        """Get path for workspace (temp or debug)."""
        base_dir = Path(self.debug_dir if debug else self.temp_dir)
        timestamp = int(time.time())
        return base_dir / f"{resume_id}_{position_id}_{timestamp}"
```

### 3.3 Data Retention and Cleanup

#### 3.3.1 New `DataManager` Class

```python
class DataManager:
    """Manages data lifecycle, retention, and cleanup."""
    
    def __init__(self, config: ScreenerConfig):
        self.config = config
        self.logger = logging.getLogger(__name__)
    
    def cleanup_old_processed_files(self) -> int:
        """
        Remove processed files older than retention period.
        
        Returns:
            Number of files deleted
        """
        processed_dir = Path(self.config.processed_dir)
        if not processed_dir.exists():
            return 0
        
        cutoff_date = datetime.now() - timedelta(days=self.config.processed_retention_days)
        deleted_count = 0
        
        for file_path in processed_dir.iterdir():
            if not file_path.is_file():
                continue
            
            # Check file modification time
            mtime = datetime.fromtimestamp(file_path.stat().st_mtime)
            if mtime < cutoff_date:
                try:
                    file_path.unlink()
                    deleted_count += 1
                    self.logger.info(f"Deleted old processed file: {file_path.name}")
                except Exception as e:
                    self.logger.error(f"Failed to delete {file_path}: {e}")
        
        return deleted_count
    
    def cleanup_temp_workspaces(self, keep_debug: bool = False) -> int:
        """
        Clean up temporary workspaces.
        
        Args:
            keep_debug: If True, don't clean debug directory
            
        Returns:
            Number of workspaces deleted
        """
        temp_dir = Path(self.config.temp_dir)
        if not temp_dir.exists():
            return 0
        
        deleted_count = 0
        for workspace_dir in temp_dir.iterdir():
            if not workspace_dir.is_dir():
                continue
            
            try:
                shutil.rmtree(workspace_dir)
                deleted_count += 1
                self.logger.info(f"Cleaned temp workspace: {workspace_dir.name}")
            except Exception as e:
                self.logger.error(f"Failed to clean workspace {workspace_dir}: {e}")
        
        return deleted_count
    
    def archive_evaluation(self, evaluation: ScreeningResult) -> Path:
        """
        Archive evaluation to organized output directory.
        
        Returns:
            Path to archived file
        """
        eval_path = self.config.get_evaluation_path(
            evaluation.id, 
            datetime.fromisoformat(evaluation.evaluated_at)
        )
        
        with open(eval_path, 'w', encoding='utf-8') as f:
            json.dump(asdict(evaluation), f, indent=2, ensure_ascii=False)
        
        self.logger.info(f"Archived evaluation to: {eval_path}")
        return eval_path
    
    def generate_report(self, evaluation: ScreeningResult) -> Optional[Path]:
        """
        Generate human-readable markdown report.
        
        Returns:
            Path to report file, or None if report generation failed
        """
        try:
            report_path = self.config.get_report_path(
                evaluation.candidate_name or "Unknown",
                datetime.fromisoformat(evaluation.evaluated_at)
            )
            
            # Generate markdown report
            report_content = self._format_report(evaluation)
            
            with open(report_path, 'w', encoding='utf-8') as f:
                f.write(report_content)
            
            self.logger.info(f"Generated report: {report_path}")
            return report_path
            
        except Exception as e:
            self.logger.error(f"Failed to generate report: {e}")
            return None
    
    def _format_report(self, evaluation: ScreeningResult) -> str:
        """Format evaluation as markdown report."""
        lines = [
            f"# Resume Screening Report",
            f"",
            f"**Candidate:** {evaluation.candidate_name or 'Unknown'}  ",
            f"**Position:** {evaluation.position_id}  ",
            f"**Date:** {evaluation.evaluated_at}  ",
            f"**Verdict:** {evaluation.verdict.upper()}  ",
            f"**Confidence:** {evaluation.confidence}  ",
            f"",
            f"## Summary",
            f"",
            evaluation.summary,
            f"",
            f"## Strengths",
            f"",
        ]
        
        for strength in evaluation.strengths:
            lines.append(f"- {strength}")
        
        lines.extend([
            f"",
            f"## Gaps",
            f"",
        ])
        
        for gap in evaluation.gaps:
            lines.append(f"- {gap}")
        
        lines.extend([
            f"",
            f"## Reasoning",
            f"",
            evaluation.reasoning,
            f"",
            f"---",
            f"*Report generated by Resume Screener*",
        ])
        
        return "\n".join(lines)
```

### 3.4 Modified Components

#### 3.4.1 Modified `ResumeScreener`

```python
class ResumeScreener:
    """Screens resumes against job descriptions with proper data management."""
    
    def __init__(self):
        self.config = get_config()
        self.jd_store = JDStore()
        self.data_manager = DataManager(self.config)  # NEW
    
    def _create_workspace(self, resume_id: str, resume_path: Path, position: Position) -> Path:
        """Create workspace in temp directory."""
        # Use temp directory instead of sample_data
        workspace = self.config.get_workspace_path(
            resume_id, 
            position.id,
            debug=self.config.debug_mode
        )
        workspace.mkdir(parents=True, exist_ok=True)
        
        # ... rest of setup ...
        return workspace
    
    async def screen(self, resume_id: str, resume_path: Path, position_id: Optional[str] = None) -> ScreeningResult:
        """Screen a resume with proper data management."""
        # ... existing screening logic ...
        
        # Save evaluation using DataManager
        self.data_manager.archive_evaluation(screening_result)
        
        # Generate human-readable report
        report_path = self.data_manager.generate_report(screening_result)
        if report_path:
            screening_result.report_path = str(report_path)
        
        # Cleanup temp workspace (unless in debug mode)
        if not self.config.debug_mode and workspace and workspace.exists():
            shutil.rmtree(workspace, ignore_errors=True)
        
        return screening_result
```

#### 3.4.2 Modified `ResumeQueue`

```python
class ResumeQueue:
    """Manages the queue with data lifecycle awareness."""
    
    def __init__(self):
        self.config = get_config()
        self.data_manager = DataManager(self.config)  # NEW
        
        # Ensure directories exist
        Path(self.config.incoming_dir).mkdir(parents=True, exist_ok=True)
        Path(self.config.processed_dir).mkdir(parents=True, exist_ok=True)
        
        # Cleanup old files on startup
        self.data_manager.cleanup_old_processed_files()
    
    async def mark_completed(self, resume_id: str, evaluation_id: str) -> None:
        """Mark resume as completed and move to processed."""
        async with self._lock:
            if resume_id in self._resumes:
                resume = self._resumes[resume_id]
                resume.status = "completed"
                resume.evaluation_id = evaluation_id
                
                # Move file to processed folder with date prefix
                date_prefix = datetime.now().strftime("%Y%m%d_")
                processed_path = Path(self.config.processed_dir) / f"{date_prefix}{resume.id}_{resume.original_name}"
                
                try:
                    shutil.move(str(resume.file_path), str(processed_path))
                    resume.file_path = processed_path
                except Exception as e:
                    logger.error(f"Failed to move file: {e}")
                
                self._processing = None
                self._notify(resume, "completed")
```

### 3.5 Gitignore Updates

```gitignore
# apps/resume_screener/.gitignore

# Runtime data
data/

# Generated outputs
output/

# Temporary files
temp/
*.tmp
*.log

# Keep sample_data and config in version control
!sample_data/
!config/
```

---

## 4. Migration Plan

### 4.1 Phase 1: Directory Structure (Non-Breaking)

1. Create new directory structure (`data/`, `output/`, `temp/`)
2. Update `ScreenerConfig` with new paths
3. Maintain backward compatibility with old paths
4. Add migration utility to move existing data

```python
def migrate_from_sample_data():
    """Migrate data from old sample_data structure."""
    app_root = Path(__file__).parent.parent
    
    # Migration mappings
    migrations = [
        ("sample_data/incoming_candidate", "data/incoming"),
        ("sample_data/processed", "data/processed"),
        ("sample_data/evaluations", "output/evaluations"),
        ("sample_data/workspaces", "temp/workspaces"),
    ]
    
    for old_rel, new_rel in migrations:
        old_path = app_root / old_rel
        new_path = app_root / new_rel
        
        if old_path.exists() and not new_path.exists():
            new_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old_path), str(new_path))
            print(f"Migrated: {old_rel} → {new_rel}")
```

### 4.2 Phase 2: Data Manager Integration

1. Implement `DataManager` class
2. Add retention policy enforcement
3. Add report generation
4. Update `ResumeScreener` to use `DataManager`

### 4.3 Phase 3: Cleanup and Documentation

1. Remove old path compatibility
2. Update documentation
3. Add data management API endpoints

---

## 5. API Enhancements

### 5.1 New Endpoints

```python
@app.get("/api/data/status")
async def get_data_status():
    """Get data storage status."""
    data_manager = DataManager(get_config())
    
    return {
        "incoming_count": len(list(Path(config.incoming_dir).glob("*"))),
        "processed_count": len(list(Path(config.processed_dir).glob("*"))),
        "processed_size_mb": get_directory_size(config.processed_dir) / (1024 * 1024),
        "evaluation_count": len(list(Path(config.evaluations_dir).rglob("*.json"))),
        "temp_workspaces_count": len(list(Path(config.temp_dir).glob("*"))),
        "retention_days": config.processed_retention_days,
    }

@app.post("/api/data/cleanup")
async def trigger_cleanup():
    """Trigger manual cleanup of old data."""
    data_manager = DataManager(get_config())
    
    deleted_processed = data_manager.cleanup_old_processed_files()
    deleted_temp = data_manager.cleanup_temp_workspaces(keep_debug=True)
    
    return {
        "deleted_processed_files": deleted_processed,
        "deleted_temp_workspaces": deleted_temp,
    }

@app.get("/api/reports")
async def list_reports():
    """List generated reports."""
    reports_dir = Path(get_config().reports_dir)
    if not reports_dir.exists():
        return {"reports": []}
    
    reports = []
    for report_file in sorted(reports_dir.rglob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True):
        reports.append({
            "path": str(report_file.relative_to(reports_dir)),
            "name": report_file.stem,
            "size": report_file.stat().st_size,
            "modified": datetime.fromtimestamp(report_file.stat().st_mtime).isoformat(),
        })
    
    return {"reports": reports[:100]}  # Limit to recent 100
```

---

## 6. Configuration Reference

### 6.1 New `config/screener_config.yaml`

```yaml
# Resume Screener Configuration

# Reference Data Paths
reference:
  jds_file: "sample_data/jds/positions.json"
  sample_resumes_dir: "sample_data/resumes"

# Runtime Data Paths
runtime:
  incoming_dir: "data/incoming"
  processed_dir: "data/processed"
  logs_dir: "data/logs"

# Output Paths
output:
  evaluations_dir: "output/evaluations"
  reports_dir: "output/reports"
  evaluation_organization: "monthly"  # Options: flat, monthly, daily

# Temporary Data Paths
temp:
  workspaces_dir: "temp/workspaces"
  debug_dir: "temp/debug"

# Data Retention
retention:
  processed_files_days: 30
  auto_cleanup_on_startup: true
  auto_cleanup_interval_hours: 24

# Processing
processing:
  poll_interval: 5.0
  max_file_size_mb: 50
  supported_extensions: [".pdf", ".docx", ".doc", ".png", ".jpg", ".jpeg"]
  debug_mode: false
```

---

## 7. Success Metrics

| Metric | Current | Target | Measurement |
|--------|---------|--------|-------------|
| Directory clarity | Mixed purposes | Clear separation | Code review |
| Accidental git commits | Common (processed PDFs) | None | Git history |
| Data retention | Unbounded | Configurable | Disk usage monitoring |
| Debug workspace access | Cluttered sample_data | Organized temp/debug | Developer survey |
| Report accessibility | JSON only | JSON + Markdown | User feedback |

---

## 8. Open Questions

1. **Should processed files be deleted or archived?**
   - Option A: Delete after retention period
   - Option B: Move to cold storage (e.g., S3, NAS)
   - Recommendation: Start with deletion, add archiving later

2. **Should we support multiple JD files?**
   - Current: Single `positions.json`
   - Future: `config/jds/*.json` with automatic loading

3. **How to handle data backup?**
   - Option A: User responsibility
   - Option B: Built-in backup to cloud storage
   - Recommendation: Document backup procedures, don't implement in MVP

---

## 9. Appendix: Before/After Comparison

### Before (Current)

```
sample_data/
├── incoming_candidate/     # ❌ Runtime data in sample folder
├── jds/                    # ✓ Reference data
├── processed/              # ❌ Generated data in sample folder
├── evaluations/            # ❌ Generated data in sample folder
└── workspaces/             # ❌ Temp data in sample folder
```

### After (Proposed)

```
sample_data/                # ✓ Read-only reference data
├── jds/
│   └── positions.json
└── resumes/
    ├── sample_engineer.pdf
    └── sample_designer.png

data/                       # ✓ Runtime data (.gitignored)
├── incoming/
├── processed/
└── logs/

output/                     # ✓ Generated results (.gitignored)
├── evaluations/
│   └── 2026-03/
│       └── eval_xxx.json
└── reports/
    └── 2026-03/
        └── Candidate_Name_20260312.md

temp/                       # ✓ Temporary data (.gitignored)
├── workspaces/             # Auto-cleaned
└── debug/                  # Manual cleanup
```

---

*Document Version: 1.0*  
*Created: 2026-03-12*  
*Status: Design Complete, Awaiting Implementation*
