# Resume Screener

A real-time resume screening web application built on top of `@agent_workspace`. Monitors an incoming folder for new resumes, automatically screens them against configured job descriptions using AI, and displays results in a clean, professional interface.

## Features

- **Real-time monitoring**: Polls incoming folder every 3 seconds for new resumes
- **Multi-format support**: PDF, DOCX, DOC, JPG, PNG, TIFF, BMP
- **AI-powered screening**: Uses `@agent_workspace` with LangGraph ReAct agent
- **Three-verdict system**: 
  - ✅ **Invite to Interview** - Clear match
  - ⏸️ **Waitlist** - Partial match, needs comparison
  - ❌ **Not a Match** - Doesn't meet requirements
- **WebSocket real-time updates**: Instant feedback when screening completes
- **Professional UI**: Clean, modern interface with scan animations
- **File management**: Automatically moves processed files to archive folder

## Architecture

```
┌─────────────────┐     WebSocket      ┌──────────────────┐
│   Frontend      │◄──────────────────►│  FastAPI Backend │
│  (3 views)      │                     │                  │
└─────────────────┘                     └────────┬─────────┘
                                                 │
                    ┌────────────────────────────┼────────────────────────────┐
                    │                            │                            │
                    ▼                            ▼                            ▼
          ┌─────────────────┐        ┌─────────────────┐           ┌─────────────────┐
          │  File Watcher   │        │  @agent_workspace│          │   JD Store      │
          │  (poll 3s)      │───────►│  ReAct Agent    │◄─────────│  (positions.json)│
          └─────────────────┘        └─────────────────┘           └─────────────────┘
                    │                            │
                    ▼                            ▼
          ┌─────────────────┐           ┌─────────────────┐
          │ incoming_candidate│          │   Evaluations   │
          │ processed/       │           │   (JSON files)  │
          └─────────────────┘           └─────────────────┘
```

## Quick Start

### 1. Install Dependencies

```bash
cd apps/resume_screener/backend
pip install -r requirements.txt
```

### 2. No Additional Configuration Needed

The Resume Screener automatically uses the `.env` file from the project root (`agent-workspace/.env`). Just make sure your root `.env` has the `LLM_API_KEY` set (same as when running `@agent_workspace` directly).

```bash
# In project root: agent-workspace/.env
LLM_API_KEY=your-api-key-here
```

### 3. Run the Application

```bash
# From the resume_screener directory
python backend/main.py
```

Or with uvicorn directly:

```bash
cd apps/resume_screener
uvicorn backend.main:app --reload --port 8000
```

### 4. Open in Browser

Navigate to: http://localhost:8000

## Usage

### 1. Waiting for Input

The app starts in "waiting" mode with a radar animation. It's monitoring the `sample_data/incoming_candidate/` folder.

### 2. Submit a Resume

Copy a resume file into the incoming folder:

```bash
cp ~/Downloads/candidate_resume.pdf apps/resume_screener/sample_data/incoming_candidate/
```

### 3. Processing

The app will automatically:
1. Detect the new file (within 3 seconds)
2. Show the processing view with scan animation
3. Run AI screening against configured positions
4. Display the conclusion

### 4. View Results

The conclusion view shows:
- Verdict (Invite/Waitlist/Not a Match)
- Candidate name (extracted from resume)
- Confidence level
- Summary of fit
- Key strengths
- Areas to explore in interview

## Configuration

### Job Descriptions

Edit `sample_data/jds/positions.json` to define your positions:

```json
{
  "positions": [
    {
      "id": "unique-id",
      "title": "Job Title",
      "department": "Department",
      "description": "Brief description",
      "requirements": ["Must-have 1", "Must-have 2"],
      "nice_to_have": ["Nice 1", "Nice 2"],
      "min_years_experience": 3,
      "required_skills": ["Skill 1", "Skill 2"]
    }
  ]
}
```

### Paths (Backend/Frontend)

Modify `backend/config.py` or use the API to update:

```python
ScreenerConfig(
    incoming_dir="/path/to/incoming",
    processed_dir="/path/to/processed",
    evaluations_dir="/path/to/evaluations",
    jds_file="/path/to/positions.json",
    poll_interval=5.0,  # seconds; can also adjust from frontend UI
)
```


