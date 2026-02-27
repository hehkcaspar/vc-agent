import { useState } from 'react';
import { PortfolioTab } from './PortfolioTab';
import './Layout.css';

type TabId = 'portfolio';

export function Layout() {
  const [activeTab, setActiveTab] = useState<TabId>('portfolio');

  const renderTab = () => {
    switch (activeTab) {
      case 'portfolio':
        return <PortfolioTab />;
      default:
        return <PortfolioTab />;
    }
  };

  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="sidebar-header">
          <h1>VC Portfolio</h1>
          <p>Portfolio Manager</p>
        </div>
        <nav className="sidebar-nav">
          <button
            className={`nav-item ${activeTab === 'portfolio' ? 'active' : ''}`}
            onClick={() => setActiveTab('portfolio')}
          >
            <span>📁</span>
            Portfolio
          </button>
        </nav>
      </aside>
      <main className="main-content">
        {renderTab()}
      </main>
    </div>
  );
}
