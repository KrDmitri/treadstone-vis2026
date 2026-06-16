import { Link } from 'react-router-dom'

function LeftSidebar({ activeView, onViewChange }) {
  const navItems = [
    { name: 'Feed', view: 'feed', icon: '📱' },
    { name: 'Data', view: 'data', icon: '📊' },
    { name: 'Branch', view: 'branch', icon: '🔀' },
    // { name: 'Agents', view: 'agents', icon: '' },
    // { name: 'Timeline', view: 'timeline', icon: '' },
    { name: 'Settings', view: 'settings', icon: '⚙️' },
  ]

  const externalLinks = [
  ]

  return (
    <div className="w-64 bg-white border-r border-gray-200 flex flex-col">
      {/* Logo / Header */}
      <div className="p-6 border-b border-gray-200">
        <h1 className="text-2xl font-bold text-blue-600">Treadstone</h1>
        <p className="text-xs text-gray-500 mt-1">AI Agent Collaboration</p>
      </div>

      {/* Navigation */}
      <nav className="flex-1 p-4">
        <ul className="space-y-2">
          {navItems.map((item) => (
            <li key={item.view}>
              <button
                onClick={() => onViewChange(item.view)}
                className={`w-full flex items-center space-x-3 px-4 py-3 rounded-lg transition-colors ${activeView === item.view
                  ? 'bg-blue-50 text-blue-600 font-medium'
                  : 'text-gray-700 hover:bg-gray-50'
                  }`}
              >
                <span className="text-xl">{item.icon}</span>
                <span>{item.name}</span>
              </button>
            </li>
          ))}

          <div className="pt-2 mt-2 border-t border-gray-200">
            {externalLinks.map((item) => (
              <li key={item.path}>
                <Link
                  to={item.path}
                  className="flex items-center space-x-3 px-4 py-3 rounded-lg transition-colors text-gray-700 hover:bg-gray-50"
                >
                  <span className="text-xl">{item.icon}</span>
                  <span>{item.name}</span>
                </Link>
              </li>
            ))}
          </div>
        </ul>
      </nav>
    </div>
  )
}

export default LeftSidebar
