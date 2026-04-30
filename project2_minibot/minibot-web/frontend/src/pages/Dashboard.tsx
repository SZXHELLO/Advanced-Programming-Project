import React, { useState, useEffect, useRef } from 'react'
import { Plus, Send, Settings, Wifi, WifiOff } from 'lucide-react'
import { MessageBubble } from '../components/MessageBubble'
import { SessionItem } from '../components/SessionItem'
import { ModelConfigModal } from '../components/ModelConfigModal'
import { ThinkingCollapsible } from '../components/ThinkingCollapsible'
import { WebSocketClient } from '../services/websocket'
import { api } from '../services/api'
import type {
    Message,
    Session,
    ModelConfig,
    WSMessage,
    WebSocketStatus,
} from '../types'

/** 将磁盘上的会话消息行转成气泡可用的结构（忽略 tool 等非对话角色） */
function normalizeHistoryMessages(rows: unknown): Message[] {
    if (!Array.isArray(rows)) return []
    const out: Message[] = []
    for (const row of rows) {
        if (typeof row !== 'object' || row === null) continue
        const r = row as Record<string, unknown>
        const role = r.role
        if (role !== 'user' && role !== 'assistant') continue
        const raw = r.content
        const content =
            typeof raw === 'string'
                ? raw
                : raw == null
                  ? ''
                  : JSON.stringify(raw)
        if (!content.trim()) continue
        const timestamp =
            typeof r.timestamp === 'string' ? r.timestamp : new Date().toISOString()
        out.push({ role, content, timestamp })
    }
    return out
}

/** 无 kind 字段的旧 gateway：用内容启发式判断是否为 ReAct 进度/Observation */
function isHeuristicProgressText(s: string): boolean {
    const t = s.trimStart()
    if (!t) return false
    if (t.startsWith('Observation')) return true
    if (t.startsWith('[tool]')) return true
    if (t.startsWith('事实对齐:')) return true
    if (t.startsWith('工具 ') && t.includes('连续') && t.includes('失败')) return true
    if (t.startsWith('连续 ') && t.includes('无法解析')) return true
    if (t.startsWith('达到最大循环次数')) return true
    return false
}

