const express = require('express');
const path = require('path');
const cors = require('cors');
const multer = require('multer');
const axios = require('axios');
const imageSize = require('image-size');
const bcrypt = require('bcryptjs');
const http = require('http');
const { WebSocketServer, WebSocket } = require('ws');
require('dotenv').config();

const { createPool, initDb } = require('./db');
const { parseDataUrl, bufferToDataUrl } = require('./utils/dataUrl');
const { logActivity } = require('./utils/activityLogger');
const {
  issueSession,
  getSessionFromRequest,
  getSessionById,
  sessionStore,
} = require('./sessionService');
const {
  projectRooms,
  getOrCreateRoom,
  buildPresenceMembers,
  publishRoomPresence,
  broadcastToRoom,
  disconnectUserSocketsForProject,
} = require('./collabRooms');
const createProjectManagementRouter = require('./routes/projectManagement');

const app = express();

// JSON 体积可能会很大（项目快照中含 base64 图片/字库图形）
app.use(express.json({ limit: '120mb' }));
app.use(cors({ origin: '*', credentials: true }));

// 托管前端静态资源（join.html、css、js 等），使邀请链接可使用与 API 相同的主机端口
const FRONTEND_ROOT = path.join(__dirname, '..');
app.use((req, res, next) => {
  if (req.method !== 'GET' && req.method !== 'HEAD') return next();
  const p = req.path || '';
  if (p === '/backend' || p.startsWith('/backend/')) return res.status(404).end();
  if (p.startsWith('/.git') || p.startsWith('/node_modules/')) return res.status(404).end();
  next();
});
app.use(express.static(FRONTEND_ROOT, { index: 'index.html' }));

// 上传走内存（OCR/分辨率获取不落盘）
const upload = multer({
  storage: multer.memoryStorage(),
  limits: { fileSize: 20 * 1024 * 1024 }, // 20MB
});

let pool;
let wss = null;

async function getProjectMemberRole(projectId, userId) {
  const [rows] = await pool.query(
    'SELECT role FROM project_members WHERE project_id = ? AND user_id = ? LIMIT 1',
    [String(projectId), userId]
  );
  return rows[0]?.role || null;
}

const VIEWER_BLOCKED_TYPES = new Set([
  'drawing_preview',
  'annotation_add',
  'page_add',
  'page_delete',
  'pages_replace',
  'annotations_add_many',
  'annotation_update',
  'annotation_delete',
  'annotations_replace',
  'page_sync_request',
  'page_sync_snapshot',
]);

