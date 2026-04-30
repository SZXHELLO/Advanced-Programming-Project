import React from 'react'
import { MessageCircle, Trash2 } from 'lucide-react'
import type { Session } from '../types'

interface Props {
    session: Session
    isActive: boolean
    onClick: () => void
    onDelete: () => void
}

export const SessionItem: React.FC<Props> = ({
    session,
    isActive,
    onClick,
    onDelete
}) => {
    const formatDate = (dateStr: string) => {
        const date = new Date(dateStr || 0)
        if (Number.isNaN(date.getTime())) {
            return '—'
        }
        return date.toLocaleString('zh-CN', {
            year: 'numeric',
            month: '2-digit',
            day: '2-digit',
            hour: '2-digit',
            minute: '2-digit',
            hour12: false
        }).replace(/\//g, '-')
    }

    const handleDelete = (e: React.MouseEvent) => {
        e.stopPropagation()
        if (confirm('确定删除此会话？此操作无法撤销。')) {
            onDelete()
        }
    }

    return (
        <div
            onClick={onClick}
            className={`group flex items-center gap-3 p-3 rounded-lg cursor-pointer transition-all ${isActive
                    ? 'bg-primary-50 border-l-4 border-primary-500'
                    : 'hover:bg-gray-50'
                }`}
        >
            <MessageCircle size={18} className="text-gray-400 flex-shrink-0" />

            <div className="flex-1 min-w-0">
                <div className="text-sm font-medium text-gray-900 truncate">
                    会话 {(session.id || session.session_key || '?').slice(0, 8)}
                </div>
                <div className="text-xs text-gray-500">
                    {formatDate(session.updated_at)}
                </div>
            </div>

            <button
                onClick={handleDelete}
                className="opacity-0 group-hover:opacity-100 p-1.5 rounded-md hover:bg-red-50 text-gray-400 hover:text-red-500 transition-all"
            >
                <Trash2 size={16} />
            </button>
        </div>
    )
}