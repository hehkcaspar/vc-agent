import { useNavigate } from 'react-router-dom';
import './routing.css';

export function NotFound() {
  const navigate = useNavigate();
  return (
    <div className="route-message route-message--center">
      <h2>Page not found</h2>
      <p className="text-muted">That route doesn't exist.</p>
      <button
        className="btn-secondary"
        onClick={() => navigate('/portfolio')}
      >
        Back to portfolio
      </button>
    </div>
  );
}
