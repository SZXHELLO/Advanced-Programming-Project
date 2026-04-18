const express = require('express');
const cors = require('cors');
const multer = require('multer');
const axios = require('axios');
const imageSize = require('image-size');
require('dotenv').config();

const { createPool, initDb } = require('./db');
const { parseDataUrl, bufferToDataUrl } = require('./utils/dataUrl');

const app = express();

// JSON 体积可能会很大（项目快照中含 base64 图片/字库图形）
app.use(express.json({ limit: '120mb' }));
app.use(cors({ origin: '*', credentials: true }));

// 上传走内存（OCR/分辨率获取不落盘）
const upload = multer({
  storage: multer.memoryStorage(),
  limits: { fileSize: 20 * 1024 * 1024 }, // 20MB
});

let pool;

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
    const { project, pages, annotations, customChars, exportedAt } = req.body || {};
    if (!project?.id) return res.status(400).json({ error: 'Missing project.id' });

    const projectId = String(project.id);
    const snapshot = {
      project,
      pages: Array.isArray(pages) ? pages : [],
      annotations: annotations || [],
      customChars: Array.isArray(customChars) ? customChars : [],
      exportedAt: exportedAt || new Date().toISOString(),
    };

    const snapshotJson = JSON.stringify(snapshot);

    await pool.query(
      `
      INSERT INTO projects (project_id, title, author, dynasty, book, volume)
      VALUES (?, ?, ?, ?, ?, ?)
      ON DUPLICATE KEY UPDATE
        title = VALUES(title),
        author = VALUES(author),
        dynasty = VALUES(dynasty),
        book = VALUES(book),
        volume = VALUES(volume)
      `,
      [
        projectId,
        project.title || '',
        project.author || '',
        project.dynasty || '',
        project.book || '',
        project.volume || '',
      ]
    );

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
    const [rows] = await pool.query(
      `
      SELECT
        p.project_id,
        p.title,
        p.author,
        p.dynasty,
        p.book,
        p.volume,
        s.snapshot_id,
        s.created_at AS saved_at
      FROM projects p
      JOIN (
        SELECT project_id, MAX(snapshot_id) AS latest_snapshot_id
        FROM project_snapshots
        GROUP BY project_id
      ) latest ON latest.project_id = p.project_id
      JOIN project_snapshots s ON s.snapshot_id = latest.latest_snapshot_id
      ORDER BY s.created_at DESC
      `
    );
    return res.json({ projects: rows });
  } catch (err) {
    console.error('list projects failed:', err?.message || err);
    return res.status(500).json({ error: err?.message || 'Failed to list projects' });
  }
});

app.get('/api/projects/:projectId/latest', async (req, res) => {
  try {
    const projectId = String(req.params.projectId);
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
    });
  } catch (err) {
    console.error('get latest project failed:', err?.message || err);
    return res.status(500).json({ error: err?.message || 'Failed to load latest project' });
  }
});

app.get('/api/projects/:projectId/download', async (req, res) => {
  try {
    const projectId = String(req.params.projectId);
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

    const fileName = `${projectId}.sdocproj`;
    res.setHeader('Content-Type', 'application/json; charset=utf-8');
    res.setHeader('Content-Disposition', `attachment; filename="${encodeURIComponent(fileName)}"`);
    return res.send(rows[0].snapshot_json || '{}');
  } catch (err) {
    console.error('download project failed:', err?.message || err);
    return res.status(500).json({ error: err?.message || 'Failed to download project' });
  }
});

app.delete('/api/projects', async (req, res) => {
  try {
    const ids = Array.isArray(req.body?.projectIds) ? req.body.projectIds.map(String) : [];
    if (!ids.length) return res.status(400).json({ error: 'projectIds is required' });

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
    const { projectId, exportType, xmlContent } = req.body || {};
    if (!xmlContent) return res.status(400).json({ error: 'Missing xmlContent' });

    const xml = String(xmlContent);
    const type = exportType || 'xml';

    let pid = projectId ? String(projectId) : null;
    if (pid === 'null' || pid === 'undefined') pid = null;

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

async function main() {
  pool = createPool();
  await initDb(pool);

  const port = process.env.PORT ? Number(process.env.PORT) : 8000;
  app.listen(port, () => {
    console.log(`Node backend listening on http://localhost:${port}`);
  });
}

main().catch((err) => {
  console.error('Server start failed:', err?.message || err);
  process.exit(1);
});

