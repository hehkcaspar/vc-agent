import { Check, ExternalLink } from 'lucide-react';
import { academicApi } from '../../services/academicApi';
import type { ScholarEvent } from '../../types/academic';
import { useState } from 'react';
import { EventIcon } from '../../lib/eventIcons';

interface TimelineTabProps {
  scholarId: string;
  events: ScholarEvent[];
  mutateEvents: () => void;
  sortBy: 'discovered' | 'event_date';
  onSortChange: (sort: 'discovered' | 'event_date') => void;
}

export function TimelineTab({ scholarId, events, mutateEvents, sortBy, onSortChange }: TimelineTabProps) {
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
        <div className="paper-filters">
          <button
            className={`filter-btn ${sortBy === 'discovered' ? 'active' : ''}`}
            onClick={() => onSortChange('discovered')}
          >
            Discovered
          </button>
          <button
            className={`filter-btn ${sortBy === 'event_date' ? 'active' : ''}`}
            onClick={() => onSortChange('event_date')}
          >
            Event date
          </button>
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
                  <EventIcon type={evt.event_type} />
                </span>
                <div className="event-body">
                  {evt.source_url ? (
                    <a className="event-title event-link" href={evt.source_url} target="_blank" rel="noopener noreferrer">
                      {evt.title ?? evt.event_type} <ExternalLink size={12} />
                    </a>
                  ) : (
                    <span className="event-title">{evt.title ?? evt.event_type}</span>
                  )}
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
                    <Check size={14} />
                  </button>
                )}
              </div>
            ))}
        </div>
      )}
    </div>
  );
}
