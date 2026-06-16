import { useState, useEffect } from 'react'
import axios from 'axios'

const AGENT_INFO = {
  'scanner': {
    icon: '🔍',
    name: 'Data Scout Agent',
    role: 'Proactive Data Explorer',
    description: 'Automatically scans datasets and initiates discussions with interesting findings',
    color: 'bg-purple-50 border-purple-200 text-purple-700'
  },
  'statistics': {
    icon: '📊',
    name: 'Statistical Analyst Agent',
    role: 'Data Analysis Expert',
    description: 'Performs statistical analysis, answers queries about trends, patterns, and distributions',
    color: 'bg-blue-50 border-blue-200 text-blue-700'
  },
  'visualization': {
    icon: '🎨',
    name: 'Visualization Expert Agent',
    role: 'Chart & Graph Creator',
    description: 'Creates Vega-Lite visualizations to help understand data patterns visually',
    color: 'bg-green-50 border-green-200 text-green-700'
  },
  'insight': {
    icon: '💡',
    name: 'Intelligence Agent',
    role: 'Strategic Advisor',
    description: 'Provides actionable intelligence insights and strategic recommendations based on data',
    color: 'bg-yellow-50 border-yellow-200 text-yellow-700'
  },
  'summary': {
    icon: '📝',
    name: 'Summary Agent',
    role: 'Narrative Synthesizer',
    description: 'Synthesizes findings from multiple agents into cohesive narratives and stories',
    color: 'bg-orange-50 border-orange-200 text-orange-700'
  }
}

const MODEL_OPTIONS = [
  { value: 'gpt-5.2', label: 'GPT-5.2 (Latest)', description: 'Newest model with enhanced reasoning capabilities' },
  { value: 'gpt-5.1', label: 'GPT-5.1', description: 'Previous GPT-5 generation' },
  { value: 'gpt-4o', label: 'GPT-4o', description: 'Previous generation, highly capable' },
  { value: 'gpt-4o-mini', label: 'GPT-4o Mini', description: 'Fast and efficient for most tasks' },
  { value: 'gpt-4-turbo', label: 'GPT-4 Turbo', description: 'Previous generation, still powerful' },
  { value: 'gpt-3.5-turbo', label: 'GPT-3.5 Turbo', description: 'Budget-friendly, good for simple tasks' }
]

