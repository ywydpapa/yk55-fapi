import os
import io
import secrets
import datetime
import calendar
from datetime import date
import bcrypt
from PIL import Image
from fastapi import HTTPException, Request, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

# ==========================================
# 상수 및 디렉토리 설정
# ==========================================
MAX_UPLOAD_SIZE = 25 * 1024 * 1024
THUMBNAIL_DIR = "./static/img/memberThumb"
MEMBERPHOTO_DIR = "./static/img/members"
CLUBLOGOS_DIR = "./static/img/clubLogos"
GOVLOGOS_DIR = "./static/img/govLogos"
EVENTPHOTO_DIR = "./static/img/event"
DOCPHOTO_DIR = "./static/img/docs"

# ==========================================
# 유틸리티 함수
# ==========================================
def currency(value, symbol="₩", suffix="", places=0):
    if value is None or value == "":
        return ""
    try:
        n = float(value)
    except (TypeError, ValueError):
        return value
    if places == 0:
        formatted = f"{int(round(n)):,}"
    else:
        formatted = f"{n:,.{places}f}"
    return f"{symbol}{formatted}{suffix}"

async def safe_file_read(file: UploadFile, max_size: int = MAX_UPLOAD_SIZE) -> bytes:
    contents = bytearray()
    while chunk := await file.read(1024 * 1024):
        contents.extend(chunk)
        if len(contents) > max_size:
            raise HTTPException(
                status_code=413,
                detail=f"파일 용량이 너무 큽니다. (최대 {max_size / 1024 / 1024}MB 허용)"
            )
    return bytes(contents)

def get_password_hash(password: str) -> str:
    pwd_bytes = password.encode('utf-8')[:72]
    salt = bcrypt.gensalt()
    hashed_password = bcrypt.hashpw(pwd_bytes, salt)
    return hashed_password.decode('utf-8')

def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        pwd_bytes = plain_password.encode('utf-8')[:72]
        hashed_bytes = hashed_password.encode('utf-8')
        return bcrypt.checkpw(pwd_bytes, hashed_bytes)
    except ValueError:
        return False

async def get_current_user(request: Request) -> int:
    user_no = request.session.get("user_No")
    if not user_no:
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="로그인이 필요합니다."
            )
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/"}
        )
    return user_no

def _clean_str(value: object) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s if s != "" else None

def _clean_int(value: object) -> int | None:
    s = _clean_str(value)
    if s is None:
        return None
    try:
        return int(s)
    except ValueError:
        raise ValueError(f"Invalid integer input: {s!r}")

def to_int(s, default=0):
    try:
        return int(s)
    except Exception:
        return default

# ==========================================
# 이미지 처리 함수
# ==========================================
async def save_thumbnail(image_data: bytes, memberno: int, size=(100, 100)):
    os.makedirs(THUMBNAIL_DIR, exist_ok=True)
    image = Image.open(io.BytesIO(image_data))
    image.thumbnail(size)
    thumbnail_path = os.path.join(THUMBNAIL_DIR, f"thumb_{memberno}.png")
    image.save(thumbnail_path, format="PNG")
    return thumbnail_path

async def resize_image_if_needed(contents: bytes, max_bytes: int = 314572) -> bytes:
    if len(contents) <= max_bytes:
        return contents
    image = Image.open(io.BytesIO(contents))
    format = image.format if image.format else 'JPEG'
    quality = 85
    for trial in range(10):
        buffer = io.BytesIO()
        save_kwargs = {'format': format}
        if format.upper() in ['JPEG', 'JPG']:
            save_kwargs['quality'] = quality
            save_kwargs['optimize'] = True
        image.save(buffer, **save_kwargs)
        data = buffer.getvalue()
        if len(data) <= max_bytes:
            return data
        if format.upper() in ['JPEG', 'JPG'] and quality > 30:
            quality -= 10
        else:
            w, h = image.size
            image = image.resize((int(w * 0.9), int(h * 0.9)), Image.LANCZOS)
    return data

