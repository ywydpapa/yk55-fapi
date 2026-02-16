from urllib import request
import status
import uvicorn
from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi import Form, Response, HTTPException, File, UploadFile, Body
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from starlette.middleware.sessions import SessionMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from sqlalchemy import text
import dotenv
import os
import base64
import datetime
from PIL import Image, ImageFont, ImageDraw
import io
from pydantic import BaseModel
from fastapi.responses import StreamingResponse
from io import BytesIO
from starlette.responses import FileResponse
from pathlib import Path
import secrets
from jinja2 import Environment, FileSystemLoader, select_autoescape
from pathlib import Path

dotenv.load_dotenv()
DATABASE_URL = os.getenv("dburl")
engine = create_async_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_timeout=10,
    pool_recycle=1800)

async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

app = FastAPI()

app.add_middleware(SessionMiddleware, secret_key="supersecretkey")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 모든 도메인 허용
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/thumbnails", StaticFiles(directory="static/img/members/"), name="thumbnails")
THUMBNAIL_DIR = "./static/img/memberThumb"
MEMBERPHOTO_DIR = "./static/img/members"
CLUBLOGOS_DIR = "./static/img/clubLogos"
GOVLOGOS_DIR = "./static/img/govLogos"
EVENTPHOTO_DIR =  "./static/img/event"
BASE_DIR = Path(__file__).resolve().parent


# 데이터베이스 세션 생성
async def get_db():
    async with async_session() as session:
        yield session


# 썸네일 생성 함수
async def save_thumbnail(image_data: bytes, memberno: int, size=(100, 100)):
    # 디렉토리가 없으면 생성
    os.makedirs(THUMBNAIL_DIR, exist_ok=True)
    # 원본 이미지를 Pillow로 열기
    image = Image.open(io.BytesIO(image_data))
    # 썸네일 생성
    image.thumbnail(size)
    # 저장 경로
    thumbnail_path = os.path.join(THUMBNAIL_DIR, f"thumb_{memberno}.png")
    # 썸네일 저장
    image.save(thumbnail_path, format="PNG")
    return thumbnail_path


async def resize_image_if_needed(contents: bytes, max_bytes: int = 102400) -> bytes:
    if len(contents) <= max_bytes:
        return contents
    image = Image.open(io.BytesIO(contents))
    format = image.format if image.format else 'JPEG'
    quality = 85  # JPEG의 경우
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
    # 디렉토리가 없으면 생성
    os.makedirs(THUMBNAIL_DIR, exist_ok=True)
    # 원본 이미지를 Pillow로 열기
    image = Image.open(io.BytesIO(image_data))
    # 썸네일 생성
    image.thumbnail(size)
    # 저장 경로
    thumbnail_path = os.path.join(MEMBERPHOTO_DIR, f"mphoto_{memberno}.png")
    # 썸네일 저장
    image.save(thumbnail_path, format="PNG")
    return thumbnail_path


async def save_eventPhoto(image_data: bytes, eventno: int, size=(200, 300)):
    # 디렉토리가 없으면 생성
    os.makedirs(EVENTPHOTO_DIR, exist_ok=True)
    # 원본 이미지를 Pillow로 열기
    image = Image.open(io.BytesIO(image_data))
    # 썸네일 생성
    image.thumbnail(size)
    # 저장 경로
    thumbnail_path = os.path.join(MEMBERPHOTO_DIR, f"ephoto_{eventno}.png")
    # 썸네일 저장
    image.save(thumbnail_path, format="PNG")
    return thumbnail_path


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


@app.post("/uploadmphoto/{memberno}")
async def upload_logoimage(request: Request,memberno: int, file: UploadFile = File(...), db: AsyncSession = Depends(get_db)):
    try:
        # 이미지 파일인지 확인
        if not file.content_type.startswith('image/'):
            raise HTTPException(status_code=400, detail="File type not supported.")
        # 파일 읽기
        contents = await file.read()
        # 이미지 사이즈 조절
        contents = await resize_image_if_needed(contents, max_bytes=102400)
        # 이미지 저장
        await save_memberPhoto(contents, memberno)
        # 썸네일 생성
        await save_thumbnail(contents, memberno, size=(100, 100))
        # 리다이렉트
        return RedirectResponse(f"/member_edit/{memberno}", status_code=303)
    except Exception as e:
        print(f"Error: {e}")
        return RedirectResponse(f"/member_edit/{memberno}", status_code=303)


@app.post("/uploadcephoto/{eventno}")
async def upload_eventimage(request: Request,eventno: int, file: UploadFile = File(...), db: AsyncSession = Depends(get_db)):
    try:
        # 이미지 파일인지 확인
        if not file.content_type.startswith('image/'):
            raise HTTPException(status_code=400, detail="File type not supported.")
        # 파일 읽기
        contents = await file.read()
        # 이미지 사이즈 조절
        contents = await resize_image_if_needed(contents, max_bytes=102400)
        # 이미지 저장
        await save_eventPhoto(contents, eventno)
        # 리다이렉트
        return RedirectResponse(f"/event_edit/{eventno}", status_code=303)
    except Exception as e:
        print(f"Error: {e}")
        return RedirectResponse(f"/event_edit/{eventno}", status_code=303)


@app.post("/uploadcmphoto/{memberno}")
async def upload_cmimage(request: Request,memberno: int, file: UploadFile = File(...), db: AsyncSession = Depends(get_db)):
    try:
        # 이미지 파일인지 확인
        if not file.content_type.startswith('image/'):
            raise HTTPException(status_code=400, detail="File type not supported.")
        # 파일 읽기
        contents = await file.read()
        # 이미지 사이즈 조절
        contents = await resize_image_if_needed(contents, max_bytes=102400)
        # 이미지 저장
        await save_memberPhoto(contents, memberno)
        # 썸네일 생성
        await save_thumbnail(contents, memberno, size=(100, 100))
        # 리다이렉트
        return RedirectResponse(f"/cmember_edit/{memberno}", status_code=303)
    except Exception as e:
        print(f"Error: {e}")
        return RedirectResponse(f"/cmember_edit/{memberno}", status_code=303)


async def generate_otp():
    return str(secrets.randbelow(10 ** 9)).zfill(9)


async def reg_otp(otp:str, userNo:int, db:AsyncSession = Depends(get_db)):
    try:
        query = text("INSERT INTO yk_seckey (userNo, otp) VALUES (:userNo, :otp)")
        await db.execute(query, {"userNo": userNo, "otp": otp})
        await db.commit()
        return True
    except Exception as e:
        return False


async def exp_otp(userNo:int, db:AsyncSession = Depends(get_db)):
    try:
        now = datetime.datetime.now()
        query = text("UPDATE yk_seckey SET modDate = now(), attrib = :xup where userNo = :userNo and attrib = :xapp")
        await db.execute(query, {"userNo": userNo, "xapp": '1000010000', "xup": 'XXXUPXXXUP'})
        await db.commit()
        return True
    except Exception as e:
        return False


async def getdocList(db:AsyncSession = Depends(get_db)):
    try:
        query = text("SELECT * FROM yk_doc where attrib = :xapp")
        doclist = await db.execute(query, {"xapp":'1000010000'})
        return doclist.fetchall()
    except Exception as e:
        return None


