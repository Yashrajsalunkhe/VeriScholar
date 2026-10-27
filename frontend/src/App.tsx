import { BrowserRouter, Routes, Route } from 'react-router-dom';
import Sidebar from './components/Sidebar';
import Dashboard from './pages/Dashboard';
import NewCheck from './pages/NewCheck';
import History from './pages/History';
import Report from './pages/Report';
import CorpusManager from './pages/CorpusManager';
import ScholarlyIngest from './pages/ScholarlyIngest';
import Settings from './pages/Settings';

export default function App() {
  return (
    <BrowserRouter>
      <div className="app-layout">
        <Sidebar />
        <main className="main-content">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/check" element={<NewCheck />} />
            <Route path="/history" element={<History />} />
            <Route path="/report/:id" element={<Report />} />
            <Route path="/corpus" element={<CorpusManager />} />
            <Route path="/scholarly" element={<ScholarlyIngest />} />
            <Route path="/settings" element={<Settings />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  );
}
