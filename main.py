from urllib import request
import status
import uvicorn
from fastapi import FastAPI, Depends, Request, Form, Response, HTTPException, File, UploadFile, Body
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
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
THUMBNAIL_DIR = "./static/img/members"
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
    thumbnail_path = os.path.join(THUMBNAIL_DIR, f"{memberno}.png")
    # 썸네일 저장
    image.save(thumbnail_path, format="PNG")
    return thumbnail_path


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
        query = text("UPDATE yk_seckey SET expDate = now(), attrib = :xup where userNo = :userNo and attrib = :xapp")
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


async def getperiod(db: AsyncSession = Depends(get_db)):
    query = text("""
        SELECT periodNo, yearFr, yearTo, periodTitle FROM yk_period WHERE attrib = :xapp ORDER BY periodNo """)
    result = await db.execute(query, {"xapp": "1000010000"})
    # Row -> dict
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
    query = text("""SELECT * FROM yk_members where attrib = :xapp""")
    result = await db.execute(query, {"xapp": "1000010000"})
    return [dict(row._mapping) for row in result.fetchall()]


async def get_club(db: AsyncSession = Depends(get_db)):
    query = text("""SELECT * FROM yk_club where attrib = :xapp order by clubCno""")
    result = await db.execute(query, {"xapp": "1000010000"})
    return [dict(row._mapping) for row in result.fetchall()]


async def get_rank_dtl(rankno:int,db: AsyncSession = Depends(get_db)):
    query = text("""SELECT * FROM yk_rank where rankNo = :rankno""")
    result = await db.execute(query, {"rankno": rankno})
    return result.fetchone()


async def getdocdetail(docno:int,db:AsyncSession = Depends(get_db)):
    try:
        query = text("SELECT docNo, docCat, memberTitle, userNo, docTitle, CONVERT(docContents using utf8mb4), docType, regDate, modDate, attrib FROM yk_doc where docNo = :docno and attrib = :xapp")
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
        "SELECT userNo, userName, userRole, defaultRegion, defaultClubno FROM yk_user WHERE userId = :username AND userPassword = password(:password)")
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
                                      {"request": request, "user_No": user_No, "user_Name": user_Name,
                                       "user_Role": user_Role, "user_region": user_region, "user_clubno": user_clubno, "otp": otp, "message": msg})


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
                                      {"request": request, "user_No": user_No, "user_Name": user_Name,
                                       "user_Role": user_Role, "user_region": user_region, "user_clubno": user_clubno, "otp": otp, "message": msg})


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

# Member

# Basic Data
@app.get("/mst_rank", response_class=HTMLResponse)
async def rankmaster(request: Request, db: AsyncSession = Depends(get_db)):
    if not request.session.get("user_No"):
        return RedirectResponse(url="login/login.html", status_code=303)
    else:
        ranklist = await get_rank(db)
        return templates.TemplateResponse("master/ranklist.html", {"request": request, "ranklist": ranklist})


@app.post("/rank_reg", response_class=HTMLResponse)
async def rankmaster(request: Request):
    if not request.session.get("user_No"):
        return RedirectResponse(url="login/login.html", status_code=303)
    else:
        return templates.TemplateResponse("master/rankreg.html", {"request": request})


@app.get("/rank_edit/{rankno}", response_class=HTMLResponse)
async def rankmaster(request: Request, rankno:int ,db: AsyncSession = Depends(get_db)):
    if not request.session.get("user_No"):
        return RedirectResponse(url="login/login.html", status_code=303)
    else:
        rank = await get_rank_dtl(rankno,db)
        return templates.TemplateResponse("master/rankedit.html", {"request": request, "rank": rank})


