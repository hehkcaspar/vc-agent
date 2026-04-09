import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Plus, Trash2, Bot, ArrowUp, ChevronDown, FileText, ArrowUpRight } from 'lucide-react';
import { Modal } from './ui/Modal';
import { useChatModelProfile } from '../context/ChatModelProfileContext';
import { parseDeliverableCardMessage } from '../lib/chatArtifactCard';
import {
  CLI_SPINNER_DOTS_FRAMES,
  CLI_SPINNER_DOTS_INTERVAL_MS,
} from '../lib/cliSpinnerDots';
import { api } from '../services/api';
import type { ChatMessage, ChatSession, DeliverableCardPayload, PresetInfo } from '../types';

function formatSessionTimestamp(iso: string): string {
  try {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return 'Chat';
    const now = new Date();
    const sameDay =
      d.getFullYear() === now.getFullYear() &&
      d.getMonth() === now.getMonth() &&
      d.getDate() === now.getDate();
    const time = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    if (sameDay) return `Today ${time}`;
    return `${d.toLocaleDateString([], { month: 'short', day: 'numeric' })} ${time}`;
  } catch {
    return 'Chat';
  }
}

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
  selectedNodeIds: Set<string>;
  onArtifactsChanged: () => void;
  onViewDeliverable: (card: DeliverableCardPayload) => void;
}