async def getregionList(db:AsyncSession = Depends(get_db)):
    try:
        query = text("SELECT * FROM yk_region where attrib = :xapp")
        regionlist = await db.execute(query, {"xapp":'1000010000'})
        return regionlist.fetchall()
    except Exception as e:
        return None


async def getperiod(db: AsyncSession = Depends(get_db)):
    query = text("""
        SELECT periodNo, yearFr, yearTo, periodTitle, periodTitle2 FROM yk_period WHERE attrib = :xapp ORDER BY periodNo """)
    result = await db.execute(query, {"xapp": "1000010000"})
    # Row -> dict
    return [dict(row._mapping) for row in result.fetchall()]


async def get_event_dist_club(clubno:int, db: AsyncSession = Depends(get_db)):
    query = text("""
        SELECT a.eventNo, a.periodNo,a.eventTitle,a.eventTitleEng,a.eventType,a.eventFrom,a.eventTo,a.clubNo, count(b.eventNo) as cnt FROM yk_event a left join yk_eventMember b on a.eventNo = b.eventNo and b.attrib = :xapp 
        WHERE a.attrib = :xapp and (a.clubNo = :clubno or a.regionNo = 0) group by b.eventNo ORDER BY a.eventFrom """)
    result = await db.execute(query, {"xapp": "1000010000", "clubno": clubno})
    return [dict(row._mapping) for row in result.fetchall()]


async def get_clubevents(clubno:int, db: AsyncSession = Depends(get_db)):
    query = text("""
        SELECT eventNo, periodNo,eventTitle,eventTitleEng,eventType,eventFrom,eventTo,clubNo FROM yk_event WHERE attrib = :xapp and clubNo = :clubno ORDER BY eventFrom """)
    result = await db.execute(query, {"xapp": "1000010000", "clubno": clubno})
    # Row -> dict
    return [dict(row._mapping) for row in result.fetchall()]


async def get_clubeventsperiod(clubno:int,periodno:int ,db: AsyncSession = Depends(get_db)):
    query = text("""
        SELECT eventNo, periodNo,eventTitle,eventTitleEng,eventType,eventFrom,eventTo,clubNo FROM yk_event WHERE attrib = :xapp and clubNo = :clubno and periodNo = :periodno ORDER BY eventFrom """)
    result = await db.execute(query, {"xapp": "1000010000", "clubno": clubno, "periodno": periodno})
    # Row -> dict
    return [dict(row._mapping) for row in result.fetchall()]


async def get_eventdtl(eventno:int, db: AsyncSession = Depends(get_db)):
    query = text("""
        SELECT * FROM yk_event WHERE attrib = :xapp and eventNo = :eventno""")
    result = await db.execute(query, {"xapp": "1000010000", "eventno": eventno})
    return result.fetchone()


async def get_eventmembers(eventno:int,db: AsyncSession = Depends(get_db)):
    query = text("""SELECT * from yk_eventMember where attrib = :xapp and eventNo = :eno""")
    result = await db.execute(query, {"xapp": "1000010000", "eno": eventno})
    return [dict(row._mapping) for row in result.fetchall()]


async def get_cabhist(periodno:int,db: AsyncSession = Depends(get_db)):
    query = text("""SELECT * from yk_cabnet where attrib = :xapp and perionNo = :pno and cabYn = :cyn """)
    result = await db.execute(query, {"xapp": "1000010000", "pno": periodno, "cyn": 'Y'})
    return [dict(row._mapping) for row in result.fetchall()]


async def get_dmemberhist(periodno:int,db: AsyncSession = Depends(get_db)):
    query = text("""SELECT * from yk_cabnet where attrib = :xapp and perionNo = :pno """)
    result = await db.execute(query, {"xapp": "1000010000", "pno": periodno})
    return [dict(row._mapping) for row in result.fetchall()]

async def get_rank(db: AsyncSession = Depends(get_db)):
    query = text("""SELECT * FROM yk_rank where attrib = :xapp order by sortNo""")
    result = await db.execute(query, {"xapp": "1000010000"})
    return [dict(row._mapping) for row in result.fetchall()]


async def get_member(db: AsyncSession = Depends(get_db)):
    query = text("""SELECT a.*, b.clubName FROM yk_members a left join yk_club b on a.clubNo = b.clubNo where a.attrib = :xapp""")
    result = await db.execute(query, {"xapp": "1000010000"})
    return [dict(row._mapping) for row in result.fetchall()]


async def get_clubmember(clubno:int,db: AsyncSession = Depends(get_db)):
    query = text("""SELECT * FROM yk_members where clubNo = :cno and attrib = :xapp order by memberEntdate""")
    result = await db.execute(query, {"cno": clubno, "xapp": "1000010000"})
    return [dict(row._mapping) for row in result.fetchall()]


async def get_clubsponser(clubno:int,db: AsyncSession = Depends(get_db)):
    query = text("""SELECT memberNo, memberName FROM yk_members where clubNo = :cno and attrib = :xapp order by memberEntdate""")
    result = await db.execute(query, {"cno": clubno, "xapp": "1000010000"})
    return [dict(row._mapping) for row in result.fetchall()]


async def get_member_dtl(memberno:int, db: AsyncSession = Depends(get_db)):
    query = text("""SELECT * FROM yk_members where attrib = :xapp and memberNo = :membno""")
    result = await db.execute(query, {"xapp": "1000010000", "membno": memberno})
    return result.fetchone()


async def get_club(db: AsyncSession = Depends(get_db)):
    query = text("""SELECT * FROM yk_club where attrib = :xapp order by clubCno""")
    result = await db.execute(query, {"xapp": "1000010000"})
    return [dict(row._mapping) for row in result.fetchall()]


async def get_clubstaff(clubno:int,db: AsyncSession = Depends(get_db)):
    query = text("""SELECT a.*,b1.memberName as n1,b2.memberName as n2,b3.memberName as n3,b4.memberName as n4,b5.memberName as n5,b6.memberName as n6,b7.memberName as n7,b8.memberName as n8, c1.periodTitle2 as per1 
                    FROM yk_clubStaff a left join yk_members b1 on a.chairmanNo = b1.memberNo
                         left join yk_members b2 on a.vice1stNo = b2.memberNo 
                         left join yk_members b3 on a.vice2ndNo = b3.memberNo
                         left join yk_members b4 on a.vice3rdNo = b4.memberNo
                         left join yk_members b5 on a.secretaryNo = b5.memberNo
                         left join yk_members b6 on a.treasureNo = b6.memberNo
                         left join yk_members b7 on a.lionsteamerNo = b7.memberNo
                         left join yk_members b8 on a.tailtNo = b8.memberNo
                         left join yk_period c1 on a.periodNo = c1.periodNo
                    where a.clubNo = :clubno and a.attrib = :xapp order by a.periodNo""")
    result = await db.execute(query, {"xapp": "1000010000", "clubno": clubno})
    return [dict(row._mapping) for row in result.fetchall()]


