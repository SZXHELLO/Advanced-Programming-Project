const express = require('express');
const crypto = require('crypto');
const { getSessionFromRequest } = require('../sessionService');
const { logActivity } = require('../utils/activityLogger');
const {
  requireAuth,
  createCheckProjectMember,
  requireAdmin,
  requireEditor,
} = require('../middleware/projectAuth');

function randomToken() {
  return crypto.randomBytes(32).toString('hex');
}

function publicJoinUrl(req, token) {
  const envOrigin = process.env.PUBLIC_APP_ORIGIN || process.env.INVITATION_PUBLIC_ORIGIN;
  if (envOrigin) {
    return `${envOrigin.replace(/\/$/, '')}/join.html?token=${encodeURIComponent(token)}`;
  }
  const proto = req.headers['x-forwarded-proto'] || req.protocol || 'http';
  const host = req.get('host') || 'localhost';
  return `${proto}://${host}/join.html?token=${encodeURIComponent(token)}`;
}

module.exports = function createProjectManagementRouter(pool, { disconnectUserSocketsForProject }) {
  const router = express.Router();
  const checkProjectMember = createCheckProjectMember(pool);

  // --- Public: validate link token (no auth) ---
  router.get('/invitation-links/:token/info', async (req, res) => {
    try {
      const token = String(req.params.token || '').trim();
      if (!token) return res.status(400).json({ error: '缺少 token' });

      const [invRows] = await pool.query(
        `
        SELECT i.*, p.title AS project_title,
               u.username AS inviter_username, u.display_name AS inviter_display
        FROM project_invitations i
        JOIN projects p ON p.project_id = i.project_id
        JOIN users u ON u.user_id = i.inviter_id
        WHERE i.token = ? AND i.invitation_type = 'link'
        LIMIT 1
        `,
        [token]
      );
      if (!invRows.length) {
        return res.status(404).json({ error: '链接已被撤销' });
      }
      const inv = invRows[0];
      if (inv.expires_at && new Date(inv.expires_at) < new Date()) {
        return res.status(400).json({ error: '邀请已过期' });
      }
      const maxUses = Number(inv.max_uses);
      const used = Number(inv.used_count || 0);
      // 次数用尽时 join 流程会把 status 置为 accepted，必须先于 status 判断，否则会误报「已撤销」
      if (maxUses !== -1 && used >= maxUses) {
        return res.status(400).json({ error: '邀请链接已达使用上限' });
      }
      if (inv.status !== 'pending') {
        return res.status(400).json({ error: '链接已被撤销' });
      }

      return res.json({
        token,
        projectId: inv.project_id,
        projectName: inv.project_title,
        inviterName: inv.inviter_display || inv.inviter_username,
        role: inv.role,
        maxUses,
        usedCount: used,
        expiresAt: inv.expires_at,
      });
    } catch (err) {
      console.error('invitation info:', err?.message || err);
      return res.status(500).json({ error: '服务器错误' });
    }
  });

  router.post('/join', requireAuth, async (req, res) => {
    try {
      const token = String(req.body?.token || '').trim();
      if (!token) return res.status(400).json({ error: '缺少 token' });
      const session = req.sessionUser;

      const [invRows] = await pool.query(
        `
        SELECT * FROM project_invitations
        WHERE token = ? AND invitation_type = 'link' AND status = 'pending'
        LIMIT 1
        `,
        [token]
      );
      if (!invRows.length) {
        return res.status(400).json({ error: '链接已被撤销' });
      }
      const inv = invRows[0];
      if (inv.expires_at && new Date(inv.expires_at) < new Date()) {
        return res.status(400).json({ error: '邀请已过期' });
      }
      const maxUses = Number(inv.max_uses);
      const used = Number(inv.used_count || 0);
      if (maxUses !== -1 && used >= maxUses) {
        return res.status(400).json({ error: '邀请链接已达使用上限' });
      }

      const projectId = inv.project_id;
      const [mem] = await pool.query(
        'SELECT 1 FROM project_members WHERE project_id = ? AND user_id = ? LIMIT 1',
        [projectId, session.userId]
      );
      if (mem.length) {
        return res.status(409).json({ error: '您已是该项目成员' });
      }

      const conn = await pool.getConnection();
      let projectName = projectId;
      try {
        await conn.beginTransaction();
        await conn.query(
          `
          INSERT INTO project_members (project_id, user_id, role, invited_by)
          VALUES (?, ?, ?, ?)
          `,
          [projectId, session.userId, inv.role, inv.inviter_id]
        );
        await conn.query(
          'UPDATE project_invitations SET used_count = used_count + 1 WHERE id = ?',
          [inv.id]
        );
        const [upd] = await conn.query(
          'SELECT max_uses, used_count FROM project_invitations WHERE id = ?',
          [inv.id]
        );
        const mu = Number(upd[0]?.max_uses);
        const uc = Number(upd[0]?.used_count);
        if (mu !== -1 && uc >= mu) {
          await conn.query("UPDATE project_invitations SET status = 'accepted' WHERE id = ?", [inv.id]);
        }

        const [pt] = await conn.query('SELECT title FROM projects WHERE project_id = ?', [projectId]);
        projectName = pt[0]?.title || projectId;
        await conn.commit();
      } catch (e) {
        await conn.rollback();
        throw e;
      } finally {
        conn.release();
      }

      await logActivity(pool, {
        projectId,
        userId: session.userId,
        actionType: 'member_joined',
        actionDetail: { via: 'link', role: inv.role },
        req,
      });

      return res.json({
        success: true,
        projectId,
        projectName,
        role: inv.role,
      });
    } catch (err) {
      console.error('join:', err?.message || err);
      return res.status(500).json({ error: err?.message || '加入失败' });
    }
  });

  // --- My invitations (username type) ---
  router.get('/invitations', requireAuth, async (req, res) => {
    try {
      const session = req.sessionUser;
      const [rows] = await pool.query(
        `
        SELECT i.id, i.project_id AS projectId, p.title AS projectName,
               u.username AS inviterName, i.role, i.invitation_type AS type,
               i.created_at AS createdAt, i.expires_at AS expiresAt, i.status
        FROM project_invitations i
        JOIN projects p ON p.project_id = i.project_id
        JOIN users u ON u.user_id = i.inviter_id
        WHERE i.invitation_type = 'username' AND i.invitee_id = ? AND i.status = 'pending'
        ORDER BY i.created_at DESC
        `,
        [session.userId]
      );
      return res.json({ invitations: rows });
    } catch (err) {
      console.error('list invitations:', err?.message || err);
      return res.status(500).json({ error: '服务器错误' });
    }
  });

  router.post('/invitations/:invitationId/respond', requireAuth, async (req, res) => {
    try {
      const invitationId = Number(req.params.invitationId);
      const action = String(req.body?.action || '');
      if (!['accept', 'reject'].includes(action)) {
        return res.status(400).json({ error: '无效操作' });
      }
      const session = req.sessionUser;

      const [invRows] = await pool.query(
        'SELECT * FROM project_invitations WHERE id = ? AND invitee_id = ? AND status = ? LIMIT 1',
        [invitationId, session.userId, 'pending']
      );
      if (!invRows.length) {
        return res.status(404).json({ error: '邀请不存在' });
      }
      const inv = invRows[0];
      if (inv.expires_at && new Date(inv.expires_at) < new Date()) {
        return res.status(400).json({ error: '邀请已过期' });
      }

      if (action === 'reject') {
        await pool.query(
          "UPDATE project_invitations SET status = 'rejected', responded_at = NOW() WHERE id = ?",
          [invitationId]
        );
        return res.json({ ok: true });
      }

      const [mem] = await pool.query(
        'SELECT 1 FROM project_members WHERE project_id = ? AND user_id = ? LIMIT 1',
        [inv.project_id, session.userId]
      );
      if (mem.length) {
        return res.status(409).json({ error: '您已是该项目成员' });
      }

      const conn = await pool.getConnection();
      try {
        await conn.beginTransaction();
        await conn.query(
          `
          INSERT INTO project_members (project_id, user_id, role, invited_by)
          VALUES (?, ?, ?, ?)
          `,
          [inv.project_id, session.userId, inv.role, inv.inviter_id]
        );
        await conn.query(
          "UPDATE project_invitations SET status = 'accepted', responded_at = NOW() WHERE id = ?",
          [invitationId]
        );
        await conn.commit();
      } catch (e) {
        await conn.rollback();
        throw e;
      } finally {
        conn.release();
      }

      await logActivity(pool, {
        projectId: inv.project_id,
        userId: session.userId,
        actionType: 'member_joined',
        actionDetail: { via: 'invitation', role: inv.role },
        req,
      });

      return res.json({ ok: true, projectId: inv.project_id });
    } catch (err) {
      console.error('respond invitation:', err?.message || err);
      return res.status(500).json({ error: '服务器错误' });
    }
  });

  router.get('/projects/public', requireAuth, async (req, res) => {
    try {
      const [rows] = await pool.query(
        `
        SELECT project_id, title, author, dynasty, book, volume, created_at, updated_at
        FROM projects
        WHERE is_public = 1
        ORDER BY updated_at DESC
        LIMIT 200
        `
      );
      return res.json({ projects: rows });
    } catch (err) {
      console.error('public projects:', err?.message || err);
      return res.status(500).json({ error: '服务器错误' });
    }
  });

  router.get('/projects/:projectId/join-info', async (req, res) => {
    try {
      const projectId = String(req.params.projectId || '');
      const [rows] = await pool.query(
        `
        SELECT p.project_id, p.title, p.author, p.is_public, p.allow_join_requests, p.owner_id,
               u.display_name AS owner_display, u.username AS owner_username
        FROM projects p
        LEFT JOIN users u ON u.user_id = p.owner_id
        WHERE p.project_id = ?
        LIMIT 1
        `,
        [projectId]
      );
      if (!rows.length) return res.status(404).json({ error: '项目不存在' });
      const p = rows[0];
      if (!p.is_public && !p.allow_join_requests) {
        return res.status(403).json({ error: '该项目不允许申请加入' });
      }
      return res.json({
        projectId: p.project_id,
        projectName: p.title,
        description: [p.author].filter(Boolean).join(' · '),
        ownerName: p.owner_display || p.owner_username || '',
        allowJoinRequests: Boolean(p.allow_join_requests),
        isPublic: Boolean(p.is_public),
      });
    } catch (err) {
      console.error('join-info:', err?.message || err);
      return res.status(500).json({ error: '服务器错误' });
    }
  });

  router.patch('/projects/:projectId/settings', requireAuth, checkProjectMember, requireAdmin, async (req, res) => {
    try {
      const projectId = req.projectId;
      const { isPublic, allowJoinRequests } = req.body || {};
      const toBool = (v) => {
        if (typeof v === 'boolean') return v;
        if (v === 1 || v === '1') return true;
        if (v === 0 || v === '0') return false;
        if (typeof v === 'string') {
          const s = v.trim().toLowerCase();
          if (s === 'true') return true;
          if (s === 'false') return false;
        }
        return undefined;
      };
      const pub = toBool(isPublic);
      const allowJr = toBool(allowJoinRequests);
      const updates = [];
      const vals = [];
      if (typeof pub === 'boolean') {
        updates.push('is_public = ?');
        vals.push(pub ? 1 : 0);
      }
      if (typeof allowJr === 'boolean') {
        updates.push('allow_join_requests = ?');
        vals.push(allowJr ? 1 : 0);
      }
      if (!updates.length) return res.status(400).json({ error: '无有效字段' });
      vals.push(projectId);
      await pool.query(`UPDATE projects SET ${updates.join(', ')} WHERE project_id = ?`, vals);

      await logActivity(pool, {
        projectId,
        userId: req.sessionUser.userId,
        actionType: 'project_updated',
        actionDetail: { changes: { isPublic: pub, allowJoinRequests: allowJr } },
        req,
      });
      return res.json({ ok: true });
    } catch (err) {
      console.error('settings:', err?.message || err);
      return res.status(500).json({ error: '服务器错误' });
    }
  });

  // --- Members & invitations ---
  router.get('/projects/:projectId/members', requireAuth, checkProjectMember, async (req, res) => {
    try {
      const projectId = req.projectId;
      const [rows] = await pool.query(
        `
        SELECT pm.user_id AS userId, u.username, u.display_name AS displayName, pm.role,
               pm.joined_at AS joinedAt, pm.invited_by AS invitedById,
               inviter.username AS invitedByUsername
        FROM project_members pm
        JOIN users u ON u.user_id = pm.user_id
        LEFT JOIN users inviter ON inviter.user_id = pm.invited_by
        WHERE pm.project_id = ?
        ORDER BY pm.joined_at ASC
        `,
        [projectId]
      );
      const members = rows.map((r) => ({
        userId: r.userId,
        username: r.username,
        role: r.role,
        joinedAt: r.joinedAt,
        invitedBy: r.invitedByUsername || null,
      }));
      return res.json({ members });
    } catch (err) {
      console.error('members:', err?.message || err);
      return res.status(500).json({ error: '服务器错误' });
    }
  });

  router.post('/projects/:projectId/invitations', requireAuth, checkProjectMember, requireAdmin, async (req, res) => {
    try {
      const projectId = req.projectId;
      const username = String(req.body?.username || '').trim();
      const role = String(req.body?.role || 'viewer');
      if (!username) return res.status(400).json({ error: '缺少用户名' });
      if (!['editor', 'viewer'].includes(role)) {
        return res.status(400).json({ error: '无效角色' });
      }

      const [uRows] = await pool.query('SELECT user_id FROM users WHERE username = ? LIMIT 1', [username]);
      if (!uRows.length) return res.status(404).json({ error: '用户不存在' });
      const inviteeId = uRows[0].user_id;

      const [existing] = await pool.query(
        'SELECT 1 FROM project_members WHERE project_id = ? AND user_id = ? LIMIT 1',
        [projectId, inviteeId]
      );
      if (existing.length) return res.status(409).json({ error: '该用户已是成员' });

      const token = randomToken();
      const expiresAt = new Date(Date.now() + 7 * 24 * 60 * 60 * 1000);

      const [ins] = await pool.query(
        `
        INSERT INTO project_invitations
          (project_id, inviter_id, invitee_username, invitee_id, role, status, token,
           invitation_type, max_uses, used_count, expires_at)
        VALUES (?, ?, ?, ?, ?, 'pending', ?, 'username', 1, 0, ?)
        `,
        [projectId, req.sessionUser.userId, username, inviteeId, role, token, expiresAt]
      );

      await logActivity(pool, {
        projectId,
        userId: req.sessionUser.userId,
        actionType: 'member_invited',
        actionDetail: { inviteeUsername: username, role },
        req,
      });

      return res.json({
        invitationId: ins.insertId,
        token,
        expiresAt: expiresAt.toISOString(),
      });
    } catch (err) {
      console.error('invite:', err?.message || err);
      return res.status(500).json({ error: '服务器错误' });
    }
  });

  router.post('/projects/:projectId/invitation-links', requireAuth, checkProjectMember, requireAdmin, async (req, res) => {
    try {
      const projectId = req.projectId;
      const role = String(req.body?.role || 'viewer');
      let maxUses = Number(req.body?.maxUses);
      const expiresInDays = Math.min(30, Math.max(1, Number(req.body?.expiresInDays || 7)));
      if (!['editor', 'viewer'].includes(role)) {
        return res.status(400).json({ error: '无效角色' });
      }
      if (Number.isNaN(maxUses)) maxUses = 10;
      const token = randomToken();
      const expiresAt = new Date(Date.now() + expiresInDays * 24 * 60 * 60 * 1000);

      const [ins] = await pool.query(
        `
        INSERT INTO project_invitations
          (project_id, inviter_id, invitee_username, invitee_id, role, status, token,
           invitation_type, max_uses, used_count, expires_at)
        VALUES (?, ?, NULL, NULL, ?, 'pending', ?, 'link', ?, 0, ?)
        `,
        [projectId, req.sessionUser.userId, role, token, maxUses, expiresAt]
      );

      await logActivity(pool, {
        projectId,
        userId: req.sessionUser.userId,
        actionType: 'invitation_link_created',
        actionDetail: { role, maxUses, expiresAt: expiresAt.toISOString() },
        req,
      });

      return res.json({
        invitationId: ins.insertId,
        token,
        invitationUrl: publicJoinUrl(req, token),
        role,
        maxUses,
        expiresAt: expiresAt.toISOString(),
      });
    } catch (err) {
      console.error('invitation-links POST:', err?.message || err);
      return res.status(500).json({ error: '服务器错误' });
    }
  });

  router.get('/projects/:projectId/invitation-links', requireAuth, checkProjectMember, requireAdmin, async (req, res) => {
    try {
      const projectId = req.projectId;
      const [rows] = await pool.query(
        `
        SELECT i.id, i.token, i.role, i.max_uses AS maxUses, i.used_count AS usedCount, i.status,
               i.created_at AS createdAt, i.expires_at AS expiresAt,
               u.username AS createdBy
        FROM project_invitations i
        JOIN users u ON u.user_id = i.inviter_id
        WHERE i.project_id = ? AND i.invitation_type = 'link'
        ORDER BY i.created_at DESC
        `,
        [projectId]
      );
      const links = rows.map((r) => ({
        id: String(r.id),
        token: r.token,
        url: publicJoinUrl(req, r.token),
        role: r.role,
        maxUses: r.maxUses,
        usedCount: r.usedCount,
        status: r.status === 'pending' && r.expires_at && new Date(r.expires_at) < new Date() ? 'expired' : r.status,
        createdBy: r.createdBy,
        createdAt: r.createdAt,
        expiresAt: r.expiresAt,
      }));
      return res.json({ links });
    } catch (err) {
      console.error('invitation-links GET:', err?.message || err);
      return res.status(500).json({ error: '服务器错误' });
    }
  });

  router.delete('/projects/:projectId/invitation-links/:invitationId', requireAuth, checkProjectMember, requireAdmin, async (req, res) => {
    try {
      const projectId = req.projectId;
      const rawId = String(req.params.invitationId || '').trim();
      if (!/^\d+$/.test(rawId)) {
        return res.status(400).json({ error: '无效的邀请ID' });
      }
      const invitationId = rawId;
      const [r] = await pool.query(
        'SELECT id FROM project_invitations WHERE id = ? AND project_id = ? AND invitation_type = ? LIMIT 1',
        [invitationId, projectId, 'link']
      );
      if (!r.length) return res.status(404).json({ error: '未找到' });
      await pool.query(
        'DELETE FROM project_invitations WHERE id = ? AND project_id = ? AND invitation_type = ?',
        [invitationId, projectId, 'link']
      );
      await logActivity(pool, {
        projectId,
        userId: req.sessionUser.userId,
        actionType: 'project_updated',
        actionDetail: { invitationLinkDeleted: invitationId },
        req,
      });
      return res.json({ ok: true });
    } catch (err) {
      console.error('revoke link:', err?.message || err);
      return res.status(500).json({ error: '服务器错误' });
    }
  });

  router.patch('/projects/:projectId/members/:userId', requireAuth, checkProjectMember, requireAdmin, async (req, res) => {
    try {
      const projectId = req.projectId;
      const targetUserId = Number(req.params.userId);
      const newRole = String(req.body?.role || '');
      if (!['admin', 'editor', 'viewer'].includes(newRole)) {
        return res.status(400).json({ error: '无效角色' });
      }

      const [pRows] = await pool.query('SELECT owner_id FROM projects WHERE project_id = ? LIMIT 1', [projectId]);
      const ownerId = pRows[0]?.owner_id;
      if (ownerId && Number(ownerId) === targetUserId) {
        return res.status(400).json({ error: '不能修改项目创建者角色' });
      }
      if (Number(req.sessionUser.userId) === targetUserId && req.projectRole === 'admin' && newRole !== 'admin') {
        const [cnt] = await pool.query(
          'SELECT COUNT(*) AS n FROM project_members WHERE project_id = ? AND role = ?',
          [projectId, 'admin']
        );
        if (Number(cnt[0]?.n) <= 1) {
          return res.status(400).json({ error: '至少需要保留一名管理员' });
        }
      }

      const [oldRows] = await pool.query(
        'SELECT role FROM project_members WHERE project_id = ? AND user_id = ? LIMIT 1',
        [projectId, targetUserId]
      );
      if (!oldRows.length) return res.status(404).json({ error: '成员不存在' });
      const oldRole = oldRows[0].role;

      await pool.query(
        'UPDATE project_members SET role = ? WHERE project_id = ? AND user_id = ?',
        [newRole, projectId, targetUserId]
      );

      await logActivity(pool, {
        projectId,
        userId: req.sessionUser.userId,
        actionType: 'member_role_changed',
        actionDetail: { oldRole, newRole, targetUserId },
        targetUserId,
        req,
      });
      return res.json({ ok: true });
    } catch (err) {
      console.error('patch member:', err?.message || err);
      return res.status(500).json({ error: '服务器错误' });
    }
  });

  router.delete('/projects/:projectId/members/:userId', requireAuth, checkProjectMember, requireAdmin, async (req, res) => {
    try {
      const projectId = req.projectId;
      const targetUserId = Number(req.params.userId);

      const [pRows] = await pool.query('SELECT owner_id FROM projects WHERE project_id = ? LIMIT 1', [projectId]);
      const ownerId = pRows[0]?.owner_id;
      if (ownerId && Number(ownerId) === targetUserId) {
        return res.status(400).json({ error: '不能移除项目创建者' });
      }
      if (Number(req.sessionUser.userId) === targetUserId) {
        return res.status(400).json({ error: '不能移除自己' });
      }

      const [uRows] = await pool.query('SELECT username FROM users WHERE user_id = ? LIMIT 1', [targetUserId]);
      const removedUsername = uRows[0]?.username || String(targetUserId);

      const [del] = await pool.query(
        'DELETE FROM project_members WHERE project_id = ? AND user_id = ?',
        [projectId, targetUserId]
      );
      if (!del.affectedRows) return res.status(404).json({ error: '成员不存在' });

      await logActivity(pool, {
        projectId,
        userId: req.sessionUser.userId,
        actionType: 'member_removed',
        actionDetail: { removedUsername },
        targetUserId,
        req,
      });

      disconnectUserSocketsForProject(projectId, targetUserId);
      return res.json({ ok: true });
    } catch (err) {
      console.error('delete member:', err?.message || err);
      return res.status(500).json({ error: '服务器错误' });
    }
  });

  /** 当前用户主动退出项目（非管理员移除他人） */
  router.post('/projects/:projectId/leave', requireAuth, checkProjectMember, async (req, res) => {
    try {
      const projectId = req.projectId;
      const userId = Number(req.sessionUser.userId);

      const [pRows] = await pool.query('SELECT owner_id FROM projects WHERE project_id = ? LIMIT 1', [projectId]);
      const ownerId = pRows[0]?.owner_id;
      if (ownerId && Number(ownerId) === userId) {
        return res.status(400).json({ error: '项目创建者不能直接退出，请先转移所有权或删除项目' });
      }

      if (req.projectRole === 'admin') {
        const [cnt] = await pool.query(
          'SELECT COUNT(*) AS n FROM project_members WHERE project_id = ? AND role = ?',
          [projectId, 'admin']
        );
        if (Number(cnt[0]?.n) <= 1) {
          return res.status(400).json({ error: '至少需要保留一名管理员，请先指定其他管理员后再退出' });
        }
      }

      const [del] = await pool.query(
        'DELETE FROM project_members WHERE project_id = ? AND user_id = ?',
        [projectId, userId]
      );
      if (!del.affectedRows) return res.status(404).json({ error: '成员不存在' });

      await logActivity(pool, {
        projectId,
        userId,
        actionType: 'member_left',
        actionDetail: { self: true },
        req,
      });

      disconnectUserSocketsForProject(projectId, userId);
      return res.json({ ok: true });
    } catch (err) {
      console.error('leave project:', err?.message || err);
      return res.status(500).json({ error: '服务器错误' });
    }
  });

  // --- Join requests ---
  router.post('/projects/:projectId/join-requests', requireAuth, async (req, res) => {
    try {
      const projectId = String(req.params.projectId || '');
      const session = req.sessionUser;
      const requestedRole = String(req.body?.requestedRole || 'viewer');
      const message = String(req.body?.message || '').trim();
      if (!['editor', 'viewer'].includes(requestedRole)) {
        return res.status(400).json({ error: '无效角色' });
      }
      if (!message) return res.status(400).json({ error: '请填写申请理由' });

      const [pRows] = await pool.query(
        'SELECT allow_join_requests FROM projects WHERE project_id = ? LIMIT 1',
        [projectId]
      );
      if (!pRows.length) return res.status(404).json({ error: '项目不存在' });
      if (!pRows[0].allow_join_requests) {
        return res.status(403).json({ error: '该项目未开放申请' });
      }

      const [mem] = await pool.query(
        'SELECT 1 FROM project_members WHERE project_id = ? AND user_id = ? LIMIT 1',
        [projectId, session.userId]
      );
      if (mem.length) return res.status(409).json({ error: '您已是成员' });

      const [pending] = await pool.query(
        `SELECT id FROM project_join_requests WHERE project_id = ? AND user_id = ? AND status = 'pending' LIMIT 1`,
        [projectId, session.userId]
      );
      if (pending.length) return res.status(409).json({ error: '您已有待审批的申请' });

      const [ins] = await pool.query(
        `
        INSERT INTO project_join_requests (project_id, user_id, requested_role, message, status)
        VALUES (?, ?, ?, ?, 'pending')
        `,
        [projectId, session.userId, requestedRole, message]
      );

      await logActivity(pool, {
        projectId,
        userId: session.userId,
        actionType: 'join_request_submitted',
        actionDetail: { requestedRole, message: message.slice(0, 500) },
        req,
      });

      return res.json({ requestId: ins.insertId, status: 'pending' });
    } catch (err) {
      console.error('join-request:', err?.message || err);
      return res.status(500).json({ error: '服务器错误' });
    }
  });

  router.get('/projects/:projectId/join-requests', requireAuth, checkProjectMember, requireAdmin, async (req, res) => {
    try {
      const projectId = req.projectId;
      const status = String(req.query.status || 'pending');
      let where = 'project_id = ?';
      const vals = [projectId];
      if (status !== 'all') {
        where += ' AND status = ?';
        vals.push(status);
      }
      const [rows] = await pool.query(
        `
        SELECT r.id, r.user_id AS userId, u.username, r.requested_role AS requestedRole,
               r.message, r.status, r.created_at AS createdAt, r.review_message AS reviewMessage
        FROM project_join_requests r
        JOIN users u ON u.user_id = r.user_id
        WHERE ${where}
        ORDER BY r.created_at DESC
        `,
        vals
      );
      return res.json({ requests: rows });
    } catch (err) {
      console.error('join-requests list:', err?.message || err);
      return res.status(500).json({ error: '服务器错误' });
    }
  });

  router.post('/projects/:projectId/join-requests/:requestId/review', requireAuth, checkProjectMember, requireAdmin, async (req, res) => {
    try {
      const projectId = req.projectId;
      const requestId = Number(req.params.requestId);
      const action = String(req.body?.action || '');
      const grantedRole = String(req.body?.grantedRole || 'viewer');
      const message = String(req.body?.message || '').trim();
      if (!['approve', 'reject'].includes(action)) {
        return res.status(400).json({ error: '无效操作' });
      }
      if (action === 'approve' && !['editor', 'viewer'].includes(grantedRole)) {
        return res.status(400).json({ error: '无效角色' });
      }

      const [reqRows] = await pool.query(
        'SELECT * FROM project_join_requests WHERE id = ? AND project_id = ? LIMIT 1',
        [requestId, projectId]
      );
      if (!reqRows.length) return res.status(404).json({ error: '申请不存在' });
      const jr = reqRows[0];
      if (jr.status !== 'pending') {
        return res.status(400).json({ error: '申请已处理' });
      }

      if (action === 'reject') {
        await pool.query(
          `UPDATE project_join_requests SET status = 'rejected', reviewed_by = ?, review_message = ?, reviewed_at = NOW() WHERE id = ?`,
          [req.sessionUser.userId, message || null, requestId]
        );
        await logActivity(pool, {
          projectId,
          userId: req.sessionUser.userId,
          actionType: 'join_request_rejected',
          actionDetail: { requestId, reason: message },
          targetUserId: jr.user_id,
          req,
        });
        return res.json({ success: true, requestId });
      }

      const [mem] = await pool.query(
        'SELECT 1 FROM project_members WHERE project_id = ? AND user_id = ? LIMIT 1',
        [projectId, jr.user_id]
      );
      if (mem.length) {
        return res.status(409).json({ error: '用户已是成员' });
      }

      const conn = await pool.getConnection();
      try {
        await conn.beginTransaction();
        await conn.query(
          `
          INSERT INTO project_members (project_id, user_id, role, invited_by)
          VALUES (?, ?, ?, ?)
          `,
          [projectId, jr.user_id, grantedRole, req.sessionUser.userId]
        );
        await conn.query(
          `UPDATE project_join_requests SET status = 'approved', reviewed_by = ?, review_message = ?, reviewed_at = NOW() WHERE id = ?`,
          [req.sessionUser.userId, message || null, requestId]
        );
        await conn.commit();
      } catch (e) {
        await conn.rollback();
        throw e;
      } finally {
        conn.release();
      }

      await logActivity(pool, {
        projectId,
        userId: req.sessionUser.userId,
        actionType: 'join_request_approved',
        actionDetail: { requestId, grantedRole },
        targetUserId: jr.user_id,
        req,
      });

      return res.json({ success: true, requestId, userId: jr.user_id, grantedRole });
    } catch (err) {
      console.error('join review:', err?.message || err);
      return res.status(500).json({ error: '服务器错误' });
    }
  });

  // --- Activity logs ---
  router.get('/projects/:projectId/activity-logs', requireAuth, checkProjectMember, async (req, res) => {
    try {
      const projectId = req.projectId;
      const page = Math.max(1, Number(req.query.page || 1));
      const limit = Math.min(200, Math.max(1, Number(req.query.limit || 50)));
      const offset = (page - 1) * limit;
      const actionType = req.query.actionType ? String(req.query.actionType) : null;
      const userIdFilter = req.query.userId ? Number(req.query.userId) : null;
      const startDate = req.query.startDate ? String(req.query.startDate) : null;
      const endDate = req.query.endDate ? String(req.query.endDate) : null;

      let where = 'l.project_id = ?';
      const vals = [projectId];
      if (actionType) {
        where += ' AND l.action_type = ?';
        vals.push(actionType);
      }
      if (userIdFilter) {
        where += ' AND l.user_id = ?';
        vals.push(userIdFilter);
      }
      if (startDate) {
        where += ' AND l.created_at >= ?';
        vals.push(startDate);
      }
      if (endDate) {
        where += ' AND l.created_at < DATE_ADD(?, INTERVAL 1 DAY)';
        vals.push(endDate);
      }

      const [countRows] = await pool.query(
        `SELECT COUNT(*) AS total FROM project_activity_logs l WHERE ${where}`,
        vals
      );
      const totalCount = Number(countRows[0]?.total || 0);
      const totalPages = Math.max(1, Math.ceil(totalCount / limit));

      const [logs] = await pool.query(
        `
        SELECT l.id, l.action_type AS actionType, l.action_detail AS actionDetail, l.ip_address AS ipAddress,
               l.created_at AS createdAt, l.user_id AS actorId, l.target_user_id AS targetUserId,
               u.username AS actorUsername,
               tu.username AS targetUsername
        FROM project_activity_logs l
        LEFT JOIN users u ON u.user_id = l.user_id
        LEFT JOIN users tu ON tu.user_id = l.target_user_id
        WHERE ${where}
        ORDER BY l.id DESC
        LIMIT ? OFFSET ?
        `,
        [...vals, limit, offset]
      );

      const out = logs.map((row) => ({
        id: row.id,
        actionType: row.actionType,
        actionDetail: typeof row.actionDetail === 'string' ? JSON.parse(row.actionDetail || '{}') : row.actionDetail,
        user: row.actorId ? { id: row.actorId, username: row.actorUsername } : null,
        targetUser: row.targetUserId ? { id: row.targetUserId, username: row.targetUsername } : null,
        ipAddress: row.ipAddress,
        createdAt: row.createdAt,
      }));

      return res.json({
        logs: out,
        pagination: {
          currentPage: page,
          totalPages,
          totalCount,
          limit,
        },
      });
    } catch (err) {
      console.error('activity-logs:', err?.message || err);
      return res.status(500).json({ error: '服务器错误' });
    }
  });

  router.get('/projects/:projectId/activity-logs/export', requireAuth, checkProjectMember, requireAdmin, async (req, res) => {
    try {
      const projectId = req.projectId;
      const actionType = req.query.actionType ? String(req.query.actionType) : null;
      const startDate = req.query.startDate ? String(req.query.startDate) : null;
      const endDate = req.query.endDate ? String(req.query.endDate) : null;

      let where = 'l.project_id = ?';
      const vals = [projectId];
      if (actionType) {
        where += ' AND l.action_type = ?';
        vals.push(actionType);
      }
      if (startDate) {
        where += ' AND l.created_at >= ?';
        vals.push(startDate);
      }
      if (endDate) {
        where += ' AND l.created_at < DATE_ADD(?, INTERVAL 1 DAY)';
        vals.push(endDate);
      }

      const [logs] = await pool.query(
        `
        SELECT l.created_at, l.action_type, u.username, l.action_detail, l.ip_address
        FROM project_activity_logs l
        LEFT JOIN users u ON u.user_id = l.user_id
        WHERE ${where}
        ORDER BY l.id DESC
        LIMIT 5000
        `,
        vals
      );

      const header = '\uFEFF时间,操作类型,操作人,操作详情,IP地址\n';
      const lines = logs.map((row) => {
        const t = row.created_at ? new Date(row.created_at).toISOString() : '';
        const detail = typeof row.action_detail === 'string'
          ? row.action_detail
          : JSON.stringify(row.action_detail || {});
        const esc = `"${String(detail).replace(/"/g, '""')}"`;
        return `${t},${row.action_type},${row.username || ''},${esc},${row.ip_address || ''}`;
      });
      res.setHeader('Content-Type', 'text/csv; charset=utf-8');
      res.setHeader('Content-Disposition', `attachment; filename="activity-${projectId}.csv"`);
      return res.send(header + lines.join('\n'));
    } catch (err) {
      console.error('activity export:', err?.message || err);
      return res.status(500).json({ error: '服务器错误' });
    }
  });

  return router;
};
