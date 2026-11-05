CREATE TABLE IF NOT EXISTS yk_notice_views (
    viewNo INT NOT NULL AUTO_INCREMENT,
    noticeNo INT NOT NULL,
    userNo INT NOT NULL,
    firstViewedAt DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    lastViewedAt DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    viewCount INT UNSIGNED NOT NULL DEFAULT 1,
    PRIMARY KEY (viewNo),
    UNIQUE KEY uq_yk_notice_views_notice_user (noticeNo, userNo),
    KEY idx_yk_notice_views_userNo (userNo),
    CONSTRAINT fk_yk_notice_views_notice
        FOREIGN KEY (noticeNo) REFERENCES yk_notice (noticeNo)
        ON DELETE CASCADE,
    CONSTRAINT fk_yk_notice_views_user
        FOREIGN KEY (userNo) REFERENCES yk_user (userNo)
        ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