async def get_clubstaffhist(memberno:int,db: AsyncSession = Depends(get_db)):
    query = text("""SELECT c1.periodTitle2 AS p1, a.clubNo, GROUP_CONCAT(DISTINCT CASE 
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
            END
            SEPARATOR '/') AS roles
            FROM yk_clubStaff a 
            LEFT JOIN yk_period c1 ON a.periodNo = c1.periodNo
            WHERE :memberNo IN (a.chairmanNo, a.vice1stNo, a.vice2ndNo, a.vice3rdNo,a.secretaryNo, a.treasureNo, a.lionsteamerNo, a.tailtNo) GROUP BY c1.periodTitle2, a.clubNo """)
    result = await db.execute(query, {"memberNo": memberno})
    return [dict(row._mapping) for row in result.fetchall()]


async def get_diststaff(clubno:int,db: AsyncSession = Depends(get_db)):
    query = text("""SELECT a.*,b1.memberName as n1, c1.periodTitle2 as per1, d1.rankTitle as r1 
                    FROM yk_distStaff a left join yk_members b1 on a.memberNo = b1.memberNo
                         left join yk_period c1 on a.periodNo = c1.periodNo
                        left join yk_rank d1 on a.rankNo = d1.rankNo
                    where a.clubNo = :clubno and a.attrib = :xapp order by a.periodNo, d1.sortNo""")
    result = await db.execute(query, {"xapp": "1000010000", "clubno": clubno})
    return [dict(row._mapping) for row in result.fetchall()]


async def get_diststaffmem(clubno:int,memberno:int,db: AsyncSession = Depends(get_db)):
    query = text("""SELECT a.*,b1.memberName as n1, c1.periodTitle2 as per1, d1.rankTitle as r1 
                    FROM yk_distStaff a left join yk_members b1 on a.memberNo = b1.memberNo
                         left join yk_period c1 on a.periodNo = c1.periodNo
                        left join yk_rank d1 on a.rankNo = d1.rankNo
                    where a.clubNo = :clubno and a.attrib = :xapp and a.memberNo = :memn order by a.periodNo, d1.sortNo""")
    result = await db.execute(query, {"xapp": "1000010000", "clubno": clubno, "memn": memberno})
    return [dict(row._mapping) for row in result.fetchall()]


async def get_club_spon(clubno:int,db: AsyncSession = Depends(get_db)):
    query = text("""SELECT clubNo, clubName, clubNameEng FROM yk_club where attrib = :xapp and clubNo < :clubno""")
    result = await db.execute(query, {"xapp": "1000010000", "clubno": clubno})
    return result.fetchall()


async def get_club_dtl(clubno:int,db: AsyncSession = Depends(get_db)):
    query = text("""SELECT * FROM yk_club where attrib = :xapp and clubNo = :clubno""")
    result = await db.execute(query, {"xapp": "1000010000", "clubno": clubno})
    return result.fetchone()


async def get_rank_dtl(rankno:int,db: AsyncSession = Depends(get_db)):
    query = text("""SELECT * FROM yk_rank where rankNo = :rankno""")
    result = await db.execute(query, {"rankno": rankno})
    return result.fetchone()


async def getdocdetail(docno:int,db:AsyncSession = Depends(get_db)):
    try:
        query = text("SELECT docNo, docEvent, memberTitle, memberName, docTitle, CONVERT(docContents using utf8mb4), docType, regDate, modDate, attrib FROM yk_doc where docNo = :docno and attrib = :xapp")
        docconts = await db.execute(query, {"docno":docno,"xapp":'1000010000'})
        row = docconts.fetchone()
        return row
    except Exception as e:
        return None


async def require_login(request: Request):
    user_no = request.session.get("user_No")
    if not user_no:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/"},
            detail="세션이 만료되어 재로그인이 필요합니다."
        )
    return user_no


async def session_chk(otp:str):
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


@app.get("/", response_class=HTMLResponse)
async def login_form(request: Request):
    if not request.session.get("user_No"):
        return templates.TemplateResponse("/login/login.html", {"request": request})
    else:
        return RedirectResponse(url="/mainpage", status_code=303)


# 로그인 요청 처리
@app.post("/login")
async def login_post(
        request: Request,
        response: Response,
        username: str = Form(...),
        password: str = Form(...),
        db: AsyncSession = Depends(get_db)
):
    query = text(
        "SELECT userNo, userName, userRole, defaultRegion, defaultClub FROM yk_user WHERE userId = :username AND userPassword = password(:password)")
    result = await db.execute(query, {"username": username, "password": password})
    user = result.fetchone()
    if user is None:
        return templates.TemplateResponse("login/login.html",{"request": request, "error": "Invalid credentials"})
    otp = await generate_otp()
    # 서버 세션에 사용자 ID 저장
    request.session["user_No"] = user[0]
    request.session["user_Name"] = user[1]
    request.session["user_Role"] = user[2]
    request.session["user_Region"] = user[3]
    request.session["user_Clubno"] = user[4]
    request.session["otp"] = otp
    return RedirectResponse(url="/success", status_code=303)


# 로그인 성공 페이지
@app.get("/success",response_class=HTMLResponse)
async def success_page(request: Request,db:AsyncSession = Depends(get_db)):
    user_No = request.session.get("user_No")
    user_Name = request.session.get("user_Name")
    user_Role = request.session.get("user_Role")
    user_region = request.session.get("user_Region")
    user_clubno = request.session.get("user_Clubno")
    otp = request.session.get("otp")
    otpstat = await reg_otp(otp,user_No,db)
    msg = ""
    if otpstat is False:
        msg = "OTP 등록 실패"
    if not user_No:
        return RedirectResponse(url="/")
    return templates.TemplateResponse("main/basic.html",
                                      {"request": request, "session": dict(request.session), "message": msg})


@app.get("/mainpage",response_class=HTMLResponse)
async def main_page(request: Request,db:AsyncSession = Depends(get_db)):
    user_No = request.session.get("user_No")
    user_Name = request.session.get("user_Name")
    user_Role = request.session.get("user_Role")
    user_region = request.session.get("user_Region")
    user_clubno = request.session.get("user_Clubno")
    otp = request.session.get("otp")
    msg = ""
    if not user_No:
        return RedirectResponse(url="/")
    return templates.TemplateResponse("main/basic.html",
                                      {"request": request,"session": dict(request.session), "message": msg})


@app.post("/changeuserpass")
async def change_password(
    data: dict = Body(...),  # JSON body를 dict로 받음
    db: AsyncSession = Depends(get_db)
):
    sql = text("UPDATE yk_user SET userPassword = PASSWORD(:passwd) WHERE userNo = :userno")
    await db.execute(sql, {"passwd": data["passwd"], "userno": data["uno"]})
    await db.commit()
    return {"result": "success"}


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse("static/favicon.ico")


@app.get("/logout")
async def logout(request: Request, db: AsyncSession = Depends(get_db)):
    try:
        user_No = request.session.get("user_No")
        await exp_otp(user_No, db)
        request.session.clear()  # 세션 삭제
        return RedirectResponse(url="/")
    except Exception as e:
        return RedirectResponse(url="/", status_code=303)

# User

# Report Member


@app.api_route("/report_event/{clubno}",response_class=HTMLResponse ,methods=["GET", "POST"] )
async def reportevent(request: Request, clubno:int, db: AsyncSession = Depends(get_db)):
    if not request.session.get("user_No"):
        return RedirectResponse(url="login/login.html", status_code=303)
    else:
        ranklist = await get_rank(db)
        periodlist = await getperiod(db)
        memberlist = await get_clubmember(clubno,db)
        eventlist = await get_event_dist_club(clubno, db)
        return templates.TemplateResponse("report/reportevent.html", {"request": request, "session": dict(request.session),"memberlist": memberlist, "ranklist": ranklist, "periodlist": periodlist, "eventlist": eventlist})


