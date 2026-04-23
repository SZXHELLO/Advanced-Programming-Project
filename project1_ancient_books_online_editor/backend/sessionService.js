const crypto = require('crypto');

const SESSION_TTL_MS = 24 * 60 * 60 * 1000;
const sessionStore = new Map();

function normalizePermissions(raw) {
  if (!raw) return [];
  if (Array.isArray(raw)) return raw;
  if (typeof raw === 'string') {
    try {
      const parsed = JSON.parse(raw);
      return Array.isArray(parsed) ? parsed : [];
    } catch (_) {
      return [];
    }
  }
  return [];
}

function issueSession(user) {
  const sessionId = crypto.randomUUID();
  const payload = {
    sessionId,
    userId: user.user_id,
    username: user.username,
    displayName: user.display_name || user.username,
    role: user.role || 'editor',
    permissions: normalizePermissions(user.permissions),
    expiresAt: Date.now() + SESSION_TTL_MS,
  };
  sessionStore.set(sessionId, payload);
  return payload;
}

function getSessionFromRequest(req) {
  const authHeader = String(req.headers.authorization || '');
  const bearer = authHeader.startsWith('Bearer ') ? authHeader.slice(7).trim() : '';
  const sessionId = bearer || String(req.query.sessionId || '');
  if (!sessionId) return null;

  const session = sessionStore.get(sessionId);
  if (!session) return null;
  if (Date.now() > session.expiresAt) {
    sessionStore.delete(sessionId);
    return null;
  }
  return session;
}

function getSessionById(sessionId) {
  if (!sessionId) return null;
  const session = sessionStore.get(String(sessionId));
  if (!session) return null;
  if (Date.now() > session.expiresAt) {
    sessionStore.delete(String(sessionId));
    return null;
  }
  return session;
}

module.exports = {
  SESSION_TTL_MS,
  sessionStore,
  normalizePermissions,
  issueSession,
  getSessionFromRequest,
  getSessionById,
};
