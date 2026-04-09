import {
  FileText,
  Lock,
  Newspaper,
  BarChart3,
  Globe,
  Target,
  Search,
  Check,
  AlertTriangle,
  MessageSquare,
  HelpCircle,
  type LucideIcon,
} from 'lucide-react';

export const EVENT_ICONS: Record<string, LucideIcon> = {
  new_paper: FileText,
  new_preprint: FileText,
  patent_filed: Lock,
  news_mention: Newspaper,
  metric_snapshot: BarChart3,
  website_updated: Globe,
  career_change: Target,
  identity_discovered: Search,
  evaluation_completed: Check,
  channel_deactivated: AlertTriangle,
  user_note_added: MessageSquare,
};

export function EventIcon({ type, size = 14 }: { type: string; size?: number }) {
  const Icon = EVENT_ICONS[type] ?? HelpCircle;
  return <Icon size={size} />;
}
