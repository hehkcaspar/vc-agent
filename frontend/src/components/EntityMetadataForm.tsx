import { EntityMetadataField, EntityUpdateData } from '../types';

interface EntityMetadataFormProps {
  data: Partial<EntityUpdateData>;
  onChange: (data: Partial<EntityUpdateData>) => void;
  disabled?: boolean;
  /** Skip rendering these fields; useful when the surrounding modal handles them itself. */
  hiddenFields?: (keyof EntityUpdateData)[];
}

/**
 * Reusable form for entity metadata fields.
 * This component automatically renders all fields defined in ENTITY_METADATA_FIELDS.
 *
 * When backend EntityUpdate schema changes:
 * 1. Update ENTITY_METADATA_FIELDS in types/index.ts
 * 2. This form will automatically reflect the changes
 */
export function EntityMetadataForm({ data, onChange, disabled, hiddenFields }: EntityMetadataFormProps) {
  const handleChange = (fieldName: keyof EntityUpdateData, value: string) => {
    // Auto-prepend https:// to website if no protocol is present
    if (fieldName === 'website' && value && !value.match(/^https?:\/\//)) {
      // Don't modify if it's just a domain without protocol
      // We'll handle this on blur
    }
    onChange({
      ...data,
      [fieldName]: value || undefined,  // Convert empty string to undefined
    });
  };

  const handleBlur = (fieldName: keyof EntityUpdateData, value: string) => {
    // Auto-prepend https:// to website on blur if no protocol and not empty
    if (fieldName === 'website' && value && !value.match(/^https?:\/\//)) {
      onChange({
        ...data,
        [fieldName]: `https://${value}`,
      });
    }
  };

  const hidden = new Set(hiddenFields ?? []);

  return (
    <>
      {/* Import the field config from types - we inline it here to avoid circular deps */}
      {getEntityMetadataFields()
        .filter((field) => !hidden.has(field.name))
        .map((field) => (
        <div className="form-group" key={field.name}>
          <label htmlFor={field.name}>
            {field.label}
            {field.required && <span style={{ color: '#ef4444' }}> *</span>}
          </label>
          
          {field.type === 'select' && field.options ? (
            <select
              id={field.name}
              value={data[field.name] || ''}
              onChange={(e) => handleChange(field.name, e.target.value)}
              disabled={disabled}
            >
              <option value="">-- Select {field.label} --</option>
              {field.options.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
          ) : field.type === 'textarea' ? (
            <textarea
              id={field.name}
              value={data[field.name] || ''}
              onChange={(e) => handleChange(field.name, e.target.value)}
              placeholder={field.placeholder}
              disabled={disabled}
              rows={4}
            />
          ) : (
            <input
              id={field.name}
              type={field.type === 'url' ? 'text' : field.type}
              value={data[field.name] || ''}
              onChange={(e) => handleChange(field.name, e.target.value)}
              onBlur={(e) => handleBlur(field.name, e.target.value)}
              placeholder={field.placeholder}
              disabled={disabled}
              required={field.required}
            />
          )}
        </div>
      ))}
    </>
  );
}

// Inline the field config to avoid circular dependency issues
// This should match ENTITY_METADATA_FIELDS in types/index.ts
function getEntityMetadataFields(): EntityMetadataField[] {
  return [
    {
      name: 'name',
      label: 'Entity Name',
      type: 'text',
      required: true,
      placeholder: 'e.g., Acme Corporation',
    },
    {
      name: 'website',
      label: 'Website',
      type: 'text',  // Changed from 'url' to allow flexible input
      required: false,
      placeholder: 'example.com or https://example.com',
    },
    {
      name: 'status',
      label: 'Status',
      type: 'select',
      required: false,
      options: [
        { value: 'active', label: 'Active' },
        { value: 'archived', label: 'Archived' },
      ],
    },
  ];
}
