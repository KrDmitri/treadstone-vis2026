import { useState, useEffect } from 'react'
import axios from 'axios'
import { getClientId } from '../utils/clientId'

/**
 * Agent Status Row - Slack/Discord style agent status indicators
 * Shows working/idle status for each agent role
 */

function AgentStatusRow({ typingAgents = {}, typingNewPost = null, clientId: propClientId }) {
  const clientId = propClientId || getClientId()
  const [hoveredAgent, setHoveredAgent] = useState(null)
  const [agents, setAgents] = useState([])
  const [allAgents, setAllAgents] = useState([]) // Store ALL agents for name lookup

  // Fetch agents to get names for each role
  useEffect(() => {
    const fetchAgents = async () => {
      try {
        const response = await axios.get(`/api/agents?client_id=${encodeURIComponent(clientId)}`)
        const agentList = response.data.agents || []

        // Store ALL agents for name-to-role lookup
        setAllAgents(agentList)

        // Get first (default) agent for each role for display
        const roleMap = {}
        agentList.forEach(agent => {
          if (!roleMap[agent.role] && agent.is_default) {
            roleMap[agent.role] = agent
          }
        })

        setAgents(Object.values(roleMap))
      } catch (err) {
        console.error('Failed to fetch agents:', err)
      }
    }

    fetchAgents()

    // Refetch every 30 seconds to detect new agents
    const interval = setInterval(fetchAgents, 30000)
    return () => clearInterval(interval)
  }, [clientId])

  // Map agent name to role by looking up in allAgents
  const getAgentRole = (agentName) => {
    const agent = allAgents.find(a => a.name === agentName)
    return agent ? agent.role : null
  }

  // Check if a role is currently working
  const getAgentStatus = (role) => {
    // Check new post creation
    if (typingNewPost?.agent) {
      // Use role from WebSocket message directly if available, fallback to name lookup
      const agentRole = typingNewPost.role || getAgentRole(typingNewPost.agent)
      if (agentRole === role) {
        return { working: true, task: 'Creating new insight...' }
      }
    }

    // Check replies
    for (const [postId, data] of Object.entries(typingAgents)) {
      if (data?.agent) {
        // Use role from WebSocket message directly if available, fallback to name lookup
        const agentRole = data.role || getAgentRole(data.agent)
        if (agentRole === role) {
          return { working: true, task: 'Analyzing...' }
        }
      }
    }

    return { working: false, task: null }
  }

  if (agents.length === 0) return null

  return (
    <div className="flex items-center gap-1">
      {agents.map((agent) => {
        const status = getAgentStatus(agent.role)
        const isHovered = hoveredAgent === agent.role

        return (
          <div
            key={agent.role}
            className="relative"
            onMouseEnter={() => setHoveredAgent(agent.role)}
            onMouseLeave={() => setHoveredAgent(null)}
          >
            <div
              className={`
                relative w-9 h-9 flex items-center justify-center rounded-full 
                cursor-pointer transition-all duration-300
                ${status.working
                  ? 'bg-gradient-to-br from-green-100 to-emerald-100 ring-2 ring-green-400 ring-offset-1'
                  : 'bg-gray-100 hover:bg-gray-200'
                }
              `}
            >
              <span className={`text-lg ${status.working ? 'animate-pulse' : ''}`}>
                {agent.icon}
              </span>

              {status.working && (
                <>
                  <span className="absolute -bottom-0.5 -right-0.5 w-3 h-3 rounded-full border-2 border-white bg-green-500 animate-pulse" />
                  <span className="absolute inset-0 rounded-full border-2 border-green-400 animate-ping opacity-30" />
                </>
              )}
            </div>

            {isHovered && (
              <div className="absolute top-full left-1/2 -translate-x-1/2 mt-2 z-50">
                <div className="bg-gray-900 text-white text-xs rounded-lg px-3 py-2 whitespace-nowrap shadow-lg">
                  <div className="absolute -top-1 left-1/2 -translate-x-1/2 w-2 h-2 bg-gray-900 rotate-45" />
                  <div className="relative">
                    <div className="font-semibold">{agent.name}</div>
                    <div className={`mt-0.5 flex items-center gap-1.5 ${status.working ? 'text-green-400' : 'text-gray-400'}`}>
                      <span className={`w-1.5 h-1.5 rounded-full ${status.working ? 'bg-green-400' : 'bg-gray-500'}`} />
                      {status.working ? status.task : 'Idle'}
                    </div>
                  </div>
                </div>
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

export default AgentStatusRow
