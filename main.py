from urllib import request
import uvicorn
from fastapi import FastAPI, Depends, Request, Form, Response, HTTPException, File, UploadFile, Body
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pycparser.ply.yacc import resultlimit
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


@app.get("/", response_class=HTMLResponse)
async def login_form(request: Request):
    if request.session.get("user_No"):
        return RedirectResponse(url="/success", status_code=303)
    return templates.TemplateResponse("login/login.html", {"request": request})

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
    # 서버 세션에 사용자 ID 저장
    request.session["user_No"] = user[0]
    request.session["user_Name"] = user[1]
    request.session["user_Role"] = user[2]
    request.session["user_Region"] = user[3]
    request.session["user_Clubno"] = user[4]
    return RedirectResponse(url="/success", status_code=303)

# 로그인 성공 페이지
@app.get("/success", response_class=HTMLResponse)
async def success_page(request: Request):
    user_No = request.session.get("user_No")
    user_Name = request.session.get("user_Name")
    user_Role = request.session.get("user_Role")
    user_region = request.session.get("user_Region")
    user_clubno = request.session.get("user_Clubno")
    if not user_No:
        return RedirectResponse(url="/")
    return templates.TemplateResponse("main/basic.html",
                                      {"request": request, "user_No": user_No, "user_Name": user_Name,
                                       "user_Role": user_Role, "user_region": user_region, "user_clubno": user_clubno})


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
async def logout(request: Request):
    request.session.clear()  # 세션 삭제
    return RedirectResponse(url="/")


# User

# Member

# Basic Data
