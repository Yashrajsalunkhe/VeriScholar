import { NavLink } from 'react-router-dom';
import {
  LayoutDashboard,
  FileSearch,
  Library,
  BookOpen,
  Settings,
  GraduationCap,
} from 'lucide-react';

export default function Sidebar() {
  return (
    <aside className="sidebar">
      <div className="sidebar-brand">
        <h1>VeriScholar AI</h1>
        <div className="tagline">Research Plagiarism Detection</div>
      </div>

      <nav className="sidebar-nav">
        <span className="nav-section-label">Analysis</span>
        <NavLink to="/" className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`} end>
          <LayoutDashboard /> Dashboard
        </NavLink>
        <NavLink to="/check" className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}>
          <FileSearch /> New Check
        </NavLink>
        <NavLink to="/history" className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}>
          <BookOpen /> Check History
        </NavLink>

        <span className="nav-section-label">Corpus</span>
        <NavLink to="/corpus" className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}>
          <Library /> Corpus Manager
        </NavLink>
        <NavLink to="/scholarly" className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}>
          <GraduationCap /> Scholarly Ingest
        </NavLink>

        <div style={{ flex: 1 }} />
        <NavLink to="/settings" className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}>
          <Settings /> Settings
        </NavLink>
      </nav>
    </aside>
  );
}
