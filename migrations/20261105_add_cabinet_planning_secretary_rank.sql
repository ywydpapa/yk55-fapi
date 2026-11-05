INSERT INTO yk_rank (rankTitle, sortNo, rankType, attrib)
SELECT '기획총장', 56, 'DIST', '1000010000'
WHERE NOT EXISTS (
    SELECT 1
    FROM yk_rank
    WHERE rankTitle = '기획총장'
      AND attrib = '1000010000'
);
