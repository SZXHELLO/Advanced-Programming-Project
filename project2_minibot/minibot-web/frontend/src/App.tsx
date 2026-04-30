import React from 'react'
import { BrowserRouter, Routes, Route, Link, useLocation } from 'react-router-dom'
import { MessageSquare, Bot } from 'lucide-react'
import { Dashboard } from './pages/Dashboard'
import { AgentsBoard } from './pages/AgentsBoard'

const Navigation: React.FC = () => {
  const location = useLocation()

  const isActive = (path: string) => location.pathname === path

  return (
    <nav className="shrink-0 bg-white border-b border-gray-200">
      <div className="flex">
        <Link
          to="/"
          className={`flex items-center gap-2 px-6 py-4 border-b-2 transition-colors ${isActive('/')
              ? 'border-primary-500 text-primary-600'
              : 'border-transparent text-gray-600 hover:text-gray-900'
            }`}
        >
          <MessageSquare size={20} />
          <span className="font-medium">Dashboard</span>
        </Link>

        <Link
          to="/agents"
          className={`flex items-center gap-2 px-6 py-4 border-b-2 transition-colors ${isActive('/agents')
              ? 'border-primary-500 text-primary-600'
              : 'border-transparent text-gray-600 hover:text-gray-900'
            }`}
        >
          <Bot size={20} />
          <span className="font-medium">Agents Board</span>
        </Link>
      </div>
    </nav>
  )
}

function App() {
  return (
    <BrowserRouter>
      <div className="flex h-screen min-h-0 flex-col">
        <Navigation />
        <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/agents" element={<AgentsBoard />} />
          </Routes>
        </div>
      </div>
    </BrowserRouter>
  )
}

export default App