function AgentCard({ agentKey, config, onModelChange, saving }) {
  const info = AGENT_INFO[agentKey]
  const [localModel, setLocalModel] = useState(config?.model || 'gpt-5.2')
  const [changed, setChanged] = useState(false)

  const handleModelChange = (e) => {
    const newModel = e.target.value
    setLocalModel(newModel)
    setChanged(newModel !== config?.model)
  }

  const handleSave = async () => {
    await onModelChange(agentKey, localModel)
    setChanged(false)
  }

  if (!info) return null

  return (
    <div className={`rounded-lg shadow-md border-2 ${info.color} p-6 transition-all hover:shadow-lg`}>
      {/* Header */}
      <div className="flex items-start space-x-4 mb-4">
        <div className="text-5xl">{info.icon}</div>
        <div className="flex-1">
          <h3 className="text-xl font-bold text-gray-800">{info.name}</h3>
          <p className="text-sm font-medium text-gray-600">{info.role}</p>
        </div>
      </div>

      {/* Description */}
      <p className="text-sm text-gray-700 mb-4 leading-relaxed">
        {info.description}
      </p>

      {/* Model Selection */}
      <div className="space-y-3">
        <label className="block text-sm font-semibold text-gray-700">
          LLM Model
        </label>
        <select
          value={localModel}
          onChange={handleModelChange}
          disabled={saving}
          className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent bg-white disabled:bg-gray-100 disabled:cursor-not-allowed"
        >
          {MODEL_OPTIONS.map(option => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>

        {/* Model Description */}
        <p className="text-xs text-gray-500 italic">
          {MODEL_OPTIONS.find(opt => opt.value === localModel)?.description}
        </p>

        {/* Save Button */}
        {changed && (
          <button
            onClick={handleSave}
            disabled={saving}
            className="w-full px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium transition-colors disabled:bg-gray-400"
          >
            {saving ? 'Saving...' : 'Save Changes'}
          </button>
        )}
      </div>
    </div>
  )
}

function AgentsView() {
  const [agentConfigs, setAgentConfigs] = useState({})
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [saveMessage, setSaveMessage] = useState(null)

  useEffect(() => {
    fetchAgentConfigs()
  }, [])

  const fetchAgentConfigs = async () => {
    try {
      setLoading(true)
      const response = await axios.get('/api/agent/config')
      setAgentConfigs(response.data)
    } catch (error) {
      console.error('Failed to fetch agent configs:', error)
    } finally {
      setLoading(false)
    }
  }

  const handleModelChange = async (agentKey, newModel) => {
    try {
      setSaving(true)
      await axios.put('/api/agent/config', {
        agent_key: agentKey,
        model: newModel
      })

      // Update local state
      setAgentConfigs(prev => ({
        ...prev,
        [agentKey]: { ...prev[agentKey], model: newModel }
      }))

      // Show success message
      setSaveMessage({ type: 'success', text: 'Model updated successfully!' })
      setTimeout(() => setSaveMessage(null), 3000)

    } catch (error) {
      console.error('Failed to update agent config:', error)
      setSaveMessage({ type: 'error', text: 'Failed to update model. Please try again.' })
      setTimeout(() => setSaveMessage(null), 3000)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="h-full flex flex-col overflow-hidden">
      {/* Header */}
      <div className="bg-white border-b border-gray-200 px-6 py-4 flex-shrink-0">
        <h2 className="text-xl font-semibold text-gray-800">🤖 AI Agents</h2>
        <p className="text-sm text-gray-500">Configure LLM models for optimal performance</p>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-6">
        {/* Save Message */}
        {saveMessage && (
          <div className={`mb-6 p-4 rounded-lg ${saveMessage.type === 'success'
              ? 'bg-green-50 border border-green-200 text-green-700'
              : 'bg-red-50 border border-red-200 text-red-700'
            }`}>
            {saveMessage.text}
          </div>
        )}

        {/* Loading State */}
        {loading ? (
          <div className="text-center py-16">
            <div className="text-4xl mb-4">⏳</div>
            <p className="text-gray-500">Loading agent configurations...</p>
          </div>
        ) : (
          <>
            {/* Agent Cards Grid */}
            <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-6 mb-8">
              {Object.keys(AGENT_INFO).map(agentKey => (
                <AgentCard
                  key={agentKey}
                  agentKey={agentKey}
                  config={agentConfigs[agentKey]}
                  onModelChange={handleModelChange}
                  saving={saving}
                />
              ))}
            </div>

            {/* Info Card */}
            <div className="bg-blue-50 border border-blue-200 rounded-lg p-6">
              <div className="flex items-start space-x-3">
                <div className="text-2xl">ℹ️</div>
                <div className="flex-1">
                  <h3 className="text-lg font-semibold text-blue-900 mb-2">
                    Model Selection Tips
                  </h3>
                  <ul className="text-sm text-blue-800 space-y-1">
                    <li>• <strong>GPT-4o:</strong> Best for complex analysis and insights (Default)</li>
                    <li>• <strong>GPT-4o Mini:</strong> Faster and cheaper, great for most tasks</li>
                    <li>• <strong>GPT-4 Turbo:</strong> Previous generation, still reliable</li>
                    <li>• <strong>GPT-3.5 Turbo:</strong> Budget option for simple queries</li>
                  </ul>
                  <p className="text-xs text-blue-700 mt-3 italic">
                    💡 Tip: Start with GPT-4o for all agents, then adjust based on your needs and budget.
                  </p>
                </div>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

export default AgentsView
