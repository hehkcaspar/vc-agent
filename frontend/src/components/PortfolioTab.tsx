import { useState } from 'react';
import { useEntities } from '../hooks/useEntities';
import { useTabContext } from '../store/TabContext';
import { Entity } from '../types';
import { api } from '../services/api';
import { EntityDetail } from './EntityDetail';
import { CreateEntityModal } from './CreateEntityModal';
import { EditEntityModal } from './EditEntityModal';
import { ParkingLotModal } from './ParkingLotModal';
import { useParkingLotCount } from '../hooks/useParkingLot';
import './PortfolioTab.css';

const TAB_ID = 'portfolio';

export function PortfolioTab() {
  const { getTabState, setTabState } = useTabContext();
  const savedState = getTabState(TAB_ID);
  
  const [viewMode, setViewMode] = useState<'list' | 'grid'>(savedState?.viewMode ?? 'grid');
  const [selectedEntity, setSelectedEntity] = useState<Entity | null>(null);
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false);
  const [isParkingLotOpen, setIsParkingLotOpen] = useState(false);
  const [editingEntity, setEditingEntity] = useState<Entity | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<Entity | null>(null);
  const [deleteStep, setDeleteStep] = useState<1 | 2>(1);
  const [isDeletingEntity, setIsDeletingEntity] = useState(false);
  
  const { entities, isLoading, mutate } = useEntities();
  const { count: parkingLotCount } = useParkingLotCount();

  const handleViewModeChange = (mode: 'list' | 'grid') => {
    setViewMode(mode);
    setTabState(TAB_ID, { viewMode: mode });
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
            <span>🅿️</span>
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

      {isLoading ? (
        <div className="loading">Loading...</div>
      ) : entities?.length === 0 ? (
        <div className="empty-state">
          No entities yet. Create your first entity to get started.
        </div>
      ) : (
        <div className={viewMode === 'list' ? 'entity-list' : 'entity-grid'}>
          {entities?.map(entity => (
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
                ×
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

function EntityRow({ entity, onClick, onEdit, onArchive, onDelete }: { entity: Entity; onClick: () => void; onEdit: (e: React.MouseEvent) => void; onArchive: (e: React.MouseEvent) => void; onDelete: (e: React.MouseEvent) => void }) {
  const isArchived = entity.status === 'archived';
  return (
    <div className={`entity-row ${isArchived ? 'archived' : ''}`} onClick={onClick}>
      <div className="entity-row-icon">🏢</div>
      <div className="entity-row-info">
        <div className="entity-row-name">
          {entity.name}
          {isArchived && <span className="archived-badge">Archived</span>}
        </div>
        <div className="entity-row-meta">
          {entity.website || 'No website'} • Updated {new Date(entity.updated_at).toLocaleDateString()}
        </div>
      </div>
      <div className="entity-row-actions">
        <button 
          className="btn-icon" 
          onClick={onEdit}
          title="Edit entity"
        >
          ✏️
        </button>
        <button 
          className="btn-icon archive-btn" 
          onClick={onArchive}
          title={isArchived ? 'Unarchive entity' : 'Archive entity'}
        >
          {isArchived ? '📂' : '📥'}
        </button>
        <button
          className="btn-icon delete-btn"
          onClick={onDelete}
          title="Delete entity"
        >
          🗑️
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
          ✏️
        </button>
        <button 
          className="btn-icon card-archive-btn" 
          onClick={onArchive}
          title={isArchived ? 'Unarchive entity' : 'Archive entity'}
        >
          {isArchived ? '📂' : '📥'}
        </button>
        <button
          className="btn-icon card-delete-btn"
          onClick={onDelete}
          title="Delete entity"
        >
          🗑️
        </button>
      </div>
      {isArchived && <div className="archived-overlay">Archived</div>}
      <div className="entity-card-icon">🏢</div>
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
