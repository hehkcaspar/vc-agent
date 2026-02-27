import { useState } from 'react';
import { useEntities } from '../hooks/useEntities';
import { useTabContext } from '../store/TabContext';
import { Entity } from '../types';
import { EntityDetail } from './EntityDetail';
import { CreateEntityModal } from './CreateEntityModal';
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

      <div className="view-toggle">
        <button 
          className={viewMode === 'list' ? 'active' : ''}
          onClick={() => handleViewModeChange('list')}
        >
          List
        </button>
        <button 
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
              />
            ) : (
              <EntityCard 
                key={entity.id} 
                entity={entity} 
                onClick={() => handleEntitySelect(entity)}
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
    </div>
  );
}

function EntityRow({ entity, onClick }: { entity: Entity; onClick: () => void }) {
  return (
    <div className="entity-row" onClick={onClick}>
      <div className="entity-row-icon">🏢</div>
      <div className="entity-row-info">
        <div className="entity-row-name">{entity.name}</div>
        <div className="entity-row-meta">
          {entity.website || 'No website'} • Updated {new Date(entity.updated_at).toLocaleDateString()}
        </div>
      </div>
    </div>
  );
}

function EntityCard({ entity, onClick }: { entity: Entity; onClick: () => void }) {
  return (
    <div className="entity-card" onClick={onClick}>
      <div className="entity-card-icon">🏢</div>
      <div className="entity-card-name">{entity.name}</div>
      <div className="entity-card-meta">
        {entity.website || 'No website'}<br />
        Updated {new Date(entity.updated_at).toLocaleDateString()}
      </div>
    </div>
  );
}
