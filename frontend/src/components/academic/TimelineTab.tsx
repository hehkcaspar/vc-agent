import { academicApi } from '../../services/academicApi';
import type { ScholarEvent } from '../../types/academic';
import { useState } from 'react';

const EVENT_ICONS: Record<string, string> = {
  new_paper: '\u{1F4C4}',
  new_preprint: '\u{1F4C4}',
  patent_filed: '\u{1F512}',
  news_mention: '\u{1F4F0}',
  metric_snapshot: '\u{1F4CA}',
  website_updated: '\u{1F310}',
  career_change: '\u{1F3AF}',
  identity_discovered: '\u{1F50D}',
  evaluation_completed: '\u{2705}',
  channel_deactivated: '\u{26A0}',
  user_note_added: '\u{1F4DD}',
};

interface TimelineTabProps {
  scholarId: string;
  events: ScholarEvent[];
  mutateEvents: () => void;
}

export function TimelineTab({ scholarId, events, mutateEvents }: TimelineTabProps) {
  const [sigFilter, setSigFilter] = useState<string | null>(null);

  return (
    <div className="tab-content timeline-content">
      <div className="timeline-toolbar">
        <div className="paper-filters">
          {[null, 'high', 'medium', 'low'].map((sig) => (
            <button
              key={sig ?? 'all'}
              className={`filter-btn ${sigFilter === sig ? 'active' : ''}`}
              onClick={() => setSigFilter(sig)}
            >
              {sig ? sig.charAt(0).toUpperCase() + sig.slice(1) : 'All'}
            </button>
          ))}
        </div>
      </div>

      {events.length === 0 ? (
        <p className="text-muted">No events yet. Events will appear here after evaluation or monitoring.</p>
      ) : (
        <div className="timeline-list">
          {events
            .filter((e) => !sigFilter || e.significance === sigFilter)
            .map((evt) => (
              <div key={evt.id} className={`timeline-event ${evt.is_read ? 'read' : 'unread'}`}>
                <span className={`event-type-icon event-type-${evt.event_type}`}>
                  {EVENT_ICONS[evt.event_type] ?? '?'}
                </span>
                <div className="event-body">
                  <span className="event-title">{evt.title ?? evt.event_type}</span>
                  <span className="event-meta">
                    {evt.event_date && (
                      <span className="event-date-label">
                        {new Date(evt.event_date).toLocaleDateString()}
                      </span>
                    )}
                    {evt.created_at && evt.event_date &&
                      new Date(evt.created_at).toLocaleDateString() !== new Date(evt.event_date).toLocaleDateString() && (
                      <span className="event-discovered text-muted">
                        discovered {new Date(evt.created_at).toLocaleDateString()}
                      </span>
                    )}
                    {!evt.event_date && evt.created_at && (
                      <span className="event-discovered text-muted">
                        {new Date(evt.created_at).toLocaleDateString()}
                      </span>
                    )}
                    {' '}
                    <span className={`sig-badge sig-${evt.significance}`}>
                      {evt.significance}
                    </span>
                  </span>
                </div>
                {!evt.is_read && (
                  <button
                    className="btn-icon"
                    title="Mark as read"
                    onClick={async () => {
                      try {
                        await academicApi.scholars.updateEvent(scholarId, evt.id, { is_read: true });
                        mutateEvents();
                      } catch { /* ignore */ }
                    }}
                  >
                    &#10003;
                  </button>
                )}
              </div>
            ))}
        </div>
      )}
    </div>
  );
}