export function EntityConversation({
  entityId,
  selectedNodeIds,
  onArtifactsChanged,
  onViewDeliverable,
}: EntityConversationProps) {
  const { profileId } = useChatModelProfile();
  const sessionIdRef = useRef<string | null>(null);
  const sessionMenuRef = useRef<HTMLDivElement | null>(null);
  const onArtifactsChangedRef = useRef(onArtifactsChanged);
  onArtifactsChangedRef.current = onArtifactsChanged;

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
  const [sessionMenuOpen, setSessionMenuOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<ChatSession | null>(null);
  const [deleteStep, setDeleteStep] = useState<1 | 2>(1);
  const [deletingSessionId, setDeletingSessionId] = useState<string | null>(null);
  const [spinnerFrame, setSpinnerFrame] = useState(0);
  const [showModelSwapConfirm, setShowModelSwapConfirm] = useState(false);
  const pendingSendRef = useRef<(() => void) | null>(null);

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
    if (!sessionMenuOpen) return;
    const onDocMouseDown = (event: MouseEvent) => {
      if (!sessionMenuRef.current?.contains(event.target as Node)) {
        setSessionMenuOpen(false);
      }
    };
    document.addEventListener('mousedown', onDocMouseDown);
    return () => document.removeEventListener('mousedown', onDocMouseDown);
  }, [sessionMenuOpen]);

  useEffect(() => {
    setAgentJob(null);
    setAgentStatus('');
  }, [entityId]);

  useEffect(() => {
    if (!agentJob) return undefined;
    let cancelled = false;
    const startedAt = Date.now();
    const POLL_TIMEOUT_MS = 3 * 60 * 1000;
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
        if (
          (st.status === 'pending' || st.status === 'running') &&
          Date.now() - startedAt > POLL_TIMEOUT_MS
        ) {
          const forSession = agentJob.sessionId;
          setAgentJob((curr) =>
            curr && curr.jobId === agentJob.jobId ? null : curr
          );
          setAgentStatus('');
          if (sessionIdRef.current === forSession) {
            setError(
              'Agent run timed out in the UI. Please retry, or reopen this chat session to refresh status.'
            );
          }
          return;
        }
        if (st.status === 'succeeded') {
          setWarnings(st.warnings);
          let detail: Awaited<ReturnType<typeof api.chat.getSession>> | null =
            null;
          try {
            detail = await api.chat.getSession(entityId, agentJob.sessionId);
          } catch {
            detail = null;
          }
          if (cancelled) return;
          if (
            sessionIdRef.current === agentJob.sessionId
          ) {
            if (detail) {
              setMessages(detail.messages);
            } else if (st.assistant_message) {
              setMessages((prev) => {
                if (prev.some((m) => m.id === st.assistant_message!.id)) return prev;
                return [...prev, st.assistant_message!];
              });
            }
          }
          try {
            onArtifactsChangedRef.current();
          } catch {
            /* non-fatal: parent refresh */
          }
          setAgentJob((curr) =>
            curr && curr.jobId === agentJob.jobId ? null : curr
          );
          setAgentStatus('');
        } else if (st.status === 'failed') {
          setWarnings(st.warnings);
          setAgentJob((curr) =>
            curr && curr.jobId === agentJob.jobId ? null : curr
          );
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
  const sourceNameById = useMemo(() => {
    return new Map<string, string>();
  }, []);

  const humanizedAgentStatus = useMemo(() => {
    if (!agentStatus) return '';
    let out = agentStatus;
    for (const [id, name] of sourceNameById.entries()) {
      if (out.includes(id)) out = out.split(id).join(name);
      const short = id.slice(0, 8);
      if (short) {
        const escaped = short.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
        out = out.replace(new RegExp(`${escaped}\\.\\.\\.`, 'g'), name);
        out = out.replace(new RegExp(`\\b${escaped}\\b`, 'g'), name);
      }
    }
    return out;
  }, [agentStatus, sourceNameById]);
  const activeAgentStatusText = humanizedAgentStatus?.trim() || 'Agent is working...';

  useEffect(() => {
    if (!agentActiveHere && !busy) {
      setSpinnerFrame(0);
      return undefined;
    }
    const id = window.setInterval(() => {
      setSpinnerFrame((f) => (f + 1) % CLI_SPINNER_DOTS_FRAMES.length);
    }, CLI_SPINNER_DOTS_INTERVAL_MS);
    return () => window.clearInterval(id);
  }, [agentActiveHere, busy]);

  const humanizedWarnings = useMemo(() => {
    if (warnings.length === 0) return warnings;
    return warnings.map((warning) => {
      let out = warning;
      for (const [id, name] of sourceNameById.entries()) {
        if (out.includes(id)) out = out.split(id).join(name);
        const short = id.slice(0, 8);
        if (short) {
          const escaped = short.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
          out = out.replace(new RegExp(`${escaped}\\.\\.\\.`, 'g'), name);
          out = out.replace(new RegExp(`\\b${escaped}\\b`, 'g'), name);
        }
      }
      return out;
    });
  }, [sourceNameById, warnings]);

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

  const requestDeleteSession = (target: ChatSession) => {
    setSessionMenuOpen(false);
    setDeleteTarget(target);
    setDeleteStep(1);
  };

  const closeDeleteModal = () => {
    if (deletingSessionId) return;
    setDeleteTarget(null);
    setDeleteStep(1);
  };

  const handleDeleteSession = async () => {
    if (!deleteTarget) return;
    setError(null);
    setDeletingSessionId(deleteTarget.id);
    try {
      await api.chat.deleteSession(entityId, deleteTarget.id);
      const list = await refreshSessions();
      if (list.length === 0) {
        const created = await api.chat.createSession(entityId, {});
        setSessions([created]);
        setSessionId(created.id);
        setMessages([]);
      } else if (sessionIdRef.current === deleteTarget.id) {
        setSessionId(list[0].id);
      }
      setDeleteTarget(null);
      setDeleteStep(1);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setDeletingSessionId(null);
    }
  };

  const doSend = async () => {
    const text = input.trim();
    if (!text || !sessionId) return;
    setError(null);
    setBusy(true);
    setWarnings([]);
    try {
      const out = await api.chat.postMessage(entityId, sessionId, {
        text,
        node_ids: Array.from(selectedNodeIds),
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
        // Update session in list (has_gemini_chain may have changed)
        setSessions((prev) =>
          prev.map((s) => (s.id === sessionId ? detail.session : s))
        );
      } else {
        setWarnings(out.result.warnings);
        const detail = await api.chat.getSession(entityId, sessionId);
        setMessages(detail.messages);
        setSessions((prev) =>
          prev.map((s) => (s.id === sessionId ? detail.session : s))
        );
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const handleSend = async () => {
    const text = input.trim();
    if (!text || !sessionId) return;

    // Confirm before switching from Gemini to Kimi (destroys Interactions API chain)
    const currentSession = sessions.find((s) => s.id === sessionId);
    if (
      profileId === 'kimi_moonshot' &&
      !chatAgentOn &&
      currentSession?.has_gemini_chain
    ) {
      pendingSendRef.current = doSend;
      setShowModelSwapConfirm(true);
      return;
    }

    await doSend();
  };

  const handleRunPreset = async (presetId: string) => {
    setError(null);
    setBusy(true);
    setWarnings([]);
    try {
      const res = await api.chat.runPreset(entityId, presetId, {
        node_ids: Array.from(selectedNodeIds),
        session_id: sessionId ?? undefined,
        model_profile_id: profileId,
        use_deep_agent: chatAgentOn,
      });
      if (res.kind === 'accepted') {
        // Deep-agent path: dispatched as background job. The polling effect
        // drives the spinner status line; refresh messages so the synthetic
        // user message shows up immediately.
        setWarnings(res.warnings);
        setAgentJob({ jobId: res.jobId, sessionId: res.sessionId });
        setAgentStatus('Queued…');
        try {
          const detail = await api.chat.getSession(entityId, res.sessionId);
          setMessages(detail.messages);
        } catch {
          /* non-fatal */
        }
      } else {
        setWarnings(res.result.warnings);
        onArtifactsChanged();
        if (sessionId) {
          const detail = await api.chat.getSession(entityId, sessionId);
          setMessages(detail.messages);
        }
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const contextCount = selectedNodeIds.size;

  return (
    <div className="entity-conversation">
      <header className="entity-chat-header">
        <h3 className="entity-chat-header-title">Chat</h3>
        <div className="entity-chat-header-actions">
          <div className="entity-chat-session-menu" ref={sessionMenuRef}>
            <button
              type="button"
              className="entity-conversation-select entity-chat-session-select entity-chat-session-trigger"
              onClick={() => setSessionMenuOpen((open) => !open)}
              disabled={busy || sessions.length === 0}
              aria-label="Select conversation"
              aria-haspopup="menu"
              aria-expanded={sessionMenuOpen}
              title={
                agentActiveHere
                  ? 'You can switch conversations while the agent runs in the background'
                  : undefined
              }
            >
              <span className="entity-chat-session-trigger-label">
                {(() => {
                  const s = sessions.find((x) => x.id === sessionId);
                  if (!s) return 'Select chat';
                  return s.title || formatSessionTimestamp(s.created_at);
                })()}
              </span>
              <ChevronDown size={14} aria-hidden="true" />
            </button>
            {sessionMenuOpen && (
              <div className="entity-chat-session-dropdown" role="menu" aria-label="Chat sessions">
                {sessions.map((s) => (
                  <div key={s.id} className="entity-chat-session-row">
                    <button
                      type="button"
                      className={
                        s.id === sessionId
                          ? 'entity-chat-session-item entity-chat-session-item--active'
                          : 'entity-chat-session-item'
                      }
                      role="menuitem"
                      onClick={() => {
                        setSessionId(s.id);
                        setSessionMenuOpen(false);
                      }}
                    >
                      {s.title || formatSessionTimestamp(s.created_at)}
                    </button>
                    <button
                      type="button"
                      className="entity-chat-session-delete"
                      onClick={() => requestDeleteSession(s)}
                      aria-label={`Delete ${s.title || formatSessionTimestamp(s.created_at)}`}
                      title="Delete chat"
                    >
                      <Trash2 size={14} strokeWidth={1.8} aria-hidden="true" />
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
          <button
            type="button"
            className="zone-header-icon-btn"
            onClick={() => void handleNewSession()}
            disabled={busy}
            aria-label="New conversation"
            title="New conversation"
          >
            <Plus size={16} strokeWidth={2} aria-hidden="true" />
          </button>
        </div>
      </header>

      {error && <div className="entity-conversation-error">{error}</div>}
      {warnings.length > 0 && (
        <ul className="entity-conversation-warnings">
          {humanizedWarnings.map((w) => (
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
            side === 'assistant' ? parseDeliverableCardMessage(m.content) : null;
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
                    onClick={() => onViewDeliverable(artifactCard)}
                  >
                    <span className="entity-conversation-artifact-card-icon" aria-hidden>
                      <FileText size={16} />
                    </span>
                    <span className="entity-conversation-artifact-card-body">
                      <span className="entity-conversation-artifact-card-title">
                        {artifactCard.artifact_title?.trim()
                          ? `${artifactCard.artifact_title} (v${artifactCard.version})`
                          : `${artifactCard.deliverable_type ?? 'Deliverable'} (v${artifactCard.version})`}
                      </span>
                      <span className="entity-conversation-artifact-card-meta">
                        {artifactCard.preset_label} · {artifactCard.status} · Open to read
                      </span>
                    </span>
                    <span className="entity-conversation-artifact-card-chevron" aria-hidden>
                      <ArrowUpRight size={14} />
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
              value={input}
              onChange={(e) => {
                if (!agentActiveHere) setInput(e.target.value);
              }}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  void handleSend();
                }
              }}
              placeholder={
                agentActiveHere
                  ? `${CLI_SPINNER_DOTS_FRAMES[spinnerFrame]} ${activeAgentStatusText}`
                  : busy
                  ? `${CLI_SPINNER_DOTS_FRAMES[spinnerFrame]} Working…`
                  : 'Message…'
              }
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
                  title="Add context: select files in the workspace to include as sources with this message."
                  aria-label="Context: select files in workspace"
                >
                  <Plus size={18} strokeWidth={2} aria-hidden />
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
                    <Bot size={16} strokeWidth={1.75} aria-hidden />
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
                  <ArrowUp size={20} strokeWidth={2} aria-hidden />
                </button>
              </div>
            </div>
          </div>
          <p className="entity-conversation-context-line" role="status">
            {contextCount === 0
              ? 'No sources in context — select files in the workspace.'
              : `${contextCount} source${contextCount === 1 ? '' : 's'} in context`}
          </p>
        </div>
      </div>
      {deleteTarget && (
        <Modal
          isOpen
          onClose={closeDeleteModal}
          size="narrow"
          title={deleteStep === 1 ? 'Delete chat?' : 'Final confirmation'}
          ariaLabel="Delete chat session"
          className="entity-chat-delete-modal"
        >
            <div className="modal-body">
              {deleteStep === 1 ? (
                <p>
                  Delete <strong>{deleteTarget.title || formatSessionTimestamp(deleteTarget.created_at)}</strong>?
                  This will permanently remove all messages in this chat.
                </p>
              ) : (
                <p>
                  This action cannot be undone. Please confirm again to permanently delete this
                  conversation.
                </p>
              )}
            </div>
            <div className="modal-footer">
              <button
                type="button"
                className="btn-secondary"
                onClick={closeDeleteModal}
                disabled={Boolean(deletingSessionId)}
              >
                Cancel
              </button>
              {deleteStep === 1 ? (
                <button
                  type="button"
                  className="entity-chat-delete-confirm"
                  onClick={() => setDeleteStep(2)}
                  disabled={Boolean(deletingSessionId)}
                >
                  Continue
                </button>
              ) : (
                <button
                  type="button"
                  className="entity-chat-delete-confirm entity-chat-delete-confirm--danger"
                  onClick={() => void handleDeleteSession()}
                  disabled={Boolean(deletingSessionId)}
                >
                  {deletingSessionId ? 'Deleting…' : 'Delete forever'}
                </button>
              )}
            </div>
        </Modal>
      )}
      {showModelSwapConfirm && (
        <Modal
          isOpen
          onClose={() => {
            setShowModelSwapConfirm(false);
            pendingSendRef.current = null;
          }}
          size="narrow"
          title="Switch to Kimi?"
          ariaLabel="Switch model"
          className="entity-chat-delete-modal"
        >
            <div className="modal-body">
              <p>
                This will end the current Gemini session. Multimodal context (images, PDFs)
                from earlier turns will be permanently lost. Text history is preserved.
              </p>
            </div>
            <div className="modal-footer">
              <button
                type="button"
                className="btn-secondary"
                onClick={() => {
                  setShowModelSwapConfirm(false);
                  pendingSendRef.current = null;
                }}
              >
                Cancel
              </button>
              <button
                type="button"
                className="entity-chat-delete-confirm entity-chat-delete-confirm--danger"
                onClick={() => {
                  setShowModelSwapConfirm(false);
                  const fn = pendingSendRef.current;
                  pendingSendRef.current = null;
                  if (fn) void fn();
                }}
              >
                Switch &amp; Send
              </button>
            </div>
        </Modal>
      )}
    </div>
  );
}
