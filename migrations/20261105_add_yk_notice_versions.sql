ALTER TABLE yk_notice
    ADD COLUMN status ENUM('PUBLISHED', 'ARCHIVED') NOT NULL DEFAULT 'PUBLISHED',
    ADD COLUMN previousNoticeNo INT NULL,
    ADD COLUMN rootNoticeNo INT NULL;

UPDATE yk_notice
SET rootNoticeNo = noticeNo
WHERE rootNoticeNo IS NULL;

ALTER TABLE yk_notice
    ADD KEY idx_yk_notice_status_regDate (status, regDate),
    ADD KEY idx_yk_notice_rootNoticeNo (rootNoticeNo),
    ADD CONSTRAINT fk_yk_notice_previous
        FOREIGN KEY (previousNoticeNo) REFERENCES yk_notice (noticeNo),
    ADD CONSTRAINT fk_yk_notice_root
        FOREIGN KEY (rootNoticeNo) REFERENCES yk_notice (noticeNo);