function setupCollabWebSocket(server) {
  wss = new WebSocketServer({ server, path: '/ws/collab' });

  wss.on('connection', (socket, req) => {
    (async () => {
    try {
      const url = new URL(req.url, 'http://localhost');
      const sessionId = String(url.searchParams.get('sessionId') || '').trim();
      const projectId = String(url.searchParams.get('projectId') || '').trim();
      const session = getSessionById(sessionId);
      if (!session || !projectId) {
        socket.close(1008, 'unauthorized');
        return;
      }

      const userRole = await getProjectMemberRole(projectId, session.userId);
      if (!userRole) {
        socket.close(4403, 'forbidden');
        return;
      }

      const room = getOrCreateRoom(projectId);
      room.set(sessionId, {
        sessionId,
        userId: session.userId,
        username: session.username,
        displayName: session.displayName || session.username,
        userRole,
        socket
      });

      socket.send(JSON.stringify({
        type: 'joined',
        projectId,
        sessionId,
        displayName: session.displayName || session.username,
        userRole
      }));
      publishRoomPresence(projectId);

      const wsLog = (actionType, actionDetail) => {
        logActivity(pool, {
          projectId,
          userId: session.userId,
          actionType,
          actionDetail: actionDetail || {},
          req: null,
        }).catch((e) => console.error('collab activity log:', e?.message || e));
      };

      socket.on('message', (raw) => {
        let data;
        try {
          data = JSON.parse(String(raw || '{}'));
        } catch (_) {
          return;
        }
        const type = String(data?.type || '');
        if (!type) return;

        if (userRole === 'viewer' && VIEWER_BLOCKED_TYPES.has(type)) {
          if (socket.readyState === WebSocket.OPEN) {
            socket.send(JSON.stringify({ type: 'error', message: '您是只读成员，无法编辑' }));
          }
          return;
        }

        if (type === 'leave') {
          const targetRoom = projectRooms.get(projectId);
          if (targetRoom) {
            targetRoom.delete(sessionId);
            if (!targetRoom.size) {
              projectRooms.delete(projectId);
            } else {
              publishRoomPresence(projectId);
            }
          }
          socket.close(1000, 'manual-leave');
          return;
        }

        if (type === 'join') {
          publishRoomPresence(projectId);
          return;
        }

        if (type === 'drawing_preview') {
          broadcastToRoom(projectId, sessionId, {
            type: 'drawing_preview',
            projectId,
            fromSessionId: sessionId,
            displayName: session.displayName || session.username,
            drawing: data.drawing || null
          });
          return;
        }

        if (type === 'annotation_add') {
          broadcastToRoom(projectId, sessionId, {
            type: 'annotation_add',
            projectId,
            fromSessionId: sessionId,
            displayName: session.displayName || session.username,
            annotation: data.annotation || null
          });
          wsLog('annotation_added', {
            pageIndex: data.annotation?.pageIndex,
            text: String(data.annotation?.text || '').slice(0, 100),
          });
          return;
        }

        if (type === 'page_add') {
          broadcastToRoom(projectId, sessionId, {
            type: 'page_add',
            projectId,
            fromSessionId: sessionId,
            displayName: session.displayName || session.username,
            page: data.page || null
          });
          return;
        }

        if (type === 'page_delete') {
          broadcastToRoom(projectId, sessionId, {
            type: 'page_delete',
            projectId,
            fromSessionId: sessionId,
            pageId: data.pageId || null
          });
          return;
        }

        if (type === 'pages_replace') {
          const version = Number(data.version || Date.now());
          const pages = Array.isArray(data.pages) ? data.pages : [];
          if (process.env.COLLAB_DEBUG === '1') {
            console.log('[collab:pages_replace:broadcast]', {
              projectId,
              fromSessionId: sessionId,
              version,
              pagesCount: pages.length
            });
          }
          broadcastToRoom(projectId, sessionId, {
            type: 'pages_replace',
            projectId,
            fromSessionId: sessionId,
            pages,
            version
          });
          wsLog('pages_replaced', { pageCount: pages.length });
          return;
        }

        if (type === 'annotations_add_many') {
          broadcastToRoom(projectId, sessionId, {
            type: 'annotations_add_many',
            projectId,
            fromSessionId: sessionId,
            displayName: session.displayName || session.username,
            annotations: Array.isArray(data.annotations) ? data.annotations : []
          });
          return;
        }

        if (type === 'annotation_update') {
          broadcastToRoom(projectId, sessionId, {
            type: 'annotation_update',
            projectId,
            fromSessionId: sessionId,
            displayName: session.displayName || session.username,
            annotation: data.annotation || null
          });
          wsLog('annotation_updated', {
            annotationId: data.annotation?.id || data.annotationId,
            changes: data.changes || {},
          });
          return;
        }

        if (type === 'annotation_delete') {
          broadcastToRoom(projectId, sessionId, {
            type: 'annotation_delete',
            projectId,
            fromSessionId: sessionId,
            annotationId: data.annotationId || null
          });
          wsLog('annotation_deleted', { annotationId: data.annotationId });
          return;
        }

        if (type === 'annotations_replace') {
          broadcastToRoom(projectId, sessionId, {
            type: 'annotations_replace',
            projectId,
            fromSessionId: sessionId,
            annotations: Array.isArray(data.annotations) ? data.annotations : [],
            version: Number(data.version || Date.now())
          });
          return;
        }

        if (type === 'page_sync_request') {
          broadcastToRoom(projectId, sessionId, {
            type: 'page_sync_request',
            projectId,
            fromSessionId: sessionId,
            pageId: data.pageId || null,
            requestAt: Number(data.requestAt || Date.now())
          });
          return;
        }

        if (type === 'page_sync_snapshot') {
          broadcastToRoom(projectId, sessionId, {
            type: 'page_sync_snapshot',
            projectId,
            fromSessionId: sessionId,
            pageId: data.pageId || null,
            annotations: Array.isArray(data.annotations) ? data.annotations : [],
            version: Number(data.version || Date.now()),
            targetSessionId: data.targetSessionId || null
          });
        }
      });

      socket.on('close', () => {
        const roomAfter = projectRooms.get(projectId);
        if (!roomAfter) return;
        roomAfter.delete(sessionId);
        if (!roomAfter.size) {
          projectRooms.delete(projectId);
        } else {
          publishRoomPresence(projectId);
        }
      });
    } catch (err) {
      console.error('ws connection failed:', err?.message || err);
      socket.close(1011, 'server-error');
    }
    })();
  });
}