@app.api_route("/report_eventedit/{eventno}",response_class=HTMLResponse ,methods=["GET", "POST"] )
async def reportevent(request: Request, eventno:int, db: AsyncSession = Depends(get_db)):
    if not request.session.get("user_No"):
        return RedirectResponse(url="login/login.html", status_code=303)
    else:
        clubno = request.session.get("user_Clubno")
        ranklist = await get_rank(db)
        periodlist = await getperiod(db)
        memberlist = await get_clubmember(clubno,db)
        eventdtl = await get_eventdtl(eventno, db)
        joinmember = await get_eventmembers(eventno, db)
        return templates.TemplateResponse("report/reporteventedit.html", {"request": request, "session": dict(request.session),"memberlist": memberlist, "ranklist": ranklist, "periodlist": periodlist, "joinmember": joinmember, "eventdtl": eventdtl})


@app.get("/report_eventlist/{clubno}", response_class=HTMLResponse)
async def reportevent(request: Request, clubno:int, db: AsyncSession = Depends(get_db)):
    if not request.session.get("user_No"):
        return RedirectResponse(url="login/login.html", status_code=303)
    else:
        periodlist = await getperiod(db)
        eventlist = await get_event_dist_club(clubno, db)
        return templates.TemplateResponse("report/reporteventlist.html", {"request": request, "session": dict(request.session),"periodlist": periodlist, "eventlist": eventlist})


@app.get("/report_memberlist/{clubno}", response_class=HTMLResponse)
async def reportmember(request: Request, clubno:int, db: AsyncSession = Depends(get_db)):
    if not request.session.get("user_No"):
        return RedirectResponse(url="login/login.html", status_code=303)
    else:
        periodlist = await getperiod(db)
        memberlist = await get_clubmember(clubno,db)
        return templates.TemplateResponse("report/reportmemberlist.html", {"request": request, "session": dict(request.session),"periodlist": periodlist, "memberlist": memberlist})


@app.post("/insert_clubevent/")
async def save_clubevent(request: Request,db: AsyncSession = Depends(get_db)):
    clubno = request.session.get("user_Clubno")
    form = await request.form()
    event_no = to_int(form.get("event"))
    if not event_no:
        return RedirectResponse(url=f"/report_eventlist/{clubno}", status_code=303)
    member_nos = form.getlist("memberNo")  # e.g. ["3","7"]
    member_nos = [to_int(x) for x in member_nos if to_int(x) > 0]
    if len(member_nos) == 0:
        return RedirectResponse(url=f"/report_eventlist/{clubno}", status_code=303)
    support_map = {}
    for k, v in form.items():
        if k.startswith("supportAmount[") and k.endswith("]"):
            mid = k[len("supportAmount["):-1]
            m_no = to_int(mid, 0)
            amt = to_int(str(v).replace(",", "").strip(), 0)
            if m_no > 0:
                support_map[m_no] = amt
    rows = []
    sql = text("""UPDATE yk_eventMember SET attrib = :xxxup WHERE eventNo = :eventNo """)
    await db.execute(sql, {"xxxup": "XXXUPXXXUP", "eventNo": event_no})
    await db.commit()
    for m_no in member_nos:
        rows.append({
            "eventNo": event_no,
            "memberNo": m_no,
            "supportAmt": support_map.get(m_no, 0)
        })
        sql = text(""" INSERT INTO yk_eventMember (eventNo, memberNo, supportAmt) VALUES (:eventNo, :memberNo, :supportAmt) """)
        await db.execute(sql, rows[-1])
        await db.commit()
    return RedirectResponse(url=f"/report_eventlist/{clubno}", status_code=303)


#Club business


#Club Member List
@app.get("/club_memberlist/{clubno}", response_class=HTMLResponse)
async def club_memberlist(request: Request, clubno:int, db: AsyncSession = Depends(get_db)):
    if not request.session.get("user_No"):
        return RedirectResponse(url="login/login.html", status_code=303)
    else:
        memberlist = await get_clubmember(clubno,db)
        return templates.TemplateResponse("club/cmemberlist.html", {"request": request, "session": dict(request.session),"memberlist": memberlist})


@app.get("/cmember_edit/{memberno}", response_class=HTMLResponse)
async def cmemberedit(request: Request,memberno:int, db: AsyncSession = Depends(get_db)):
    if not request.session.get("user_No"):
        return RedirectResponse(url="login/login.html", status_code=303)
    else:
        clubno = request.session.get("user_Clubno")
        clubs = await get_club(db)
        spons = await get_clubsponser(clubno, db)
        member = await get_member_dtl(memberno,db)
        dstaffhist = await get_diststaffmem(clubno, memberno,db)
        cstaffhist = await get_clubstaffhist(memberno, db)
        return templates.TemplateResponse("club/cmemberedit.html", {"request": request, "clubs": clubs,"session": dict(request.session),"memberdtl": member, "spons": spons, "dstaffhist": dstaffhist, "cstaffhist":cstaffhist})


@app.get("/dist_stafflist/{clubno}", response_class=HTMLResponse)
async def dist_stafflist(request: Request, clubno:int, db: AsyncSession = Depends(get_db)):
    if not request.session.get("user_No"):
        return RedirectResponse(url="login/login.html", status_code=303)
    else:
        memberlist = await get_clubsponser(clubno,db)
        ranklist = await get_rank(db)
        stafflist = await get_diststaff(clubno, db)
        periods = await getperiod(db)
        return templates.TemplateResponse("club/dstafflist.html", {"request": request, "session": dict(request.session),"memberlist": memberlist, "stafflist": stafflist, "periods": periods, "periodno": None, "ranklist": ranklist })


@app.get("/club_stafflist/{clubno}", response_class=HTMLResponse)
async def club_stafflist(request: Request, clubno:int, db: AsyncSession = Depends(get_db)):
    if not request.session.get("user_No"):
        return RedirectResponse(url="login/login.html", status_code=303)
    else:
        memberlist = await get_clubsponser(clubno,db)
        stafflist = await get_clubstaff(clubno, db)
        periods = await getperiod(db)
        return templates.TemplateResponse("club/cstafflist.html", {"request": request, "session": dict(request.session),"memberlist": memberlist, "stafflist": stafflist, "periods": periods, "periodno": None })