async def save_memberPhoto(image_data: bytes, memberno: int, size=(200, 300)):
    os.makedirs(MEMBERPHOTO_DIR, exist_ok=True)  # 수정: THUMBNAIL_DIR -> MEMBERPHOTO_DIR
    image = Image.open(io.BytesIO(image_data))
    image.thumbnail(size)
    thumbnail_path = os.path.join(MEMBERPHOTO_DIR, f"mphoto_{memberno}.png")
    image.save(thumbnail_path, format="PNG")
    return thumbnail_path

async def save_docPhoto(image_data: bytes, docno: int, size=(200, 300)):
    os.makedirs(DOCPHOTO_DIR, exist_ok=True)
    image = Image.open(io.BytesIO(image_data))
    image.thumbnail(size)
    thumbnail_path = os.path.join(DOCPHOTO_DIR, f"docphoto_{docno}.png")
    image.save(thumbnail_path, format="PNG")
    return thumbnail_path

async def save_eventPhoto(image_data: bytes, eventno: int, size=(200, 300)):
    os.makedirs(EVENTPHOTO_DIR, exist_ok=True)
    image = Image.open(io.BytesIO(image_data))
    image.thumbnail(size)
    thumbnail_path = os.path.join(EVENTPHOTO_DIR, f"ephoto_{eventno}.png") # 수정: MEMBERPHOTO_DIR -> EVENTPHOTO_DIR
    image.save(thumbnail_path, format="PNG")
    return thumbnail_path

# ==========================================
# OTP 및 세션 관련 함수
# ==========================================
async def generate_otp():
    return str(secrets.randbelow(10 ** 9)).zfill(9)

async def reg_otp(otp: str, userNo: int, db: AsyncSession):
    try:
        query = text("INSERT INTO yk_seckey (userNo, otp) VALUES (:userNo, :otp)")
        await db.execute(query, {"userNo": userNo, "otp": otp})
        await db.commit()
        return True
    except Exception as e:
        return False

async def exp_otp(userNo: int, db: AsyncSession):
    try:
        now = datetime.datetime.now()
        query = text("UPDATE yk_seckey SET modDate = now(), attrib = :xup where userNo = :userNo and attrib = :xapp")
        await db.execute(query, {"userNo": userNo, "xapp": '1000010000', "xup": 'XXXUPXXXUP'})
        await db.commit()
        return True
    except Exception as e:
        return False

async def session_chk(request: Request, otp: str):
    try:
        sotp = request.session.get("otp")
        if otp != sotp:
            raise HTTPException(
                status_code=status.HTTP_303_SEE_OTHER,
                headers={"Location": "/"},
                detail="세션이 만료되어 재로그인이 필요합니다.")
        return False
    except Exception as e:
        return False

# ==========================================
# 데이터베이스 조회 함수 (DB 쿼리)
# ==========================================
async def get_member_prize_list(memberno: int, db: AsyncSession):
    q = text("""
             SELECT d.mpNo                               as id,
                    d.prizeNo,
                    c.prizeTitle,
                    d.prizeMemo,
                    DATE_FORMAT(d.prizeDate, '%Y-%m-%d') AS prizeDate
             FROM yk_memberPrize d
                      JOIN yk_prize c ON c.prizeNo = d.prizeNo
             WHERE d.memberNo = :mno
               AND d.attrib = :xapp
             ORDER BY d.prizeDate ASC
             """)
    result = await db.execute(q, {"mno": memberno, "xapp": "1000010000"})
    return [dict(r._mapping) for r in result.fetchall()]

async def get_member_detail_list(memberno: int, db: AsyncSession):
    q = text("""
             SELECT d.infoNo                           as id,
                    d.catNo,
                    c.catTitle,
                    d.detailInfo,
                    DATE_FORMAT(d.regDate, '%Y-%m-%d') AS regDate
             FROM yk_memberDetailinfo d
                      JOIN yk_category c ON c.catNo = d.catNo
             WHERE d.memberNo = :mno
               AND d.attrib = :xapp
             ORDER BY d.regDate, d.infoNo
             """)
    result = await db.execute(q, {"mno": memberno, "xapp": "1000010000"})
    return [dict(r._mapping) for r in result.fetchall()]

