import React, { createContext, useContext, useCallback, useState } from 'react';
import { TabState } from '../types';

interface TabContextType {
  getTabState: (tabId: string) => TabState | undefined;
  setTabState: (tabId: string, state: Partial<TabState>) => void;
  clearTabState: (tabId: string) => void;
}

const TabContext = createContext<TabContextType | undefined>(undefined);

const STORAGE_KEY_PREFIX = 'vc_tab_state_';

const defaultTabState: TabState = {
  viewMode: 'grid',
  scrollPosition: 0,
  searchQuery: '',
};

export function TabProvider({ children }: { children: React.ReactNode }) {
  const [states, setStates] = useState<Record<string, TabState>>(() => {
    // Load all saved states from sessionStorage
    const saved: Record<string, TabState> = {};
    for (let i = 0; i < sessionStorage.length; i++) {
      const key = sessionStorage.key(i);
      if (key?.startsWith(STORAGE_KEY_PREFIX)) {
        const tabId = key.replace(STORAGE_KEY_PREFIX, '');
        try {
          saved[tabId] = JSON.parse(sessionStorage.getItem(key) || '{}');
        } catch {
          // Ignore invalid JSON
        }
      }
    }
    return saved;
  });

  const getTabState = useCallback((tabId: string): TabState => {
    return states[tabId] || { ...defaultTabState };
  }, [states]);

  const setTabState = useCallback((tabId: string, newState: Partial<TabState>) => {
    setStates(prev => {
      const current = prev[tabId] || { ...defaultTabState };
      const updated = { ...current, ...newState };
      
      // Persist to sessionStorage
      sessionStorage.setItem(
        `${STORAGE_KEY_PREFIX}${tabId}`,
        JSON.stringify(updated)
      );
      
      return { ...prev, [tabId]: updated };
    });
  }, []);

  const clearTabState = useCallback((tabId: string) => {
    setStates(prev => {
      const newStates = { ...prev };
      delete newStates[tabId];
      sessionStorage.removeItem(`${STORAGE_KEY_PREFIX}${tabId}`);
      return newStates;
    });
  }, []);

  return (
    <TabContext.Provider value={{ getTabState, setTabState, clearTabState }}>
      {children}
    </TabContext.Provider>
  );
}

export function useTabContext() {
  const context = useContext(TabContext);
  if (!context) {
    throw new Error('useTabContext must be used within TabProvider');
  }
  return context;
}