### API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Serve frontend |
| `/api/status` | GET | System status |
| `/api/positions` | GET | List positions |
| `/api/current` | GET | Currently processing resume |
| `/api/evaluation/{id}` | GET | Get evaluation result |
| `/api/config` | POST | Update configuration |
| `/ws` | WS | WebSocket for real-time updates |

## Frontend Views

### View 1: Waiting
- Radar animation indicating monitoring
- Supported format badges
- Connection status

### View 2: Processing
- File preview with placeholder
- Animated scan line effect
- Progress steps (extract → analyze → match → verdict)
- Spinner with "Analyzing Resume" badge

### View 3: Conclusion
- Large verdict card with color coding
  - Green: Invite
  - Yellow: Waitlist
  - Red: Not a Match
- Candidate info section
- Assessment summary
- Strengths and gaps lists
- Action buttons (Back, View Full Report)

## File Structure

```
apps/resume_screener/
├── backend/
│   ├── main.py           # FastAPI app & WebSocket
│   ├── config.py         # Configuration management
│   ├── watcher.py        # File polling & queue
│   ├── screener.py       # AI screening logic
│   └── requirements.txt  # Dependencies
├── frontend/
│   ├── index.html        # Single-page app
│   ├── styles.css        # Professional styling
│   └── app.js            # Frontend logic
├── sample_data/
│   ├── incoming_candidate/   # Drop resumes here
│   ├── processed/            # Archived resumes
│   ├── evaluations/          # JSON evaluation results
│   └── jds/
│       └── positions.json    # Job descriptions
└── README.md
```

## Customization

### Change Polling Interval

```python
# backend/config.py
poll_interval: float = 5.0  # seconds
```

### Add New File Types

Edit `supported_extensions` in `backend/config.py`.

### Customize Verdict Logic

Modify the parsing in `backend/screener.py` in `_parse_result()` method.

### Change Position Matching

**⚠️ Current Limitation**: The screener only evaluates against the first position (`positions[0]`) in `positions.json`.

**Workaround**: To evaluate against a specific position, temporarily reorder `positions.json` to put the target position first.

**Proper Solution**: See [Multi-Position Design Plan](../../docs/07_MULTI_JD_DESIGN_PLAN.md) for the planned parallel evaluation system.

## Integration with @agent_workspace

The screener creates temporary workspaces for each resume:

1. Creates temp workspace with `resources/` and `instructions/`
2. Copies resume to `resources/`
3. Writes JD as screening task to `instructions/`
4. Calls `run_agent()` with the task
5. Parses the structured output
6. Cleans up temp workspace

## Troubleshooting

### WebSocket not connecting
- Check firewall settings
- Ensure port 8000 is available
- Check browser console for errors

### Resumes not detected
- Verify `incoming_dir` path is correct
- Check file permissions
- Ensure file extension is supported

### AI screening fails
- Ensure root `.env` has `LLM_API_KEY` set
- Verify `@agent_workspace` works independently: `python -m agent_workspace --help`
- Check model name is correct in root `.env`

### Slow processing
- First run may be slow (model loading)
- Large PDFs may take longer to extract
- Check LLM API latency

## Known Limitations

⚠️ **Single Position Matching**: Currently, the screener only evaluates candidates against the **first position** in `positions.json` (software-engineer), even when multiple positions are configured. See [Multi-Position Design Plan](../../docs/07_MULTI_JD_DESIGN_PLAN.md) for the planned solution.

⚠️ **Data Organization**: The `sample_data/` folder mixes sample inputs, runtime data, and generated outputs without clear separation. This may lead to accidental git commits of processed files and unbounded data growth. See [Data Organization Design Plan](../../docs/08_DATA_ORGANIZATION_DESIGN_PLAN.md) for the proposed directory structure.

## Future Enhancements

- [x] Multi-position screening (select which JD to use) → **Design complete**, see [docs/07_MULTI_JD_DESIGN_PLAN.md](../../docs/07_MULTI_JD_DESIGN_PLAN.md)
- [ ] Batch processing multiple resumes
- [ ] Detailed report view with full reasoning
- [ ] Export evaluations to CSV/Excel
- [ ] Webhook integration for notifications
- [ ] Resume parsing to pre-fill candidate info
- [ ] Comparison view for waitlisted candidates

## License

Same as @agent_workspace project.