@app.api_route("/rank_update/{rankno}", response_class=HTMLResponse, methods=["GET", "POST"])
async def updaterank(request: Request, rankno: int, db: AsyncSession = Depends(get_db)):
    form_data = await request.form()
    rtitle = form_data.get("rtitle")
    rtitleeng = form_data.get("rtitleeng")
    rtitlechn = form_data.get("rtitlechn")
    rtype = form_data.get("rtype")
    rsortno = form_data.get("rsortno")
    query = text(f"update yk_rank set rankTitle=:rtitle,rankTitleEng=:rtitleeng, rankTitleChn=:rtitlechn, rankType=:rtype, sortNo=:rsortno, modDate=now() where rankNo=:rankno")
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
    query = text(f"INSERT INTO yk_rank (rankTitle,rankTitleEng,rankTitleChn, rankType, sortNo) values (:rtitle,:rtitleeng,:rtitlechn,:rtype,:rsortno)")
    await db.execute(query, {"rtitle": rtitle, "rtitleeng": rtitleeng, "rtitlechn": rtitlechn, "rtype": rtype, "rsortno": rsortno})
    await db.commit()
    return RedirectResponse(f"/mst_rank", status_code=303)


@app.get("/mst_member", response_class=HTMLResponse)
async def membermaster(request: Request, db: AsyncSession = Depends(get_db)):
    if not request.session.get("user_No"):
        return RedirectResponse(url="login/login.html", status_code=303)
    else:
        memberlist = await get_member(db)
        return templates.TemplateResponse("master/memberlist.html", {"request": request, "memberlist": memberlist})


@app.get("/mst_club", response_class=HTMLResponse)
async def clubmaster(request: Request, db: AsyncSession = Depends(get_db)):
    if not request.session.get("user_No"):
        return RedirectResponse(url="login/login.html", status_code=303)
    else:
        clublist = await get_club(db)
        return templates.TemplateResponse("master/clublist.html", {"request": request, "clublist": clublist})


#YK55
@app.get("/yk55greet", response_class=HTMLResponse)
async def yk55greet(request: Request, db: AsyncSession = Depends(get_db)):
    if not request.session.get("user_No"):
        return RedirectResponse(url="login/login.html", status_code=303)
    else:
        doclist = await getdocList(db)
        return templates.TemplateResponse("yk55/yk55_greetings.html", {"request": request, "doclist": doclist})


@app.post("/yk55greet_reg", response_class=HTMLResponse)
async def yk55greet_reg(request: Request):
    if not request.session.get("user_No"):
        return RedirectResponse(url="login/login.html", status_code=303)
    else:
        return templates.TemplateResponse("yk55/yk55_greetings_reg.html", {"request": request})


@app.get("/yk55greet_edit/{greetno}", response_class=HTMLResponse)
async def yk55greet_reg(request: Request, greetno: int, db: AsyncSession = Depends(get_db)):
    if not request.session.get("user_No"):
        return RedirectResponse(url="login/login.html", status_code=303)
    else:
        docs = await getdocdetail(greetno, db)
        return templates.TemplateResponse("yk55/yk55_greetings_edit.html", {"request": request, "docs": docs})


@app.get("/yk55greet_preview/{greetno}", response_class=HTMLResponse)
async def yk55greet_prv(request: Request, greetno: int, db: AsyncSession = Depends(get_db)):
    if not request.session.get("user_No"):
        return RedirectResponse(url="login/login.html", status_code=303)
    else:
        docs = await getdocdetail(greetno, db)
        return templates.TemplateResponse("tmplets/greet01.html", {"request": request, "docs": docs})


@app.api_route("/yk55greetupdate/{docno}", response_class=HTMLResponse, methods=["GET", "POST"])
async def updatedoc(request: Request, docno: int, db: AsyncSession = Depends(get_db)):
    form_data = await request.form()
    doctitle = form_data.get("dtitle")
    docconts = form_data.get("dcontent")
    doctype = form_data.get("dtype")
    dwriter = form_data.get("dwriter")
    query = text(f"update yk_doc set docTitle=:doctitle,docContents=:docconts,memberTitle=:dwriter, docType=:doctype,  modDate=now() where docNo=:docno")
    await db.execute(query, {"docno": docno, "doctitle": doctitle, "docconts": docconts, "doctype": doctype, "dwriter": dwriter})
    await db.commit()
    return RedirectResponse(f"/yk55greet", status_code=303)