function isValidRegisterUsername(username) {
  return /^[A-Za-z0-9_]{4,20}$/.test(username);
}

// --------------------------
// 登录鉴权：/api/auth/*
// --------------------------
app.post('/api/auth/register', async (req, res) => {
  try {
    const username = String(req.body?.username || '').trim();
    const displayName = String(req.body?.displayName || '').trim();
    const password = String(req.body?.password || '');
    const confirmPassword = String(req.body?.confirmPassword || '');

    if (!username || !displayName || !password || !confirmPassword) {
      return res.status(400).json({ error: '请完整填写注册信息' });
    }
    if (!isValidRegisterUsername(username)) {
      return res.status(400).json({ error: '账号需为4-20位字母、数字或下划线' });
    }
    if (displayName.length > 50) {
      return res.status(400).json({ error: '昵称长度不能超过50个字符' });
    }
    if (password.length < 6) {
      return res.status(400).json({ error: '密码至少需要6位' });
    }
    if (password !== confirmPassword) {
      return res.status(400).json({ error: '两次输入的密码不一致' });
    }

    const [existingRows] = await pool.query(
      'SELECT user_id FROM users WHERE username = ? LIMIT 1',
      [username]
    );
    if (existingRows.length) {
      return res.status(409).json({ error: '账号已存在' });
    }

    const passwordHash = await bcrypt.hash(password, 10);
    const defaultPermissions = JSON.stringify(['project:read']);
    await pool.query(
      `
      INSERT INTO users (username, password_hash, display_name, role, permissions)
      VALUES (?, ?, ?, ?, CAST(? AS JSON))
      `,
      [username, passwordHash, displayName, 'editor', defaultPermissions]
    );

    return res.status(201).json({ ok: true, username, displayName });
  } catch (err) {
    console.error('auth register failed:', err?.message || err);
    return res.status(500).json({ error: '注册失败，请稍后重试' });
  }
});

app.post('/api/auth/login', async (req, res) => {
  try {
    const username = String(req.body?.username || '').trim();
    const password = String(req.body?.password || '');
    if (!username || !password) {
      return res.status(400).json({ error: '用户名和密码不能为空' });
    }

    const [rows] = await pool.query(
      `
      SELECT user_id, username, password_hash, display_name, role, permissions
      FROM users
      WHERE username = ?
      LIMIT 1
      `,
      [username]
    );

    if (!rows.length) {
      return res.status(401).json({ error: '账号或密码错误' });
    }

    const user = rows[0];
    const passOk = await bcrypt.compare(password, user.password_hash);
    if (!passOk) {
      return res.status(401).json({ error: '账号或密码错误' });
    }

    const session = issueSession(user);
    return res.json({
      sessionId: session.sessionId,
      userId: session.userId,
      username: session.username,
      displayName: session.displayName,
      role: session.role,
      permissions: session.permissions,
      expiresAt: session.expiresAt,
    });
  } catch (err) {
    console.error('auth login failed:', err?.message || err);
    return res.status(500).json({ error: '登录失败，请稍后重试' });
  }
});

app.get('/api/auth/me', (req, res) => {
  const session = getSessionFromRequest(req);
  if (!session) {
    return res.status(401).json({ error: '未登录或会话已失效' });
  }

  return res.json({
    sessionId: session.sessionId,
    userId: session.userId,
    username: session.username,
    displayName: session.displayName,
    role: session.role,
    permissions: session.permissions,
    expiresAt: session.expiresAt,
  });
});

app.post('/api/auth/logout', (req, res) => {
  const session = getSessionFromRequest(req);
  if (session) {
    sessionStore.delete(session.sessionId);
  }
  return res.json({ ok: true });
});

