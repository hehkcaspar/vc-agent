import { useNavigate, useParams } from 'react-router-dom';
import {
  Landmark,
  FileText,
  BookOpen,
  Activity,
  Target,
  ListOrdered,
  Palette,
  Info,
  LucideIcon,
} from 'lucide-react';
import { SettingsNav, SettingsNavGroup } from './SettingsNav';
import { FundsSettings } from './sections/FundsSettings';
import { ChecklistSettings } from './sections/ChecklistSettings';
import { TemplatesSettings } from './sections/TemplatesSettings';
import { TasksSettings } from './sections/TasksSettings';
import { DimensionsSettings } from './sections/DimensionsSettings';
import { RankingSettings } from './sections/RankingSettings';
import { AppearanceSettings } from './sections/AppearanceSettings';
import { AboutSettings } from './sections/AboutSettings';
import './Settings.css';

export type SettingsSectionId =
  | 'funds'
  | 'legal-checklist'
  | 'legal-templates'
  | 'academic-tasks'
  | 'academic-dimensions'
  | 'academic-ranking'
  | 'appearance'
  | 'about';

const VALID_SECTIONS: SettingsSectionId[] = [
  'funds',
  'legal-checklist',
  'legal-templates',
  'academic-tasks',
  'academic-dimensions',
  'academic-ranking',
  'appearance',
  'about',
];

export interface SettingsNavItem {
  id: SettingsSectionId;
  label: string;
  icon?: LucideIcon;
}

interface SettingsPageProps {
  theme: 'light' | 'dark';
  onThemeChange: (theme: 'light' | 'dark') => void;
  /** Switches the top-level tab (e.g. "academic"). Used by sections that
   *  defer to an existing page for editing. */
  onNavigateTab: (tab: 'portfolio' | 'academic') => void;
}

const GROUPS: SettingsNavGroup[] = [
  {
    id: 'portfolio',
    label: 'Portfolio',
    items: [
      { id: 'funds', label: 'Funds', icon: Landmark },
      { id: 'legal-checklist', label: 'Legal Review Checklist', icon: BookOpen },
      { id: 'legal-templates', label: 'Legal Templates', icon: FileText },
    ],
  },
  {
    id: 'academic',
    label: 'Academic',
    items: [
      { id: 'academic-tasks', label: 'Continuous Tasks', icon: Activity },
      { id: 'academic-dimensions', label: 'Custom Dimensions', icon: Target },
      { id: 'academic-ranking', label: 'Ranking Presets', icon: ListOrdered },
    ],
  },
  {
    id: 'application',
    label: 'Application',
    items: [
      { id: 'appearance', label: 'Appearance', icon: Palette },
      { id: 'about', label: 'About', icon: Info },
    ],
  },
];

export function SettingsPage({
  theme,
  onThemeChange,
  onNavigateTab,
}: SettingsPageProps) {
  const navigate = useNavigate();
  const { section } = useParams<{ section: string }>();
  const active: SettingsSectionId = VALID_SECTIONS.includes(section as SettingsSectionId)
    ? (section as SettingsSectionId)
    : 'funds';

  const handleSelect = (id: SettingsSectionId) => {
    navigate(`/settings/${id}`, { replace: true });
  };

  const renderSection = () => {
    switch (active) {
      case 'funds':
        return <FundsSettings />;
      case 'legal-checklist':
        return <ChecklistSettings />;
      case 'legal-templates':
        return <TemplatesSettings />;
      case 'academic-tasks':
        return <TasksSettings />;
      case 'academic-dimensions':
        return <DimensionsSettings />;
      case 'academic-ranking':
        return <RankingSettings onNavigateTab={onNavigateTab} />;
      case 'appearance':
        return <AppearanceSettings theme={theme} onThemeChange={onThemeChange} />;
      case 'about':
        return <AboutSettings />;
      default:
        return null;
    }
  };

  return (
    <div className="settings-root">
      <SettingsNav groups={GROUPS} active={active} onSelect={handleSelect} />
      <main className="settings-content">{renderSection()}</main>
    </div>
  );
}
