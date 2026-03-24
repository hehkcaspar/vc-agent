"""Resume screening logic using agent_workspace."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import tempfile
import time
import warnings
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# Suppress LangChain Pydantic V1 warning
warnings.filterwarnings("ignore", message="Core Pydantic V1 functionality isn't compatible with Python 3.14 or greater.")

# Add agent_workspace to path (fallback for when package is not pip-installed)
project_root = Path(__file__).parent.parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from agent_workspace.agent import run_agent

# Import screener-local config (same directory)
# When running from backend/ dir or via uvicorn, the backend dir is on sys.path
_backend_dir = str(Path(__file__).parent)
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)
from config import get_config, get_llm_settings

logger = logging.getLogger(__name__)


@dataclass
class Position:
    """Job position definition."""
    id: str
    title: str
    department: str
    level: str
    headcount: int
    description: str
    requirements: List[str]
    nice_to_have: List[str]
    required_skills: List[str]
    technical_keywords: List[str]
    ai_competency_indicators: List[str]
    screening_criteria: Dict[str, List[str]]
    evaluation_focus: List[str]
    min_years_experience: Optional[int] = None
    max_years_experience: Optional[int] = None
    experience_levels: Optional[Dict[str, str]] = None


@dataclass
class ScreeningResult:
    """Result of a resume screening."""
    id: str
    resume_id: str
    position_id: str
    candidate_name: Optional[str]
    verdict: str  # "invite", "waitlist", "reject"
    confidence: str  # "high", "medium", "low"
    summary: str
    strengths: List[str]
    gaps: List[str]
    experience_years: Optional[float]
    skills_match: Dict[str, Any]
    ai_competency: Dict[str, Any]  # New field for AI collaboration assessment
    reasoning: str
    evaluated_at: str
    processing_time_seconds: float
    raw_output: str  # Store the raw agent output for debugging


class JDStore:
    """Manages job description storage."""
    
    def __init__(self):
        self.config = get_config()
        self._positions: Dict[str, Position] = {}
        self._load()
    
    def _load(self) -> None:
        """Load positions from JSON file."""
        jds_path = Path(self.config.jds_file)
        if not jds_path.exists():
            logger.warning(f"JD file not found: {jds_path}")
            return
        
        try:
            with open(jds_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            for pos_data in data.get("positions", []):
                pos = Position(
                    id=pos_data["id"],
                    title=pos_data["title"],
                    department=pos_data.get("department", ""),
                    level=pos_data.get("level", ""),
                    headcount=pos_data.get("headcount", 1),
                    description=pos_data.get("description", ""),
                    requirements=pos_data.get("requirements", []),
                    nice_to_have=pos_data.get("nice_to_have", []),
                    required_skills=pos_data.get("required_skills", []),
                    technical_keywords=pos_data.get("technical_keywords", []),
                    ai_competency_indicators=pos_data.get("ai_competency_indicators", []),
                    screening_criteria=pos_data.get("screening_criteria", {}),
                    evaluation_focus=pos_data.get("evaluation_focus", []),
                    min_years_experience=pos_data.get("min_years_experience"),
                    max_years_experience=pos_data.get("max_years_experience"),
                    experience_levels=pos_data.get("experience_levels"),
                )
                self._positions[pos.id] = pos
            
            logger.info(f"Loaded {len(self._positions)} positions")
        except Exception as e:
            logger.error(f"Failed to load JDs: {e}")
    
    def get_position(self, position_id: str) -> Optional[Position]:
        """Get a position by ID."""
        return self._positions.get(position_id)
    
    def list_positions(self) -> List[Position]:
        """List all positions."""
        return list(self._positions.values())
    
    def reload(self) -> None:
        """Reload positions from file."""
        self._positions.clear()
        self._load()


class ResumeScreener:
    """Screens resumes against job descriptions."""
    
    def __init__(self):
        self.config = get_config()
        self.jd_store = JDStore()
    
    def _create_workspace(self, resume_id: str, resume_path: Path, position: Position) -> Path:
        """Create a persistent workspace for screening within the app's directory."""
        app_dir = Path(__file__).resolve().parent.parent
        workspaces_dir = app_dir / "sample_data" / "workspaces"
        
        timestamp = int(time.time())
        workspace = workspaces_dir / f"{resume_id}_{position.id}_{timestamp}"
        workspace.mkdir(parents=True, exist_ok=True)
        
        # Create structure
        resources_dir = workspace / "resources"
        artifacts_dir = workspace / "artifacts"
        instructions_dir = workspace / "instructions"
        
        resources_dir.mkdir()
        artifacts_dir.mkdir()
        instructions_dir.mkdir()
        
        # Copy resume to resources
        resume_dest = resources_dir / f"resume{resume_path.suffix}"
        resume_dest.write_bytes(resume_path.read_bytes())
        
        # Create JD summary as task
        jd_text = self._format_jd(position)
        task_file = instructions_dir / "screening_task.md"
        task_file.write_text(jd_text, encoding="utf-8")
        
        # Create config.yaml
        # Allow enabling trace during debug runs via env var RESUME_SCREENER_DEBUG_TRACE
        debug_trace = os.getenv("RESUME_SCREENER_DEBUG_TRACE", "0").lower() in ("1", "true", "yes")
        trace_flag = "true" if debug_trace else "false"

        config_content = f"""
workspace:
  resources_dir: resources
  instructions_dir: instructions
  artifacts_dir: artifacts
  snapshots_dir: .snapshots

extraction:
  max_text_chars: 15000
  max_images: 10
  max_excel_rows: 100
  max_excel_sheets: 5

agent:
  max_iterations: 15
  memory_turns: 10
  trace_enabled: {trace_flag}
"""
        (workspace / "config.yaml").write_text(config_content.strip(), encoding="utf-8")
        
        return workspace
    
    def _format_jd(self, position: Position) -> str:
        """Format JD into screening task with enhanced Chinese support."""
        requirements = "\n".join(f"- {r}" for r in position.requirements)
        nice_to_have = "\n".join(f"- {n}" for n in position.nice_to_have) if position.nice_to_have else "无"
        skills = ", ".join(position.required_skills) if position.required_skills else "见要求"
        
        # Build experience guidance
        exp_guidance = ""
        if position.experience_levels:
            exp_guidance = "### 经验层级参考\n" + "\n".join(
                f"- **{k}**: {v}" for k, v in position.experience_levels.items()
            )
        
        # Build technical keywords section
        tech_keywords = ""
        if position.technical_keywords:
            tech_keywords = f"\n### 技术关键词（用于匹配）\n{', '.join(position.technical_keywords[:20])}"
        
        # Build AI competency indicators
        ai_indicators = ""
        if position.ai_competency_indicators:
            ai_indicators = f"\n### AI能力指标\n寻找以下关键词或证据：\n" + "\n".join(f"- {i}" for i in position.ai_competency_indicators[:10])
        
        # Build screening criteria
        screening_guide = ""
        if position.screening_criteria:
            sc = position.screening_criteria
            screening_guide = f"\n### 筛选标准\n"
            if "strong_match" in sc:
                screening_guide += f"**强匹配信号**：{', '.join(sc['strong_match'])}\n"
            if "deal_breakers" in sc:
                screening_guide += f"**一票否决**：{', '.join(sc['deal_breakers'])}\n"
            if "concerns" in sc:
                screening_guide += f"**需关注**：{', '.join(sc['concerns'])}\n"
        
        # Build evaluation focus
        eval_focus = ""
        if position.evaluation_focus:
            eval_focus = "\n### 评估重点\n" + "\n".join(f"{i+1}. {ef}" for i, ef in enumerate(position.evaluation_focus))
        
        return f"""# 简历筛选任务 - {position.title}

## 职位信息
**职位**：{position.title}  
**部门**：{position.department}  
**级别**：{position.level}  
**招聘数量**：{position.headcount}人  

## 职位描述
{position.description}

## 必备要求
{requirements}

## 加分项
{nice_to_have}

## 核心技能
{skills}
{tech_keywords}
{ai_indicators}
{exp_guidance}
{screening_guide}
{eval_focus}

---

## ⚠️ 极其重要：核心提交流程 (Core-Shell Architecture)

**你必须使用 `write_artifact` 工具将最终的结构化评估报告写入 `reports` 分类下（例如 `evaluation.md`）。**
**不要在最终的群聊对话或回复消息中直接输出长篇大论。系统会严格从你生成的 artifact 文件中提取结构化数据。**

请用**中文**提供评估结果，严格遵循以下格式（每个字段名必须全大写并加粗）：

**VERDICT:** (三选一：invite / waitlist / reject)
**CONFIDENCE:** (三选一：high / medium / low)
**CANDIDATE_NAME:** (从简历中提取的姓名，或写"未知")
**EXPERIENCE_YEARS:** (数字，如3.5，或"未知")

**SUMMARY:**
(用2-3句话总结候选人与职位的匹配度)

**STRENGTHS:**
- (列出每个优势，以"- "开头，至少3条)
- 
- 

**GAPS:**
- (列出每个不足或需验证点，以"- "开头，至少1条)
- 

**REASONING:**
(用1-2句话解释你的判断理由)

---

## 评判标准

- **INVITE (邀请面试)**：候选人明确满足所有必备要求，AI协作能力强，有相关作品/项目经验
- **WAITLIST (待定)**：候选人基本符合但存在不确定性，或某些方面有亮点但其他方面不足
- **REJECT (不匹配)**：候选人明显不符合必备要求，或缺乏核心技能

## 特别提醒

1. **AI协作能力**是本公司的核心要求，请特别关注候选人是否使用AI工具（Cursor、Claude、ChatGPT等）
2. **作品心态**：寻找有个人项目、开源贡献、作品集等证据的候选人
3. **结果导向**：关注候选人是否能从目标出发拆解问题，而非被动执行任务
4. **简历可能是中文**，请准确提取中文姓名和中文项目描述
5. **严禁在最后一条消息中输出评估结果**。你必须使用 `write_artifact` 工具提交结构化评估数据到 `reports` 目录下。

请客观、全面地进行评估。
"""
    
    def _parse_result(self, output: str, resume_id: str, position_id: str, start_time: datetime) -> ScreeningResult:
        """Parse agent output into structured result with Chinese support."""
        # Default values
        verdict = "waitlist"
        confidence = "medium"
        candidate_name = None
        experience_years = None
        summary = ""
        strengths = []
        gaps = []
        skills_match = {}
        ai_competency = {}
        reasoning = output
        
        # Try to extract structured fields using flexible regex patterns
        # that handle variations in LLM output formatting
        try:
            # Extract VERDICT (support markdown bold, plain text, Chinese labels)
            verdict_patterns = [
                r'\*\*VERDICT:?\*\*\s*:?\s*(\w+)',
                r'VERDICT\s*:?\s*(\w+)',
                r'\*\*(?:决[定策]|Recommendation|推荐|结论):?\*\*\s*:?\s*([^\n]+)',
                r'#{1,3}\s*(?:推荐结论|结论|综合评价|Verdict)[^\n]*\n+\s*(?:\*\*)?([^\n]+)',
            ]
            for pat in verdict_patterns:
                if match := re.search(pat, output, re.IGNORECASE):
                    v = match.group(1).strip().lower()
                    if any(kw in v for kw in ("invite", "interview", "yes", "strong", "proceed", "邀请", "推荐", "通过", "面试")):
                        verdict = "invite"
                    elif any(kw in v for kw in ("reject", "no", "not-match", "poor", "fail", "拒绝", "不匹配", "不符合", "不通过", "淘汰")):
                        verdict = "reject"
                    else:
                        verdict = "waitlist"
                    break
            
            # Extract CONFIDENCE
            conf_patterns = [
                r'\*\*CONFIDENCE:?\*\*\s*:?\s*(\w+)',
                r'CONFIDENCE\s*:?\s*(\w+)',
                r'\*\*置信度:?\*\*\s*:?\s*(\w+)',
            ]
            for pat in conf_patterns:
                if match := re.search(pat, output, re.IGNORECASE):
                    c = match.group(1).lower()
                    if c in ("high", "strong", "高"):
                        confidence = "high"
                    elif c in ("low", "weak", "低"):
                        confidence = "low"
                    else:
                        confidence = "medium"
                    break
            
            # Extract CANDIDATE_NAME (support Chinese names, various label styles)
            name_patterns = [
                r'\*\*CANDIDATE_NAME:?\*\*\s*:?\s*([^\n]+)',
                r'CANDIDATE_NAME\s*:?\s*([^\n]+)',
                r'\*\*(?:Name|姓名|候选人|Candidate):?\*\*\s*:?\s*([^\n]+)',
                r'(?:Name|姓名|候选人|Candidate)\s*[:：]\s*([^\n]+)',
            ]
            for pat in name_patterns:
                if match := re.search(pat, output, re.IGNORECASE):
                    name = match.group(1).strip().strip('*').strip()
                    # Filter out placeholder values in both English and Chinese
                    if name.lower() not in ("unknown", "not specified", "n/a", "未知", "未指定", "-", "—") and len(name) > 1:
                        candidate_name = name
                        break
            
            # Extract EXPERIENCE_YEARS
            exp_patterns = [
                r'\*\*EXPERIENCE_YEARS:?\*\*\s*:?\s*([\d.]+)',
                r'EXPERIENCE_YEARS\s*:?\s*([\d.]+)',
                r'\*\*(?:Experience|经验|工作年限):?\*\*\s*:?\s*~?([\d.]+)',
                r'(?:Experience|经验|工作年限)\s*[:：]\s*~?(?:约)?([\d.]+)',
            ]
            for pat in exp_patterns:
                if match := re.search(pat, output, re.IGNORECASE):
                    try:
                        experience_years = float(match.group(1))
                        break
                    except ValueError:
                        pass
            
            # Extract SUMMARY — try multiple section header styles
            summary_patterns = [
                r'\*\*SUMMARY:?\*\*\s*\n?(.+?)(?=\n\s*\*\*[A-Z]|\n\s*#{1,3}\s|$)',
                r'#{1,3}\s*(?:Summary|评估摘要|总结|概述)\s*\n(.+?)(?=\n\s*\*\*|\n\s*#{1,3}\s|$)',
            ]
            for pat in summary_patterns:
                if match := re.search(pat, output, re.IGNORECASE | re.DOTALL):
                    summary = match.group(1).strip()
                    if len(summary) > 10:
                        break
            
            # Extract STRENGTHS — handle multiple section header styles
            strength_patterns = [
                r'\*\*STRENGTHS:?\*\*\s*\n?(.+?)(?=\n\s*\*\*(?:GAPS|REASONING|CONCERNS|待|不足)|\n\s*#{1,3}\s|$)',
                r'\*\*(?:Strengths?|优势|关键优势|Key Strengths?):?\*\*\s*\n?(.+?)(?=\n\s*\*\*(?:Concerns?|Gaps?|Weakness|不足|待)|\n\s*#{1,3}\s|$)',
                r'#{1,3}\s*(?:Strengths?|优势|关键优势|Key Strengths?)\s*\n(.+?)(?=\n\s*\*\*|\n\s*#{1,3}\s|$)',
            ]
            for pat in strength_patterns:
                if match := re.search(pat, output, re.IGNORECASE | re.DOTALL):
                    strengths_text = match.group(1).strip()
                    parsed = [s.strip("- •✅⚠️\t ").strip() for s in strengths_text.split('\n') 
                                if s.strip() and len(s.strip("- •✅\t ").strip()) > 2]
                    if parsed:
                        strengths = parsed
                        break
            
            # Extract GAPS — handle multiple section header styles
            gap_patterns = [
                r'\*\*GAPS:?\*\*\s*\n?(.+?)(?=\n\s*\*\*(?:REASONING|AI|结论)|\n\s*#{1,3}\s|$)',
                r'\*\*(?:Gaps?|Concerns?|Weakness(?:es)?|不足|待探索|需关注):?\*\*\s*\n?(.+?)(?=\n\s*\*\*(?:Recommend|Reason|结论|AI)|\n\s*#{1,3}\s|$)',
                r'#{1,3}\s*(?:Gaps?|Concerns?|不足|待探索)\s*\n(.+?)(?=\n\s*\*\*|\n\s*#{1,3}\s|$)',
            ]
            for pat in gap_patterns:
                if match := re.search(pat, output, re.IGNORECASE | re.DOTALL):
                    gaps_text = match.group(1).strip()
                    parsed = [g.strip("- •⚠️\t ").strip() for g in gaps_text.split('\n') 
                             if g.strip() and len(g.strip("- •⚠️\t ").strip()) > 2]
                    if parsed:
                        gaps = parsed
                        break
            
            # Extract REASONING
            reasoning_patterns = [
                r'\*\*REASONING:?\*\*\s*\n?(.+?)(?=\n\s*\*\*|\n\s*#{1,3}\s|$)',
                r'\*\*(?:Reasoning|Recommendation|判断理由|推荐理由|结论):?\*\*\s*\n?(.+?)(?=\n\s*\*\*|\n\s*#{1,3}\s|$)',
                r'#{1,3}\s*(?:Reasoning|Recommendation|结论|推荐)\s*\n(.+?)(?=\n\s*\*\*|\n\s*#{1,3}\s|$)',
            ]
            for pat in reasoning_patterns:
                if match := re.search(pat, output, re.IGNORECASE | re.DOTALL):
                    r_text = match.group(1).strip()
                    if len(r_text) > 10:
                        reasoning = r_text
                        break
        
        except Exception as e:
            logger.warning(f"Failed to parse result: {e}")
        
        # Fallback: if we couldn't extract structured data, try to infer from raw output
        if not summary and output:
            # Use first 300 chars as summary
            summary = output[:300].strip().replace('\n', ' ')
            
        # If reasoning was never extracted, it stays as the full output. Try to narrow it.
        if reasoning == output and output:
            # Look for a conclusion block
            if match := re.search(r'(?:结论|综合评价|推荐理由|Reasoning|Recommendation)[^\n]*\n(.+)', output, re.IGNORECASE | re.DOTALL):
                reasoning = match.group(1).strip()
        
        # If still no verdict, try to detect from the conclusion/reasoning text, NOT the whole document
        # Scanning the whole document causes false positives (e.g. "不匹配" in weak points).
        if verdict == "waitlist" and output:
            # Use reasoning if we narrowed it down, otherwise just the last 600 chars of output
            check_text = reasoning if (reasoning != output and len(reasoning) < 1500) else output[-600:]
            lower = check_text.lower()
            # Check invite signals first, as candidates missing mandatory skills are usually quick rejects
            invite_signals = ["invite", "recommend", "strong fit", "good match", "excellent", "推荐", "强匹配", "符合", "建议面试", "可以面试", "通过"]
            reject_signals = ["reject", "not match", "doesn't meet", "not suitable", "拒绝", "不匹配", "不符合", "淘汰", "不推荐"]
            
            if any(word in lower for word in invite_signals):
                verdict = "invite"
            elif any(word in lower for word in reject_signals):
                verdict = "reject"
        
        # Infer AI competency from output
        ai_competency = {
            "uses_ai_tools": any(kw in output.lower() for kw in ["cursor", "claude", "copilot", "ai工具", "ai编程"]),
            "has_projects": any(kw in output.lower() for kw in ["project", "作品", "项目", "portfolio", "github"]),
            "ownership_mindset": any(kw in output.lower() for kw in ["ownership", "responsible", "owner", "责任", "负责"])
        }
        
        processing_time = (datetime.now() - start_time).total_seconds()
        
        return ScreeningResult(
            id=f"eval_{resume_id}_{position_id}",
            resume_id=resume_id,
            position_id=position_id,
            candidate_name=candidate_name,
            verdict=verdict,
            confidence=confidence,
            summary=summary,
            strengths=strengths,
            gaps=gaps,
            experience_years=experience_years,
            skills_match=skills_match,
            ai_competency=ai_competency,
            reasoning=reasoning,
            evaluated_at=datetime.now().isoformat(),
            processing_time_seconds=processing_time,
            raw_output=output[:2000] if output else "",  # Store truncated output for debugging
        )
    
    async def screen(self, resume_id: str, resume_path: Path, position_id: Optional[str] = None) -> ScreeningResult:
        """Screen a resume against a position."""
        start_time = datetime.now()
        
        # Get position (use first available if not specified)
        if position_id:
            position = self.jd_store.get_position(position_id)
        else:
            positions = self.jd_store.list_positions()
            position = positions[0] if positions else None
        
        if not position:
            raise ValueError(f"Position not found: {position_id}")
        
        # Create workspace and run agent
        workspace = None
        try:
            workspace = self._create_workspace(resume_id, resume_path, position)
            
            # Use LLM settings from agent_workspace core (loaded from root .env)
            llm_settings = get_llm_settings()
            
            # If the resume is an image, we should attach it to the prompt directly for vision-native LLMs
            images = None
            if resume_path.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
                images = [workspace / "resources" / f"resume{resume_path.suffix}"]
            
            # Run the agent in a thread to avoid blocking the async event loop
            jd_text = self._format_jd(position)
            task = (
                f"Evaluate the candidate's resume for the {position.title} position.\n\n"
                f"=== INSTRUCTIONS & JOB DESCRIPTION ===\n{jd_text}\n======================================\n\n"
                f"IMPORTANT: You must write your final evaluation report using the write_artifact tool "
                f"(e.g., to 'artifacts/reports/evaluation.md') BEFORE finishing."
            )
            result = await asyncio.to_thread(run_agent, workspace, task, llm_settings, False, images)

            # Dump full raw agent result to workspace artifacts for debugging
            try:
                trace_dir = workspace / "artifacts" / "traces"
                trace_dir.mkdir(parents=True, exist_ok=True)
                trace_file = trace_dir / f"agent_result_{resume_id}.json"
                import json as _json
                trace_file.write_text(_json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
                logger.info(f"Agent result dumped: {trace_file}")
            except Exception as e:
                logger.warning(f"Failed to write agent result dump: {e}")

            # Robustly extract final textual output from the agent result.
            # Searches ALL messages in reverse for the last AI message with
            # substantial content, instead of only checking msgs[-1].
            def _extract_text_from_result(res: dict) -> str:
                try:
                    msgs = res.get("messages") or res.get("message") or []
                except Exception:
                    msgs = []

                if isinstance(msgs, dict):
                    msgs = [msgs]

                out = ""
                if isinstance(msgs, (list, tuple)) and msgs:
                    # Search backwards through all messages for the last AI
                    # message with substantial content (>50 chars).
                    # Skip ToolMessages and trivial/empty AI messages.
                    for msg in reversed(msgs):
                        # Skip tool response messages
                        msg_type = getattr(msg, "type", None)
                        if msg_type == "tool":
                            continue
                        if isinstance(msg, dict) and msg.get("role") == "tool":
                            continue
                        # Skip user messages
                        if msg_type == "human":
                            continue
                        if isinstance(msg, dict) and msg.get("role") == "user":
                            continue

                        # Extract content from this message
                        content = ""
                        if hasattr(msg, "content"):
                            content = getattr(msg, "content") or ""
                        elif isinstance(msg, dict):
                            for k in ("content", "text", "message", "output", "body"):
                                if k in msg and msg[k]:
                                    content = msg[k]
                                    break
                        elif isinstance(msg, (list, tuple)) and len(msg) >= 2:
                            content = str(msg[1])

                        if isinstance(content, str) and len(content) > 200:
                            out = content
                            break

                    # If no substantial AI message found, concatenate all AI
                    # message content as last resort
                    if not out:
                        parts = []
                        for msg in msgs:
                            msg_type = getattr(msg, "type", None)
                            if msg_type in ("tool", "human"):
                                continue
                            if isinstance(msg, dict) and msg.get("role") in ("tool", "user"):
                                continue
                            content = ""
                            if hasattr(msg, "content"):
                                content = getattr(msg, "content") or ""
                            elif isinstance(msg, dict):
                                content = msg.get("content", "") or ""
                            if content:
                                parts.append(content)
                        if parts:
                            out = "\n\n".join(parts)

                # Fallback: top-level result keys
                if not out:
                    for k in ("result", "output", "final", "final_output"):
                        if isinstance(res, dict) and k in res and res[k]:
                            cand = res[k]
                            if isinstance(cand, (list, dict)):
                                try:
                                    out = json.dumps(cand, ensure_ascii=False)
                                except Exception:
                                    out = str(cand)
                            else:
                                out = str(cand)
                            break

                # Coerce non-string types
                if isinstance(out, (list, tuple)):
                    out = "\n".join(str(x) for x in out)
                if isinstance(out, dict):
                    out = json.dumps(out, ensure_ascii=False)

                return out or ""

            # 1. Primary Source: read agent-written artifact reports from workspace
            # The core agent prompt explicitly instructs writing artifacts for persistent data.
            output = ""
            logger.info("Checking agent artifacts as primary output source...")
            try:
                reports_dir = workspace / "artifacts" / "reports"
                if reports_dir.exists():
                    for artifact_file in sorted(reports_dir.iterdir()):
                        if artifact_file.is_file() and artifact_file.suffix in (".md", ".txt", ".json"):
                            artifact_text = artifact_file.read_text(encoding="utf-8")
                            if artifact_text and len(artifact_text) > len(output):
                                logger.info(f"Using artifact as output: {artifact_file.name} ({len(artifact_text)} chars)")
                                output = artifact_text
            except Exception as e:
                logger.warning(f"Failed to read agent artifacts: {e}")

            # 2. Fallback: extract final textual output from the agent result history
            if not output or len(output) < 300:
                logger.info("No substantial artifact found; falling back to agent message history...")
                msg_output = _extract_text_from_result(result)
                if msg_output and len(msg_output) > len(output):
                    output = msg_output

            # Log the result structure and extracted output (info level for visibility)
            try:
                logger.info(f"Agent raw result keys: {list(result.keys())}")
                msg_count = len(result.get('messages', []))
                logger.info(f"Agent returned {msg_count} messages")
            except Exception:
                logger.info("Agent raw result (non-mapping)")
            logger.info(f"Extracted agent output length: {len(output)} chars")
            if output:
                logger.info(f"First 500 chars of output: {output[:500]}")

            # Parse result
            screening_result = self._parse_result(output, resume_id, position.id, start_time)
            
            # Save evaluation
            self._save_evaluation(screening_result)
            
            return screening_result
            
        finally:
            # Cleanup temp workspace
            if workspace and workspace.exists():
                import shutil
                shutil.rmtree(workspace, ignore_errors=True)
    
    def _save_evaluation(self, result: ScreeningResult) -> None:
        """Save evaluation to file."""
        eval_dir = Path(self.config.evaluations_dir)
        eval_dir.mkdir(parents=True, exist_ok=True)
        
        eval_file = eval_dir / f"{result.id}.json"
        with open(eval_file, "w", encoding="utf-8") as f:
            json.dump(asdict(result), f, indent=2, ensure_ascii=False)
        
        logger.info(f"Evaluation saved: {eval_file.name}")
    
    def get_evaluation(self, evaluation_id: str) -> Optional[ScreeningResult]:
        """Load evaluation from file."""
        eval_file = Path(self.config.evaluations_dir) / f"{evaluation_id}.json"
        if not eval_file.exists():
            return None
        
        with open(eval_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        return ScreeningResult(**data)