@app.post("/club_staffupdate", response_class=HTMLResponse)
async def updatecstaff(request: Request, db: AsyncSession = Depends(get_db)):
    form_data = await request.form()
    clubno = request.session.get("user_Clubno")
    periodno = _clean_int(form_data.get("speriod"))
    if clubno is None or periodno is None:
        return RedirectResponse("/club_stafflist", status_code=303)
    data = {
        "clubNo": clubno,
        "periodNo": periodno,
        "chairmanNo": _clean_int(form_data.get("staff1")),
        "vice1stNo": _clean_int(form_data.get("staff2")),
        "vice2ndNo": _clean_int(form_data.get("staff3")),
        "vice3rdNo": _clean_int(form_data.get("staff4")),
        "secretaryNo": _clean_int(form_data.get("staff5")),
        "treasureNo": _clean_int(form_data.get("staff6")),
        "lionsteamerNo": _clean_int(form_data.get("staff7")),
        "tailtNo": _clean_int(form_data.get("staff8")),
        "slogan": _clean_str(form_data.get("slogan")),
    }
    insert_fields = list(data.keys())
    cols = ", ".join(insert_fields)
    vals = ", ".join([f":{k}" for k in insert_fields])
    update_keys = [k for k in data.keys() if k not in ("clubNo", "periodNo") and data[k] is not None]
    if not update_keys:
        return RedirectResponse(f"/club_stafflist/{clubno}", status_code=303)
    update_clause = ", ".join([f"{k} = VALUES({k})" for k in update_keys])
    q = text(f"""
        INSERT INTO yk_clubStaff ({cols})
        VALUES ({vals})
        ON DUPLICATE KEY UPDATE
        {update_clause}
    """)
    await db.execute(q, data)
    await db.commit()
    return RedirectResponse(f"/club_stafflist/{clubno}", status_code=303)


@app.post("/dist_staffupdate", response_class=HTMLResponse)
async def updatedstaff(request: Request, db: AsyncSession = Depends(get_db)):
    form_data = await request.form()
    clubno = request.session.get("user_Clubno")
    periodno = _clean_int(form_data.get("speriod"))
    if clubno is None or periodno is None:
        return RedirectResponse("/dist_stafflist/{clubno}", status_code=303)
    data = {
        "clubNo": clubno,
        "periodNo": periodno,
        "rankNo": _clean_int(form_data.get("drank")),
        "memberNo": _clean_int(form_data.get("dstaff")),
    }
    insert_fields = list(data.keys())
    cols = ", ".join(insert_fields)
    vals = ", ".join([f":{k}" for k in insert_fields])
    update_keys = [k for k in data.keys() if k not in ("clubNo", "periodNo") and data[k] is not None]
    if not update_keys:
        return RedirectResponse(f"/dist_stafflist/{clubno}", status_code=303)
    update_clause = ", ".join([f"{k} = VALUES({k})" for k in update_keys])
    q = text(f"""
        INSERT INTO yk_distStaff ({cols})
        VALUES ({vals})
        ON DUPLICATE KEY UPDATE
        {update_clause}
    """)
    await db.execute(q, data)
    await db.commit()
    return RedirectResponse(f"/dist_stafflist/{clubno}", status_code=303)

@app.get("/club_eventlist/{clubno}", response_class=HTMLResponse)
async def ceventlist(request: Request,clubno:int,db: AsyncSession = Depends(get_db)):
    if not request.session.get("user_No"):
        return RedirectResponse(url="login/login.html", status_code=303)
    else:
        periodlist = await getperiod(db)
        ceventlist = await get_clubevents(clubno,db)
        return templates.TemplateResponse("club/club_eventlist.html", {"request": request,"session": dict(request.session), "periodlist": periodlist, "ceventlist": ceventlist, "periodno": None})


@app.get("/club_eventlist/{clubno}/{periodno}", response_class=HTMLResponse)
async def ceventlist(request: Request,clubno:int,periodno:int,db: AsyncSession = Depends(get_db)):
    if not request.session.get("user_No"):
        return RedirectResponse(url="login/login.html", status_code=303)
    else:
        periodlist = await getperiod(db)
        ceventlist = await get_clubeventsperiod(clubno,periodno,db)
        return templates.TemplateResponse("club/club_eventlist.html", {"request": request,"session": dict(request.session), "periodlist": periodlist, "ceventlist": ceventlist, "periodno": periodno})


@app.post("/club_eventnew/{clubno}", response_class=HTMLResponse)
async def ceventnew(request: Request,clubno:int,db: AsyncSession = Depends(get_db)):
    if not request.session.get("user_No"):
        return RedirectResponse(url="login/login.html", status_code=303)
    else:
        periodlist = await getperiod(db)
        return templates.TemplateResponse("club/club_eventnew.html", {"request": request,"session": dict(request.session), "periodlist": periodlist,})


@app.get("/club_eventedit/{eventno}", response_class=HTMLResponse)
async def ceventedit(request: Request,eventno:int,db: AsyncSession = Depends(get_db)):
    if not request.session.get("user_No"):
        return RedirectResponse(url="login/login.html", status_code=303)
    else:
        periodlist = await getperiod(db)
        eventdtl = await get_eventdtl(eventno,db)
        return templates.TemplateResponse("club/club_eventedit.html", {"request": request,"session": dict(request.session), "periodlist": periodlist, "eventdtl": eventdtl})


#cevent_insert
@app.post("/cevent_insert/{clubno}", response_class=HTMLResponse)
async def insertcevent(request: Request, clubno:int, db: AsyncSession = Depends(get_db)):
    form_data = await request.form()
    data4insert = {
        "eventTitle": form_data.get("ceventtitle"),
        "eventType": form_data.get("eventtype"),
        "eventFrom": form_data.get("eventfrom"),
        "eventTo": form_data.get("eventto"),
        "periodNo": form_data.get("eventperiod"),
        "clubNo": clubno,
        }
    insert_fields = {key: value for key, value in data4insert.items() if value is not None}
    columns = ", ".join(insert_fields.keys())
    values = ", ".join([f":{key}" for key in insert_fields.keys()])
    query = text(f"INSERT INTO yk_event ({columns}) VALUES ({values})")
    await db.execute(query, insert_fields)
    await db.commit()
    return RedirectResponse(f"/club_eventlist/{clubno}", status_code=303)


@app.post("/cevent_update/{eventno}/{clubno}", response_class=HTMLResponse)
async def insertcevent(request: Request, eventno:int, clubno:int, db: AsyncSession = Depends(get_db)):
    form_data = await request.form()
    data4update = {
        "eventTitle": form_data.get("ceventtitle"),
        "eventType": form_data.get("eventtype"),
        "eventFrom": form_data.get("eventfrom"),
        "eventTo": form_data.get("eventto"),
        "periodNo": form_data.get("eventperiod"),
        "clubNo": clubno,
    }
    update_fields = {k: v for k, v in data4update.items() if v is not None}
    if not update_fields:
        return RedirectResponse(f"/club_eventlist/{clubno}", status_code=303)
    set_clause = ", ".join([f"{k} = :{k}" for k in update_fields.keys()])
    params = dict(update_fields)
    params["eventNo"] = eventno
    query = text(f"""UPDATE yk_event SET {set_clause} WHERE eventNo = :eventNo""")
    await db.execute(query, params)
    await db.commit()
    return RedirectResponse(f"/club_eventlist/{clubno}", status_code=303)


#Club Report List
@app.api_route("/report_member/{clubno}", response_class=HTMLResponse, methods=["GET", "POST"] )
async def reportmember(request: Request,clubno:int, db: AsyncSession = Depends(get_db)):
    if not request.session.get("user_No"):
        return RedirectResponse(url="login/login.html", status_code=303)
    else:
        memberlist = await get_clubmember(clubno,db)
        periodlist = await getperiod(db)
        return templates.TemplateResponse("report/reportmember.html", {"request": request, "session": dict(request.session),"memberlist": memberlist, "periodlist": periodlist})


