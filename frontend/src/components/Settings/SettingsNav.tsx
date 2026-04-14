import type { SettingsSectionId, SettingsNavItem } from './SettingsPage';

export interface SettingsNavGroup {
  id: string;
  label: string;
  items: SettingsNavItem[];
}

interface Props {
  groups: SettingsNavGroup[];
  active: SettingsSectionId;
  onSelect: (id: SettingsSectionId) => void;
}

export function SettingsNav({ groups, active, onSelect }: Props) {
  return (
    <aside className="settings-sidebar">
      <h1 className="settings-title">Settings</h1>
      {groups.map((group) => (
        <div key={group.id} className="settings-group">
          <span className="settings-group-label">{group.label}</span>
          {group.items.map((item) => {
            const Icon = item.icon;
            return (
              <button
                key={item.id}
                type="button"
                className={`settings-nav-item ${active === item.id ? 'active' : ''}`}
                aria-current={active === item.id ? 'page' : undefined}
                onClick={() => onSelect(item.id)}
              >
                {Icon ? <Icon size={15} /> : null}
                <span>{item.label}</span>
              </button>
            );
          })}
        </div>
      ))}
    </aside>
  );
}
