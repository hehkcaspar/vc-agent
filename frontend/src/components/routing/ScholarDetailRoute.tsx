import { useCallback } from 'react';
import { Navigate, useNavigate, useParams } from 'react-router-dom';
import { useScholars } from '../../hooks/useAcademic';
import {
  CONTENT_TABS,
  ScholarDetail,
  type ContentTab,
} from '../academic/ScholarDetail';
import { makeTabValidator } from '../../lib/tabValidator';
import './routing.css';

const isValidSubTab = makeTabValidator(CONTENT_TABS);

export function ScholarDetailRoute() {
  const { scholarId, subTab } = useParams<{ scholarId: string; subTab?: string }>();
  const navigate = useNavigate();
  const { scholars, isLoading, mutate } = useScholars();
  const scholar = scholars.find((s) => s.id === scholarId);

  const contentTab: ContentTab = isValidSubTab(subTab) ? subTab : 'report';

  const handleContentTabChange = useCallback(
    (tab: ContentTab) => {
      if (!scholarId) return;
      navigate(`/academic/scholars/${scholarId}/${tab}`, { replace: true });
    },
    [scholarId, navigate],
  );

  const handleBack = useCallback(() => {
    mutate();
    navigate('/academic');
  }, [mutate, navigate]);

  if (!scholarId) return <Navigate to="/academic" replace />;
  if (isLoading && !scholar) {
    return <div className="route-message">Loading scholar…</div>;
  }
  if (!scholar) {
    return (
      <div className="route-message">
        <p>Scholar not found.</p>
        <button className="btn-secondary" onClick={handleBack}>
          Back to Academic
        </button>
      </div>
    );
  }

  return (
    <ScholarDetail
      scholar={scholar}
      contentTab={contentTab}
      onContentTabChange={handleContentTabChange}
      onBack={handleBack}
    />
  );
}
