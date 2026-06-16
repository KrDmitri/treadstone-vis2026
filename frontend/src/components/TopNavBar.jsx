function TopNavBar({ activeView, onViewChange }) {
  const navItems = [
    { name: 'Feed', view: 'feed', icon: '📱' },
    { name: 'Data', view: 'data', icon: '📊' },
    { name: 'Branch', view: 'branch', icon: '🔀' },
    { name: 'Settings', view: 'settings', icon: '⚙️' },
  ]

  return (
    <div className="bg-white border-b border-gray-200 px-6 py-2 flex items-center gap-6 flex-shrink-0">
      <h1 className="text-lg font-bold text-blue-600 mr-4">Treadstone</h1>
      <nav className="flex items-center gap-1">
        {navItems.map((item) => (
          <button
            key={item.view}
            onClick={() => onViewChange(item.view)}
            className={`flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
              activeView === item.view
                ? 'bg-blue-50 text-blue-600'
                : 'text-gray-600 hover:bg-gray-50 hover:text-gray-800'
            }`}
          >
            <span>{item.icon}</span>
            <span>{item.name}</span>
          </button>
        ))}
      </nav>
    </div>
  )
}

export default TopNavBar