# Basic Data
@app.get("/mst_rank", response_class=HTMLResponse)
async def rankmaster(request: Request, db: AsyncSession = Depends(get_db)):
    if not request.session.get("user_No"):
        return RedirectResponse(url="login/login.html", status_code=303)
    else:
        ranklist = await get_rank(db)
        return templates.TemplateResponse("master/ranklist.html", {"request": request, "session": dict(request.session),"ranklist": ranklist})


@app.post("/rank_reg", response_class=HTMLResponse)
async def rankmaster(request: Request):
    if not request.session.get("user_No"):
        return RedirectResponse(url="login/login.html", status_code=303)
    else:
        return templates.TemplateResponse("master/rankreg.html", {"request": request,"session": dict(request.session)})


@app.get("/rank_edit/{rankno}", response_class=HTMLResponse)
async def rankmaster(request: Request, rankno:int ,db: AsyncSession = Depends(get_db)):
    if not request.session.get("user_No"):
        return RedirectResponse(url="login/login.html", status_code=303)
    else:
        rank = await get_rank_dtl(rankno,db)
        return templates.TemplateResponse("master/rankedit.html", {"request": request, "rank": rank,"session": dict(request.session)})


@app.api_route("/rank_update/{rankno}", response_class=HTMLResponse, methods=["GET", "POST"])
async def updaterank(request: Request, rankno: int, db: AsyncSession = Depends(get_db)):
    form_data = await request.form()
    rtitle = form_data.get("rtitle")
    rtitleeng = form_data.get("rtitleeng")
    rtitlechn = form_data.get("rtitlechn")
    rtype = form_data.get("rtype")
    rsortno = form_data.get("rsortno")
    query = text(f"update yk_rank set rankTitle=:rtitle,rankTitleEng=:rtitleeng, rankTitleCn=:rtitlechn, rankType=:rtype, sortNo=:rsortno, modDate=now() where rankNo=:rankno")
    await db.execute(query, {"rtitle": rtitle, "rtitleeng": rtitleeng, "rtitlechn": rtitlechn, "rtype": rtype, "rsortno": rsortno, "rankno": rankno})
    await db.commit()
    return RedirectResponse(f"/mst_rank", status_code=303)


@app.api_route("/rank_insert", response_class=HTMLResponse, methods=["GET", "POST"])
async def insertrank(request: Request, db: AsyncSession = Depends(get_db)):
    form_data = await request.form()
    rtitle = form_data.get("rtitle")
    rtitleeng = form_data.get("rtitleeng")
    rtitlechn = form_data.get("rtitlechn")
    rtype = form_data.get("rtype")
    rsortno = form_data.get("rsortno")
    query = text(f"INSERT INTO yk_rank (rankTitle,rankTitleEng,rankTitleCn, rankType, sortNo) values (:rtitle,:rtitleeng,:rtitlechn,:rtype,:rsortno)")
    await db.execute(query, {"rtitle": rtitle, "rtitleeng": rtitleeng, "rtitlechn": rtitlechn, "rtype": rtype, "rsortno": rsortno})
    await db.commit()
    return RedirectResponse(f"/mst_rank", status_code=303)


@app.get("/mst_member", response_class=HTMLResponse)
async def membermaster(request: Request, db: AsyncSession = Depends(get_db)):
    if not request.session.get("user_No"):
        return RedirectResponse(url="login/login.html", status_code=303)
    else:
        memberlist = await get_member(db)
        return templates.TemplateResponse("master/memberlist.html", {"request": request,"session": dict(request.session),"memberlist": memberlist})


@app.get("/member_edit/{memberno}", response_class=HTMLResponse)
async def memberedit(request: Request,memberno:int, db: AsyncSession = Depends(get_db)):
    if not request.session.get("user_No"):
        return RedirectResponse(url="login/login.html", status_code=303)
    else:
        clubs = await get_club(db)
        member = await get_member_dtl(memberno,db)
        return templates.TemplateResponse("master/memberedit.html", {"request": request, "clubs": clubs,"session": dict(request.session),"memberdtl": member})


@app.post("/member_reg", response_class=HTMLResponse)
async def memberedit(request: Request, db: AsyncSession = Depends(get_db)):
    if not request.session.get("user_No"):
        return RedirectResponse(url="login/login.html", status_code=303)
    else:
        clubs = await get_club(db)
        return templates.TemplateResponse("master/memberreg.html", {"request": request, "clubs": clubs,"session": dict(request.session)})


@app.post("/member_insert", response_class=HTMLResponse)
async def insertmember(request: Request, db: AsyncSession = Depends(get_db)):
    form_data = await request.form()
    data4insert = {
        "memberName": _clean_str(form_data.get("membername")),
        "memberNameEng": _clean_str(form_data.get("membernameeng")),
        "memberNameCn": _clean_str(form_data.get("membernamecn")),
        "memberBirth": _clean_str(form_data.get("memberbirth")),
        "memberEntdate": _clean_str(form_data.get("regdate")),
        "memberMF": _clean_str(form_data.get("membermf")),
        "memberSponser": _clean_int(form_data.get("memberspon")),
        "regNo": _clean_int(form_data.get("regno")),
        "clubNo": _clean_int(form_data.get("memberclub")),
        "memberStatus": _clean_str(form_data.get("memberstat")),
        }
    insert_fields = {key: value for key, value in data4insert.items() if value is not None}
    columns = ", ".join(insert_fields.keys())
    values = ", ".join([f":{key}" for key in insert_fields.keys()])
    query = text(f"INSERT INTO yk_members ({columns}) VALUES ({values})")
    await db.execute(query, insert_fields)
    await db.commit()
    return RedirectResponse(f"/mst_member", status_code=303)


@app.post("/cmember_insert", response_class=HTMLResponse)
async def insertcmember(request: Request, db: AsyncSession = Depends(get_db)):
    form_data = await request.form()
    data4insert = {
        "memberName": _clean_str(form_data.get("membername")),
        "memberNameEng": _clean_str(form_data.get("membernameeng")),
        "memberNameCn": _clean_str(form_data.get("membernamecn")),
        "memberBirth": _clean_str(form_data.get("memberbirth")),
        "memberEntdate": _clean_str(form_data.get("regdate")),
        "memberMF": _clean_str(form_data.get("membermf")),
        "memberSponser": _clean_int(form_data.get("memberspon")),
        "regNo": _clean_int(form_data.get("regno")),
        "clubNo": _clean_int(form_data.get("memberclub")),
        "memberStatus": _clean_str(form_data.get("memberstat")),
        }
    clubno = _clean_int(form_data.get("memberclub"))
    insert_fields = {key: value for key, value in data4insert.items() if value is not None}
    columns = ", ".join(insert_fields.keys())
    values = ", ".join([f":{key}" for key in insert_fields.keys()])
    query = text(f"INSERT INTO yk_members ({columns}) VALUES ({values})")
    await db.execute(query, insert_fields)
    await db.commit()
    return RedirectResponse(f"/club_memberlist/{clubno}", status_code=303)


@app.post("/cmember_reg", response_class=HTMLResponse)
async def cmemberreg(request: Request, db: AsyncSession = Depends(get_db)):
    if not request.session.get("user_No"):
        return RedirectResponse(url="login/login.html", status_code=303)
    else:
        clubno = request.session.get("user_Clubno")
        clubs = await get_club(db)
        spons = await get_clubsponser(clubno, db)
        return templates.TemplateResponse("club/cmemberreg.html", {"request": request, "clubs": clubs,"session": dict(request.session), "spons": spons})


