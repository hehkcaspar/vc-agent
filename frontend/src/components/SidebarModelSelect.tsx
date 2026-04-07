import { useChatModelProfile } from '../context/ChatModelProfileContext';
import type { ChatModelProfileId } from '../types';

const OPTIONS: { value: ChatModelProfileId; label: string }[] = [
  { value: 'gemini_google', label: 'Gemini (Google)' },
  { value: 'kimi_moonshot', label: 'Kimi K2.5' },
];

export function SidebarModelSelect() {
  const { profileId, setProfileId } = useChatModelProfile();

  return (
    <div className="sidebar-model-block">
      <label className="sidebar-model-label">
        <span className="sidebar-model-label-text">Chat model</span>
        <select
          className="sidebar-model-select"
          value={profileId}
          onChange={(e) => setProfileId(e.target.value as ChatModelProfileId)}
          aria-label="Model for entity chat (Deep Agent harness)"
          title="Model used for portfolio chat and presets (both direct and agent modes)."
        >
          {OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </label>
    </div>
  );
}
