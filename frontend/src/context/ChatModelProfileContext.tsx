import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from 'react';
import type { ChatModelProfileId } from '../types';

const STORAGE_KEY = 'vc_chat_model_profile';

function readStored(): ChatModelProfileId {
  if (typeof window === 'undefined') return 'gemini_google';
  const v = localStorage.getItem(STORAGE_KEY);
  if (v === 'kimi_moonshot' || v === 'gemini_google') return v;
  return 'gemini_google';
}

type ChatModelProfileContextValue = {
  profileId: ChatModelProfileId;
  setProfileId: (id: ChatModelProfileId) => void;
};

const ChatModelProfileContext = createContext<ChatModelProfileContextValue | null>(
  null
);

export function ChatModelProfileProvider({ children }: { children: ReactNode }) {
  const [profileId, setProfileIdState] = useState<ChatModelProfileId>(readStored);

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, profileId);
  }, [profileId]);

  const setProfileId = useCallback((id: ChatModelProfileId) => {
    setProfileIdState(id);
  }, []);

  return (
    <ChatModelProfileContext.Provider value={{ profileId, setProfileId }}>
      {children}
    </ChatModelProfileContext.Provider>
  );
}

export function useChatModelProfile(): ChatModelProfileContextValue {
  const ctx = useContext(ChatModelProfileContext);
  if (!ctx) {
    throw new Error('useChatModelProfile must be used within ChatModelProfileProvider');
  }
  return ctx;
}
