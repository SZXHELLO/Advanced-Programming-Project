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
}

module.exports = {
  createPool,
  initDb,
};