@app.post("/cmember_update/{memberno}", response_class=HTMLResponse)
async def cupdatemember(request: Request, memberno: int, db: AsyncSession = Depends(get_db)):
    form_data = await request.form()
    data4update = {
        "memberName": _clean_str(form_data.get("membername")),
        "memberNameEng": _clean_str(form_data.get("membernameeng")),
        "memberNameCn": _clean_str(form_data.get("membernamecn")),
        "memberBirth": _clean_str(form_data.get("memberbirth")),
        "memberEntdate": _clean_str(form_data.get("regdate")),
        "memberMF": _clean_str(form_data.get("membermf")),
        "memberSponser": _clean_int(form_data.get("memberspon")),
        "regNo": _clean_str(form_data.get("regno")),
        "clubNo": _clean_int(form_data.get("memberclub")),
        "maskYN": _clean_str(form_data.get("membermask")),
        "memberStatus": _clean_str(form_data.get("memberstat")),
        "memberMemo": form_data.get("membermemo",''),
    }
    clubno = _clean_int(form_data.get("memberclub"))
    update_fields = {k: v for k, v in data4update.items() if v is not None}
    if not update_fields:
        return RedirectResponse(f"/club_memberlist/{clubno}", status_code=303)
    set_clause = ", ".join([f"{k} = :{k}" for k in update_fields.keys()])
    params = dict(update_fields)
    params["memberNo"] = memberno
    query = text(f"UPDATE yk_members SET {set_clause} WHERE memberNo = :memberNo")
    await db.execute(query, params)
    await db.commit()
    return RedirectResponse(f"/club_memberlist/{clubno}", status_code=303)


@app.post("/member_update/{memberno}", response_class=HTMLResponse)
async def updatemember(request: Request, memberno: int, db: AsyncSession = Depends(get_db)):
    form_data = await request.form()
    data4update = {
        "memberName": _clean_str(form_data.get("membername")),
        "memberNameEng": _clean_str(form_data.get("membernameeng")),
        "memberNameCn": _clean_str(form_data.get("membernamecn")),
        "memberBirth": _clean_str(form_data.get("memberbirth")),
        "memberEntdate": _clean_str(form_data.get("regdate")),
        "memberMF": _clean_str(form_data.get("membermf")),
        "memberSponser": _clean_int(form_data.get("memberspon")),
        "regNo": _clean_int(form_data.get("regno")),
        "clubNo": _clean_int(form_data.get("memberclub")),
        "maskYN": _clean_str(form_data.get("membermask")),
        "memberStatus": _clean_str(form_data.get("memberstat")),
        "memberMemo": form_data.get("membermemo",''),
    }
    update_fields = {k: v for k, v in data4update.items() if v is not None}
    if not update_fields:
        return RedirectResponse(f"/mst_member", status_code=303)
    set_clause = ", ".join([f"{k} = :{k}" for k in update_fields.keys()])
    params = dict(update_fields)
    params["memberNo"] = memberno
    query = text(f"UPDATE yk_members SET {set_clause} WHERE memberNo = :memberNo")
    await db.execute(query, params)
    await db.commit()
    return RedirectResponse(f"/mst_member", status_code=303)


@app.get("/mst_region", response_class=HTMLResponse)
async def regionmaster(request: Request, db: AsyncSession = Depends(get_db)):
    if not request.session.get("user_No"):
        return RedirectResponse(url="login/login.html", status_code=303)
    else:
        regionlist = await getregionList(db) # 지역 데이터로 변경해야 함
        return templates.TemplateResponse("master/regionlist.html", {"request": request,"session": dict(request.session), "regionlist": regionlist})


@app.get("/mst_club", response_class=HTMLResponse)
async def clubmaster(request: Request, db: AsyncSession = Depends(get_db)):
    if not request.session.get("user_No"):
        return RedirectResponse(url="login/login.html", status_code=303)
    else:
        clublist = await get_club(db)
        return templates.TemplateResponse("master/clublist.html", {"request": request,"session": dict(request.session), "clublist": clublist})


@app.get("/club_edit/{clubno}", response_class=HTMLResponse)
async def clubedit(request: Request,clubno:int, db: AsyncSession = Depends(get_db)):
    if not request.session.get("user_No"):
        return RedirectResponse(url="login/login.html", status_code=303)
    else:
        club = await get_club_dtl(clubno,db)
        spons = await get_club_spon(clubno,db)
        return templates.TemplateResponse("master/clubedit.html", {"request": request,"session": dict(request.session), "clubdtl": club, "spons": spons})


@app.post("/club_reg", response_class=HTMLResponse)
async def clubedit(request: Request,db: AsyncSession = Depends(get_db)):
    if not request.session.get("user_No"):
        return RedirectResponse(url="login/login.html", status_code=303)
    else:
        club = await get_club(db)
        return templates.TemplateResponse("master/clubreg.html", {"request": request, "session": dict(request.session),"clubs": club})


@app.post("/club_update/{clubno}", response_class=HTMLResponse)
async def clubupdate(request: Request,clubno:int, db: AsyncSession = Depends(get_db)):
    form_data = await request.form()
    clubname = form_data.get("clubname")
    clubnameeng = form_data.get("clubnameeng")
    clubnamecn = form_data.get("clubnamecn")
    estdate = form_data.get("estdate")
    charno = form_data.get("charno")
    clubaddr = form_data.get("clubaddr")
    clubtel = form_data.get("clubtel")
    clubfax = form_data.get("clubfax")
    clubemail = form_data.get("clubemail")
    clubspon = form_data.get("clubspon")
    query = text(
        f"update yk_club set clubName=:clubname,clubNameEng=:clubnameeng,clubNameCn=:clubnamecn, clubEstdate=:estdate, "
        f"clubCno=:charno, clubTel=:clubtel, clubFax=:clubfax, clubEmail=:clubemail, clubSponser=:clubspon, modDate=now() where clubNo=:clubno")
    await db.execute(query, {"clubname":clubname, "clubnameeng":clubnameeng, "clubnamecn":clubnamecn, "estdate":estdate, "charno":charno, "clubtel":clubtel, "clubfax":clubfax, "clubemail":clubemail, "clubspon":clubspon, "clubaddr":clubaddr, "clubno":clubno})
    await db.commit()
    return RedirectResponse(f"/mst_club", status_code=303)


@app.post("/club_insert", response_class=HTMLResponse)
async def clubinsert(request: Request, db: AsyncSession = Depends(get_db)):
    form_data = await request.form()
    clubname = form_data.get("clubname")
    clubnameeng = form_data.get("clubnameeng")
    clubnamecn = form_data.get("clubnamecn")
    estdate = form_data.get("estdate")
    charno = form_data.get("charno")
    clubaddr = form_data.get("clubaddr")
    clubtel = form_data.get("clubtel")
    clubfax = form_data.get("clubfax")
    clubemail = form_data.get("clubemail")
    clubspon = form_data.get("clubspon")
    query = text(
        f"INSERT INTO yk_club (clubName,clubNameEng,clubNameCn, clubEstdate, clubCno, clubTel, clubFax, clubEmail, clubSponser) "
        f"values (:clubname,:clubnameeng,:clubnamecn,:estdate,:charno,:clubtel,:clubfax,:clubemail,:clubspon)")
    await db.execute(query, {"clubname":clubname, "clubnameeng":clubnameeng, "clubnamecn":clubnamecn, "estdate":estdate, "charno":charno, "clubtel":clubtel, "clubfax":clubfax, "clubemail":clubemail, "clubspon":clubspon, "clubaddr":clubaddr})
    await db.commit()
    return RedirectResponse(f"/mst_club", status_code=303)


