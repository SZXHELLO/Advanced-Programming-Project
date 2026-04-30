import type { Session, SubAgent, ModelConfig, WebSocketStatus } from '../types'

const API_BASE = '/api'

export const api = {
    // 配置相关
    async getConfig(): Promise<ModelConfig> {
        const res = await fetch(`${API_BASE}/config`)
        if (!res.ok) throw new Error('Failed to get config')
        return res.json()
    },

    async getWebSocketStatus(): Promise<WebSocketStatus> {
        const res = await fetch(`${API_BASE}/websocket-status`)
        if (!res.ok) throw new Error('Failed to get websocket status')
        return res.json()
    },

    async saveConfig(config: ModelConfig): Promise<void> {
        const res = await fetch(`${API_BASE}/config`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(config)
        })
        if (!res.ok) throw new Error('Failed to save config')
    },

    // 会话相关
    async getSessions(): Promise<Session[]> {
        const res = await fetch(`${API_BASE}/sessions`)
        if (!res.ok) throw new Error('Failed to get sessions')
        return res.json()
    },

    async deleteSession(id: string): Promise<void> {
        const res = await fetch(`${API_BASE}/sessions/${id}`, {
            method: 'DELETE'
        })
        if (!res.ok) throw new Error('Failed to delete session')
    },

    // 子 Agent 相关
    async getSubAgents(): Promise<SubAgent[]> {
        const res = await fetch(`${API_BASE}/subagents`)
        if (!res.ok) throw new Error('Failed to get subagents')
        return res.json()
    },

    async createSubAgent(data: { label: string; task: string }): Promise<SubAgent> {
        const res = await fetch(`${API_BASE}/subagents`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        })
        if (!res.ok) throw new Error('Failed to create subagent')
        return res.json()
    },

    async updateSubAgent(id: string, data: Partial<SubAgent>): Promise<void> {
        const res = await fetch(`${API_BASE}/subagents/${id}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        })
        if (!res.ok) throw new Error('Failed to update subagent')
    },

    async deleteSubAgent(id: string): Promise<void> {
        const res = await fetch(`${API_BASE}/subagents/${id}`, {
            method: 'DELETE'
        })
        if (!res.ok) throw new Error('Failed to delete subagent')
    }
}