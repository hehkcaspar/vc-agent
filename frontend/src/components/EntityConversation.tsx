import { useCallback, useEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { useChatModelProfile } from '../context/ChatModelProfileContext';
import { parseArtifactCardMessage, resolveArtifactForViewer } from '../lib/chatArtifactCard';
import { api } from '../services/api';
import type { Artifact, ChatMessage, ChatSession, PresetInfo, Resource } from '../types';

function roleLabel(role: string): string {
  const r = role.toLowerCase();
  if (r === 'user') return 'User';
  if (r === 'assistant') return 'Assistant';
  return role;
}

const CHAT_AGENT_PREF_KEY = 'vc_chat_use_deep_agent';

function readChatAgentPref(): boolean {
  try {
    const v = localStorage.getItem(CHAT_AGENT_PREF_KEY);
    if (v === '0' || v === 'false') return false;
    if (v === '1' || v === 'true') return true;
  } catch {
    /* ignore */
  }
  return true;
}

interface EntityConversationProps {
  entityId: string;
  resources: Resource[] | undefined;
  artifacts: Artifact[] | undefined;
  selectedResources: Set<string>;
  selectedArtifacts: Set<string>;
  onArtifactsChanged: () => void;
  onViewArtifact: (artifact: Artifact) => void;
}

export function EntityConversation({
  entityId,
  resources,
  artifacts,
  selectedResources,
  selectedArtifacts,
  onArtifactsChanged,
  onViewArtifact,
}: EntityConversationProps) {
  const { profileId } = useChatModelProfile();
  const sessionIdRef = useRef<string | null>(null);

  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  sessionIdRef.current = sessionId;
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [presets, setPresets] = useState<PresetInfo[]>([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  /** Deep-agent run for this panel; polling updates `agentStatus`. */
  const [agentJob, setAgentJob] = useState<{
    sessionId: string;
    jobId: string;
  } | null>(null);
  const [agentStatus, setAgentStatus] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [chatAgentOn, setChatAgentOn] = useState(readChatAgentPref);

  const toggleChatAgent = useCallback(() => {
    setChatAgentOn((on) => {
      const next = !on;
      try {
        localStorage.setItem(CHAT_AGENT_PREF_KEY, next ? '1' : '0');
      } catch {
        /* ignore */
      }
      return next;
    });
  }, []);

  const refreshSessions = useCallback(async () => {
    const list = await api.chat.listSessions(entityId);
    setSessions(list);
    return list;
  }, [entityId]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        setError(null);
        const [p, list] = await Promise.all([
          api.chat.listPresets(entityId),
          api.chat.listSessions(entityId),
        ]);
        if (cancelled) return;
        setPresets(p);
        setSessions(list);
        if (list.length === 0) {
          const s = await api.chat.createSession(entityId, {});
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
    return () => {
      cancelled = true;
    };
  }, [entityId]);

  useEffect(() => {
    if (!sessionId) return;
    let cancelled = false;
    api.chat.getSession(entityId, sessionId).then((d) => {
      if (!cancelled) setMessages(d.messages);
    });
    return () => {
      cancelled = true;
    };
  }, [entityId, sessionId]);

  useEffect(() => {
    setAgentJob(null);
    setAgentStatus('');
  }, [entityId]);

  useEffect(() => {
    if (!agentJob) return undefined;
    let cancelled = false;
    const poll = async () => {
      try {
        const st = await api.chat.getMessageJob(
          entityId,
          agentJob.sessionId,
          agentJob.jobId
        );
        if (cancelled) return;
        const viewingThis = sessionIdRef.current === agentJob.sessionId;
        if (viewingThis) {
          setAgentStatus(
            st.step_detail?.trim() ||
              (st.status === 'pending' ? 'Queued…' : st.status)
          );
        }
        if (st.status === 'succeeded') {
          setWarnings(st.warnings);
          setAgentJob(null);
          setAgentStatus('');
          const detail = await api.chat.getSession(
            entityId,
            agentJob.sessionId
          );
          if (
            !cancelled &&
            sessionIdRef.current === agentJob.sessionId
          ) {
            setMessages(detail.messages);
          }
        } else if (st.status === 'failed') {
          setAgentJob(null);
          setAgentStatus('');
          if (sessionIdRef.current === agentJob.sessionId) {
            setError(st.error_message || 'Agent run failed');
          }
        }
      } catch (e) {
        if (!cancelled) {
          const forSession = agentJob.sessionId;
          setAgentJob(null);
          setAgentStatus('');
          if (sessionIdRef.current === forSession) {
            setError(e instanceof Error ? e.message : String(e));
          }
        }
      }
    };
    const id = window.setInterval(() => void poll(), 450);
    void poll();
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [agentJob, entityId]);

  const agentActiveHere = Boolean(
    agentJob && sessionId && agentJob.sessionId === sessionId
  );

  const handleNewSession = async () => {
    setError(null);
    setBusy(true);
    try {
      const s = await api.chat.createSession(entityId, {});
      await refreshSessions();
      setSessionId(s.id);
      setMessages([]);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const handleSend = async () => {
    const text = input.trim();
    if (!text || !sessionId) return;
    setError(null);
    setBusy(true);
    setWarnings([]);
    try {
      const out = await api.chat.postMessage(entityId, sessionId, {
        text,
        resource_ids: [...selectedResources],
        artifact_ids: [...selectedArtifacts],
        model_profile_id: profileId,
        use_deep_agent: chatAgentOn,
      });
      setInput('');
      if (out.kind === 'accepted') {
        setWarnings(out.warnings);
        setAgentJob({ sessionId, jobId: out.jobId });
        setAgentStatus('Queued…');
        const detail = await api.chat.getSession(entityId, sessionId);
        setMessages(detail.messages);
      } else {
        setWarnings(out.result.warnings);
        const detail = await api.chat.getSession(entityId, sessionId);
        setMessages(detail.messages);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const handleRunPreset = async (presetId: string) => {
    setError(null);
    setBusy(true);
    setWarnings([]);
    try {
      const res = await api.chat.runPreset(entityId, presetId, {
        resource_ids: [...selectedResources],
        artifact_ids: [...selectedArtifacts],
        session_id: sessionId ?? undefined,
      });
      setWarnings(res.warnings);
      onArtifactsChanged();
      if (sessionId) {
        const detail = await api.chat.getSession(entityId, sessionId);
        setMessages(detail.messages);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const contextCount = selectedResources.size + selectedArtifacts.size;

  return (
    <div className="entity-conversation">
      <header className="entity-chat-header">
        <h3 className="entity-chat-header-title">Chat</h3>
        <div className="entity-chat-header-actions">
          <label className="entity-conversation-label entity-chat-session-label">
            <span className="visually-hidden">Conversation</span>
            <select
              className="entity-conversation-select entity-chat-session-select"
              value={sessionId ?? ''}
              onChange={(e) => setSessionId(e.target.value || null)}
              disabled={busy || sessions.length === 0}
              aria-label="Select conversation"
              title={
                agentActiveHere
                  ? 'You can switch conversations while the agent runs in the background'
                  : undefined
              }
            >
              {sessions.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.title || `Chat ${s.id.slice(0, 8)}…`}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            className="entity-chat-new-icon"
            onClick={() => void handleNewSession()}
            disabled={busy}
            aria-label="New conversation"
            title="New conversation"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <path
                d="M12 5v14M5 12h14"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
              />
            </svg>
          </button>
        </div>
      </header>

      {error && <div className="entity-conversation-error">{error}</div>}
      {warnings.length > 0 && (
        <ul className="entity-conversation-warnings">
          {warnings.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      )}

      <div className="entity-conversation-messages">
        {messages.length === 0 && (
          <div className="entity-conversation-empty muted">
            Messages appear here. Type below or use a shortcut to run a preset.
          </div>
        )}
        {messages.map((m) => {
          const side = m.role === 'user' ? 'user' : 'assistant';
          const artifactCard =
            side === 'assistant' ? parseArtifactCardMessage(m.content) : null;
          return (
            <div
              key={m.id}
              className={`entity-conversation-msg entity-conversation-msg--${side}`}
            >
              <span className="entity-conversation-msg-role">{roleLabel(m.role)}</span>
              <div className={`entity-conversation-msg-bubble entity-conversation-msg-bubble--${side}`}>
                {artifactCard ? (
                  <button
                    type="button"
                    className="entity-conversation-artifact-card"
                    onClick={() =>
                      onViewArtifact(resolveArtifactForViewer(artifactCard, artifacts))
                    }
                  >
                    <span className="entity-conversation-artifact-card-icon" aria-hidden>
                      📝
                    </span>
                    <span className="entity-conversation-artifact-card-body">
                      <span className="entity-conversation-artifact-card-title">
                        {artifactCard.artifact_title?.trim()
                          ? `${artifactCard.artifact_title} (v${artifactCard.version})`
                          : `${artifactCard.artifact_type} (v${artifactCard.version})`}
                      </span>
                      <span className="entity-conversation-artifact-card-meta">
                        {artifactCard.preset_label} · {artifactCard.status} · Open to read
                      </span>
                    </span>
                    <span className="entity-conversation-artifact-card-chevron" aria-hidden>
                      ↗
                    </span>
                  </button>
                ) : side === 'assistant' ? (
                  <div className="markdown-viewer entity-conversation-markdown">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{m.content}</ReactMarkdown>
                  </div>
                ) : (
                  <div className="entity-conversation-msg-text">{m.content}</div>
                )}
              </div>
            </div>
          );
        })}
      </div>

      <div className="entity-conversation-footer">
        <div className="entity-conversation-compose-cluster">
          <div className="entity-conversation-shortcuts" aria-label="Preset workflows">
            <span className="entity-conversation-shortcuts-legend">Run preset</span>
            {presets.map((p) => (
              <button
                key={p.id}
                type="button"
                className="entity-conversation-shortcut-pill"
                onClick={() => void handleRunPreset(p.id)}
                disabled={busy || agentActiveHere}
                title={`${p.description} (runs once when clicked — not a persistent mode)`}
              >
                {p.label}
              </button>
            ))}
          </div>
          <div className="entity-conversation-compose-shell">
            <textarea
              className="entity-conversation-textarea entity-conversation-textarea--shell"
              value={agentActiveHere ? agentStatus : input}
              onChange={(e) => {
                if (!agentActiveHere) setInput(e.target.value);
              }}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  void handleSend();
                }
              }}
              placeholder={agentActiveHere ? 'Working…' : 'Message…'}
              rows={2}
              disabled={busy || !sessionId || agentActiveHere}
              title={
                agentActiveHere
                  ? 'Agent progress for this conversation'
                  : undefined
              }
            />
            <div className="entity-conversation-compose-toolbar">
              <div className="entity-conversation-compose-toolbar-left">
                <button
                  type="button"
                  className="entity-conversation-attach-chip"
                  title="Add context: use the Resources and Artifacts columns to include sources with this message."
                  aria-label="Context: select items in side columns"
                >
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden>
                    <path
                      d="M12 5v14M5 12h14"
                      stroke="currentColor"
                      strokeWidth="2"
                      strokeLinecap="round"
                    />
                  </svg>
                </button>
                <button
                  type="button"
                  role="switch"
                  aria-checked={chatAgentOn}
                  aria-label={chatAgentOn ? 'Agent on' : 'Agent off'}
                  className={
                    chatAgentOn
                      ? 'entity-conversation-agent-pill entity-conversation-agent-pill--on'
                      : 'entity-conversation-agent-pill'
                  }
                  onClick={() => toggleChatAgent()}
                  disabled={busy || agentActiveHere}
                  title={
                    chatAgentOn
                      ? 'Agent mode is on: multi-step tools. Click to turn off for quick one-shot replies.'
                      : 'Agent mode is off: one-shot reply. Click to turn on for tools and longer runs.'
                  }
                >
                  <span className="entity-conversation-agent-pill__icon" aria-hidden>
                    <svg
                      width="16"
                      height="16"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="1.75"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    >
                      <rect x="4" y="8" width="16" height="11" rx="2" />
                      <circle cx="9.5" cy="13.5" r="1.25" fill="currentColor" stroke="none" />
                      <circle cx="14.5" cy="13.5" r="1.25" fill="currentColor" stroke="none" />
                      <path d="M9 8V5M15 8V5M12 4v2" />
                    </svg>
                  </span>
                  <span className="entity-conversation-agent-pill__label">Agent</span>
                  <span
                    className={
                      chatAgentOn
                        ? 'entity-conversation-agent-pill__state entity-conversation-agent-pill__state--on'
                        : 'entity-conversation-agent-pill__state entity-conversation-agent-pill__state--off'
                    }
                  >
                    {chatAgentOn ? 'On' : 'Off'}
                  </span>
                </button>
              </div>
              <div className="entity-conversation-compose-toolbar-right">
                <button
                  type="button"
                  className="entity-conversation-send entity-conversation-send--round"
                  onClick={() => void handleSend()}
                  disabled={
                    busy || !sessionId || !input.trim() || agentActiveHere
                  }
                  aria-label="Send message"
                >
                  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden>
                    <path
                      d="M12 19V6m0 0l-6.5 6.5M12 6l6.5 6.5"
                      stroke="currentColor"
                      strokeWidth="2"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    />
                  </svg>
                </button>
              </div>
            </div>
          </div>
          <p className="entity-conversation-context-line" role="status">
            {contextCount === 0
              ? 'No sources in context — select resources or artifacts in the side columns.'
              : `${contextCount} source${contextCount === 1 ? '' : 's'} in context · ${resources?.length ?? 0} resources · ${artifacts?.length ?? 0} artifacts`}
          </p>
        </div>
      </div>
    </div>
  );
}
