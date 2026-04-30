import express from 'express'
import cors from 'cors'
import fs from 'fs/promises'
import path from 'path'
import { homedir } from 'os'

function asObject(value: unknown): Record<string, unknown> {
  if (typeof value === 'object' && value !== null && !Array.isArray(value)) {
    return value as Record<string, unknown>
  }
  return {}
}

type SubagentRecord = Record<string, unknown>

type SubagentStore = {
  version: number
  records: SubagentRecord[]
}

function parseSubagentStore(raw: string): SubagentStore {
  try {
    const data = JSON.parse(raw) as unknown
    if (
      typeof data !== 'object' ||
      data === null ||
      !('records' in data) ||
      !Array.isArray((data as { records: unknown }).records)
    ) {
      return { version: 1, records: [] }
    }
    const o = data as { version?: number; records: unknown[] }
    return {
      version: typeof o.version === 'number' ? o.version : 1,
      records: o.records.filter(
        (r): r is SubagentRecord =>
          typeof r === 'object' && r !== null && !Array.isArray(r),
      ),
    }
  } catch {
    return { version: 1, records: [] }
  }
}

const app = express()
const PORT = 3001

app.use(cors({
  origin: 'http://localhost:3000',
  credentials: true
}))
app.use(express.json())

// 获取路径
const getConfigPath = () => path.join(homedir(), '.minibot', 'config.json')
const getWorkspacePath = async () => {
  const configPath = getConfigPath()
  try {
    const config = JSON.parse(await fs.readFile(configPath, 'utf-8'))
    const workspace = config?.agents?.defaults?.workspace || '~/.minibot/workspace'
    return workspace.replace('~', homedir())
  } catch {
    return path.join(homedir(), '.minibot', 'workspace')
  }
}

// ========== 配置相关 ==========
app.get('/api/config', async (req, res) => {
  try {
    const configPath = getConfigPath()
    const data = await fs.readFile(configPath, 'utf-8')
    const config = JSON.parse(data)

    res.json({
      api_key: config?.providers?.custom?.apiKey || '',
      base_url: config?.providers?.custom?.apiBase || 'https://api.openai.com/v1',
      model: config?.agents?.defaults?.model || 'gpt-4'
    })
  } catch (err) {
    res.status(500).json({ error: 'Failed to read config' })
  }
})

app.post('/api/config', async (req, res) => {
  try {
    const { api_key, base_url, model } = req.body
    const configPath = getConfigPath()

    let config: Record<string, unknown> = {}
    try {
      const data = await fs.readFile(configPath, 'utf-8')
      const parsed = JSON.parse(data) as unknown
      if (typeof parsed === 'object' && parsed !== null && !Array.isArray(parsed)) {
        config = parsed as Record<string, unknown>
      }
    } catch { }

    const agents = asObject(config.agents)
    const defaults = asObject(agents.defaults)
    const providers = asObject(config.providers)
    const custom = asObject(providers.custom)

    // 更新配置
    config = {
      ...config,
      agents: {
        ...agents,
        defaults: {
          ...defaults,
          model,
          provider: 'custom',
        },
      },
      providers: {
        ...providers,
        custom: {
          ...custom,
          apiKey: api_key,
          apiBase: base_url,
        },
      },
    }

    await fs.writeFile(configPath, JSON.stringify(config, null, 2))
    res.json({ success: true })
  } catch (err) {
    res.status(500).json({ error: 'Failed to save config' })
  }
})

function buildWsConnectUrl(host: string, port: number, pathStr: string): string {
  const h = host.trim() || '127.0.0.1'
  const p = Number.isFinite(port) && port > 0 ? port : 8765
  const raw =
    typeof pathStr === 'string' && pathStr.startsWith('/') ? pathStr : '/'
  const base = `ws://${h}:${p}`
  return raw === '/' ? `${base}/` : `${base}${raw}`
}

/** 供 Web 前端判断为何连不上 WS（与 QQ/微信等通道是否启用无关） */
app.get('/api/websocket-status', async (_req, res) => {
  const fallback = {
    enabled: false,
    connectUrl: 'ws://127.0.0.1:8765/',
    websocketRequiresToken: true,
    tokenConfigured: false,
    hintKey: 'config_unreadable' as const,
  }
  try {
    const configPath = getConfigPath()
    const raw = await fs.readFile(configPath, 'utf-8')
    const config = JSON.parse(raw) as Record<string, unknown>
    const channels = asObject(config.channels)
    const ws = asObject(channels.websocket)
    const enabled = ws.enabled === true
    const host = typeof ws.host === 'string' ? ws.host : '127.0.0.1'
    const port =
      typeof ws.port === 'number'
        ? ws.port
        : typeof ws.port === 'string'
          ? parseInt(ws.port, 10) || 8765
          : 8765
    const pathStr = typeof ws.path === 'string' ? ws.path : '/'
    const token =
      typeof ws.token === 'string' && ws.token.length > 0 ? ws.token : ''
    const wrt =
      ws.websocketRequiresToken ?? ws.websocket_requires_token
    const requiresToken = wrt !== false && wrt !== 'false'
    const connectUrl = buildWsConnectUrl(host, port, pathStr)
    let hintKey: 'ok' | 'disabled' | 'token_required' | 'config_unreadable' = 'ok'
    if (!enabled) hintKey = 'disabled'
    else if (requiresToken && !token) hintKey = 'token_required'
    res.json({
      enabled,
      connectUrl,
      websocketRequiresToken: requiresToken,
      tokenConfigured: token.length > 0,
      hintKey,
    })
  } catch {
    res.json(fallback)
  }
})

