import { TabProvider } from './store/TabContext';
import { Layout } from './components/Layout';

function App() {
  return (
    <TabProvider>
      <Layout />
    </TabProvider>
  );
}

export default App;
