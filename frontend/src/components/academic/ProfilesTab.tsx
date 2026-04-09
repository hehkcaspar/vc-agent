import { ArrowRight } from 'lucide-react';
import { showToast } from '../../lib/appToast';
import { academicApi } from '../../services/academicApi';
import type { Channel } from '../../types/academic';

interface ProfileLink {
  label: string;
  url: string;
}

interface ProfilesTabProps {
  scholarId: string;
  profileLinks: ProfileLink[];
  channels: Channel[];
  mutateChannels: () => void;
}

export function ProfilesTab({ scholarId, profileLinks, channels, mutateChannels }: ProfilesTabProps) {
  if (profileLinks.length === 0) {
    return (
      <div className="tab-content profiles-content">
        <p className="text-muted">No profile links discovered yet. Run an evaluation to discover profiles.</p>
      </div>
    );
  }

  return (
    <div className="tab-content profiles-content">
      <div className="profiles-grid">
        {profileLinks.map((link) => {
          const channelType = link.label === 'Google Scholar' ? 'google_scholar_profile'
            : link.label === 'Semantic Scholar' ? 'semantic_scholar_profile'
            : null;
          const ch = channelType ? channels.find((c) => c.channel_type === channelType) : null;

          return (
            <div key={link.url} className="profile-card">
              <a href={link.url} target="_blank" rel="noopener noreferrer" className="profile-card-link">
                <span className="profile-card-label">{link.label}</span>
                <span className="profile-card-url">{link.url}</span>
                <span className="profile-card-arrow"><ArrowRight size={14} /></span>
              </a>
              {ch && (
                <div className="channel-controls">
                  <span className={`channel-status channel-${ch.is_active ? (ch.poll_error_count > 0 ? 'error' : 'active') : 'paused'}`}>
                    {ch.is_active ? (ch.poll_error_count > 0 ? 'Error' : 'Active') : 'Paused'}
                  </span>
                  {ch.last_polled_at && (
                    <span className="channel-polled text-muted">
                      Polled: {new Date(ch.last_polled_at).toLocaleDateString()}
                    </span>
                  )}
                  <span className="channel-interval text-muted">
                    Every {ch.polling_interval_hours}h
                  </span>
                  <button
                    className="btn-text btn-sm"
                    onClick={async (e) => {
                      e.preventDefault();
                      try {
                        await academicApi.scholars.updateChannel(scholarId, ch.id, {
                          is_active: !ch.is_active,
                        });
                        mutateChannels();
                        showToast(ch.is_active ? 'Channel paused' : 'Channel resumed', 'success');
                      } catch { showToast('Failed to update channel', 'error'); }
                    }}
                  >
                    {ch.is_active ? 'Pause' : 'Resume'}
                  </button>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