// ========== 会话相关 ==========
app.get('/api/sessions', async (req, res) => {
  try {
    const workspace = await getWorkspacePath()
    const sessionsDir = path.join(workspace, 'sessions')

    const files = await fs.readdir(sessionsDir)
    const sessions = []

    for (const file of files) {
      if (file.endsWith('.json')) {
        const filePath = path.join(sessionsDir, file)
        const data = await fs.readFile(filePath, 'utf-8')
        const session = JSON.parse(data) as Record<string, unknown>
        const stem = path.basename(file, '.json')
        const key = typeof session.key === 'string' ? session.key : stem
        sessions.push({
          ...session,
          id: stem,
          session_key: key,
          messages: Array.isArray(session.messages) ? session.messages : [],
        })
      }
    }

    res.json(sessions)
  } catch (err) {
    res.json([])
  }
})

app.delete('/api/sessions/:id', async (req, res) => {
  try {
    const { id } = req.params
    const workspace = await getWorkspacePath()
    const sessionsDir = path.join(workspace, 'sessions')

    const files = await fs.readdir(sessionsDir)
    for (const file of files) {
      if (file.includes(id)) {
        await fs.unlink(path.join(sessionsDir, file))
      }
    }

    res.json({ success: true })
  } catch (err) {
    res.status(500).json({ error: 'Failed to delete session' })
  }
})

// ========== 子 Agent 相关 ==========
app.get('/api/subagents', async (req, res) => {
  try {
    const workspace = await getWorkspacePath()
    const agentsFile = path.join(workspace, '.minibot', 'persistent_subagents.json')

    const data = await fs.readFile(agentsFile, 'utf-8')
    const json = JSON.parse(data)
    res.json(json.records || [])
  } catch (err) {
    res.json([])
  }
})

app.post('/api/subagents', async (req, res) => {
  try {
    const { label, task } = req.body
    const workspace = await getWorkspacePath()
    const agentsFile = path.join(workspace, '.minibot', 'persistent_subagents.json')

    let json: SubagentStore = { version: 1, records: [] }
    try {
      const data = await fs.readFile(agentsFile, 'utf-8')
      json = parseSubagentStore(data)
    } catch { }

    const newAgent: SubagentRecord = {
      id: crypto.randomUUID().replace(/-/g, ''),
      label,
      task,
      session_key: 'web:ui',
      status: 'standby',
    }

    json.records.push(newAgent)

    // 原子写入
    const tempFile = agentsFile + '.tmp'
    await fs.writeFile(tempFile, JSON.stringify(json, null, 2))
    await fs.rename(tempFile, agentsFile)

    res.json(newAgent)
  } catch (err) {
    res.status(500).json({ error: 'Failed to create agent' })
  }
})

app.patch('/api/subagents/:id', async (req, res) => {
  try {
    const { id } = req.params
    const updates = req.body
    const workspace = await getWorkspacePath()
    const agentsFile = path.join(workspace, '.minibot', 'persistent_subagents.json')

    const data = await fs.readFile(agentsFile, 'utf-8')
    const json = parseSubagentStore(data)

    const index = json.records.findIndex((a) => String(a.id) === id)
    if (index === -1) {
      return res.status(404).json({ error: 'Agent not found' })
    }

    json.records[index] = { ...json.records[index], ...updates }

    const tempFile = agentsFile + '.tmp'
    await fs.writeFile(tempFile, JSON.stringify(json, null, 2))
    await fs.rename(tempFile, agentsFile)

    res.json({ success: true })
  } catch (err) {
    res.status(500).json({ error: 'Failed to update agent' })
  }
})

app.delete('/api/subagents/:id', async (req, res) => {
  try {
    const { id } = req.params
    const workspace = await getWorkspacePath()
    const agentsFile = path.join(workspace, '.minibot', 'persistent_subagents.json')

    const data = await fs.readFile(agentsFile, 'utf-8')
    const json = parseSubagentStore(data)

    json.records = json.records.filter((a) => String(a.id) !== id)

    const tempFile = agentsFile + '.tmp'
    await fs.writeFile(tempFile, JSON.stringify(json, null, 2))
    await fs.rename(tempFile, agentsFile)

    res.json({ success: true })
  } catch (err) {
    res.status(500).json({ error: 'Failed to delete agent' })
  }
})

app.listen(PORT, '127.0.0.1', () => {
  console.log(`✅ BFF Server running on http://127.0.0.1:${PORT}`)
})