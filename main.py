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


async def getdocdetail(docno:int,db:AsyncSession = Depends(get_db)):
    try:
        query = text("SELECT docNo, docCat, memberNo, userNo, docTitle, CONVERT(docContents using utf8mb4), docType, regDate, modDate, attrib FROM yk_doc where docNo = :docno and attrib = :xapp")
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
        "SELECT userNo, userName,userRole, defaultRegion, defaultClubno FROM yk_user WHERE userId = :username AND userPassword = password(:password)")
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


@app.api_route("/yk55greetupdate/{docno}", response_class=HTMLResponse, methods=["GET", "POST"])
async def updatedoc(request: Request, docno: int, db: AsyncSession = Depends(get_db)):
    form_data = await request.form()
    doctitle = form_data.get("dtitle")
    docconts = form_data.get("dcontent")
    query = text(f"update yk_doc set docTitle=:doctitle,docContents=:docconts where docNo=:docno")
    await db.execute(query, {"docno": docno, "doctitle": doctitle, "docconts": docconts})
    await db.commit()
    return RedirectResponse(f"/yk55greet", status_code=303)


@app.get("/yk55cabhist", response_class=HTMLResponse)
async def yk55cabhist(request: Request):
    if not request.session.get("user_No"):
        return RedirectResponse(url="login/login.html", status_code=303)
    else:
        return templates.TemplateResponse("yk55/yk55_cabhist.html", {"request": request})


@app.get("/yk55servhist", response_class=HTMLResponse)
async def yk55servhist(request: Request):
    if not request.session.get("user_No"):
        return RedirectResponse(url="login/login.html", status_code=303)
    else:
        return templates.TemplateResponse("yk55/yk55_servhist.html", {"request": request})


@app.get("/yk55membhist", response_class=HTMLResponse)
async def yk55membhist(request: Request):
    if not request.session.get("user_No"):
        return RedirectResponse(url="login/login.html", status_code=303)
    else:
        return templates.TemplateResponse("yk55/yk55_memberhist.html", {"request": request})


@app.get("/yk55mjfhist", response_class=HTMLResponse)
async def yk55mjfhist(request: Request):
    if not request.session.get("user_No"):
        return RedirectResponse(url="login/login.html", status_code=303)
    else:
        return templates.TemplateResponse("yk55/yk55_mjfhist.html", {"request": request})