async def getdocList(db: AsyncSession):
    try:
        query = text("SELECT a.*, b.eventTitle FROM yk_doc a left join yk_event b on a.docEvent = b.eventNo where a.attrib = :xapp")
        doclist = await db.execute(query, {"xapp": '1000010000'})
        return doclist.fetchall()
    except Exception as e:
        return None

async def getregionList(db: AsyncSession):
    try:
        query = text("SELECT * FROM yk_region where attrib = :xapp")
        regionlist = await db.execute(query, {"xapp": '1000010000'})
        return regionlist.fetchall()
    except Exception as e:
        return None

async def getperiod(db: AsyncSession):
    query = text("""
                 SELECT periodNo, yearFr, yearTo, periodTitle, periodTitle2
                 FROM yk_period
                 WHERE attrib = :xapp
                 ORDER BY periodNo """)
    result = await db.execute(query, {"xapp": "1000010000"})
    return [dict(row._mapping) for row in result.fetchall()]

async def getcategory(cattype: str, db: AsyncSession):
    query = text("""
                 SELECT catNo, catTitle, catTitleEng
                 FROM yk_category
                 WHERE attrib = :xapp
                   and catType = :cattype""")
    result = await db.execute(query, {"xapp": "1000010000", "cattype": cattype})
    return [dict(row._mapping) for row in result.fetchall()]

async def getprizelist(db: AsyncSession):
    query = text("""
                 SELECT prizeNo, prizeTitle, prizeType
                 FROM yk_prize
                 WHERE attrib = :xapp""")
    result = await db.execute(query, {"xapp": "1000010000"})
    return [dict(row._mapping) for row in result.fetchall()]

async def get_event_dist_club(clubno: int, db: AsyncSession):
    query = text("""
                 SELECT a.eventNo,
                        a.periodNo,
                        a.eventTitle,
                        a.eventTitleEng,
                        a.eventType,
                        a.eventFrom,
                        a.eventTo,
                        a.clubNo,
                        count(b.eventNo) as cnt,
                        a.eventCost,
                        a.locationNo
                 FROM yk_event a
                          left join yk_eventMember b on a.eventNo = b.eventNo and b.attrib = :xapp
                 WHERE a.attrib = :xapp
                   and (a.clubNo = :clubno or a.regionNo = 0)
                 group by b.eventNo
                 HAVING COUNT(b.eventNo) > 0
                 ORDER BY a.eventFrom, a.sortNo """)
    result = await db.execute(query, {"xapp": "1000010000", "clubno": clubno})
    return [dict(row._mapping) for row in result.fetchall()]

async def get_clubevents(clubno: int, db: AsyncSession):
    query = text("""
                 SELECT eventNo, periodNo, eventTitle, eventTitleEng, eventType,
                        eventFrom, eventTo, clubNo, eventCost, sortNo, locationNo
                 FROM yk_event
                 WHERE attrib = :xapp and clubNo = :clubno
                 ORDER BY eventFrom """)
    result = await db.execute(query, {"xapp": "1000010000", "clubno": clubno})
    return [dict(row._mapping) for row in result.fetchall()]

async def get_distevents(db: AsyncSession):
    query = text("""
                 SELECT eventNo, periodNo, eventTitle, eventTitleEng, eventType,
                        eventFrom, eventTo, clubNo, eventCost, sortNo, locationNo
                 FROM yk_event
                 WHERE attrib = :xapp and eventType = :eventType 
                 ORDER BY eventFrom """)
    result = await db.execute(query, {"xapp": "1000010000", "eventType": "DISTE"})
    return [dict(row._mapping) for row in result.fetchall()]

async def get_clubeventsperiod(clubno: int, periodno: int, db: AsyncSession):
    query = text("""
                 SELECT a.eventNo, a.periodNo, a.eventTitle, a.eventTitleEng, a.eventType,
                        a.eventFrom, a.eventTo, a.clubNo, a.eventCost, a.sortNo, b.clubName, a.locationNo
                 FROM yk_event a
                     left join yk_club b on a.clubNo = b.clubNo
                 WHERE a.attrib = :xapp and a.clubNo = :clubno and a.periodNo = :periodno
                 ORDER BY a.eventFrom """)
    result = await db.execute(query, {"xapp": "1000010000", "clubno": clubno, "periodno": periodno})
    return [dict(row._mapping) for row in result.fetchall()]


