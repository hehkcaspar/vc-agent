import { useState, FormEvent } from 'react';
import { api } from '../services/api';
import { Entity, EntityUpdateData } from '../types';
import { EntityMetadataForm } from './EntityMetadataForm';
import './CreateEntityModal.css';

interface EditEntityModalProps {
  entity: Entity;
  onClose: () => void;
  onSuccess: (entity: Entity) => void;
}

/**
 * Edit Entity Modal
 * 
 * This modal uses EntityMetadataForm which automatically renders all fields
 * defined in ENTITY_METADATA_FIELDS (types/index.ts).
 * 
 * When you modify the backend EntityUpdate schema:
 * 1. Update ENTITY_METADATA_FIELDS in types/index.ts
 * 2. Update getEntityMetadataFields() in EntityMetadataForm.tsx
 * 3. Both Create and Edit modals will automatically sync
 */
export function EditEntityModal({ entity, onClose, onSuccess }: EditEntityModalProps) {
  const [data, setData] = useState<EntityUpdateData>({
    name: entity.name,
    website: entity.website || '',
    status: entity.status,
  });
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!data.name?.trim()) return;

    setIsSubmitting(true);
    setError(null);

    try {
      // Only send fields that have values
      const updatePayload: Partial<EntityUpdateData> = {
        name: data.name.trim(),
      };
      if (data.website?.trim()) {
        updatePayload.website = data.website.trim();
      }
      if (data.status) {
        updatePayload.status = data.status;
      }

      const updatedEntity = await api.entities.update(entity.id, updatePayload);
      onSuccess(updatedEntity);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'An error occurred');
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h3>Edit Entity</h3>
          <button className="modal-close" onClick={onClose}>×</button>
        </div>

        <form onSubmit={handleSubmit}>
          <div className="modal-body">
            {/* This form automatically renders all fields from ENTITY_METADATA_FIELDS */}
            <EntityMetadataForm 
              data={data} 
              onChange={(newData) => setData(prev => ({ ...prev, ...newData }) as EntityUpdateData)}
              disabled={isSubmitting}
            />

            {error && <div className="error-message">{error}</div>}
          </div>

          <div className="modal-footer">
            <button 
              type="button" 
              className="btn-secondary"
              onClick={onClose}
              disabled={isSubmitting}
            >
              Cancel
            </button>
            <button 
              type="submit" 
              className="btn-primary"
              disabled={isSubmitting || !data.name?.trim()}
            >
              {isSubmitting ? 'Saving...' : 'Save Changes'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
