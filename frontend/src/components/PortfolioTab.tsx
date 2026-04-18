import { useMemo, useState } from 'react';
import { ParkingSquare, Building2, Pencil, Archive, ArchiveRestore, Trash2, X } from 'lucide-react';
import { useEntities } from '../hooks/useEntities';
import { useTabContext } from '../store/TabContext';
import { DealStage, Entity, StageFilter, StatusFilter } from '../types';
import { api } from '../services/api';
import { EntityDetail } from './EntityDetail';
import { CreateEntityModal } from './CreateEntityModal';
import { EditEntityModal } from './EditEntityModal';
import { ParkingLotModal } from './ParkingLotModal';
import { useParkingLotCount } from '../hooks/useParkingLot';
import './PortfolioTab.css';

const TAB_ID = 'portfolio';

const DEAL_STAGE_LABELS: Record<DealStage, string> = {
  prospect: 'Prospect',
  diligence: 'Diligence',
  portfolio: 'Portfolio',
  passed: 'Passed',
  exited: 'Exited',
};

// "Funnel" = live pipeline (prospect + diligence + portfolio). Passed/exited
// are historical and noisy in day-to-day browsing, so they're hidden by default.
const FUNNEL_STAGES: DealStage[] = ['prospect', 'diligence', 'portfolio'];

// Order in the segmented control. "Funnel" is the default slice; individual
// stages follow in funnel order. An explicit "All stages" button is omitted —
// it was visually indistinguishable from "Funnel" until passed/exited grew.
const STAGE_FILTER_ORDER: StageFilter[] = [
  'funnel',
  'prospect',
  'diligence',
  'portfolio',
  'passed',
  'exited',
];

const STAGE_FILTER_LABELS: Record<StageFilter, string> = {
  all: 'All',
  funnel: 'Funnel',
  prospect: 'Prospect',
  diligence: 'Diligence',
  portfolio: 'Portfolio',
  passed: 'Passed',
  exited: 'Exited',
};

// Archive dim — orthogonal to workflow stage. Default "Active" hides archived.
const STATUS_FILTER_ORDER: StatusFilter[] = ['active', 'archived', 'all'];

const STATUS_FILTER_LABELS: Record<StatusFilter, string> = {
  active: 'Active',
  archived: 'Archived',
  all: 'All',
};

/** Back-compat for stage filter values persisted before we redesigned the bar. */
function normaliseStageFilter(value: StageFilter | undefined): StageFilter {
  // 'active' was renamed to 'funnel' when the archive dim split out.
  if ((value as string) === 'active') return 'funnel';
  // 'all' was removed from the UI because it was visually redundant with
  // 'funnel' for small portfolios; fall back to 'funnel' when loading.
  if (value === 'all') return 'funnel';
  return value ?? 'funnel';
}