app.delete('/api/auth/account', async (req, res) => {
  try {
    const session = getSessionFromRequest(req);
    if (!session) {
      return res.status(401).json({ error: '未登录或会话已失效' });
    }

    const [result] = await pool.query(
      'DELETE FROM users WHERE user_id = ? LIMIT 1',
      [session.userId]
    );
    sessionStore.delete(session.sessionId);
    projectRooms.forEach((room, pid) => {
      if (room.has(session.sessionId)) {
        room.delete(session.sessionId);
        if (!room.size) {
          projectRooms.delete(pid);
        } else {
          publishRoomPresence(pid);
        }
      }
    });

    if (!result?.affectedRows) {
      return res.status(404).json({ error: '账号不存在或已被删除' });
    }
    return res.json({ ok: true });
  } catch (err) {
    console.error('delete account failed:', err?.message || err);
    return res.status(500).json({ error: '注销失败，请稍后重试' });
  }
});

// --------------------------
// 百度 OCR：access_token 缓存
// --------------------------
let baiduToken = null;
let baiduTokenExpireAt = 0;

async function getBaiduAccessToken() {
  const apiKey = process.env.BAIDU_OCR_API_KEY;
  const secretKey = process.env.BAIDU_OCR_SECRET_KEY;
  if (!apiKey || !secretKey) {
    throw new Error('Missing Baidu OCR env vars: BAIDU_OCR_API_KEY / BAIDU_OCR_SECRET_KEY');
  }

  const now = Date.now();
  if (baiduToken && now < baiduTokenExpireAt - 60_000) {
    return baiduToken;
  }

  const url = 'https://aip.baidubce.com/oauth/2.0/token';
  const params = {
    grant_type: 'client_credentials',
    client_id: apiKey,
    client_secret: secretKey,
  };
  const resp = await axios.get(url, { params });
  const token = resp.data?.access_token;
  if (!token) {
    throw new Error('Failed to get Baidu access token');
  }
  const expiresIn = Number(resp.data?.expires_in || 2592000); // default 30d
  baiduToken = token;
  baiduTokenExpireAt = Date.now() + expiresIn * 1000;
  return baiduToken;
}

// --------------------------
// OCR：/api/recognize-text
// --------------------------
app.post('/api/recognize-text', upload.single('image'), async (req, res) => {
  try {
    if (!req.file) return res.status(400).json({ error: 'Missing image file' });

    const token = await getBaiduAccessToken();
    const imgBase64 = req.file.buffer.toString('base64');

    const url = `https://aip.baidubce.com/rest/2.0/ocr/v1/accurate?access_token=${token}`;
    const params = new URLSearchParams();
    params.append('image', imgBase64);
    params.append('recognize_granularity', 'small');

    const resp = await axios.post(url, params.toString(), {
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      timeout: 60_000,
    });

    const ocrResult = resp.data || {};
    const wordsResult = ocrResult.words_result;
    if (!Array.isArray(wordsResult)) {
      return res.json({ words: [], total: 0 });
    }

    const words = [];
    for (const line of wordsResult) {
      if (Array.isArray(line?.chars)) {
        for (const charObj of line.chars) {
          const loc = charObj?.location;
          if (!loc) continue;
          words.push({
            text: charObj?.char || '',
            x: Number(loc.left),
            y: Number(loc.top),
            width: Number(loc.width),
            height: Number(loc.height),
          });
        }
      } else if (line?.location && line?.word) {
        const loc = line.location;
        words.push({
          text: line.word,
          x: Number(loc.left),
          y: Number(loc.top),
          width: Number(loc.width),
          height: Number(loc.height),
        });
      }
    }

    return res.json({ words, total: words.length });
  } catch (err) {
    console.error('recognize-text failed:', err?.message || err);
    return res.status(500).json({ error: err?.message || 'OCR failed' });
  }
});

