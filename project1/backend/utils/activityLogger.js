/**
 * @param {import('mysql2/promise').Pool} pool
 */
async function logActivity(pool, {
  projectId,
  userId,
  actionType,
  actionDetail = {},
  targetUserId = null,
  req = null,
}) {
  const ipAddress = req
    ? (req.headers['x-forwarded-for'] || req.socket?.remoteAddress || '').split(',')[0].trim() || null
    : null;
  const userAgent = req ? req.headers['user-agent'] : null;
  const detailJson = typeof actionDetail === 'string' ? actionDetail : JSON.stringify(actionDetail || {});

  await pool.query(
    `
    INSERT INTO project_activity_logs
      (project_id, user_id, action_type, action_detail, target_user_id, ip_address, user_agent)
    VALUES (?, ?, ?, ?, ?, ?, ?)
    `,
    [projectId, userId, actionType, detailJson, targetUserId, ipAddress, userAgent]
  );
}

module.exports = { logActivity };
