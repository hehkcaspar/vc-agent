import { useState } from 'react';
import { useParkingLot, resolveParkingLotItem } from '../hooks/useParkingLot';
import { useEntities } from '../hooks/useEntities';
import { IngestItem, Entity } from '../types';
import './CreateEntityModal.css';
import './ParkingLotModal.css';

interface ParkingLotModalProps {
  onClose: () => void;
  onResolved: () => void;
}

export function ParkingLotModal({ onClose, onResolved }: ParkingLotModalProps) {
  const { items, isLoading, mutate } = useParkingLot();
  const { entities } = useEntities();
  const [resolvingId, setResolvingId] = useState<string | null>(null);
  const [newEntityName, setNewEntityName] = useState('');

  const pendingItems = items.filter(
    item => ['parked', 'resolution_required', 'failed'].includes(item.status)
  );

  const handleResolve = async (ingestId: string, entityId: string) => {
    setResolvingId(ingestId);
    try {
      await resolveParkingLotItem(ingestId, { entity_id: entityId });
      await mutate();
      onResolved();
    } finally {
      setResolvingId(null);
    }
  };

  const handleCreateAndResolve = async (ingestId: string) => {
    if (!newEntityName.trim()) return;
    setResolvingId(ingestId);
    try {
      await resolveParkingLotItem(ingestId, { 
        create_entity: { name: newEntityName.trim() } 
      });
      setNewEntityName('');
      await mutate();
      onResolved();
    } finally {
      setResolvingId(null);
    }
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal parking-lot-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h3>Parking Lot ({pendingItems.length})</h3>
          <button className="modal-close" onClick={onClose}>×</button>
        </div>

        <div className="modal-body">
          {isLoading ? (
            <div className="loading">Loading...</div>
          ) : pendingItems.length === 0 ? (
            <div className="empty-parking">
              <div className="empty-parking-icon">🅿️</div>
              <p>No items in parking lot</p>
              <p style={{ fontSize: 13, color: '#9ca3af' }}>
                All items have been resolved
              </p>
            </div>
          ) : (
            pendingItems.map(item => (
              <ParkingItem
                key={item.ingest_id}
                item={item}
                entities={entities || []}
                onResolve={(entityId) => handleResolve(item.ingest_id, entityId)}
                onCreateAndResolve={() => handleCreateAndResolve(item.ingest_id)}
                newEntityName={newEntityName}
                onNewEntityNameChange={setNewEntityName}
                isResolving={resolvingId === item.ingest_id}
              />
            ))
          )}
        </div>

        <div className="modal-footer">
          <button className="btn-secondary" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </div>
  );
}

interface ParkingItemProps {
  item: IngestItem;
  entities: Entity[];
  onResolve: (entityId: string) => void;
  onCreateAndResolve: () => void;
  newEntityName: string;
  onNewEntityNameChange: (name: string) => void;
  isResolving: boolean;
}

function ParkingItem({
  item,
  entities,
  onResolve,
  onCreateAndResolve,
  newEntityName,
  onNewEntityNameChange,
  isResolving
}: ParkingItemProps) {
  const [selectedEntityId, setSelectedEntityId] = useState('');
  const [showCreateNew, setShowCreateNew] = useState(false);

  return (
    <div className="parking-item">
      <div className="parking-item-header">
        <div className="parking-item-info">
          <h4>
            {item.entity_hint_name || 'Unnamed Item'}
          </h4>
          <div className="parking-item-meta">
            {new Date(item.created_at).toLocaleString()} • {item.source}
          </div>
        </div>
        <span className={`parking-item-status ${item.status}`}>
          {item.status.replace('_', ' ')}
        </span>
      </div>

      {item.entity_hint_name && (
        <div className="parking-item-files">
          <span className="file-chip">Hint: {item.entity_hint_name}</span>
        </div>
      )}

      {item.error && (
        <div style={{ color: '#ef4444', fontSize: 12, marginBottom: 12 }}>
          Error: {item.error}
        </div>
      )}

      {!showCreateNew ? (
        <div className="parking-item-actions">
          <select
            value={selectedEntityId}
            onChange={(e) => setSelectedEntityId(e.target.value)}
            disabled={isResolving}
          >
            <option value="">Select existing entity...</option>
            {entities.map(entity => (
              <option key={entity.id} value={entity.id}>
                {entity.name}
              </option>
            ))}
          </select>
          <button
            className="btn-small btn-primary"
            onClick={() => selectedEntityId && onResolve(selectedEntityId)}
            disabled={!selectedEntityId || isResolving}
          >
            {isResolving ? '...' : 'Attach'}
          </button>
          <button
            className="btn-small btn-secondary"
            onClick={() => setShowCreateNew(true)}
            disabled={isResolving}
          >
            Create New
          </button>
        </div>
      ) : (
        <div className="resolve-section">
          <h5>Create New Entity</h5>
          <div className="new-entity-form">
            <input
              type="text"
              placeholder="Entity name"
              value={newEntityName}
              onChange={(e) => onNewEntityNameChange(e.target.value)}
              disabled={isResolving}
            />
            <button
              className="btn-small btn-primary"
              onClick={onCreateAndResolve}
              disabled={!newEntityName.trim() || isResolving}
            >
              {isResolving ? '...' : 'Create & Attach'}
            </button>
            <button
              className="btn-small btn-secondary"
              onClick={() => setShowCreateNew(false)}
              disabled={isResolving}
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