async def get_allclubeventsperiod(periodno: int, db: AsyncSession):
    query = text("""
                 SELECT a.eventNo, a.periodNo, a.eventTitle, a.eventTitleEng, a.eventType,
                        a.eventFrom, a.eventTo, a.clubNo, a.eventCost, a.sortNo, b.clubName, a.locationNo
                 FROM yk_event a
                     left join yk_club b on a.clubNo = b.clubNo
                 WHERE a.attrib = :xapp and a.periodNo = :periodno and a.clubNo != 0 
                 ORDER BY a.eventFrom """)
    result = await db.execute(query, {"xapp": "1000010000", "periodno": periodno})
    return [dict(row._mapping) for row in result.fetchall()]


async def get_disteventsperiod(periodno: int, db: AsyncSession):
    query = text("""
                 SELECT a.eventNo, a.periodNo, a.eventTitle, a.eventTitleEng, a.eventType,
                        a.eventFrom, a.eventTo, a.clubNo, a.eventCost, a.sortNo, b.clubName, a.locationNo
                 FROM yk_event a
                     left join yk_club b on a.clubNo = b.clubNo
                 WHERE a.attrib = :xapp and a.periodNo = :periodno and a.eventType = :eventType
                 ORDER BY a.clubNo, a.eventFrom """)
    result = await db.execute(query, {"xapp": "1000010000", "periodno": periodno, "eventType": "DISTE"})
    return [dict(row._mapping) for row in result.fetchall()]

async def get_eventdtl(eventno: int, db: AsyncSession):
    query = text("""
                 SELECT * FROM yk_event
                 WHERE attrib = :xapp and eventNo = :eventno""")
    result = await db.execute(query, {"xapp": "1000010000", "eventno": eventno})
    return result.fetchone()

async def get_eventmembers(eventno: int, db: AsyncSession):
    query = text("""SELECT * from yk_eventMember
                    where attrib = :xapp and eventNo = :eno""")
    result = await db.execute(query, {"xapp": "1000010000", "eno": eventno})
    return [dict(row._mapping) for row in result.fetchall()]

async def get_cabhist(periodno: int, db: AsyncSession):
    query = text("""SELECT * from yk_distStaff
                    where attrib = :xapp and periodNo = :pno and cabYn = :cyn """)
    result = await db.execute(query, {"xapp": "1000010000", "pno": periodno, "cyn": 'Y'})
    return [dict(row._mapping) for row in result.fetchall()]

async def get_cabhist_wname(periodno: int, db: AsyncSession):
    query = text("""SELECT a.*, b.rankTitle, c.memberName, d.clubName, b.sortNo
                    from yk_distStaff a
                             left join yk_rank b on a.rankNo = b.rankNo
                             left join yk_members c on a.memberNo = c.memberNo
                             left join yk_club d on a.clubNo = d.clubNo
                    where a.attrib = :xapp and b.rankTitle in ('지구총재','사무총장', '재무총장', '기획총장', '수석부총장', '사무부총장', '재무부총장')
                      and a.periodNo = :pno
                    order by b.sortNo""")
    result = await db.execute(query, {"xapp": "1000010000", "pno": periodno})
    return [dict(row._mapping) for row in result.fetchall()]

async def get_dmemberhist(periodno: int, db: AsyncSession):
    query = text("""SELECT * from yk_distStaff
                    where attrib = :xapp and periodNo = :pno """)
    result = await db.execute(query, {"xapp": "1000010000", "pno": periodno})
    return [dict(row._mapping) for row in result.fetchall()]

async def get_dmemberhist_wname(periodno: int, db: AsyncSession):
    query = text("""SELECT a.*, b.rankTitle, c.memberName, d.clubName, b.sortNo
                    from yk_distStaff a
                             left join yk_rank b on a.rankNo = b.rankNo
                             left join yk_members c on a.memberNo = c.memberNo
                             left join yk_club d on a.clubNo = d.clubNo
                    where a.attrib = :xapp and a.periodNo = :pno
                    order by b.sortNo""")
    result = await db.execute(query, {"xapp": "1000010000", "pno": periodno})
    return [dict(row._mapping) for row in result.fetchall()]

