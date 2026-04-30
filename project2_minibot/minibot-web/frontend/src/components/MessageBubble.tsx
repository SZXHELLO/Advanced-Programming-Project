import React from 'react'
import { User, Bot } from 'lucide-react'
import type { Message } from '../types'

interface Props {
    message: Message
}

export const MessageBubble: React.FC<Props> = ({ message }) => {
    if (!message.content.trim()) {
        return null
    }
    const isUser = message.role === 'user'

    return (
        <div className={`flex gap-3 mb-4 ${isUser ? 'flex-row-reverse' : ''}`}>
            <div className={`flex-shrink-0 w-8 h-8 rounded-full flex items-center justify-center ${isUser ? 'bg-primary-500 text-white' : 'bg-gray-200 text-gray-700'
                }`}>
                {isUser ? <User size={18} /> : <Bot size={18} />}
            </div>

            <div className={`max-w-[70%] rounded-2xl px-4 py-2.5 ${isUser
                    ? 'bg-primary-500 text-white'
                    : 'bg-gray-100 text-gray-800'
                }`}>
                <div className="whitespace-pre-wrap break-words">
                    {message.content}
                </div>
                <div className={`text-xs mt-1 ${isUser ? 'text-primary-100' : 'text-gray-500'
                    }`}>
                    {new Date(message.timestamp).toLocaleTimeString('zh-CN', {
                        hour: '2-digit',
                        minute: '2-digit'
                    })}
                </div>
            </div>
        </div>
    )
}