// --------------------------
// 区域划分：/api/segment-regions
// --------------------------
app.post('/api/segment-regions', upload.single('image'), async (req, res) => {
  try {
    if (!req.file) return res.status(400).json({ error: 'Missing image file' });

    const dims = imageSize(req.file.buffer);
    if (!dims?.width || !dims?.height) {
      throw new Error('Cannot detect image dimensions');
    }

    const imgW = dims.width;
    const imgH = dims.height;
    const regions = [
      { type: 'text', x: 0, y: 0, width: Math.floor(imgW * 0.7), height: imgH },
      { type: 'image', x: Math.floor(imgW * 0.7), y: 0, width: Math.floor(imgW * 0.3), height: imgH },
    ];

    return res.json({ regions });
  } catch (err) {
    console.error('segment-regions failed:', err?.message || err);
    return res.status(500).json({ error: err?.message || 'segment-regions failed' });
  }
});

// --------------------------
// 造字：/api/custom-chars
// --------------------------
app.get('/api/custom-chars', async (req, res) => {
  try {
    const [rows] = await pool.query(
      'SELECT custom_char_id, unicode, name, image_mime, image_blob FROM custom_chars ORDER BY unicode ASC'
    );

    const list = rows.map((r) => {
      const dataUrl = bufferToDataUrl(r.image_blob, r.image_mime || 'image/png');
      return {
        id: r.custom_char_id,
        unicode: r.unicode,
        name: r.name,
        imageData: dataUrl,
      };
    });

    return res.json({ customChars: list });
  } catch (err) {
    console.error('get custom chars failed:', err?.message || err);
    return res.status(500).json({ error: err?.message || 'Failed to load custom chars' });
  }
});

app.get('/api/custom-chars/next-unicode', async (req, res) => {
  try {
    const [rows] = await pool.query('SELECT MAX(unicode) AS maxUnicode FROM custom_chars');
    const maxUnicode = rows?.[0]?.maxUnicode;
    const nextUnicode = maxUnicode === null || maxUnicode === undefined
      ? 0xE000
      : Number(maxUnicode) + 1;
    return res.json({ nextUnicode });
  } catch (err) {
    console.error('next-unicode failed:', err?.message || err);
    return res.status(500).json({ error: err?.message || err });
  }
});

app.post('/api/custom-chars', async (req, res) => {
  try {
    const { customChar } = req.body || {};
    if (!customChar) return res.status(400).json({ error: 'Missing customChar' });

    const { id, unicode, name, imageData } = customChar;
    if (!id || !name || !imageData || unicode === undefined) {
      return res.status(400).json({ error: 'customChar must include id, unicode, name, imageData' });
    }

    const { mime, buffer } = parseDataUrl(imageData);
    const customCharId = String(id);
    const unicodeNum = Number(unicode);

    await pool.query(
      `
      INSERT INTO custom_chars (custom_char_id, unicode, name, image_mime, image_blob)
      VALUES (?, ?, ?, ?, ?)
      ON DUPLICATE KEY UPDATE
        unicode = VALUES(unicode),
        name = VALUES(name),
        image_mime = VALUES(image_mime),
        image_blob = VALUES(image_blob)
      `,
      [customCharId, unicodeNum, name, mime, buffer]
    );

    return res.json({ ok: true, id: customCharId, unicode: unicodeNum, name });
  } catch (err) {
    console.error('save custom char failed:', err?.message || err);
    return res.status(500).json({ error: err?.message || 'Failed to save custom char' });
  }
});

app.delete('/api/custom-chars/:id', async (req, res) => {
  try {
    const id = String(req.params.id);
    await pool.query('DELETE FROM custom_chars WHERE custom_char_id = ?', [id]);
    return res.json({ ok: true });
  } catch (err) {
    console.error('delete custom char failed:', err?.message || err);
    return res.status(500).json({ error: err?.message || 'Failed to delete custom char' });
  }
});