export function PortfolioTab() {
  const { getTabState, setTabState } = useTabContext();
  const savedState = getTabState(TAB_ID);
  
  const [viewMode, setViewMode] = useState<'list' | 'grid'>(savedState?.viewMode ?? 'grid');
  const [stageFilter, setStageFilter] = useState<StageFilter>(normaliseStageFilter(savedState?.stageFilter));
  const [statusFilter, setStatusFilter] = useState<StatusFilter>(savedState?.statusFilter ?? 'active');
  const [selectedEntity, setSelectedEntity] = useState<Entity | null>(null);
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false);
  const [isParkingLotOpen, setIsParkingLotOpen] = useState(false);
  const [editingEntity, setEditingEntity] = useState<Entity | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<Entity | null>(null);
  const [deleteStep, setDeleteStep] = useState<1 | 2>(1);
  const [isDeletingEntity, setIsDeletingEntity] = useState(false);

  const { entities, isLoading, mutate } = useEntities();
  const { count: parkingLotCount } = useParkingLotCount();

  // Counts per filter bucket — rendered inline on each segmented control so
  // the user sees stage and archival volumes at a glance. Each dim is counted
  // independently against the full entity list so counts don't jitter when
  // the other dim changes.
  const stageCounts = useMemo(() => {
    const counts: Record<StageFilter, number> = {
      all: 0, funnel: 0,
      prospect: 0, diligence: 0, portfolio: 0, passed: 0, exited: 0,
    };
    if (!entities) return counts;
    for (const e of entities) {
      counts.all += 1;
      if (FUNNEL_STAGES.includes(e.deal_stage)) counts.funnel += 1;
      counts[e.deal_stage] += 1;
    }
    return counts;
  }, [entities]);

  const statusCounts = useMemo(() => {
    const counts: Record<StatusFilter, number> = { active: 0, archived: 0, all: 0 };
    if (!entities) return counts;
    for (const e of entities) {
      counts.all += 1;
      if (e.status === 'archived') counts.archived += 1;
      else counts.active += 1;
    }
    return counts;
  }, [entities]);

  const visibleEntities = useMemo(() => {
    if (!entities) return entities;
    return entities.filter((e) => {
      if (statusFilter === 'active' && e.status !== 'active') return false;
      if (statusFilter === 'archived' && e.status !== 'archived') return false;
      if (stageFilter === 'all') return true;
      if (stageFilter === 'funnel') return FUNNEL_STAGES.includes(e.deal_stage);
      return e.deal_stage === stageFilter;
    });
  }, [entities, stageFilter, statusFilter]);

  const handleViewModeChange = (mode: 'list' | 'grid') => {
    setViewMode(mode);
    setTabState(TAB_ID, { viewMode: mode });
  };

  const handleStageFilterChange = (next: StageFilter) => {
    setStageFilter(next);
    setTabState(TAB_ID, { stageFilter: next });
  };

  const handleStatusFilterChange = (next: StatusFilter) => {
    setStatusFilter(next);
    setTabState(TAB_ID, { statusFilter: next });
  };

  const handleEntitySelect = (entity: Entity) => {
    setSelectedEntity(entity);
    setTabState(TAB_ID, { selectedEntityId: entity.id });
  };

  const handleBack = () => {
    setSelectedEntity(null);
    setTabState(TAB_ID, { selectedEntityId: undefined });
  };

  const handleEntityCreated = (entity: Entity) => {
    mutate();
    setSelectedEntity(entity);
  };

  const handleEntityUpdated = () => {
    mutate();
  };

  const handleArchive = async (e: React.MouseEvent, entity: Entity) => {
    e.stopPropagation();
    const newStatus = entity.status === 'active' ? 'archived' : 'active';
    try {
      await api.entities.update(entity.id, { status: newStatus });
      mutate();
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Failed to update status');
    }
  };

  const requestDeleteEntity = (e: React.MouseEvent, entity: Entity) => {
    e.stopPropagation();
    setDeleteTarget(entity);
    setDeleteStep(1);
  };

  const closeDeleteModal = () => {
    if (isDeletingEntity) return;
    setDeleteTarget(null);
    setDeleteStep(1);
  };

  const handleDeleteEntity = async () => {
    if (!deleteTarget) return;
    setIsDeletingEntity(true);
    try {
      await api.entities.delete(deleteTarget.id);
      if (selectedEntity?.id === deleteTarget.id) {
        setSelectedEntity(null);
      }
      setDeleteTarget(null);
      setDeleteStep(1);
      mutate();
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Failed to delete entity');
    } finally {
      setIsDeletingEntity(false);
    }
  };

  if (selectedEntity) {
    return (
      <EntityDetail 
        entity={selectedEntity} 
        onBack={handleBack}
      />
    );
  }

  return (
    <div className="portfolio-tab">
      <div className="portfolio-header">
        <h2>Portfolio</h2>
        <div className="header-actions">
          <button 
            className={`parking-lot-button ${parkingLotCount > 0 ? 'has-items' : ''}`}
            onClick={() => setIsParkingLotOpen(true)}
          >
            <ParkingSquare size={16} />
            Parking Lot
            {parkingLotCount > 0 && (
              <span className="parking-lot-badge">{parkingLotCount}</span>
            )}
          </button>
          <button 
            className="btn-primary"
            onClick={() => setIsCreateModalOpen(true)}
          >
            + Create Entity
          </button>
        </div>
      </div>

      <div className="portfolio-toolbar">
        <div className="portfolio-filter-group">
          <span className="portfolio-filter-label">Stage</span>
          <div className="segmented-toggle portfolio-stage-filter" role="tablist" aria-label="Filter by deal stage">
            {STAGE_FILTER_ORDER.map((f) => (
              <button
                key={f}
                type="button"
                role="tab"
                aria-selected={stageFilter === f}
                className={stageFilter === f ? 'active' : ''}
                onClick={() => handleStageFilterChange(f)}
              >
                {STAGE_FILTER_LABELS[f]}
                <span className="portfolio-stage-count">{stageCounts[f]}</span>
              </button>
            ))}
          </div>
        </div>
        <div className="portfolio-filter-group portfolio-filter-group--secondary">
          <span className="portfolio-filter-label">Status</span>
          <div className="segmented-toggle portfolio-status-filter" role="tablist" aria-label="Filter by archival status">
            {STATUS_FILTER_ORDER.map((f) => (
              <button
                key={f}
                type="button"
                role="tab"
                aria-selected={statusFilter === f}
                className={statusFilter === f ? 'active' : ''}
                onClick={() => handleStatusFilterChange(f)}
              >
                {STATUS_FILTER_LABELS[f]}
                <span className="portfolio-stage-count">{statusCounts[f]}</span>
              </button>
            ))}
          </div>
        </div>
        <div className="segmented-toggle portfolio-view-toggle">
          <button
            type="button"
            className={viewMode === 'list' ? 'active' : ''}
            onClick={() => handleViewModeChange('list')}
          >
            List
          </button>
          <button
            type="button"
            className={viewMode === 'grid' ? 'active' : ''}
            onClick={() => handleViewModeChange('grid')}
          >
            Grid
          </button>
        </div>
      </div>

      {isLoading ? (
        <div className="loading">Loading...</div>
      ) : entities?.length === 0 ? (
        <div className="empty-state">
          No entities yet. Create your first entity to get started.
        </div>
      ) : visibleEntities?.length === 0 ? (
        <div className="empty-state">
          No entities matching <strong>{STAGE_FILTER_LABELS[stageFilter]}</strong>
          {' · '}
          <strong>{STATUS_FILTER_LABELS[statusFilter]}</strong>. Try a different filter.
        </div>
      ) : (
        <div className={viewMode === 'list' ? 'entity-list' : 'entity-grid'}>
          {visibleEntities?.map(entity => (
            viewMode === 'list' ? (
              <EntityRow
                key={entity.id}
                entity={entity}
                onClick={() => handleEntitySelect(entity)}
                onEdit={(e) => {
                  e.stopPropagation();
                  setEditingEntity(entity);
                }}
                onArchive={(e) => handleArchive(e, entity)}
                onDelete={(e) => requestDeleteEntity(e, entity)}
              />
            ) : (
              <EntityCard
                key={entity.id}
                entity={entity}
                onClick={() => handleEntitySelect(entity)}
                onEdit={(e) => {
                  e.stopPropagation();
                  setEditingEntity(entity);
                }}
                onArchive={(e) => handleArchive(e, entity)}
                onDelete={(e) => requestDeleteEntity(e, entity)}
              />
            )
          ))}
        </div>
      )}

      {isCreateModalOpen && (
        <CreateEntityModal
          onClose={() => setIsCreateModalOpen(false)}
          onSuccess={handleEntityCreated}
        />
      )}

      {isParkingLotOpen && (
        <ParkingLotModal
          onClose={() => setIsParkingLotOpen(false)}
          onResolved={() => {
            mutate();
          }}
        />
      )}

      {editingEntity && (
        <EditEntityModal
          entity={editingEntity}
          onClose={() => setEditingEntity(null)}
          onSuccess={handleEntityUpdated}
        />
      )}

      {deleteTarget && (
        <div className="portfolio-delete-modal-overlay" role="dialog" aria-modal="true" aria-label="Delete entity">
          <div className="portfolio-delete-modal">
            <div className="portfolio-delete-modal-header">
              <h3>{deleteStep === 1 ? 'Delete entity?' : 'Final confirmation'}</h3>
              <button
                type="button"
                className="portfolio-delete-modal-close"
                onClick={closeDeleteModal}
                disabled={isDeletingEntity}
                aria-label="Close"
              >
                <X size={16} />
              </button>
            </div>
            <div className="portfolio-delete-modal-body">
              {deleteStep === 1 ? (
                <p>
                  Delete <strong>{deleteTarget.name}</strong>? This removes the entity and its related
                  resources/artifacts.
                </p>
              ) : (
                <p>This action cannot be undone. Confirm again to permanently delete this entity.</p>
              )}
            </div>
            <div className="portfolio-delete-modal-footer">
              <button
                type="button"
                className="btn-secondary"
                onClick={closeDeleteModal}
                disabled={isDeletingEntity}
              >
                Cancel
              </button>
              {deleteStep === 1 ? (
                <button
                  type="button"
                  className="portfolio-delete-confirm"
                  onClick={() => setDeleteStep(2)}
                  disabled={isDeletingEntity}
                >
                  Continue
                </button>
              ) : (
                <button
                  type="button"
                  className="portfolio-delete-confirm portfolio-delete-confirm--danger"
                  onClick={() => void handleDeleteEntity()}
                  disabled={isDeletingEntity}
                >
                  {isDeletingEntity ? 'Deleting…' : 'Delete forever'}
                </button>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function StageChip({ stage }: { stage: DealStage }) {
  // Reuses the .tag-menu-trigger + .deal-stage-* palette from EntityDetail so
  // chip colours match the header badge on the detail page.
  return (
    <span className={`tag-menu-trigger deal-stage-${stage} portfolio-stage-chip`}>
      {DEAL_STAGE_LABELS[stage]}
    </span>
  );
}

function EntityRow({ entity, onClick, onEdit, onArchive, onDelete }: { entity: Entity; onClick: () => void; onEdit: (e: React.MouseEvent) => void; onArchive: (e: React.MouseEvent) => void; onDelete: (e: React.MouseEvent) => void }) {
  const isArchived = entity.status === 'archived';
  return (
    <div className={`entity-row ${isArchived ? 'archived' : ''}`} onClick={onClick}>
      <div className="entity-row-icon"><Building2 size={20} /></div>
      <div className="entity-row-info">
        <div className="entity-row-name">
          {entity.name}
          {isArchived && <span className="archived-badge">Archived</span>}
        </div>
        <div className="entity-row-meta">
          {entity.website || 'No website'} • Updated {new Date(entity.updated_at).toLocaleDateString()}
        </div>
      </div>
      <StageChip stage={entity.deal_stage} />
      <div className="entity-row-actions">
        <button
          className="btn-icon"
          onClick={onEdit}
          title="Edit entity"
        >
          <Pencil size={14} />
        </button>
        <button
          className="btn-icon archive-btn"
          onClick={onArchive}
          title={isArchived ? 'Unarchive entity' : 'Archive entity'}
        >
          {isArchived ? <ArchiveRestore size={14} /> : <Archive size={14} />}
        </button>
        <button
          className="btn-icon delete-btn"
          onClick={onDelete}
          title="Delete entity"
        >
          <Trash2 size={14} />
        </button>
      </div>
    </div>
  );
}

function EntityCard({ entity, onClick, onEdit, onArchive, onDelete }: { entity: Entity; onClick: () => void; onEdit: (e: React.MouseEvent) => void; onArchive: (e: React.MouseEvent) => void; onDelete: (e: React.MouseEvent) => void }) {
  const isArchived = entity.status === 'archived';
  return (
    <div className={`entity-card ${isArchived ? 'archived' : ''}`} onClick={onClick}>
      <div className="card-actions">
        <button
          className="btn-icon card-edit-btn"
          onClick={onEdit}
          title="Edit entity"
        >
          <Pencil size={14} />
        </button>
        <button
          className="btn-icon card-archive-btn"
          onClick={onArchive}
          title={isArchived ? 'Unarchive entity' : 'Archive entity'}
        >
          {isArchived ? <ArchiveRestore size={14} /> : <Archive size={14} />}
        </button>
        <button
          className="btn-icon card-delete-btn"
          onClick={onDelete}
          title="Delete entity"
        >
          <Trash2 size={14} />
        </button>
      </div>
      {isArchived && <div className="archived-overlay">Archived</div>}
      <div className="entity-card-header">
        <div className="entity-card-icon"><Building2 size={32} /></div>
        <StageChip stage={entity.deal_stage} />
      </div>
      <div className="entity-card-name">
        {entity.name}
      </div>
      <div className="entity-card-meta">
        {entity.website || 'No website'}<br />
        Updated {new Date(entity.updated_at).toLocaleDateString()}
      </div>
    </div>
  );
}