async def get_rank(db: AsyncSession):
    query = text("""SELECT * FROM yk_rank
                    where attrib = :xapp order by sortNo""")
    result = await db.execute(query, {"xapp": "1000010000"})
    return [dict(row._mapping) for row in result.fetchall()]

async def get_category(db: AsyncSession):
    query = text("""SELECT a.* FROM yk_category a where a.attrib = :xapp""")
    result = await db.execute(query, {"xapp": "1000010000"})
    return [dict(row._mapping) for row in result.fetchall()]

async def get_cat_detail(catno: int, db: AsyncSession):
    query = text("""SELECT a.* FROM yk_category a
                    where a.attrib = :xapp and a.catNo = :catno""")
    result = await db.execute(query, {"xapp": "1000010000", "catno": catno})
    return result.fetchone()

async def get_prize(db: AsyncSession):
    query = text("""SELECT a.* FROM yk_prize a where a.attrib = :xapp""")
    result = await db.execute(query, {"xapp": "1000010000"})
    return [dict(row._mapping) for row in result.fetchall()]

async def get_prize_detail(prizeno: int, db: AsyncSession):
    query = text("""SELECT a.* FROM yk_prize a
                    where a.attrib = :xapp and a.prizeNo = :prizeno""")
    result = await db.execute(query, {"xapp": "1000010000", "prizeno": prizeno})
    return result.fetchone()

async def get_member(db: AsyncSession):
    query = text("""SELECT a.*, b.clubName
                    FROM yk_members a
                             left join yk_club b on a.clubNo = b.clubNo
                    where a.attrib = :xapp""")
    result = await db.execute(query, {"xapp": "1000010000"})
    return [dict(row._mapping) for row in result.fetchall()]

async def get_clubmember(clubno: int, db: AsyncSession):
    query = text("""SELECT * FROM yk_members
                    where clubNo = :cno and attrib = :xapp
                    order by memberName asc""")
    result = await db.execute(query, {"cno": clubno, "xapp": "1000010000"})
    members =  [dict(row._mapping) for row in result.fetchall()]
    members.sort(key=lambda x: x['memberName'])
    return members

async def get_distmember(db: AsyncSession):
    query = text("""SELECT count(*) FROM yk_members
                    where memberStatus = :sts and attrib = :xapp""")
    result = await db.execute(query, { "sts": "ACTIV", "xapp": "1000010000"})
    return result.fetchone()[0]

async def get_clubsponser(clubno: int, db: AsyncSession):
    query = text("""SELECT memberNo, memberName FROM yk_members
                    where clubNo = :cno and attrib = :xapp
                    order by memberEntdate""")
    result = await db.execute(query, {"cno": clubno, "xapp": "1000010000"})
    return [dict(row._mapping) for row in result.fetchall()]

async def get_member_dtl(memberno: int, db: AsyncSession):
    query = text("""SELECT * FROM yk_members
                    where attrib = :xapp and memberNo = :membno""")
    result = await db.execute(query, {"xapp": "1000010000", "membno": memberno})
    return result.fetchone()

async def get_club(db: AsyncSession):
    query = text("""SELECT * FROM yk_club
                    where attrib = :xapp order by clubCno""")
    result = await db.execute(query, {"xapp": "1000010000"})
    return [dict(row._mapping) for row in result.fetchall()]

