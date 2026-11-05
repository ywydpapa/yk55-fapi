CREATE TABLE IF NOT EXISTS yk_notice_files (
    fileNo INT NOT NULL AUTO_INCREMENT,
    noticeNo INT NOT NULL,
    originalName VARCHAR(255) NOT NULL,
    storedName VARCHAR(64) NOT NULL,
    mediaType VARCHAR(100) NOT NULL,
    fileSize BIGINT NOT NULL,
    regDate DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (fileNo),
    KEY idx_yk_notice_files_noticeNo (noticeNo),
    CONSTRAINT fk_yk_notice_files_notice
        FOREIGN KEY (noticeNo) REFERENCES yk_notice (noticeNo)
        ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
