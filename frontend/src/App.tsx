import { TabProvider } from './store/TabContext';
import { Layout } from './components/Layout';
import { ToastHost } from './components/ToastHost';

function App() {
  return (
    <TabProvider>
      <Layout />
      <ToastHost />
    </TabProvider>
  );
}

export default App;
