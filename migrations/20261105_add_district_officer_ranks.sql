INSERT INTO yk_rank (rankTitle, sortNo, rankType, attrib)
SELECT '지역위원장', 60, 'DIST', '1000010000'
WHERE NOT EXISTS (
    SELECT 1
    FROM yk_rank
    WHERE rankTitle = '지역위원장'
      AND attrib = '1000010000'
);

INSERT INTO yk_rank (rankTitle, sortNo, rankType, attrib)
SELECT '기획위원장', 61, 'DIST', '1000010000'
WHERE NOT EXISTS (
    SELECT 1
    FROM yk_rank
    WHERE rankTitle = '기획위원장'
      AND attrib = '1000010000'
);
