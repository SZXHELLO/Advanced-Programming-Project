import React, { useState, useEffect } from 'react'
import { Plus, X, Save } from 'lucide-react'
import { SubAgentCard } from '../components/SubAgentCard'
import { api } from '../services/api'
import type { SubAgent } from '../types'

export const AgentsBoard: React.FC = () => {
    const [agents, setAgents] = useState<SubAgent[]>([])
    const [showModal, setShowModal] = useState(false)
    const [editingAgent, setEditingAgent] = useState<SubAgent | null>(null)
    const [formData, setFormData] = useState({ label: '', task: '' })

    useEffect(() => {
        loadAgents()
    }, [])

    const loadAgents = async () => {
        try {
            const data = await api.getSubAgents()
            setAgents(data)
        } catch (err) {
            alert('加载失败：' + (err as Error).message)
        }
    }

    const handleCreate = () => {
        setEditingAgent(null)
        setFormData({ label: '', task: '' })
        setShowModal(true)
    }

    const handleEdit = (agent: SubAgent) => {
        setEditingAgent(agent)
        setFormData({ label: agent.label, task: agent.task })
        setShowModal(true)
    }

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault()

        try {
            if (editingAgent) {
                await api.updateSubAgent(editingAgent.id, formData)
            } else {
                await api.createSubAgent(formData)
            }
            await loadAgents()
            setShowModal(false)
        } catch (err) {
            alert('保存失败：' + (err as Error).message)
        }
    }

    const handleDelete = async (id: string) => {
        try {
            await api.deleteSubAgent(id)
            setAgents(prev => prev.filter(a => a.id !== id))
        } catch (err) {
            alert('删除失败：' + (err as Error).message)
        }
    }

    return (
        <div className="flex min-h-0 flex-1 flex-col bg-gray-50">
            {/* Header */}
            <div className="shrink-0 bg-white border-b border-gray-200">
                <div className="max-w-7xl mx-auto px-6 py-6">
                    <div className="flex items-center justify-between">
                        <h1 className="text-2xl font-semibold text-gray-900">Agents Board</h1>
                        <button
                            onClick={handleCreate}
                            className="flex items-center gap-2 px-4 py-2.5 bg-primary-500 text-white rounded-lg hover:bg-primary-600 transition-colors"
                        >
                            <Plus size={20} />
                            创建 Agent
                        </button>
                    </div>
                </div>
            </div>

            {/* Grid */}
            <div className="min-h-0 flex-1 overflow-y-auto">
                <div className="max-w-7xl mx-auto px-6 py-8">
                {agents.length === 0 ? (
                    <div className="text-center py-20 text-gray-400">
                        <div className="text-6xl mb-4">🤖</div>
                        <p className="text-lg mb-4">暂无 Agent</p>
                        <button
                            onClick={handleCreate}
                            className="text-primary-500 hover:text-primary-600"
                        >
                            创建第一个 Agent
                        </button>
                    </div>
                ) : (
                    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                        {agents.map(agent => (
                            <SubAgentCard
                                key={agent.id}
                                agent={agent}
                                onClick={() => handleEdit(agent)}
                                onDelete={() => handleDelete(agent.id)}
                            />
                        ))}
                    </div>
                )}
                </div>
            </div>

            {/* Modal */}
            {showModal && (
                <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
                    <div className="bg-white rounded-2xl shadow-2xl max-w-2xl w-full">
                        <div className="flex items-center justify-between p-6 border-b">
                            <h2 className="text-xl font-semibold">
                                {editingAgent ? '编辑 Agent' : '创建 Agent'}
                            </h2>
                            <button
                                onClick={() => setShowModal(false)}
                                className="p-2 hover:bg-gray-100 rounded-lg"
                            >
                                <X size={20} />
                            </button>
                        </div>

                        <form onSubmit={handleSubmit} className="p-6 space-y-4">
                            <div>
                                <label className="block text-sm font-medium text-gray-700 mb-2">
                                    标签
                                </label>
                                <input
                                    type="text"
                                    value={formData.label}
                                    onChange={(e) => setFormData({ ...formData, label: e.target.value })}
                                    className="w-full px-4 py-2.5 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-transparent outline-none"
                                    placeholder="我的助手"
                                    required
                                />
                            </div>

                            <div>
                                <label className="block text-sm font-medium text-gray-700 mb-2">
                                    任务描述
                                </label>
                                <textarea
                                    value={formData.task}
                                    onChange={(e) => setFormData({ ...formData, task: e.target.value })}
                                    className="w-full px-4 py-2.5 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-transparent outline-none resize-none"
                                    rows={6}
                                    placeholder="描述此 Agent 的任务..."
                                    required
                                />
                            </div>

                            {editingAgent && (
                                <div className="pt-4 border-t space-y-2 text-sm text-gray-600">
                                    <div><span className="font-medium">ID:</span> {editingAgent.id}</div>
                                    <div><span className="font-medium">状态:</span> {editingAgent.status}</div>
                                    <div><span className="font-medium">Session Key:</span> {editingAgent.session_key}</div>
                                </div>
                            )}

                            <div className="flex gap-3 pt-4">
                                <button
                                    type="button"
                                    onClick={() => setShowModal(false)}
                                    className="flex-1 px-4 py-2.5 border border-gray-300 rounded-lg hover:bg-gray-50 transition-colors"
                                >
                                    取消
                                </button>
                                <button
                                    type="submit"
                                    className="flex-1 px-4 py-2.5 bg-primary-500 text-white rounded-lg hover:bg-primary-600 transition-colors flex items-center justify-center gap-2"
                                >
                                    <Save size={18} />
                                    保存
                                </button>
                            </div>
                        </form>
                    </div>
                </div>
            )}
        </div>
    )
}