async def get_memberreports(clubno: int, db: AsyncSession):
    query = text("""SELECT a.periodNo, a.periodMonth, a.clubNo, b.periodTitle2,
                           (select count(*) from yk_members where clubNo = :clubNo and memberStatus = :stat) act,
                           SUM(CASE WHEN a.statusType = 'JOIN' THEN 1 ELSE 0 END)  AS joinc,
                           SUM(CASE WHEN a.statusType = 'RETIR' THEN 1 ELSE 0 END) AS retir,
                           SUM(CASE WHEN a.statusType = 'REPEL' THEN 1 ELSE 0 END) AS repel,
                           SUM(CASE WHEN a.statusType = 'RIP' THEN 1 ELSE 0 END)   AS rip,
                           SUM(CASE WHEN a.statusType = 'TRANS' THEN 1 ELSE 0 END) AS trans
                    FROM yk_memberStatus a
                             LEFT JOIN yk_period b ON a.periodNo = b.periodNo
                    WHERE a.clubNo = :clubNo and a.attrib = :xapp
                    GROUP BY a.periodNo, a.periodMonth, a.clubNo
                    ORDER BY a.periodNo, a.periodMonth """)
    result = await db.execute(query, {"xapp": "1000010000", "clubNo": clubno, "stat": "ACTIV"})
    return [dict(row._mapping) for row in result.fetchall()]

async def get_clubstaff(clubno: int, db: AsyncSession):
    query = text("""SELECT a.*,
                           b1.memberName as n1, b2.memberName as n2, b3.memberName as n3,
                           b4.memberName as n4, b5.memberName as n5, b6.memberName as n6,
                           b7.memberName as n7, b8.memberName as n8, c1.periodTitle2 as per1
                    FROM yk_clubStaff a
                             left join yk_members b1 on a.chairmanNo = b1.memberNo
                             left join yk_members b2 on a.vice1stNo = b2.memberNo
                             left join yk_members b3 on a.vice2ndNo = b3.memberNo
                             left join yk_members b4 on a.vice3rdNo = b4.memberNo
                             left join yk_members b5 on a.secretaryNo = b5.memberNo
                             left join yk_members b6 on a.treasureNo = b6.memberNo
                             left join yk_members b7 on a.lionsteamerNo = b7.memberNo
                             left join yk_members b8 on a.tailtNo = b8.memberNo
                             left join yk_period c1 on a.periodNo = c1.periodNo
                    where a.clubNo = :clubno and a.attrib = :xapp
                    order by a.periodNo""")
    result = await db.execute(query, {"xapp": "1000010000", "clubno": clubno})
    return [dict(row._mapping) for row in result.fetchall()]

async def get_clubstaffhist(memberno: int, db: AsyncSession):
    query = text("""SELECT c1.periodTitle2 AS p1, a.clubNo,
                           GROUP_CONCAT(DISTINCT CASE 
            WHEN a.chairmanNo    = :memberNo THEN '회장'
            WHEN a.vice1stNo     = :memberNo THEN '1부회장'
            WHEN a.vice2ndNo     = :memberNo THEN '2부회장'
            WHEN a.vice3rdNo     = :memberNo THEN '3부회장'
            WHEN a.secretaryNo   = :memberNo THEN '총무'
            WHEN a.treasureNo    = :memberNo THEN '재무'
            WHEN a.lionsteamerNo = :memberNo THEN 'L.T'
            WHEN a.tailtNo       = :memberNo THEN 'T.T'
            END
            ORDER BY CASE 
                WHEN a.chairmanNo    = :memberNo THEN 1
                WHEN a.vice1stNo     = :memberNo THEN 2
                WHEN a.vice2ndNo     = :memberNo THEN 3
                WHEN a.vice3rdNo     = :memberNo THEN 4
                WHEN a.secretaryNo   = :memberNo THEN 5
                WHEN a.treasureNo    = :memberNo THEN 6
                WHEN a.lionsteamerNo = :memberNo THEN 7
                WHEN a.tailtNo       = :memberNo THEN 8
            END SEPARATOR '/') AS roles
                    FROM yk_clubStaff a
                             LEFT JOIN yk_period c1 ON a.periodNo = c1.periodNo
                    WHERE :memberNo IN
                          (a.chairmanNo, a.vice1stNo, a.vice2ndNo, a.vice3rdNo, a.secretaryNo, a.treasureNo,
                           a.lionsteamerNo, a.tailtNo)
                    GROUP BY c1.periodTitle2, a.clubNo """)
    result = await db.execute(query, {"memberNo": memberno})
    return [dict(row._mapping) for row in result.fetchall()]

