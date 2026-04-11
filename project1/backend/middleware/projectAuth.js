const { getSessionFromRequest } = require('../sessionService');

function requireAuth(req, res, next) {
  const session = getSessionFromRequest(req);
  if (!session) {
    return res.status(401).json({ error: '未登录或会话已失效' });
  }
  req.sessionUser = session;
  next();
}

function createCheckProjectMember(pool) {
  return async function checkProjectMember(req, res, next) {
    const session = getSessionFromRequest(req);
    if (!session) {
      return res.status(401).json({ error: '未登录或会话已失效' });
    }
    const projectId = String(req.params.projectId || '');
    if (!projectId) {
      return res.status(400).json({ error: 'Missing projectId' });
    }
    try {
      const [rows] = await pool.query(
        'SELECT role FROM project_members WHERE project_id = ? AND user_id = ? LIMIT 1',
        [projectId, session.userId]
      );
      if (!rows.length) {
        return res.status(403).json({ error: '无权访问此项目' });
      }
      req.sessionUser = session;
      req.projectRole = rows[0].role;
      req.projectId = projectId;
      next();
    } catch (err) {
      console.error('checkProjectMember:', err?.message || err);
      return res.status(500).json({ error: '服务器错误' });
    }
  };
}

function requireAdmin(req, res, next) {
  if (req.projectRole !== 'admin') {
    return res.status(403).json({ error: '需要管理员权限' });
  }
  next();
}

function requireEditor(req, res, next) {
  if (!['admin', 'editor'].includes(req.projectRole)) {
    return res.status(403).json({ error: '无编辑权限' });
  }
  next();
}

module.exports = {
  requireAuth,
  createCheckProjectMember,
  requireAdmin,
  requireEditor,
};
