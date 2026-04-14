import { TasksView } from '../../academic/TasksView';

export function TasksSettings() {
  return (
    <>
      <header className="settings-section-header">
        <h2 className="settings-section-title">Continuous Tasks</h2>
        <p className="settings-section-subtitle">
          The Academic heartbeat runs these tasks on a cadence — external
          source fetchers (Layer 2), per-dimension evaluators (Layer 3), and
          system tasks. Edits to cadence or enabled state take effect on the
          next tick (~60s). "Run now" force-executes one task across active
          scholars.
        </p>
      </header>
      <div className="settings-block">
        <TasksView />
      </div>
    </>
  );
}
