import type { WSMessage } from '../types'

export class WebSocketClient {
    private ws: WebSocket | null = null
    private url: string
    private reconnectAttempts = 0
    private maxReconnectAttempts = 5
    private reconnectDelay = 2000

    public onMessage: ((msg: WSMessage) => void) | null = null
    public onConnected: ((chatId: string) => void) | null = null
    public onDisconnected: (() => void) | null = null

    constructor(url: string = 'ws://127.0.0.1:8765/') {
        this.url = url
    }

    connect() {
        try {
            this.ws = new WebSocket(this.url)

            this.ws.onopen = () => {
                console.log('✅ WebSocket connected')
                this.reconnectAttempts = 0
            }

            this.ws.onmessage = (event) => {
                try {
                    const msg: WSMessage = JSON.parse(event.data)

                    if (msg.event === 'ready' && msg.chat_id) {
                        this.onConnected?.(msg.chat_id)
                    }

                    this.onMessage?.(msg)
                } catch (err) {
                    console.error('Failed to parse message:', err)
                }
            }

            this.ws.onclose = () => {
                console.log('❌ WebSocket disconnected')
                this.onDisconnected?.()
                this.attemptReconnect()
            }

            this.ws.onerror = (error) => {
                console.error('WebSocket error:', error)
            }
        } catch (err) {
            console.error('Failed to create WebSocket:', err)
        }
    }

    private attemptReconnect() {
        if (this.reconnectAttempts < this.maxReconnectAttempts) {
            this.reconnectAttempts++
            console.log(`Reconnecting... (${this.reconnectAttempts}/${this.maxReconnectAttempts})`)
            setTimeout(() => this.connect(), this.reconnectDelay)
        }
    }

    send(content: string) {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify({ content }))
        } else {
            console.error('WebSocket is not connected')
        }
    }

    disconnect() {
        if (this.ws) {
            this.ws.close()
            this.ws = null
        }
    }

    isConnected(): boolean {
        return this.ws?.readyState === WebSocket.OPEN
    }
}