@app.api_route("/yk55greetinsert/", response_class=HTMLResponse, methods=["GET", "POST"])
async def insertdoc(request: Request, db: AsyncSession = Depends(get_db)):
    form_data = await request.form()
    doctitle = form_data.get("dtitle")
    docconts = form_data.get("dcontent")
    doctype = form_data.get("dtype")
    dwriter = form_data.get("dwriter")
    query = text(f"INSERT INTO yk_doc (docTitle, docContents, memberTitle, userNo, docType) values (:doctitle,:docconts,:dwriter, :userno, :doctype) ")
    await db.execute(query, {"doctitle": doctitle, "docconts": docconts, "userno": request.session.get("user_No"), "doctype": doctype, "dwriter": dwriter })
    await db.commit()
    return RedirectResponse(f"/yk55greet", status_code=303)


@app.get("/yk55cabhist", response_class=HTMLResponse)
async def yk55cabhist(request: Request,db: AsyncSession = Depends(get_db)):
    if not request.session.get("user_No"):
        return RedirectResponse(url="login/login.html", status_code=303)
    else:
        periods = await getperiod(db)
        return templates.TemplateResponse("yk55/yk55_cabhist.html", {"request": request, "periods": periods})
    
    
@app.get("/yk55cabhist_view/{periodno}", response_class=HTMLResponse)
async def yk55cabhist(request: Request,periodno:int,db: AsyncSession = Depends(get_db)):
    if not request.session.get("user_No"):
        return RedirectResponse(url="login/login.html", status_code=303)
    else:
        cabs = await get_cabhist(periodno, db)
        return templates.TemplateResponse("yk55/yk55_cabhistview.html", {"request": request, "cabs": cabs})


@app.get("/yk55servhist", response_class=HTMLResponse)
async def yk55servhist(request: Request,db: AsyncSession = Depends(get_db)):
    if not request.session.get("user_No"):
        return RedirectResponse(url="login/login.html", status_code=303)
    else:
        periods = await getperiod(db)
        return templates.TemplateResponse("yk55/yk55_servhist.html", {"request": request, "periods": periods})


@app.get("/yk55servhist_view/{period}", response_class=HTMLResponse)
async def yk55servhist(request: Request,period:int,db: AsyncSession = Depends(get_db)):
    if not request.session.get("user_No"):
        return RedirectResponse(url="login/login.html", status_code=303)
    else:
        svrs = await getperiod(db)
        return templates.TemplateResponse("yk55/yk55_servhistview.html", {"request": request, "svrs": svrs})


@app.get("/yk55membhist", response_class=HTMLResponse)
async def yk55membhist(request: Request,db: AsyncSession = Depends(get_db)):
    if not request.session.get("user_No"):
        return RedirectResponse(url="login/login.html", status_code=303)
    else:
        periods = await getperiod(db)
        return templates.TemplateResponse("yk55/yk55_memberhist.html", {"request": request, "periods": periods})


@app.get("/yk55membhist_view/{periodno}", response_class=HTMLResponse)
async def yk55membhist(request: Request,periodno:int,db: AsyncSession = Depends(get_db)):
    if not request.session.get("user_No"):
        return RedirectResponse(url="login/login.html", status_code=303)
    else:
        membs = await get_dmemberhist(periodno, db)
        return templates.TemplateResponse("yk55/yk55_memberhistview.html", {"request": request, "membs": membs})


@app.get("/yk55mjfhist", response_class=HTMLResponse)
async def yk55mjfhist(request: Request):
    if not request.session.get("user_No"):
        return RedirectResponse(url="login/login.html", status_code=303)
    else:
        return templates.TemplateResponse("yk55/yk55_mjfhist.html", {"request": request})