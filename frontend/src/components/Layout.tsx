import { useState, useEffect } from 'react';
import { Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom';
import { Menu, ChevronLeft, ChevronRight, Briefcase, GraduationCap, Moon, Sun, Settings as SettingsIcon } from 'lucide-react';
import { ChatModelProfileProvider } from '../context/ChatModelProfileContext';
import { PortfolioTab } from './PortfolioTab';
import { AcademicTab } from './academic/AcademicTab';
import { SettingsPage } from './Settings/SettingsPage';
import { EntityDetailRoute } from './routing/EntityDetailRoute';
import { ScholarDetailRoute } from './routing/ScholarDetailRoute';
import { NotFound } from './routing/NotFound';
import './Layout.css';

type TabId = 'portfolio' | 'academic' | 'settings';
type Theme = 'light' | 'dark';

function resolveActiveTab(pathname: string): TabId {
  if (pathname.startsWith('/academic')) return 'academic';
  if (pathname.startsWith('/settings')) return 'settings';
  return 'portfolio';
}

function LayoutShell() {
  const location = useLocation();
  const navigate = useNavigate();
  const activeTab = resolveActiveTab(location.pathname);
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(false);
  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false);

  const goToTab = (tab: TabId) => {
    navigate(`/${tab}`);
    setIsMobileMenuOpen(false);
  };
  
  // Theme state logic
  const [theme, setTheme] = useState<Theme>(() => {
    if (typeof window !== 'undefined') {
      const savedTheme = localStorage.getItem('theme');
      if (savedTheme === 'light' || savedTheme === 'dark') return savedTheme;
      return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
    }
    return 'light';
  });

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('theme', theme);
  }, [theme]);

  // Handle responsive sidebar behavior on mount and resize
  useEffect(() => {
    const handleResize = () => {
      if (window.innerWidth <= 1024 && window.innerWidth > 768) {
        setIsSidebarCollapsed(true);
      } else if (window.innerWidth > 1024) {
        setIsSidebarCollapsed(false);
      } else if (window.innerWidth <= 768) {
        // On mobile, "collapsed" state is reused for the drawer menu
        setIsSidebarCollapsed(false); 
      }
    };

    // Initial check
    handleResize();

    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, []);

  const toggleTheme = () => {
    setTheme(prev => prev === 'light' ? 'dark' : 'light');
  };


  return (
    <div className="layout">
      {/* Mobile Header */}
      <div className="mobile-header">
        <button 
          className="mobile-toggle"
          onClick={() => setIsMobileMenuOpen(true)}
        >
          <Menu size={20} />
        </button>
        <h1>VC Portfolio</h1>
      </div>

      {/* Mobile Overlay */}
      <div 
        className={`mobile-overlay ${isMobileMenuOpen ? 'open' : ''}`}
        onClick={() => setIsMobileMenuOpen(false)}
      />

      {/* Sidebar */}
      <aside className={`sidebar ${isSidebarCollapsed ? 'collapsed' : ''} ${isMobileMenuOpen ? 'collapsed' : ''}`}>
        <div className="sidebar-header">
  <div className="sidebar-title">
            <h1>VC Portfolio</h1>
            <p>Portfolio Manager</p>
          </div>
          <button 
            className="sidebar-toggle"
            onClick={() => setIsSidebarCollapsed(!isSidebarCollapsed)}
            title={isSidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
          >
            {isSidebarCollapsed ? <ChevronRight size={16} /> : <ChevronLeft size={16} />}
          </button>
        </div>
        <nav className="sidebar-nav">
          <button
            className={`nav-item ${activeTab === 'portfolio' ? 'active' : ''}`}
            onClick={() => goToTab('portfolio')}
          >
            <span className="nav-icon"><Briefcase size={18} /></span>
            <span className="nav-text">Portfolio</span>
          </button>
          <button
            className={`nav-item ${activeTab === 'academic' ? 'active' : ''}`}
            onClick={() => goToTab('academic')}
          >
            <span className="nav-icon"><GraduationCap size={18} /></span>
            <span className="nav-text">Academic</span>
          </button>
        </nav>
        <div className="sidebar-footer">
          <button
            className={`nav-item ${activeTab === 'settings' ? 'active' : ''}`}
            onClick={() => goToTab('settings')}
          >
            <span className="nav-icon"><SettingsIcon size={18} /></span>
            <span className="nav-text">Settings</span>
          </button>
          <button className="theme-toggle" onClick={toggleTheme} title="Toggle Theme">
            <span className="nav-icon">{theme === 'light' ? <Moon size={18} /> : <Sun size={18} />}</span>
            <span className="theme-text">{theme === 'light' ? 'Dark Mode' : 'Light Mode'}</span>
          </button>
        </div>
      </aside>

      {/* Main Content */}
      <main className={`main-content ${isSidebarCollapsed ? 'expanded' : ''}`}>
        <Routes>
          <Route path="/" element={<Navigate to="/portfolio" replace />} />
          <Route path="/portfolio" element={<PortfolioTab />} />
          <Route path="/portfolio/new" element={<PortfolioTab />} />
          <Route path="/portfolio/parking-lot" element={<PortfolioTab />} />
          <Route
            path="/portfolio/entities/:entityId"
            element={<EntityDetailRoute />}
          />
          <Route
            path="/portfolio/entities/:entityId/edit"
            element={<EntityDetailRoute />}
          />
          <Route
            path="/portfolio/entities/:entityId/:subTab"
            element={<EntityDetailRoute />}
          />
          <Route path="/academic" element={<AcademicTab />} />
          <Route path="/academic/new" element={<AcademicTab />} />
          <Route
            path="/academic/scholars/:scholarId"
            element={<ScholarDetailRoute />}
          />
          <Route
            path="/academic/scholars/:scholarId/:subTab"
            element={<ScholarDetailRoute />}
          />
          <Route
            path="/settings"
            element={<Navigate to="/settings/funds" replace />}
          />
          <Route
            path="/settings/:section"
            element={
              <SettingsPage
                theme={theme}
                onThemeChange={setTheme}
                onNavigateTab={(tab) => navigate(`/${tab}`)}
              />
            }
          />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </main>
    </div>
  );
}

export function Layout() {
  return (
    <ChatModelProfileProvider>
      <LayoutShell />
    </ChatModelProfileProvider>
  );
}
