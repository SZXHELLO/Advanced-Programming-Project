import React from 'react'
import { Bot, Edit, Trash2 } from 'lucide-react'
import type { SubAgent } from '../types'

interface Props {
    agent: SubAgent
    onClick: () => void
    onDelete: () => void
}

const statusColors = {
    standby: 'bg-gray-100 text-gray-700',
    running: 'bg-blue-100 text-blue-700',
    interrupted: 'bg-yellow-100 text-yellow-700',
    completed: 'bg-green-100 text-green-700'
}

const statusLabels = {
    standby: '待机',
    running: '运行中',
    interrupted: '已中断',
    completed: '已完成'
}

export const SubAgentCard: React.FC<Props> = ({ agent, onClick, onDelete }) => {
    const handleDelete = (e: React.MouseEvent) => {
        e.stopPropagation()
        if (confirm(`确定删除 Agent "${agent.label}"？`)) {
            onDelete()
        }
    }

    return (
        <div
            onClick={onClick}
            className="group bg-white rounded-xl border border-gray-200 p-5 hover:shadow-lg hover:border-primary-300 transition-all cursor-pointer"
        >
            <div className="flex items-start justify-between mb-3">
                <div className="flex items-center gap-3">
                    <div className="w-10 h-10 bg-primary-100 rounded-lg flex items-center justify-center">
                        <Bot size={20} className="text-primary-600" />
                    </div>
                    <div>
                        <h3 className="font-semibold text-gray-900">{agent.label}</h3>
                        <span className={`inline-block px-2 py-0.5 rounded-full text-xs font-medium mt-1 ${statusColors[agent.status]}`}>
                            {statusLabels[agent.status]}
                        </span>
                    </div>
                </div>

                <div className="flex gap-2 opacity-0 group-hover:opacity-100 transition-opacity">
                    <button
                        onClick={onClick}
                        className="p-2 hover:bg-primary-50 rounded-lg text-primary-600 transition-colors"
                    >
                        <Edit size={16} />
                    </button>
                    <button
                        onClick={handleDelete}
                        className="p-2 hover:bg-red-50 rounded-lg text-red-600 transition-colors"
                    >
                        <Trash2 size={16} />
                    </button>
                </div>
            </div>

            <p className="text-sm text-gray-600 line-clamp-2">
                {agent.task}
            </p>

            {agent.id && (
                <div className="mt-3 pt-3 border-t border-gray-100">
                    <span className="text-xs text-gray-400 font-mono">ID: {agent.id.slice(0, 12)}...</span>
                </div>
            )}
        </div>
    )
}