export const Dashboard: React.FC = () => {
    const [sessions, setSessions] = useState<Session[]>([])
    const [currentSession, setCurrentSession] = useState<Session | null>(null)
    const [messages, setMessages] = useState<Message[]>([])
    const [input, setInput] = useState('')
    const [isConnected, setIsConnected] = useState(false)
    const [showConfig, setShowConfig] = useState(false)
    const [config, setConfig] = useState<ModelConfig | undefined>()
    const [streamingMessage, setStreamingMessage] = useState('')
    /** ReAct 循环中 Observation / 工具等，折叠展示 */
    const [thinkingLog, setThinkingLog] = useState('')
    const [wsStatus, setWsStatus] = useState<WebSocketStatus | null>(null)

    const wsRef = useRef<WebSocketClient | null>(null)
    const messagesEndRef = useRef<HTMLDivElement>(null)
    const currentChatId = useRef<string>('')

    useEffect(() => {
        loadSessions()
        loadConfig()
        void initWebSocket()

        return () => {
            wsRef.current?.disconnect()
        }
    }, [])

    useEffect(() => {
        messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
    }, [messages, streamingMessage, thinkingLog])

    const loadSessions = async () => {
        try {
            const data = await api.getSessions()
            const list = Array.isArray(data) ? data : []
            const normalized: Session[] = list.map((raw, i) => {
                const r = raw as unknown as Record<string, unknown>
                const key = String(r.key ?? r.session_key ?? r.id ?? '')
                const id = String((r.id ?? key) || `session-${i}`)
                return {
                    id,
                    session_key: String(r.session_key ?? key),
                    created_at: String(r.created_at ?? ''),
                    updated_at: String(r.updated_at ?? r.created_at ?? ''),
                    messages: normalizeHistoryMessages(r.messages),
                }
            })
            setSessions(
                normalized.sort(
                    (a, b) =>
                        new Date(b.updated_at || 0).getTime() -
                        new Date(a.updated_at || 0).getTime(),
                ),
            )
        } catch (err) {
            console.error('Failed to load sessions:', err)
        }
    }

    const loadConfig = async () => {
        try {
            const data = await api.getConfig()
            setConfig(data)
        } catch (err) {
            console.error('Failed to load config:', err)
        }
    }

    const initWebSocket = async () => {
        let connectUrl = 'ws://127.0.0.1:8765/'
        try {
            const st = await api.getWebSocketStatus()
            setWsStatus(st)
            if (st.connectUrl) connectUrl = st.connectUrl
        } catch {
            setWsStatus(null)
        }

        const ws = new WebSocketClient(connectUrl)

        ws.onConnected = (chatId) => {
            setIsConnected(true)
            currentChatId.current = chatId
            console.log('Chat ID:', chatId)
        }

        ws.onDisconnected = () => {
            setIsConnected(false)
        }

        ws.onMessage = (msg: WSMessage) => {
            const fullText = msg.content ?? msg.text ?? ''
            const deltaText = msg.delta ?? msg.text ?? ''

            if (msg.event === 'message' && fullText) {
                const isProgress =
                    msg.kind === 'progress' ||
                    msg.toolHint === true ||
                    isHeuristicProgressText(fullText)
                if (isProgress) {
                    setThinkingLog((prev) =>
                        prev ? `${prev}\n\n${fullText.trimEnd()}` : fullText.trimEnd(),
                    )
                    return
                }
                setStreamingMessage('')
                addAssistantDeduped(fullText)
                return
            }

            if (msg.event === 'delta' && deltaText) {
                setStreamingMessage((prev) => prev + deltaText)
                return
            }

            if (msg.event === 'stream_end') {
                setStreamingMessage((prev) => {
                    if (prev.trim()) {
                        const flushed = prev.trimEnd()
                        setTimeout(() => addAssistantDeduped(flushed), 0)
                    }
                    return ''
                })
            }
        }

        ws.connect()
        wsRef.current = ws
    }

    const wsHintText = (): string | null => {
        if (isConnected) return null
        if (!wsStatus) {
            return '无法从本地 BFF 读取 WebSocket 配置。请确认已在 minibot-web/backend 运行 npm run dev（默认 127.0.0.1:3001），且前端 Vite 已代理 /api。'
        }
        const u = wsStatus.connectUrl
        switch (wsStatus.hintKey) {
            case 'disabled':
                return `配置里未启用 Web 对话通道。请在用户目录下 .minibot/config.json 的 channels.websocket 中设置 "enabled": true，并建议按 minibot 文档 docs/WEBSOCKET.md 加入 "websocketRequiresToken": false；保存后重启 minibot gateway。成功时日志中应出现「WebSocket server listening on ${u}」。当前与 QQ/微信等通道是否开启无关。`
            case 'token_required':
                return `已启用 WebSocket，但当前要求握手 token（websocketRequiresToken 为 true）且未配置 channels.websocket.token。本地使用请在 config 中设置 "websocketRequiresToken": false 后重启 gateway；或配置静态 token 并在连接 URL 使用 ?token=…（见 docs/WEBSOCKET.md）。`
            case 'config_unreadable':
                return '无法读取 ~/.minibot/config.json（BFF 将仍尝试连接默认地址）。请确认本机 BFF 已启动且路径正确。'
            default:
                return `已按配置尝试连接 ${u} 仍失败。请确认 gateway 已用最新配置重启、端口未被占用，且防火墙未拦截。`
        }
    }

    const addMessage = (role: 'user' | 'assistant', content: string) => {
        const normalized = content.trim()
        if (!normalized) return
        const message: Message = {
            role,
            content: normalized,
            timestamp: new Date().toISOString(),
        }
        if (role === 'assistant') {
            setMessages((prev) => {
                const last = prev[prev.length - 1]
                if (last?.role === 'assistant' && last.content.trim() === normalized) {
                    return prev
                }
                return [...prev, message]
            })
            return
        }
        setMessages((prev) => [...prev, message])
    }

    /** 流式结束与最终 message 可能各投递一次相同正文，在此去重 */
    const addAssistantDeduped = (content: string) => {
        const trimmed = content.trim()
        if (!trimmed) return
        addMessage('assistant', content)
    }

    const handleSend = () => {
        if (!input.trim()) return
        if (!wsRef.current?.isConnected()) {
            window.alert(
                wsHintText() ??
                    '未连接到 minibot WebSocket。请启动 minibot gateway，并在 ~/.minibot/config.json 中启用 channels.websocket（参见 minibot docs/WEBSOCKET.md）。',
            )
            return
        }

        addMessage('user', input)
        setThinkingLog('')
        wsRef.current.send(input)
        setInput('')
    }

    const handleNewChat = () => {
        setCurrentSession(null)
        setMessages([])
        setStreamingMessage('')
        setThinkingLog('')
        wsRef.current?.disconnect()
        void initWebSocket()
    }

    const handleSelectSession = (session: Session) => {
        setCurrentSession(session)
        setMessages(normalizeHistoryMessages(session.messages))
        setStreamingMessage('')
        setThinkingLog('')
    }

    const handleDeleteSession = async (id: string) => {
        try {
            await api.deleteSession(id)
            setSessions(prev => prev.filter(s => s.id !== id))
            if (currentSession?.id === id) {
                setCurrentSession(null)
                handleNewChat()
            }
        } catch (err) {
            alert('删除失败：' + (err as Error).message)
        }
    }

    const handleSaveConfig = async (newConfig: ModelConfig) => {
        await api.saveConfig(newConfig)
        setConfig(newConfig)
        const ws = wsRef.current
        if (ws?.isConnected()) {
            // minibot 在启动时读取 config；这里触发内建 /restart 让新模型立即生效
            addMessage('assistant', '模型配置已保存，正在重启 minibot 以应用新模型...')
            ws.send('/restart')
            window.setTimeout(() => {
                wsRef.current?.disconnect()
                void initWebSocket()
            }, 1500)
        } else {
            window.alert(
                '模型配置已保存。当前未连接到 gateway，请手动重启 `minibot gateway` 后再发起对话，模型才会切换生效。',
            )
        }
    }

    const disconnectedHint = !isConnected ? wsHintText() : null

    return (
        <div className="flex min-h-0 flex-1 flex-row bg-gray-50">
            {/* Sidebar */}
            <div className="w-80 bg-white border-r border-gray-200 flex flex-col">
                <div className="p-4 border-b">
                    <button
                        onClick={handleNewChat}
                        className="w-full flex items-center justify-center gap-2 px-4 py-3 bg-primary-500 text-white rounded-lg hover:bg-primary-600 transition-colors font-medium"
                    >
                        <Plus size={20} />
                        新对话
                    </button>
                </div>

                <div className="flex-1 overflow-y-auto p-4 space-y-2">
                    {sessions.map(session => (
                        <SessionItem
                            key={session.id}
                            session={session}
                            isActive={currentSession?.id === session.id}
                            onClick={() => handleSelectSession(session)}
                            onDelete={() => handleDeleteSession(session.id)}
                        />
                    ))}
                </div>
            </div>

            {/* Main */}
            <div className="flex min-h-0 flex-1 flex-col">
                {/* Header */}
                <div className="shrink-0 border-b border-gray-200 bg-white">
                    <div className="flex h-16 items-center justify-between px-6">
                        <div className="flex items-center gap-3">
                            <h1 className="text-xl font-semibold text-gray-900">Dashboard</h1>
                            <div
                                className={`flex items-center gap-2 rounded-full px-3 py-1.5 text-sm ${isConnected ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'
                                    }`}
                            >
                                {isConnected ? <Wifi size={16} /> : <WifiOff size={16} />}
                                {isConnected ? '已连接' : '未连接'}
                            </div>
                        </div>

                        <button
                            type="button"
                            onClick={() => setShowConfig(true)}
                            className="flex items-center gap-2 rounded-lg border border-gray-300 px-4 py-2 hover:bg-gray-50 transition-colors"
                        >
                            <Settings size={18} />
                            Model
                        </button>
                    </div>
                    {disconnectedHint && (
                        <div className="border-t border-amber-200 bg-amber-50 px-6 py-3 text-sm leading-relaxed text-amber-950">
                            {disconnectedHint}
                        </div>
                    )}
                </div>

                {/* Messages */}
                <div className="flex-1 overflow-y-auto p-6">
                    {messages.length === 0 && !streamingMessage && !thinkingLog && (
                        <div className="flex items-center justify-center h-full text-gray-400">
                            <div className="text-center">
                                <div className="text-6xl mb-4">💬</div>
                                <p className="text-lg">开始新对话</p>
                            </div>
                        </div>
                    )}

                    <ThinkingCollapsible text={thinkingLog} />

                    {messages.filter((msg) => msg.content.trim()).map((msg, idx) => (
                        <MessageBubble key={idx} message={msg} />
                    ))}

                    {streamingMessage.trim() && (
                        <MessageBubble
                            message={{
                                role: 'assistant',
                                content: streamingMessage,
                                timestamp: new Date().toISOString()
                            }}
                        />
                    )}

                    <div ref={messagesEndRef} />
                </div>

                {/* Input */}
                <div className="bg-white border-t border-gray-200 p-6">
                    <div className="flex gap-3">
                        <input
                            type="text"
                            value={input}
                            onChange={(e) => setInput(e.target.value)}
                            onKeyDown={(e) => {
                                if (e.key === 'Enter') {
                                    e.preventDefault()
                                    handleSend()
                                }
                            }}
                            placeholder={
                                isConnected
                                    ? '输入消息...'
                                    : '输入消息…（未连接：请先启动 minibot gateway 并开启 WebSocket）'
                            }
                            className="flex-1 px-4 py-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-transparent outline-none"
                        />
                        <button
                            type="button"
                            onClick={handleSend}
                            disabled={!input.trim() || !isConnected}
                            title={
                                !isConnected
                                    ? '请先启动 minibot gateway，并在 config 中启用 channels.websocket'
                                    : undefined
                            }
                            className="px-6 py-3 bg-primary-500 text-white rounded-lg hover:bg-primary-600 transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
                        >
                            <Send size={18} />
                            发送
                        </button>
                    </div>
                </div>
            </div>

            <ModelConfigModal
                isOpen={showConfig}
                onClose={() => setShowConfig(false)}
                onSave={handleSaveConfig}
                initialConfig={config}
            />
        </div>
    )
}