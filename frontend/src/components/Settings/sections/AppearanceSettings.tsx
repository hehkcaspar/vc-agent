import { Moon, Sun } from 'lucide-react';

type Theme = 'light' | 'dark';

interface Props {
  theme: Theme;
  onThemeChange: (theme: Theme) => void;
}

const OPTIONS: Array<{
  value: Theme;
  label: string;
  description: string;
  Icon: typeof Moon;
}> = [
  {
    value: 'light',
    label: 'Light',
    description: 'High-contrast editorial palette.',
    Icon: Sun,
  },
  {
    value: 'dark',
    label: 'Dark',
    description: 'Dimmed background with soft indigo accents.',
    Icon: Moon,
  },
];

export function AppearanceSettings({ theme, onThemeChange }: Props) {
  return (
    <>
      <header className="settings-section-header">
        <h2 className="settings-section-title">Appearance</h2>
        <p className="settings-section-subtitle">
          Choose how the app looks. The preference is stored locally in this
          browser only (not synced across devices).
        </p>
      </header>
      <fieldset
        className="settings-block"
        style={{ border: 'none', padding: 0, margin: 0 }}
      >
        <legend className="settings-block-title">Color mode</legend>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-3)' }}>
          {OPTIONS.map(({ value, label, description, Icon }) => {
            const active = theme === value;
            return (
              <label
                key={value}
                className={`appearance-option ${active ? 'active' : ''}`}
              >
                <input
                  type="radio"
                  name="theme"
                  value={value}
                  checked={active}
                  onChange={() => onThemeChange(value)}
                />
                <Icon size={18} style={{ color: 'var(--color-text-secondary)' }} />
                <div className="appearance-option-body">
                  <span className="appearance-option-label">{label}</span>
                  <span className="appearance-option-description">
                    {description}
                  </span>
                </div>
              </label>
            );
          })}
        </div>
      </fieldset>
    </>
  );
}
