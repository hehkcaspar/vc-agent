/**
 * Mirrors MAX_ATTACHMENTS in backend/app/services/gemini_context.py.
 * One-shot (Chat) mode inlines files into a single Gemini call;
 * the backend truncates beyond this limit, so the frontend enforces it.
 * Agent (react) mode reads files on demand — no limit.
 */
export const ONE_SHOT_MAX_FILES = 10;
