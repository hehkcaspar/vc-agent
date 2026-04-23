/**
 * Scholar Chat — simplified conversation UI backed by the scholar agent.
 * Adapted from EntityConversation.tsx but without resource/artifact/preset features.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { ChevronDown } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { academicApi } from '../../services/academicApi';
import { formatMessageTime } from '../../lib/relativeTime';
import type {
  AcademicChatMessage,
  AcademicChatSession,
} from '../../types/academic';

const POLL_INTERVAL_MS = 450;
const POLL_TIMEOUT_MS = 5 * 60 * 1000;

/* ── Spinner frames ─────────────────────────────────────── */
const SPINNER_FRAMES = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏'];
const SPINNER_INTERVAL_MS = 80;

interface ScholarConversationProps {
  scholarId: string;
}

export function ScholarConversation({ scholarId }: ScholarConversationProps) {
  const sessionIdRef = useRef<string | null>(null);
  const sessionMenuRef = useRef<HTMLDivElement | null>(null);
  const messagesEndRef = useRef<HTMLDivElement | null>(null);

  const [sessions, setSessions] = useState<AcademicChatSession[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  sessionIdRef.current = sessionId;
  const [messages, setMessages] = useState<AcademicChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [agentJob, setAgentJob] = useState<{
    sessionId: string;
    jobId: string;
  } | null>(null);
  const [agentStatus, setAgentStatus] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [sessionMenuOpen, setSessionMenuOpen] = useState(false);
  const [spinnerFrame, setSpinnerFrame] = useState(0);

  /* ── Spinner animation ─────────────────────────────── */
  useEffect(() => {
    if (!agentJob) return;
    const id = window.setInterval(
      () => setSpinnerFrame((f) => (f + 1) % SPINNER_FRAMES.length),
      SPINNER_INTERVAL_MS,
    );
    return () => window.clearInterval(id);
  }, [agentJob]);

  /* ── Load sessions on mount / scholarId change ─────── */
  const refreshSessions = useCallback(async () => {
    const list = await academicApi.scholars.chat.listSessions(scholarId);
    setSessions(list);
    return list;
  }, [scholarId]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        setError(null);
        const list = await academicApi.scholars.chat.listSessions(scholarId);
        if (cancelled) return;
        setSessions(list);
        if (list.length === 0) {
          const s = await academicApi.scholars.chat.createSession(scholarId, {});
          if (cancelled) return;
          setSessions([s]);
          setSessionId(s.id);
        } else {
          setSessionId((prev) => prev ?? list[0].id);
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      }
    })();
    return () => { cancelled = true; };
  }, [scholarId]);

  /* ── Load messages when session changes ────────────── */
  useEffect(() => {
    if (!sessionId) return;
    let cancelled = false;
    academicApi.scholars.chat
      .getSession(scholarId, sessionId)
      .then((d) => { if (!cancelled) setMessages(d.messages); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [scholarId, sessionId]);

  /* ── Close session menu on outside click ───────────── */
  useEffect(() => {
    if (!sessionMenuOpen) return;
    const handler = (e: MouseEvent) => {
      if (!sessionMenuRef.current?.contains(e.target as Node)) {
        setSessionMenuOpen(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [sessionMenuOpen]);

  /* ── Reset agent state on scholarId change ─────────── */
  useEffect(() => {
    setAgentJob(null);
    setAgentStatus('');
  }, [scholarId]);

  /* ── Job polling ───────────────────────────────────── */
  useEffect(() => {
    if (!agentJob) return;
    let cancelled = false;
    const startedAt = Date.now();

    const poll = async () => {
      try {
        const st = await academicApi.scholars.chat.getJob(
          scholarId,
          agentJob.sessionId,
          agentJob.jobId,
        );
        if (cancelled) return;

        const viewingThis = sessionIdRef.current === agentJob.sessionId;
        if (viewingThis) {
          setAgentStatus(
            st.step_detail?.trim() ||
              (st.status === 'pending' ? 'Queued...' : st.status),
          );
        }

        // Timeout
        if (
          (st.status === 'pending' || st.status === 'running') &&
          Date.now() - startedAt > POLL_TIMEOUT_MS
        ) {
          setAgentJob((c) => (c?.jobId === agentJob.jobId ? null : c));
          setAgentStatus('');
          if (sessionIdRef.current === agentJob.sessionId) {
            setError('Agent timed out. Please try again.');
          }
          return;
        }

        if (st.status === 'succeeded') {
          // Refresh messages from the server for consistency
          let detail: Awaited<ReturnType<typeof academicApi.scholars.chat.getSession>> | null = null;
          try {
            detail = await academicApi.scholars.chat.getSession(scholarId, agentJob.sessionId);
          } catch { /* fallback below */ }
          if (cancelled) return;
          if (sessionIdRef.current === agentJob.sessionId) {
            if (detail) {
              setMessages(detail.messages);
            } else if (st.assistant_message) {
              setMessages((prev) => {
                if (prev.some((m) => m.id === st.assistant_message!.id)) return prev;
                return [...prev, st.assistant_message!];
              });
            }
          }
          setAgentJob((c) => (c?.jobId === agentJob.jobId ? null : c));
          setAgentStatus('');
        } else if (st.status === 'failed') {
          setAgentJob((c) => (c?.jobId === agentJob.jobId ? null : c));
          setAgentStatus('');
          if (sessionIdRef.current === agentJob.sessionId) {
            setError(st.error_message || 'Agent run failed');
          }
        }
      } catch (e) {
        if (!cancelled) {
          setAgentJob(null);
          setAgentStatus('');
          if (sessionIdRef.current === agentJob.sessionId) {
            setError(e instanceof Error ? e.message : String(e));
          }
        }
      }
    };

    const id = window.setInterval(() => void poll(), POLL_INTERVAL_MS);
    void poll();
    return () => { cancelled = true; window.clearInterval(id); };
  }, [agentJob, scholarId]);

  const agentActiveHere = Boolean(agentJob && sessionId && agentJob.sessionId === sessionId);

  /* ── Scroll to bottom on new messages ──────────────── */
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, agentActiveHere]);

  /* ── Send message ──────────────────────────────────── */
  const send = useCallback(async () => {
    const text = input.trim();
    if (!text || !sessionId || busy || agentActiveHere) return;
    setBusy(true);
    setError(null);

    try {
      const res = await academicApi.scholars.chat.postMessage(scholarId, sessionId, { text });
      // Optimistic: add user message immediately
      setMessages((prev) => [...prev, res.user_message]);
      setInput('');
      setAgentJob({ sessionId, jobId: res.job_id });
      setAgentStatus('Queued...');
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [input, sessionId, scholarId, busy, agentActiveHere]);

  /* ── Session management ────────────────────────────── */
  const handleNewSession = useCallback(async () => {
    try {
      const s = await academicApi.scholars.chat.createSession(scholarId, {});
      await refreshSessions();
      setSessionId(s.id);
      setMessages([]);
      setSessionMenuOpen(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [scholarId, refreshSessions]);

  const handleDeleteSession = useCallback(
    async (sid: string) => {
      try {
        await academicApi.scholars.chat.deleteSession(scholarId, sid);
        const list = await refreshSessions();
        if (sessionId === sid) {
          setSessionId(list.length > 0 ? list[0].id : null);
          setMessages([]);
        }
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    },
    [scholarId, sessionId, refreshSessions],
  );

  /* ── Render ────────────────────────────────────────── */
  return (
    <div className="entity-conversation">
      {/* Session bar */}
      <div className="entity-conversation-bar">
        <div className="entity-conversation-session-menu" ref={sessionMenuRef}>
          <button
            className="entity-conversation-session-btn"
            onClick={() => setSessionMenuOpen((o) => !o)}
            title="Chat sessions"
          >
            {sessions.find((s) => s.id === sessionId)?.title || 'Chat'}{' '}
            <ChevronDown size={12} />
          </button>
          {sessionMenuOpen && (
            <div className="entity-conversation-session-dropdown">
              {sessions.map((s) => (
                <div
                  key={s.id}
                  className={`entity-conversation-session-item ${s.id === sessionId ? 'active' : ''}`}
                >
                  <button
                    className="entity-conversation-session-select"
                    onClick={() => {
                      setSessionId(s.id);
                      setSessionMenuOpen(false);
                    }}
                  >
                    {s.title || `Session ${s.id.slice(0, 8)}`}
                  </button>
                  <button
                    className="entity-conversation-session-delete"
                    onClick={() => handleDeleteSession(s.id)}
                    title="Delete session"
                  >
                    x
                  </button>
                </div>
              ))}
              <button
                className="entity-conversation-session-new"
                onClick={handleNewSession}
              >
                + New Chat
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Error banner */}
      {error && (
        <div className="entity-conversation-error">
          {error}
          <button onClick={() => setError(null)} style={{ marginLeft: 8, cursor: 'pointer' }}>
            Dismiss
          </button>
        </div>
      )}

      {/* Messages */}
      <div className="entity-conversation-messages">
        {messages.length === 0 && !agentActiveHere && (
          <div className="entity-conversation-empty">
            Ask anything about this scholar — the agent has access to their full dossier and tools.
          </div>
        )}
        {messages.map((m) => (
          <div key={m.id} className={`entity-conversation-msg entity-conversation-msg-${m.role}`}>
            <div className="entity-conversation-msg-header">
              <div className="entity-conversation-msg-role">
                {m.role === 'user' ? 'You' : 'Agent'}
              </div>
              <div
                className="entity-conversation-msg-time"
                title={new Date(m.created_at).toLocaleString()}
              >
                {formatMessageTime(m.created_at)}
              </div>
            </div>
            <div className="entity-conversation-msg-content">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{m.content}</ReactMarkdown>
            </div>
          </div>
        ))}
        {agentActiveHere && (
          <div className="entity-conversation-msg entity-conversation-msg-assistant">
            <div className="entity-conversation-msg-role">Agent</div>
            <div className="entity-conversation-msg-content entity-conversation-msg-thinking">
              <span className="entity-conversation-spinner">
                {SPINNER_FRAMES[spinnerFrame]}
              </span>{' '}
              {agentStatus || 'Thinking...'}
            </div>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* Compose */}
      <div className="entity-conversation-compose">
        <textarea
          className="entity-conversation-input"
          rows={2}
          aria-label="Ask about this scholar"
          placeholder={
            agentActiveHere
              ? agentStatus || 'Agent is working...'
              : 'Ask about this scholar...'
          }
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              void send();
            }
          }}
          disabled={busy || agentActiveHere}
        />
        <button
          className="entity-conversation-send"
          onClick={() => void send()}
          disabled={busy || agentActiveHere || !input.trim()}
        >
          Send
        </button>
      </div>
    </div>
  );
}
