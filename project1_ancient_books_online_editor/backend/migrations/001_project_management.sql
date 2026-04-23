-- SDOC Editor: project RBAC (reference migration; initDb also applies compatible DDL)
-- project_id is VARCHAR(64) to match existing projects table

ALTER TABLE projects
  ADD COLUMN owner_id BIGINT NULL AFTER project_id,
  ADD COLUMN is_public TINYINT(1) NOT NULL DEFAULT 0,
  ADD COLUMN allow_join_requests TINYINT(1) NOT NULL DEFAULT 0;

ALTER TABLE projects
  ADD CONSTRAINT fk_projects_owner FOREIGN KEY (owner_id) REFERENCES users(user_id) ON DELETE SET NULL;

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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