// --------------------------
// 项目：保存快照（/api/projects）
// --------------------------
app.post('/api/projects', async (req, res) => {
  try {
    const session = getSessionFromRequest(req);
    if (!session) {
      return res.status(401).json({ error: '未登录或会话已失效' });
    }

    const { project, pages, annotations, customChars, exportedAt } = req.body || {};
    if (!project?.id) return res.status(400).json({ error: 'Missing project.id' });

    const projectId = String(project.id);
    const [existing] = await pool.query(
      'SELECT project_id FROM projects WHERE project_id = ? LIMIT 1',
      [projectId]
    );

    if (!existing.length) {
      await pool.query(
        `
        INSERT INTO projects (project_id, title, author, dynasty, book, volume, owner_id)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        `,
        [
          projectId,
          project.title || '',
          project.author || '',
          project.dynasty || '',
          project.book || '',
          project.volume || '',
          session.userId,
        ]
      );
      await pool.query(
        `
        INSERT INTO project_members (project_id, user_id, role, invited_by)
        VALUES (?, ?, 'admin', NULL)
        `,
        [projectId, session.userId]
      );
      await logActivity(pool, {
        projectId,
        userId: session.userId,
        actionType: 'project_created',
        actionDetail: { projectName: project.title || projectId },
        req,
      });
    } else {
      const role = await getProjectMemberRole(projectId, session.userId);
      if (!role) {
        return res.status(403).json({ error: '无权访问此项目' });
      }
      if (!['admin', 'editor'].includes(role)) {
        return res.status(403).json({ error: '无编辑权限' });
      }
      await pool.query(
        `
        UPDATE projects SET
          title = ?, author = ?, dynasty = ?, book = ?, volume = ?
        WHERE project_id = ?
        `,
        [
          project.title || '',
          project.author || '',
          project.dynasty || '',
          project.book || '',
          project.volume || '',
          projectId,
        ]
      );
      await logActivity(pool, {
        projectId,
        userId: session.userId,
        actionType: 'project_updated',
        actionDetail: { saved: true },
        req,
      });
    }

    const snapshot = {
      project,
      pages: Array.isArray(pages) ? pages : [],
      annotations: annotations || [],
      customChars: Array.isArray(customChars) ? customChars : [],
      exportedAt: exportedAt || new Date().toISOString(),
    };

    const snapshotJson = JSON.stringify(snapshot);

    const [result] = await pool.query(
      'INSERT INTO project_snapshots (project_id, snapshot_json) VALUES (?, ?)',
      [projectId, snapshotJson]
    );

    const snapshotId = result?.insertId || null;
    return res.json({ ok: true, projectId, snapshotId });
  } catch (err) {
    console.error('save project snapshot failed:', err?.message || err);
    return res.status(500).json({ error: err?.message || 'Failed to save project snapshot' });
  }
});

app.get('/api/projects', async (req, res) => {
  try {
    const session = getSessionFromRequest(req);
    if (!session) {
      return res.status(401).json({ error: '未登录或会话已失效' });
    }

    const uid = session.userId;

    const [memberRows] = await pool.query(
      `
      SELECT
        p.project_id,
        p.title,
        p.author,
        p.dynasty,
        p.book,
        p.volume,
        s.snapshot_id,
        s.created_at AS saved_at,
        pm.role AS user_role,
        (SELECT COUNT(*) FROM project_join_requests jr
         WHERE jr.project_id = p.project_id AND jr.status = 'pending') AS pending_requests_count
      FROM projects p
      INNER JOIN project_members pm ON pm.project_id = p.project_id AND pm.user_id = ?
      JOIN (
        SELECT project_id, MAX(snapshot_id) AS latest_snapshot_id
        FROM project_snapshots
        GROUP BY project_id
      ) latest ON latest.project_id = p.project_id
      JOIN project_snapshots s ON s.snapshot_id = latest.latest_snapshot_id
      ORDER BY s.created_at DESC
      `,
      [uid]
    );

    const [publicRows] = await pool.query(
      `
      SELECT
        p.project_id,
        p.title,
        p.author,
        p.dynasty,
        p.book,
        p.volume,
        p.allow_join_requests,
        s.snapshot_id,
        s.created_at AS saved_at,
        (SELECT COUNT(*) FROM project_join_requests jr
         WHERE jr.project_id = p.project_id AND jr.status = 'pending') AS pending_requests_count
      FROM projects p
      JOIN (
        SELECT project_id, MAX(snapshot_id) AS latest_snapshot_id
        FROM project_snapshots
        GROUP BY project_id
      ) latest ON latest.project_id = p.project_id
      JOIN project_snapshots s ON s.snapshot_id = latest.latest_snapshot_id
      WHERE p.is_public = 1
        AND NOT EXISTS (
          SELECT 1 FROM project_members pm
          WHERE pm.project_id = p.project_id AND pm.user_id = ?
        )
      ORDER BY s.created_at DESC
      LIMIT 200
      `,
      [uid]
    );

    const mapRow = (r, fromPublicListing) => ({
      project_id: r.project_id,
      title: r.title,
      author: r.author,
      dynasty: r.dynasty,
      book: r.book,
      volume: r.volume,
      snapshot_id: r.snapshot_id,
      saved_at: r.saved_at,
      userRole: r.user_role != null ? r.user_role : null,
      pendingRequestsCount: Number(r.pending_requests_count || 0),
      fromPublicListing: Boolean(fromPublicListing),
      allowJoinRequests: fromPublicListing ? Boolean(Number(r.allow_join_requests)) : undefined,
    });

    const projects = [...memberRows.map((r) => mapRow(r, false)), ...publicRows.map((r) => mapRow(r, true))].sort(
      (a, b) => new Date(b.saved_at).getTime() - new Date(a.saved_at).getTime()
    );

    return res.json({ projects });
  } catch (err) {
    console.error('list projects failed:', err?.message || err);
    return res.status(500).json({ error: err?.message || 'Failed to list projects' });
  }
});

