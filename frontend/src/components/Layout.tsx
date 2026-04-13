import { useState, useEffect } from 'react';
import { Menu, ChevronLeft, ChevronRight, Briefcase, GraduationCap, Moon, Sun } from 'lucide-react';
import { ChatModelProfileProvider } from '../context/ChatModelProfileContext';
import { PortfolioTab } from './PortfolioTab';
import { AcademicTab } from './academic/AcademicTab';
import './Layout.css';

type TabId = 'portfolio' | 'academic';
type Theme = 'light' | 'dark';

function LayoutShell() {
  const [activeTab, setActiveTab] = useState<TabId>('portfolio');
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(false);
  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false);
  
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

  const renderTab = () => {
    switch (activeTab) {
      case 'portfolio':
        return <PortfolioTab />;
      case 'academic':
        return <AcademicTab />;
      default:
        return <PortfolioTab />;
    }
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
            onClick={() => {
              setActiveTab('portfolio');
              setIsMobileMenuOpen(false);
            }}
          >
            <span className="nav-icon"><Briefcase size={18} /></span>
            <span className="nav-text">Portfolio</span>
          </button>
          <button
            className={`nav-item ${activeTab === 'academic' ? 'active' : ''}`}
            onClick={() => {
              setActiveTab('academic');
              setIsMobileMenuOpen(false);
            }}
          >
            <span className="nav-icon"><GraduationCap size={18} /></span>
            <span className="nav-text">Academic</span>
          </button>
        </nav>
        <div className="sidebar-footer">
          <button className="theme-toggle" onClick={toggleTheme} title="Toggle Theme">
            <span className="nav-icon">{theme === 'light' ? <Moon size={18} /> : <Sun size={18} />}</span>
            <span className="theme-text">{theme === 'light' ? 'Dark Mode' : 'Light Mode'}</span>
          </button>
        </div>
      </aside>

      {/* Main Content */}
      <main className={`main-content ${isSidebarCollapsed ? 'expanded' : ''}`}>
        {renderTab()}
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
