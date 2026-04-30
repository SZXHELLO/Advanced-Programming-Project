// WebSocket 消息类型
export interface WSMessage {
    event: 'ready' | 'message' | 'delta' | 'stream_end' | 'error'
    content?: string
    /** minibot WebSocket 协议里完整回复与流式片段常用字段名 */
    text?: string
    chat_id?: string
    client_id?: string
    delta?: string
    /** minibot ≥ 当前仓库：ReAct 进度 / Observation 等（见 websocket channel send） */
    kind?: 'progress' | string
    toolHint?: boolean
}

// 会话类型
export interface Session {
    id: string
    session_key: string
    created_at: string
    updated_at: string
    messages: Message[]
}

export interface Message {
    role: 'user' | 'assistant'
    content: string
    timestamp: string
}

// 子 Agent 类型
export interface SubAgent {
    id: string
    label: string
    task: string
    session_key: string
    status: 'standby' | 'running' | 'interrupted' | 'completed'
    origin_channel?: string
    origin_chat_id?: string
}

/** BFF 根据 ~/.minibot/config.json 推断的 WebSocket 通道状态 */
export type WebSocketHintKey =
    | 'ok'
    | 'disabled'
    | 'token_required'
    | 'config_unreadable'

export interface WebSocketStatus {
    enabled: boolean
    connectUrl: string
    websocketRequiresToken: boolean
    tokenConfigured: boolean
    hintKey: WebSocketHintKey
}

// 配置类型
export interface ModelConfig {
    api_key: string
    base_url: string
    model: string
}