async def get_diststaff(clubno: int, db: AsyncSession):
    query = text("""SELECT a.*, b1.memberName as n1, c1.periodTitle2 as per1, d1.rankTitle as r1
                    FROM yk_distStaff a
                             left join yk_members b1 on a.memberNo = b1.memberNo
                             left join yk_period c1 on a.periodNo = c1.periodNo
                             left join yk_rank d1 on a.rankNo = d1.rankNo
                    where a.clubNo = :clubno and a.attrib = :xapp
                    order by a.periodNo, d1.sortNo""")
    result = await db.execute(query, {"xapp": "1000010000", "clubno": clubno})
    return [dict(row._mapping) for row in result.fetchall()]

async def get_diststaffmem(clubno: int, memberno: int, db: AsyncSession):
    query = text("""SELECT a.*, b1.memberName as n1, c1.periodTitle2 as per1, d1.rankTitle as r1
                    FROM yk_distStaff a
                             left join yk_members b1 on a.memberNo = b1.memberNo
                             left join yk_period c1 on a.periodNo = c1.periodNo
                             left join yk_rank d1 on a.rankNo = d1.rankNo
                    where a.clubNo = :clubno and a.attrib = :xapp and a.memberNo = :memn
                    order by a.periodNo, d1.sortNo""")
    result = await db.execute(query, {"xapp": "1000010000", "clubno": clubno, "memn": memberno})
    return [dict(row._mapping) for row in result.fetchall()]

async def get_event_reports(db: AsyncSession):
    query = text("""SELECT a.eventNo, c.clubName, b.eventTitle, b.eventFrom, b.eventTo,
                           count(a.memberNo) mcnt, sum(a.supportAmt) eamt
                    FROM yk_eventMember a
                             left join yk_event b on a.eventNo = b.eventNo
                             left join yk_club c on b.clubNo = c.clubNo
                    where a.attrib = :xapp
                    group by eventNo""")
    result = await db.execute(query, {"xapp": "1000010000"})
    return [dict(row._mapping) for row in result.fetchall()]

async def get_member_reports(db: AsyncSession):
    query = text("""SELECT a.*, b1.memberName as n1, c1.periodTitle2 as per1, d1.rankTitle as r1
                    FROM yk_distStaff a
                             left join yk_members b1 on a.memberNo = b1.memberNo
                             left join yk_period c1 on a.periodNo = c1.periodNo
                             left join yk_rank d1 on a.rankNo = d1.rankNo
                    where a.attrib = :xapp
                    order by a.periodNo, d1.sortNo""")
    result = await db.execute(query, {"xapp": "1000010000"})
    return [dict(row._mapping) for row in result.fetchall()]

async def get_club_spon(clubno: int, db: AsyncSession):
    query = text("""SELECT clubNo, clubName, clubNameEng FROM yk_club
                    where attrib = :xapp and clubNo < :clubno""")
    result = await db.execute(query, {"xapp": "1000010000", "clubno": clubno})
    return result.fetchall()

async def get_club_dtl(clubno: int, db: AsyncSession):
    query = text("""SELECT * FROM yk_club
                    where attrib = :xapp and clubNo = :clubno""")
    result = await db.execute(query, {"xapp": "1000010000", "clubno": clubno})
    return result.fetchone()

async def get_rank_dtl(rankno: int, db: AsyncSession):
    query = text("""SELECT * FROM yk_rank where rankNo = :rankno""")
    result = await db.execute(query, {"rankno": rankno})
    return result.fetchone()


async def get_locations(db: AsyncSession):
    query = text("""SELECT * FROM yk_location where attrib = :attr""")
    result = await db.execute(query, {"attr": "1000010000"})
    return result.fetchall()

async def getdocdetail(docno: int, db: AsyncSession):
    try:
        query = text(
            "SELECT a.docNo, a.docEvent, a.memberTitle, a.memberName, a.docTitle, CONVERT(a.docContents using utf8mb4), a.docType, a.regDate, a.modDate, b.eventTitle FROM yk_doc a left join yk_event b on a.docEvent = b.eventNo where a.docNo = :docno and a.attrib = :xapp")
        docconts = await db.execute(query, {"docno": docno, "xapp": '1000010000'})
        row = docconts.fetchone()
        return row
    except Exception as e:
        return None