app.get('/api/projects/:projectId/latest', async (req, res) => {
  try {
    const session = getSessionFromRequest(req);
    if (!session) {
      return res.status(401).json({ error: '未登录或会话已失效' });
    }
    const projectId = String(req.params.projectId);
    const userRole = await getProjectMemberRole(projectId, session.userId);
    if (!userRole) {
      return res.status(403).json({ error: '无权访问此项目' });
    }

    const [rows] = await pool.query(
      `
      SELECT snapshot_id, snapshot_json, created_at
      FROM project_snapshots
      WHERE project_id = ?
      ORDER BY snapshot_id DESC
      LIMIT 1
      `,
      [projectId]
    );
    if (!rows.length) return res.status(404).json({ error: 'Project snapshot not found' });

    const [projRows] = await pool.query(
      'SELECT is_public, allow_join_requests FROM projects WHERE project_id = ? LIMIT 1',
      [projectId]
    );
    const projFlags = projRows[0] || {};

    let snapshot;
    try {
      snapshot = JSON.parse(rows[0].snapshot_json || '{}');
    } catch (e) {
      return res.status(500).json({ error: 'Project snapshot JSON is invalid' });
    }

    return res.json({
      projectId,
      snapshotId: rows[0].snapshot_id,
      savedAt: rows[0].created_at,
      project: snapshot.project || null,
      pages: Array.isArray(snapshot.pages) ? snapshot.pages : [],
      annotations: Array.isArray(snapshot.annotations) ? snapshot.annotations : [],
      customChars: Array.isArray(snapshot.customChars) ? snapshot.customChars : [],
      exportedAt: snapshot.exportedAt || null,
      userRole,
      isPublic: Boolean(projFlags.is_public),
      allowJoinRequests: Boolean(projFlags.allow_join_requests),
    });
  } catch (err) {
    console.error('get latest project failed:', err?.message || err);
    return res.status(500).json({ error: err?.message || 'Failed to load latest project' });
  }
});

app.get('/api/projects/:projectId/download', async (req, res) => {
  try {
    const session = getSessionFromRequest(req);
    if (!session) {
      return res.status(401).json({ error: '未登录或会话已失效' });
    }
    const projectId = String(req.params.projectId);
    const userRole = await getProjectMemberRole(projectId, session.userId);
    if (!userRole) {
      return res.status(403).json({ error: '无权访问此项目' });
    }

    const [rows] = await pool.query(
      `
      SELECT snapshot_json
      FROM project_snapshots
      WHERE project_id = ?
      ORDER BY snapshot_id DESC
      LIMIT 1
      `,
      [projectId]
    );
    if (!rows.length) return res.status(404).json({ error: 'Project snapshot not found' });

    const raw = rows[0].snapshot_json || '{}';
    await logActivity(pool, {
      projectId,
      userId: session.userId,
      actionType: 'project_exported',
      actionDetail: { format: 'sdocproj', fileSize: Buffer.byteLength(raw, 'utf8') },
      req,
    });

    const fileName = `${projectId}.sdocproj`;
    res.setHeader('Content-Type', 'application/json; charset=utf-8');
    res.setHeader('Content-Disposition', `attachment; filename="${encodeURIComponent(fileName)}"`);
    return res.send(raw);
  } catch (err) {
    console.error('download project failed:', err?.message || err);
    return res.status(500).json({ error: err?.message || 'Failed to download project' });
  }
});

