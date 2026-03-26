"""
ProjectIntelligenceService - AI-powered metadata extraction for project creation.

Simplified approach:
- Files: Passed directly to Gemini (local storage in dev, GCS in production)
- URLs: Gemini accesses via its internet/search capability
- Text: Included directly in prompt

No vector database needed for this use case.
"""
import os
import json
import logging
from typing import List, Optional, Dict, Any, Tuple
from google.genai import types
from taihill_shared.gemini_client import create_genai_client

logger = logging.getLogger(__name__)


class ProjectIntelligenceService:
    """
    Extracts structured project metadata from unstructured sources.
    Uses Gemini with direct file upload and internet access.
    """
    
    def __init__(self, api_key: Optional[str] = None):
        api_key = api_key or os.getenv("GEMINI_API_KEY")
        if api_key:
            self.client = create_genai_client(api_key)
        else:
            logger.warning("GEMINI_API_KEY not set")
            self.client = None
        
        # Use Gemini 3 Flash Preview for fast extraction with file support
        # Doc: https://ai.google.dev/gemini-api/docs/gemini-3
        self.model_name = "gemini-3-flash-preview"
    
    async def extract_metadata(
        self,
        text: Optional[str] = None,
        urls: Optional[List[str]] = None,
        file_paths: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Extract structured metadata from all sources in a single call.
        
        Args:
            text: Free-form text (email, notes, IM)
            urls: URLs for Gemini to access (homepage, articles)
            file_paths: Local file paths to upload to Gemini (PDFs, images)
        
        Returns:
            Extracted metadata with confidence scores
        """
        if not self.client:
            raise RuntimeError("Gemini client not initialized - GEMINI_API_KEY not set")
        
        uploaded_files = []  # Track for cleanup
        
        try:
            # Build the prompt
            prompt = self._build_extraction_prompt(text, urls)
            
            # Upload files to Gemini and build contents
            contents, uploaded_files = await self._build_contents(prompt, file_paths)
            
            logger.info(f"Extracting metadata with {len(file_paths or [])} files, {len(urls or [])} URLs")
            
            # Call Gemini
            response = await self.client.aio.models.generate_content(
                model=self.model_name,
                contents=contents,
                config=types.GenerateContentConfig(
                    temperature=0.1,
                    response_mime_type="application/json"
                )
            )
            
            # Parse response
            try:
                result = json.loads(response.text)
                # Normalize the response
                return self._normalize_result(result)
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse Gemini response: {e}")
                logger.error(f"Response text: {response.text[:500]}")
                raise ValueError(f"Failed to parse extraction result. Response: {response.text[:500]}") from e
        finally:
            # Cleanup uploaded Gemini files
            await self._cleanup_gemini_files(uploaded_files)
    
    async def _build_contents(
        self, 
        prompt: str, 
        file_paths: Optional[List[str]]
    ) -> Tuple[List[Any], List[Any]]:
        """
        Build contents array for Gemini.
        Uploads files and returns ([file1, file2, ..., prompt], [uploaded_refs])
        
        Returns:
            Tuple of (contents_list, uploaded_file_refs)
        """
        contents = []
        uploaded_refs = []
        
        # Upload each file to Gemini
        if file_paths:
            for file_path in file_paths:
                try:
                    if os.path.exists(file_path):
                        # Upload to Gemini File API
                        file_ref = await self.client.aio.files.upload(file=file_path)
                        contents.append(file_ref)
                        uploaded_refs.append(file_ref)
                        logger.info(f"Uploaded file: {file_path} -> {file_ref.name}")
                    else:
                        logger.warning(f"File not found: {file_path}")
                except Exception as e:
                    logger.error(f"Failed to upload file {file_path}: {e}")
        
        # Add the prompt text
        contents.append(prompt)
        
        return contents, uploaded_refs
    
    async def _cleanup_gemini_files(self, file_refs: List[Any]) -> None:
        """
        Delete uploaded files from Gemini File API.
        Silently handles errors (files auto-expire anyway).
        """
        if not file_refs or not self.client:
            return
        
        for file_ref in file_refs:
            try:
                await self.client.aio.files.delete(name=file_ref.name)
                logger.info(f"Cleaned up Gemini file: {file_ref.name}")
            except Exception as e:
                # Silently ignore - files auto-expire after 48h anyway
                logger.debug(f"Failed to delete Gemini file {file_ref.name}: {e}")
    
    def _build_extraction_prompt(
        self, 
        text: Optional[str], 
        urls: Optional[List[str]]
    ) -> str:
        """Build the extraction prompt"""
        
        url_section = ""
        if urls:
            url_section = f"""
URLs to analyze (access these via internet search):
{chr(10).join(f"- {url}" for url in urls)}

"""
        
        text_section = ""
        if text:
            text_section = f"""
Additional Context:
---
{text}
---

"""
        
        return f"""You are an expert VC analyst at Taihill Venture. Extract structured investment information from the provided documents, URLs, and context.

{url_section}{text_section}Extract the following fields. Return ONLY valid JSON.

JSON Structure:
{{
    "company_name": {{
        "value": "Company Name",
        "confidence": "high/medium/low"
    }},
    "founders": [
        {{
            "name": "Founder Name",
            "title": "CEO/CTO/etc",
            "background": "brief background",
            "linkedin_url": "url or null",
            "confidence": "high/medium/low"
        }}
    ],
    "industry_tags": ["AI/ML", "Biotech", "etc"],
    "investment_stage": {{
        "value": "seed/pre-seed/series_a/series_b/angel/growth/unknown",
        "confidence": "high/medium/low"
    }},
    "company_description": {{
        "value": "2-3 sentence description",
        "confidence": "high/medium/low"
    }},
    "company_website": "https://... or null",
    "funding_ask": {{
        "amount": "$5M or 5000000",
        "currency": "USD/CNY",
        "confidence": "high/medium/low"
    }},
    "referral_source": "who referred this deal or null",
    "priority_indicators": ["warm intro", "top accelerator", "serial founder"],
    "red_flags": ["any concerns"],
    "competitors_mentioned": ["competitor1", "competitor2"]
}}

Rules:
1. Access URLs via Google Search to gather information
2. Set confidence to "high" only when explicitly stated or very clear from documents
3. Set confidence to "low" when inferred or ambiguous
4. Use "unknown" for investment_stage if not mentioned
5. Return null or empty arrays for missing information
6. Keep descriptions concise (2-3 sentences max)
7. Normalize funding amounts to standard format
"""
    
    def _normalize_result(self, result: Dict) -> Dict[str, Any]:
        """Normalize the Gemini response to ensure consistent structure"""
        
        # Handle case where Gemini returns a list instead of a dict
        if isinstance(result, list):
            logger.warning(f"Gemini returned a list instead of dict: {result}")
            # If it's a list with at least one dict element, use the first element
            if len(result) > 0 and isinstance(result[0], dict):
                result = result[0]
            else:
                # Return default structure if list is empty or contains non-dict elements
                return self._get_default_result()
        
        # Handle case where result is not a dict at all
        if not isinstance(result, dict):
            logger.warning(f"Gemini returned unexpected type {type(result)}: {result}")
            return self._get_default_result()
        
        # Ensure all required fields exist
        normalized = {
            "company_name": result.get("company_name") or {"value": None, "confidence": "low"},
            "founders": result.get("founders") or [],
            "industry_tags": result.get("industry_tags") or [],
            "investment_stage": result.get("investment_stage") or {"value": "unknown", "confidence": "low"},
            "company_description": result.get("company_description") or {"value": None, "confidence": "low"},
            "company_website": result.get("company_website"),
            "funding_ask": result.get("funding_ask"),
            "referral_source": result.get("referral_source"),
            "priority_indicators": result.get("priority_indicators") or [],
            "red_flags": result.get("red_flags") or [],
            "competitors_mentioned": result.get("competitors_mentioned") or [],
        }
        
        return normalized
    
    def _get_default_result(self) -> Dict[str, Any]:
        """Return a default empty result structure"""
        return {
            "company_name": {"value": None, "confidence": "low"},
            "founders": [],
            "industry_tags": [],
            "investment_stage": {"value": "unknown", "confidence": "low"},
            "company_description": {"value": None, "confidence": "low"},
            "company_website": None,
            "funding_ask": None,
            "referral_source": None,
            "priority_indicators": [],
            "red_flags": [],
            "competitors_mentioned": [],
        }
    
    async def generate_initial_report(
        self,
        project_data: Dict[str, Any],
        file_paths: Optional[List[str]] = None,
    ) -> str:
        """
        Generate an initial assessment report (Investment Memo style).
        
        Args:
            project_data: Extracted project metadata
            file_paths: Additional files for context
        
        Returns:
            Markdown report content
        """
        if not self.client:
            raise RuntimeError("Gemini client not initialized")
        
        uploaded_files = []
        
        try:
            prompt = f"""You are a VC analyst at Taihill Venture. Generate a concise initial assessment report (Investment Memo style) based on the following project information.

Project Information:
- Company: {project_data.get('company_name', {}).get('value', 'Unknown')}
- Stage: {project_data.get('investment_stage', {}).get('value', 'Unknown')}
- Industry: {', '.join(project_data.get('industry_tags', []))}
- Description: {project_data.get('company_description', {}).get('value', 'N/A')}
- Founders: {', '.join(f["name"] for f in project_data.get('founders', []))}
- Funding Ask: {project_data.get('funding_ask', {}).get('amount', 'Not specified')}
- Referral: {project_data.get('referral_source', 'Not specified')}

Generate a structured report with these sections:

1. **Executive Summary** (2-3 sentences)
2. **Market Opportunity** (Brief assessment)
3. **Team Assessment** (Founder backgrounds, strengths/gaps)
4. **Technology/Product** (Key differentiators)
5. **Traction** (What we know so far)
6. **Competitive Landscape** (Known competitors)
7. **Key Risks** (Red flags and concerns)
8. **Recommended Next Steps**
9. **Initial Verdict** (Track/Pass/Deep Dive)

Keep the report concise but insightful. Use investment-grade language.
"""
            
            # Build contents with files if provided
            contents, uploaded_files = await self._build_contents(prompt, file_paths)
            
            response = await self.client.aio.models.generate_content(
                model=self.model_name,
                contents=contents,
                config=types.GenerateContentConfig(
                    temperature=0.3,
                )
            )
            
            return response.text
        finally:
            # Cleanup uploaded Gemini files
            await self._cleanup_gemini_files(uploaded_files)


# Singleton instance
_intelligence_service: Optional[ProjectIntelligenceService] = None


def get_intelligence_service() -> ProjectIntelligenceService:
    """Get or create singleton instance"""
    global _intelligence_service
    if _intelligence_service is None:
        _intelligence_service = ProjectIntelligenceService()
    return _intelligence_service
