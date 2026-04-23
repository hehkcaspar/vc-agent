import { useCallback, useMemo } from 'react';
import { Navigate, useLocation, useNavigate, useParams } from 'react-router-dom';
import { useEntity } from '../../hooks/useEntities';
import { CONTENT_TABS, EntityDetail, type ContentTab } from '../EntityDetail';
import { makeTabValidator } from '../../lib/tabValidator';
import './routing.css';

const isValidSubTab = makeTabValidator(CONTENT_TABS);

export function EntityDetailRoute() {
  const { entityId, subTab } = useParams<{ entityId: string; subTab?: string }>();
  const location = useLocation();
  const navigate = useNavigate();
  const { entity, isLoading, error } = useEntity(entityId);

  const isEditing = location.pathname.endsWith('/edit');
  const contentTab: ContentTab = isValidSubTab(subTab) ? subTab : 'workroom';
  const detailBase = useMemo(
    () => `/portfolio/entities/${entityId}`,
    [entityId],
  );

  const handleContentTabChange = useCallback(
    (tab: ContentTab) => {
      if (!entityId) return;
      navigate(`${detailBase}/${tab}`, { replace: true });
    },
    [entityId, navigate, detailBase],
  );

  const handleBack = useCallback(() => navigate('/portfolio'), [navigate]);

  const handleOpenEdit = useCallback(() => {
    navigate(`${detailBase}/edit`);
  }, [navigate, detailBase]);

  const handleCloseEdit = useCallback(() => {
    navigate(detailBase);
  }, [navigate, detailBase]);

  if (!entityId) return <Navigate to="/portfolio" replace />;
  if (isLoading) return <div className="route-message">Loading entity…</div>;
  if (error || !entity) {
    return (
      <div className="route-message">
        <p>Entity not found.</p>
        <button className="btn-secondary" onClick={handleBack}>
          Back to portfolio
        </button>
      </div>
    );
  }

  return (
    <EntityDetail
      entity={entity}
      contentTab={contentTab}
      onContentTabChange={handleContentTabChange}
      onBack={handleBack}
      editModalOpen={isEditing}
      onOpenEditModal={handleOpenEdit}
      onCloseEditModal={handleCloseEdit}
    />
  );
}
