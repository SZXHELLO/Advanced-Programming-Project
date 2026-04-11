const mysql = require('mysql2/promise');
const bcrypt = require('bcryptjs');

function createPool() {
  const {
    MYSQL_HOST,
    MYSQL_PORT,
    MYSQL_USER,
    MYSQL_PASSWORD,
    MYSQL_DATABASE,
  } = process.env;

  if (!MYSQL_HOST || !MYSQL_USER || !MYSQL_DATABASE) {
    throw new Error('Missing MySQL env vars (MYSQL_HOST, MYSQL_USER, MYSQL_DATABASE, ...).');
  }

  return mysql.createPool({
    host: MYSQL_HOST,
    port: MYSQL_PORT ? Number(MYSQL_PORT) : 3306,
    user: MYSQL_USER,
    password: MYSQL_PASSWORD || '',
    database: MYSQL_DATABASE,
    waitForConnections: true,
    connectionLimit: 10,
    queueLimit: 0,
    charset: 'utf8mb4',
    namedPlaceholders: true,
    supportBigNumbers: true,
    bigNumberStrings: true,
  });
}

async function initDb(pool) {
  // 说明：这里用 IF NOT EXISTS 让开发期更容易反复启动。
  // 生产环境建议改成正式 migration 管理。
  await pool.query(`
    CREATE TABLE IF NOT EXISTS projects (
      project_id VARCHAR(64) PRIMARY KEY,
      title VARCHAR(255) NOT NULL,
      author VARCHAR(255) DEFAULT '',
      dynasty VARCHAR(255) DEFAULT '',
      book VARCHAR(255) DEFAULT '',
      volume VARCHAR(255) DEFAULT '',
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
  `);

  await pool.query(`
    CREATE TABLE IF NOT EXISTS project_snapshots (
      snapshot_id BIGINT AUTO_INCREMENT PRIMARY KEY,
      project_id VARCHAR(64) NOT NULL,
      snapshot_json LONGTEXT NOT NULL,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      INDEX (project_id),
      CONSTRAINT fk_project_snapshots
        FOREIGN KEY (project_id) REFERENCES projects(project_id)
        ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
  `);

  await pool.query(`
    CREATE TABLE IF NOT EXISTS custom_chars (
      custom_char_id VARCHAR(64) PRIMARY KEY,
      unicode INT NOT NULL,
      name VARCHAR(255) NOT NULL,
      image_mime VARCHAR(64) DEFAULT 'image/png',
      image_blob LONGBLOB NOT NULL,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
      INDEX (unicode)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
  `);

  await pool.query(`
    CREATE TABLE IF NOT EXISTS exports (
      export_id BIGINT AUTO_INCREMENT PRIMARY KEY,
      project_id VARCHAR(64) DEFAULT NULL,
      export_type VARCHAR(32) NOT NULL,
      xml_content MEDIUMTEXT NOT NULL,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      INDEX (project_id),
      CONSTRAINT fk_exports_projects
        FOREIGN KEY (project_id) REFERENCES projects(project_id)
        ON DELETE SET NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
  `);

  await pool.query(`
    CREATE TABLE IF NOT EXISTS users (
      user_id BIGINT AUTO_INCREMENT PRIMARY KEY,
      username VARCHAR(64) NOT NULL UNIQUE,
      password_hash VARCHAR(255) NOT NULL,
      display_name VARCHAR(255) NOT NULL DEFAULT '',
      role VARCHAR(32) NOT NULL DEFAULT 'editor',
      permissions JSON DEFAULT NULL,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
  `);

  // 启动时保证至少有一个默认账号，便于首次联调登录流程。
  const defaultUsername = String(process.env.DEFAULT_ADMIN_USERNAME || 'admin');
  const defaultPassword = String(process.env.DEFAULT_ADMIN_PASSWORD || 'admin123');
  const defaultDisplayName = String(process.env.DEFAULT_ADMIN_DISPLAY_NAME || '管理员');
  const defaultRole = String(process.env.DEFAULT_ADMIN_ROLE || 'admin');
  const defaultPermissions = JSON.stringify(['project:read', 'project:write', 'editor:collab']);
  const passwordHash = await bcrypt.hash(defaultPassword, 10);

  await pool.query(
    `
    INSERT IGNORE INTO users (username, password_hash, display_name, role, permissions)
    VALUES (?, ?, ?, ?, CAST(? AS JSON))
    `,
    [defaultUsername, passwordHash, defaultDisplayName, defaultRole, defaultPermissions]
  );

  await ensureProjectManagementSchema(pool, defaultUsername);
}

async function columnExists(pool, tableName, columnName) {
  const [rows] = await pool.query(
    `
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = ? AND COLUMN_NAME = ?
    LIMIT 1
    `,
    [tableName, columnName]
  );
  return rows.length > 0;
}

async function constraintExists(pool, constraintName) {
  const [rows] = await pool.query(
    `
    SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
    WHERE CONSTRAINT_SCHEMA = DATABASE() AND CONSTRAINT_NAME = ?
    LIMIT 1
    `,
    [constraintName]
  );
  return rows.length > 0;
}

