import { useState, useEffect } from 'react';
import './AgentManagementModal.css';
import { getClientId } from '../utils/clientId';

export default function AgentManagementModal({ isOpen, onClose, clientId: propClientId }) {
    // Use prop clientId if provided, otherwise get from utility
    const clientId = propClientId || getClientId();

    const [agents, setAgents] = useState([]);
    const [availableRoles, setAvailableRoles] = useState([]);
    const [loading, setLoading] = useState(true);
    const [editingId, setEditingId] = useState(null);
    const [editName, setEditName] = useState('');
    const [showAddForm, setShowAddForm] = useState(false);
    const [newAgent, setNewAgent] = useState({ role: '', name: '' });

    useEffect(() => {
        if (isOpen) {
            fetchAgents();
        }
    }, [isOpen]);

    const fetchAgents = async () => {
        try {
            setLoading(true);
            const response = await fetch(`/api/agents?client_id=${encodeURIComponent(clientId)}`);
            const data = await response.json();
            setAgents(data.agents || []);
            setAvailableRoles(data.available_roles || []);
        } catch (err) {
            console.error('Failed to load agents:', err);
        } finally {
            setLoading(false);
        }
    };

    const handleStartEdit = (agent) => {
        setEditingId(agent.id);
        setEditName(agent.name);
    };

    const handleSaveName = async (agentId) => {
        if (!editName.trim()) {
            setEditingId(null);
            return;
        }

        try {
            await fetch(`/api/agents/${agentId}?client_id=${encodeURIComponent(clientId)}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: editName.trim() })
            });
            setEditingId(null);
            fetchAgents();
        } catch (err) {
            console.error('Failed to update name:', err);
        }
    };

    const handleKeyDown = (e, agentId) => {
        if (e.key === 'Enter') handleSaveName(agentId);
        else if (e.key === 'Escape') setEditingId(null);
    };

    const handleCreateAgent = async () => {
        if (!newAgent.role || !newAgent.name.trim()) return;

        try {
            await fetch(`/api/agents?client_id=${encodeURIComponent(clientId)}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(newAgent)
            });
            setShowAddForm(false);
            setNewAgent({ role: '', name: '' });
            fetchAgents();
        } catch (err) {
            console.error('Failed to create agent:', err);
        }
    };

    const handleDeleteAgent = async (agentId) => {
        try {
            await fetch(`/api/agents/${agentId}?client_id=${encodeURIComponent(clientId)}`, { method: 'DELETE' });
            fetchAgents();
        } catch (err) {
            console.error('Failed to delete agent:', err);
        }
    };

    // Group agents by role
    const groupedAgents = agents.reduce((acc, agent) => {
        if (!acc[agent.role]) acc[agent.role] = [];
        acc[agent.role].push(agent);
        return acc;
    }, {});

    const canAddAny = availableRoles.some(r => r.can_add);

    if (!isOpen) return null;

    return (
        <div className="agent-modal-overlay" onClick={onClose}>
            <div className="agent-modal" onClick={e => e.stopPropagation()}>
                <div className="agent-modal-header">
                    <h2>🤖 Agent Settings</h2>
                    <button className="close-btn" onClick={onClose}>×</button>
                </div>

                <div className="agent-modal-content">
                    {loading ? (
                        <div className="loading">Loading agents...</div>
                    ) : (
                        <>
                            {/* Agents grouped by role */}
                            {Object.entries(groupedAgents).map(([role, roleAgents]) => (
                                <div key={role} className="role-group">
                                    <div className="role-header">
                                        <span>{roleAgents[0]?.icon} {role}</span>
                                        <span className="role-count">{roleAgents.length}/2</span>
                                    </div>

                                    {roleAgents.map(agent => (
                                        <div key={agent.id} className="agent-item">
                                            {editingId === agent.id ? (
                                                <input
                                                    type="text"
                                                    className="edit-name-input"
                                                    value={editName}
                                                    onChange={e => setEditName(e.target.value)}
                                                    onBlur={() => handleSaveName(agent.id)}
                                                    onKeyDown={e => handleKeyDown(e, agent.id)}
                                                    autoFocus
                                                    maxLength={30}
                                                />
                                            ) : (
                                                <span
                                                    className="agent-name"
                                                    onClick={() => handleStartEdit(agent)}
                                                    title="Click to edit"
                                                >
                                                    {agent.name}
                                                    <span className="edit-hint">✏️</span>
                                                </span>
                                            )}

                                            {agent.is_default ? (
                                                <span className="default-badge">Default</span>
                                            ) : (
                                                <button
                                                    className="delete-btn"
                                                    onClick={() => handleDeleteAgent(agent.id)}
                                                    title="Delete agent"
                                                >
                                                    🗑️
                                                </button>
                                            )}
                                        </div>
                                    ))}
                                </div>
                            ))}

                            {/* Add new agent form */}
                            {showAddForm ? (
                                <div className="add-form">
                                    <select
                                        value={newAgent.role}
                                        onChange={e => setNewAgent({ ...newAgent, role: e.target.value })}
                                    >
                                        <option value="">Select role...</option>
                                        {availableRoles.filter(r => r.can_add).map(r => (
                                            <option key={r.role} value={r.role}>
                                                {r.icon} {r.role}
                                            </option>
                                        ))}
                                    </select>
                                    <input
                                        type="text"
                                        placeholder="Agent name..."
                                        value={newAgent.name}
                                        onChange={e => setNewAgent({ ...newAgent, name: e.target.value })}
                                        maxLength={30}
                                    />
                                    <div className="form-actions">
                                        <button onClick={handleCreateAgent} className="create-btn">Create</button>
                                        <button onClick={() => setShowAddForm(false)} className="cancel-btn">Cancel</button>
                                    </div>
                                </div>
                            ) : canAddAny && (
                                <button className="add-agent-btn" onClick={() => setShowAddForm(true)}>
                                    + Add Agent
                                </button>
                            )}
                        </>
                    )}

                    <p className="modal-note">
                        Max 2 agents per role. Names reset on server restart.
                    </p>
                </div>
            </div>
        </div>
    );
}
