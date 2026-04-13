import { useState } from 'react';
import { ExternalLink, Pause, Play, Pencil, Plus, Trash2 } from 'lucide-react';
import { showToast } from '../../lib/appToast';
import { academicApi } from '../../services/academicApi';
import type { Channel } from '../../types/academic';
import { EditProfileModal, type ProfileSourceId } from './EditProfileModal';

interface ProfileLink {
  sourceKey: string;
  label: string;
  url: string;
  id?: string;
  confidence?: string;
  verifiedBy?: string;
  llmConfidence?: number;
  llmReason?: string;
}

interface ProfilesTabProps {
  scholarId: string;
  profileLinks: ProfileLink[];
  channels: Channel[];
  mutateChannels: () => void;
  mutateScholar: () => void;
}

type EditState =
  | { mode: 'add' }
  | { mode: 'edit'; link: ProfileLink };

export function ProfilesTab({
  scholarId,
  profileLinks,
  channels,
  mutateChannels,
  mutateScholar,
}: ProfilesTabProps) {
  const [editState, setEditState] = useState<EditState | null>(null);

  const handleSaved = () => {
    mutateScholar();
    showToast('Profile saved', 'success');
  };

  const handleDelete = async (link: ProfileLink) => {
    const confirmed = window.confirm(
      `Remove the ${link.label} profile from this scholar?`,
    );
    if (!confirmed) return;
    const blacklist = window.confirm(
      `Also blacklist this id so the next refresh won't re-pick it?\n\n` +
        `OK  = remove and blacklist\n` +
        `Cancel = remove only (the resolver is free to find it again)`,
    );
    try {
      await academicApi.scholars.deleteIdentity(scholarId, link.sourceKey, {
        blacklist,
      });
      mutateScholar();
      showToast(
        blacklist
          ? `${link.label} removed and blacklisted`
          : `${link.label} removed`,
        'success',
      );
    } catch (err) {
      showToast(
        err instanceof Error ? err.message : 'Failed to remove profile',
        'error',
      );
    }
  };

  const isLowConfidence = (link: ProfileLink): boolean =>
    (link.verifiedBy ?? '').startsWith('llm_low_confidence');

  return (
    <div className="tab-content profiles-content">
      <div
        style={{
          display: 'flex',
          justifyContent: 'flex-end',
          marginBottom: 'var(--space-3)',
        }}
      >
        <button
          type="button"
          className="btn-primary btn-sm"
          onClick={() => setEditState({ mode: 'add' })}
        >
          <Plus size={14} />
          <span style={{ marginLeft: 'var(--space-2)' }}>Add profile URL</span>
        </button>
      </div>

      {profileLinks.length === 0 ? (
        <p className="text-muted">
          No profile links discovered yet. Run an evaluation to discover
          profiles, or add one manually with the button above.
        </p>
      ) : (
        <table className="profiles-table">
          <thead>
            <tr>
              <th className="col-source">Source</th>
              <th>URL</th>
              <th className="col-monitoring">Monitoring</th>
              <th className="col-actions">Actions</th>
            </tr>
          </thead>
          <tbody>
            {profileLinks.map((link) => {
              const channelType =
                link.label === 'Google Scholar'
                  ? 'google_scholar_profile'
                  : link.label === 'Semantic Scholar'
                  ? 'semantic_scholar_profile'
                  : null;
              const ch = channelType
                ? channels.find((c) => c.channel_type === channelType)
                : null;
              const lowConf = isLowConfidence(link);

              return (
                <tr key={link.sourceKey} className="profile-row">
                  <td className="profile-source">
                    <span className="profile-source-label">{link.label}</span>
                    {link.id && (
                      <span className="profile-source-id text-muted">{link.id}</span>
                    )}
                    {lowConf && (
                      <span
                        title={link.llmReason ?? 'LLM flagged this match as unverified'}
                        style={{
                          marginLeft: 'var(--space-2)',
                          padding: '2px 6px',
                          borderRadius: 'var(--radius-sm)',
                          fontSize: 'var(--text-xs)',
                          background: 'color-mix(in srgb, #f59e0b 18%, transparent)',
                          color: '#92400e',
                          border: '1px solid color-mix(in srgb, #f59e0b 40%, transparent)',
                        }}
                      >
                        unverified — review
                      </span>
                    )}
                  </td>
                  <td className="profile-url-cell">
                    <a
                      href={link.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="profile-url-link"
                      title={link.url}
                    >
                      <span className="profile-url-text">{link.url}</span>
                      <ExternalLink size={12} />
                    </a>
                  </td>
                  <td className="profile-monitoring-cell">
                    {ch ? (
                      <div className="channel-controls">
                        <span
                          className={`channel-dot channel-dot--${
                            ch.is_active
                              ? ch.poll_error_count > 0
                                ? 'error'
                                : 'active'
                              : 'paused'
                          }`}
                        />
                        <span className="channel-detail text-muted">
                          {ch.is_active
                            ? ch.poll_error_count > 0
                              ? 'Error'
                              : `Every ${ch.polling_interval_hours}h`
                            : 'Paused'}
                          {ch.last_polled_at &&
                            ` · ${new Date(ch.last_polled_at).toLocaleDateString()}`}
                        </span>
                        <button
                          className="btn-icon btn-sm"
                          title={ch.is_active ? 'Pause monitoring' : 'Resume monitoring'}
                          onClick={async () => {
                            try {
                              await academicApi.scholars.updateChannel(scholarId, ch.id, {
                                is_active: !ch.is_active,
                              });
                              mutateChannels();
                              showToast(
                                ch.is_active ? 'Channel paused' : 'Channel resumed',
                                'success',
                              );
                            } catch {
                              showToast('Failed to update channel', 'error');
                            }
                          }}
                        >
                          {ch.is_active ? <Pause size={12} /> : <Play size={12} />}
                        </button>
                      </div>
                    ) : (
                      <span className="text-muted">-</span>
                    )}
                  </td>
                  <td style={{ textAlign: 'right' }}>
                    <button
                      type="button"
                      className="btn-icon btn-sm"
                      title="Edit URL"
                      onClick={() => setEditState({ mode: 'edit', link })}
                    >
                      <Pencil size={12} />
                    </button>
                    <button
                      type="button"
                      className="btn-icon btn-sm"
                      title="Remove profile"
                      style={{ marginLeft: 'var(--space-1)' }}
                      onClick={() => handleDelete(link)}
                    >
                      <Trash2 size={12} />
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}

      {editState && (
        <EditProfileModal
          scholarId={scholarId}
          mode={editState.mode}
          initial={
            editState.mode === 'edit'
              ? {
                  sourceId: editState.link.sourceKey as ProfileSourceId,
                  url: editState.link.url,
                  id: editState.link.id,
                }
              : undefined
          }
          onClose={() => setEditState(null)}
          onSaved={handleSaved}
        />
      )}
    </div>
  );
}