app.delete('/api/projects', async (req, res) => {
  try {
    const session = getSessionFromRequest(req);
    if (!session) {
      return res.status(401).json({ error: '未登录或会话已失效' });
    }

    const ids = Array.isArray(req.body?.projectIds) ? req.body.projectIds.map(String) : [];
    if (!ids.length) return res.status(400).json({ error: 'projectIds is required' });

    for (const id of ids) {
      const role = await getProjectMemberRole(id, session.userId);
      if (role !== 'admin') {
        return res.status(403).json({ error: `无权删除项目：${id}` });
      }
    }

    const placeholders = ids.map(() => '?').join(', ');
    const [result] = await pool.query(
      `DELETE FROM projects WHERE project_id IN (${placeholders})`,
      ids
    );

    return res.json({ ok: true, deleted: result?.affectedRows || 0 });
  } catch (err) {
    console.error('delete projects failed:', err?.message || err);
    return res.status(500).json({ error: err?.message || 'Failed to delete projects' });
  }
});

// --------------------------
// 生成文件：保存 exportXml XML（/api/exports）
// --------------------------
app.post('/api/exports', async (req, res) => {
  try {
    const session = getSessionFromRequest(req);
    if (!session) {
      return res.status(401).json({ error: '未登录或会话已失效' });
    }

    const { projectId, exportType, xmlContent } = req.body || {};
    if (!xmlContent) return res.status(400).json({ error: 'Missing xmlContent' });

    const xml = String(xmlContent);
    const type = exportType || 'xml';

    let pid = projectId ? String(projectId) : null;
    if (pid === 'null' || pid === 'undefined') pid = null;

    if (pid) {
      const role = await getProjectMemberRole(pid, session.userId);
      if (!role) {
        return res.status(403).json({ error: '无权关联此导出到项目' });
      }
      await logActivity(pool, {
        projectId: pid,
        userId: session.userId,
        actionType: 'project_exported',
        actionDetail: { format: type, fileSize: Buffer.byteLength(xml, 'utf8') },
        req,
      });
    }

    const [result] = await pool.query(
      'INSERT INTO exports (project_id, export_type, xml_content) VALUES (?, ?, ?)',
      [pid, type, xml]
    );

    return res.json({ ok: true, exportId: result?.insertId || null });
  } catch (err) {
    console.error('save export failed:', err?.message || err);
    return res.status(500).json({ error: err?.message || 'Failed to save export' });
  }
});

app.get('/api/exports/:exportId', async (req, res) => {
  try {
    const exportId = String(req.params.exportId);
    const [rows] = await pool.query('SELECT export_type, xml_content FROM exports WHERE export_id = ?', [exportId]);
    if (!rows || rows.length === 0) return res.status(404).send('Not found');

    const xmlContent = rows[0].xml_content || '';
    res.setHeader('Content-Type', 'application/xml; charset=utf-8');
    return res.send(xmlContent);
  } catch (err) {
    console.error('load export failed:', err?.message || err);
    return res.status(500).json({ error: err?.message || 'Failed to load export' });
  }
});

// --------------------------
// Health
// --------------------------
app.get('/api/health', (req, res) => res.json({ ok: true }));

app.get('/api/collab/status', (req, res) => {
  const projectId = String(req.query?.projectId || '').trim();
  if (!projectId) {
    return res.status(400).json({ error: 'projectId is required' });
  }
  const members = buildPresenceMembers(projectId);
  return res.json({
    ok: true,
    projectId,
    onlineCount: members.length,
    users: members
  });
});

async function main() {
  pool = createPool();
  await initDb(pool);

  app.use('/api', createProjectManagementRouter(pool, { disconnectUserSocketsForProject }));

  const port = process.env.PORT ? Number(process.env.PORT) : 8000;
  const server = http.createServer(app);
  setupCollabWebSocket(server);
  server.listen(port, () => {
    console.log(`Node backend listening on http://localhost:${port}`);
  });
}

main().catch((err) => {
  console.error('Server start failed:', err?.message || err);
  process.exit(1);
});