async function ensureProjectManagementSchema(pool, defaultAdminUsername) {
  if (!(await columnExists(pool, 'projects', 'owner_id'))) {
    await pool.query(`
      ALTER TABLE projects
        ADD COLUMN owner_id BIGINT NULL AFTER project_id,
        ADD COLUMN is_public TINYINT(1) NOT NULL DEFAULT 0,
        ADD COLUMN allow_join_requests TINYINT(1) NOT NULL DEFAULT 0
    `);
  } else {
    if (!(await columnExists(pool, 'projects', 'is_public'))) {
      await pool.query('ALTER TABLE projects ADD COLUMN is_public TINYINT(1) NOT NULL DEFAULT 0');
    }
    if (!(await columnExists(pool, 'projects', 'allow_join_requests'))) {
      await pool.query('ALTER TABLE projects ADD COLUMN allow_join_requests TINYINT(1) NOT NULL DEFAULT 0');
    }
  }

  if (!(await constraintExists(pool, 'fk_projects_owner'))) {
    await pool.query(`
      ALTER TABLE projects
        ADD CONSTRAINT fk_projects_owner
        FOREIGN KEY (owner_id) REFERENCES users(user_id) ON DELETE SET NULL
    `);
  }

  await pool.query(`
    CREATE TABLE IF NOT EXISTS project_members (
      id BIGINT AUTO_INCREMENT PRIMARY KEY,
      project_id VARCHAR(64) NOT NULL,
      user_id BIGINT NOT NULL,
      role ENUM('admin', 'editor', 'viewer') NOT NULL DEFAULT 'viewer',
      invited_by BIGINT NULL,
      invited_at DATETIME DEFAULT CURRENT_TIMESTAMP,
      joined_at DATETIME DEFAULT CURRENT_TIMESTAMP,
      UNIQUE KEY unique_project_user (project_id, user_id),
      CONSTRAINT fk_pm_project FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE,
      CONSTRAINT fk_pm_user FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
      CONSTRAINT fk_pm_inviter FOREIGN KEY (invited_by) REFERENCES users(user_id) ON DELETE SET NULL,
      INDEX idx_project (project_id),
      INDEX idx_user (user_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  `);

  await pool.query(`
    CREATE TABLE IF NOT EXISTS project_invitations (
      id BIGINT AUTO_INCREMENT PRIMARY KEY,
      project_id VARCHAR(64) NOT NULL,
      inviter_id BIGINT NOT NULL,
      invitee_username VARCHAR(100) NULL,
      invitee_id BIGINT NULL,
      role ENUM('editor', 'viewer') NOT NULL DEFAULT 'viewer',
      status ENUM('pending', 'accepted', 'rejected', 'expired') NOT NULL DEFAULT 'pending',
      token VARCHAR(64) NOT NULL,
      invitation_type ENUM('username', 'link') NOT NULL DEFAULT 'username',
      max_uses INT NOT NULL DEFAULT 1,
      used_count INT NOT NULL DEFAULT 0,
      created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
      expires_at DATETIME NULL,
      responded_at DATETIME NULL,
      UNIQUE KEY uk_token (token),
      CONSTRAINT fk_pinv_project FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE,
      CONSTRAINT fk_pinv_inviter FOREIGN KEY (inviter_id) REFERENCES users(user_id) ON DELETE CASCADE,
      CONSTRAINT fk_pinv_invitee FOREIGN KEY (invitee_id) REFERENCES users(user_id) ON DELETE SET NULL,
      INDEX idx_token (token),
      INDEX idx_project (project_id),
      INDEX idx_status (status)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  `);

  await pool.query(`
    CREATE TABLE IF NOT EXISTS project_join_requests (
      id BIGINT AUTO_INCREMENT PRIMARY KEY,
      project_id VARCHAR(64) NOT NULL,
      user_id BIGINT NOT NULL,
      requested_role ENUM('editor', 'viewer') NOT NULL DEFAULT 'viewer',
      message TEXT NULL,
      status ENUM('pending', 'approved', 'rejected') NOT NULL DEFAULT 'pending',
      reviewed_by BIGINT NULL,
      review_message TEXT NULL,
      created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
      reviewed_at DATETIME NULL,
      CONSTRAINT fk_pjr_project FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE,
      CONSTRAINT fk_pjr_user FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
      CONSTRAINT fk_pjr_reviewer FOREIGN KEY (reviewed_by) REFERENCES users(user_id) ON DELETE SET NULL,
      INDEX idx_project (project_id),
      INDEX idx_user (user_id),
      INDEX idx_status (status)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  `);

  await pool.query(`
    CREATE TABLE IF NOT EXISTS project_activity_logs (
      id BIGINT AUTO_INCREMENT PRIMARY KEY,
      project_id VARCHAR(64) NOT NULL,
      user_id BIGINT NULL,
      action_type VARCHAR(50) NOT NULL,
      action_detail JSON NULL,
      target_user_id BIGINT NULL,
      ip_address VARCHAR(45) NULL,
      user_agent TEXT NULL,
      created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
      CONSTRAINT fk_pal_project FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE,
      CONSTRAINT fk_pal_user FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE SET NULL,
      CONSTRAINT fk_pal_target FOREIGN KEY (target_user_id) REFERENCES users(user_id) ON DELETE SET NULL,
      INDEX idx_project (project_id),
      INDEX idx_user (user_id),
      INDEX idx_action (action_type),
      INDEX idx_created (created_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  `);

  const [adminRows] = await pool.query(
    'SELECT user_id FROM users WHERE username = ? LIMIT 1',
    [defaultAdminUsername]
  );
  const adminId = adminRows[0]?.user_id;
  if (adminId) {
    await pool.query('UPDATE projects SET owner_id = ? WHERE owner_id IS NULL', [adminId]);
    await pool.query(
      `
      INSERT IGNORE INTO project_members (project_id, user_id, role, invited_by)
      SELECT p.project_id, p.owner_id, 'admin', NULL
      FROM projects p
      WHERE p.owner_id IS NOT NULL
      `
    );
  }
}

module.exports = {
  createPool,
  initDb,
};