#YK55
@app.get("/yk55greet", response_class=HTMLResponse)
async def yk55greet(request: Request, db: AsyncSession = Depends(get_db)):
    if not request.session.get("user_No"):
        return RedirectResponse(url="login/login.html", status_code=303)
    else:
        doclist = await getdocList(db)
        return templates.TemplateResponse("yk55/yk55_greetings.html", {"request": request,"session": dict(request.session), "doclist": doclist})


@app.post("/yk55greet_reg", response_class=HTMLResponse)
async def yk55greet_reg(request: Request):
    if not request.session.get("user_No"):
        return RedirectResponse(url="login/login.html", status_code=303)
    else:
        return templates.TemplateResponse("yk55/yk55_greetings_reg.html", {"request": request, "session": dict(request.session)})


@app.get("/yk55greet_edit/{greetno}", response_class=HTMLResponse)
async def yk55greet_reg(request: Request, greetno: int, db: AsyncSession = Depends(get_db)):
    if not request.session.get("user_No"):
        return RedirectResponse(url="login/login.html", status_code=303)
    else:
        docs = await getdocdetail(greetno, db)
        return templates.TemplateResponse("yk55/yk55_greetings_edit.html", {"request": request,"session": dict(request.session), "docs": docs})


@app.get("/yk55greet_preview/{greetno}", response_class=HTMLResponse)
async def yk55greet_prv(request: Request, greetno: int, db: AsyncSession = Depends(get_db)):
    if not request.session.get("user_No"):
        return RedirectResponse(url="login/login.html", status_code=303)
    else:
        docs = await getdocdetail(greetno, db)
        return templates.TemplateResponse("tmplets/greet01.html", {"request": request,"session": dict(request.session), "docs": docs})


@app.api_route("/yk55greetupdate/{docno}", response_class=HTMLResponse, methods=["GET", "POST"])
async def updatedoc(request: Request, docno: int, db: AsyncSession = Depends(get_db)):
    form_data = await request.form()
    doctitle = form_data.get("dtitle")
    docconts = form_data.get("dcontent")
    doctype = form_data.get("dtype")
    docevent = form_data.get("docevent")
    dwriter1 = form_data.get("dwriter1")
    dwriter2 = form_data.get("dwriter2")
    query = text(f"update yk_doc set docTitle=:doctitle,docContents=:docconts,memberTitle=:dwriter1,memberName=:dwriter2, docType=:doctype,docEvent=:docevent, modDate=now() where docNo=:docno")
    await db.execute(query, {"docno": docno, "doctitle": doctitle, "docconts": docconts, "doctype": doctype, "dwriter1": dwriter1, "dwriter2": dwriter2, "docevent": docevent})
    await db.commit()
    return RedirectResponse(f"/yk55greet", status_code=303)


@app.api_route("/yk55greetinsert/", response_class=HTMLResponse, methods=["GET", "POST"])
async def insertdoc(request: Request, db: AsyncSession = Depends(get_db)):
    form_data = await request.form()
    doctitle = form_data.get("dtitle")
    docconts = form_data.get("dcontent")
    doctype = form_data.get("dtype")
    docevent = form_data.get("docevent")
    dwriter1 = form_data.get("dwriter1")
    dwriter2 = form_data.get("dwriter2")
    query = text(f"INSERT INTO yk_doc (docTitle, docContents, memberTitle, memberName, userNo, docType, docEvent) values (:doctitle,:docconts,:dwriter1,:dwriter2, :userno, :doctype, :docevent) ")
    await db.execute(query, {"doctitle": doctitle, "docconts": docconts, "userno": request.session.get("user_No"), "doctype": doctype, "dwriter1": dwriter1, "dwriter2": dwriter2, "docevent": docevent })
    await db.commit()
    return RedirectResponse(f"/yk55greet", status_code=303)


@app.get("/yk55cabhist", response_class=HTMLResponse)
async def yk55cabhist(request: Request,db: AsyncSession = Depends(get_db)):
    if not request.session.get("user_No"):
        return RedirectResponse(url="login/login.html", status_code=303)
    else:
        periods = await getperiod(db)
        return templates.TemplateResponse("yk55/yk55_cabhist.html", {"request": request,"session": dict(request.session), "periods": periods})
    
    
@app.get("/yk55cabhist_view/{periodno}", response_class=HTMLResponse)
async def yk55cabhist(request: Request,periodno:int,db: AsyncSession = Depends(get_db)):
    if not request.session.get("user_No"):
        return RedirectResponse(url="login/login.html", status_code=303)
    else:
        cabs = await get_cabhist(periodno, db)
        return templates.TemplateResponse("yk55/yk55_cabhistview.html", {"request": request,"session": dict(request.session), "cabs": cabs})


@app.get("/yk55servhist", response_class=HTMLResponse)
async def yk55servhist(request: Request,db: AsyncSession = Depends(get_db)):
    if not request.session.get("user_No"):
        return RedirectResponse(url="login/login.html", status_code=303)
    else:
        periods = await getperiod(db)
        return templates.TemplateResponse("yk55/yk55_servhist.html", {"request": request,"session": dict(request.session), "periods": periods})


@app.get("/yk55servhist_view/{period}", response_class=HTMLResponse)
async def yk55servhist(request: Request,period:int,db: AsyncSession = Depends(get_db)):
    if not request.session.get("user_No"):
        return RedirectResponse(url="login/login.html", status_code=303)
    else:
        svrs = await getperiod(db)
        return templates.TemplateResponse("yk55/yk55_servhistview.html", {"request": request,"session": dict(request.session), "svrs": svrs})


@app.get("/yk55membhist", response_class=HTMLResponse)
async def yk55membhist(request: Request,db: AsyncSession = Depends(get_db)):
    if not request.session.get("user_No"):
        return RedirectResponse(url="login/login.html", status_code=303)
    else:
        periods = await getperiod(db)
        return templates.TemplateResponse("yk55/yk55_memberhist.html", {"request": request,"session": dict(request.session), "periods": periods})


@app.get("/yk55membhist_view/{periodno}", response_class=HTMLResponse)
async def yk55membhist(request: Request,periodno:int,db: AsyncSession = Depends(get_db)):
    if not request.session.get("user_No"):
        return RedirectResponse(url="login/login.html", status_code=303)
    else:
        membs = await get_dmemberhist(periodno, db)
        return templates.TemplateResponse("yk55/yk55_memberhistview.html", {"request": request,"session": dict(request.session), "membs": membs})


@app.get("/yk55mjfhist", response_class=HTMLResponse)
async def yk55mjfhist(request: Request):
    if not request.session.get("user_No"):
        return RedirectResponse(url="login/login.html", status_code=303)
    else:
        return templates.TemplateResponse("yk55/yk55_mjfhist.html", {"request": request,"session": dict(request.session)})

