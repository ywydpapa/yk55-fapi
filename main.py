import os
import calendar
import re
import secrets
import html
import json
import zipfile
from io import BytesIO
from datetime import date
from pathlib import Path
from html.parser import HTMLParser
from PIL import Image, UnidentifiedImageError
from urllib.parse import urlsplit
import dotenv
from fastapi import Depends, FastAPI, Request, Query, Form, Response, HTTPException, File, UploadFile, Body
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, FileResponse
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from starlette.middleware.sessions import SessionMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import bindparam, text
import webhard
from event_import import (
    EVENT_IMPORT_COLUMNS,
    MAX_EVENT_IMPORT_ROWS,
    EventImportError,
    event_duplicate_key,
    parse_event_workbook,
    validate_event_rows,
)

CABINET_ROLE_TITLES = {
    "지구총재": "지구총재",
    "사무총장": "사무총장",
    "재무총장": "재무총장",
    "기획총장": "기획총장",
    "수석부총장": "수석부총장",
    "사무부총장": "사무부총장",
    "재무부총장": "재무부총장",
    "지구1부총재": "지구 제1부총재",
    "지구2부총재": "지구 제2부총재",
}
CABINET_PREVIOUS_GOVERNOR_TITLE = "직전지구총재"
CABINET_EDIT_ROLE_TITLES = {
    **CABINET_ROLE_TITLES,
    CABINET_PREVIOUS_GOVERNOR_TITLE: CABINET_PREVIOUS_GOVERNOR_TITLE,
}

import funchub
# ✅ funchub.py에서 분리한 모든 함수와 상수를 가져옵니다.
from funchub import *
from funchub import _clean_int, _clean_str

dotenv.load_dotenv()
DATABASE_URL = os.getenv("dburl")
current_period = os.getenv("cperiod")

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
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

templates = Jinja2Templates(directory="templates")
templates.env.filters["currency"] = currency

app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/thumbnails", StaticFiles(directory="static/img/members/"), name="thumbnails")
BASE_DIR = Path(__file__).resolve().parent
NOTICE_UPLOAD_DIR = BASE_DIR / "uploads" / "notices"
NOTICE_IMAGE_DIR = NOTICE_UPLOAD_DIR / "images"
NOTICE_ATTACHMENT_DIR = NOTICE_UPLOAD_DIR / "attachments"
NOTICE_MAX_ATTACHMENTS = 10
NOTICE_MAX_IMAGE_SIZE = 10 * 1024 * 1024
NOTICE_ALLOWED_ATTACHMENT_EXTENSIONS = {".pdf", ".hwp", ".hwpx"}

def sanitize_notice_html(content: str) -> str:
    allowed_tags = {
        "a", "blockquote", "br", "code", "em", "h1", "h2", "h3", "img",
        "li", "ol", "p", "pre", "s", "span", "strong", "u", "ul",
    }
    void_tags = {"br", "img"}
    blocked_tags = {"iframe", "math", "noscript", "object", "script", "style", "svg", "template"}
    allowed_styles = {
        "background-color", "color", "font-family", "font-size", "font-style",
        "font-weight", "text-align", "text-decoration",
    }

    def safe_style(value: str) -> str:
        declarations = []
        for declaration in value.split(";"):
            if ":" not in declaration:
                continue
            name, raw_value = (part.strip().lower() for part in declaration.split(":", 1))
            if name not in allowed_styles or not re.fullmatch(r"[\w\s#(),.%\"'-]+", raw_value):
                continue
            if re.search(r"url|expression|var\s*\(", raw_value, re.IGNORECASE):
                continue
            if name in {"color", "background-color"} and not re.fullmatch(
                r"(?:#[0-9a-f]{3,8}|[a-z]{3,20}|rgba?\([\d\s.,%]+\))", raw_value
            ):
                continue
            if name == "text-align" and raw_value not in {"left", "center", "right", "justify"}:
                continue
            if name == "font-style" and raw_value not in {"normal", "italic"}:
                continue
            if name == "font-weight" and raw_value not in {"normal", "bold"} | {str(n) for n in range(100, 1000, 100)}:
                continue
            declarations.append(f"{name}: {raw_value}")
        return "; ".join(declarations)

    class NoticeHTMLSanitizer(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self.output: list[str] = []
            self.stack: list[str] = []
            self.blocked_stack: list[str] = []

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            tag = tag.lower()
            if self.blocked_stack:
                if tag in blocked_tags:
                    self.blocked_stack.append(tag)
                return
            if tag in blocked_tags:
                self.blocked_stack.append(tag)
                return
            if tag not in allowed_tags:
                return

            safe_attrs = []
            attr_map = dict(attrs)
            if tag == "a":
                href = attr_map.get("href") or ""
                try:
                    parsed = urlsplit(href)
                except ValueError:
                    parsed = None
                if parsed and parsed.scheme.lower() in {"http", "https", "mailto"} and not any(
                    ord(char) < 32 or char == "\\" for char in href
                ):
                    safe_attrs.append(("href", href))
                if attr_map.get("title"):
                    safe_attrs.append(("title", attr_map["title"]))
                if any(name == "href" for name, _ in safe_attrs):
                    safe_attrs.append(("rel", "nofollow noopener noreferrer"))
            elif tag == "img":
                src = attr_map.get("src") or ""
                if not re.fullmatch(r"/notice-media/[a-f0-9]{32}\.png", src):
                    return
                safe_attrs.append(("src", src))
                for name in ("alt", "title"):
                    if attr_map.get(name) is not None:
                        safe_attrs.append((name, attr_map[name] or ""))
                for name in ("height", "width"):
                    value = attr_map.get(name) or ""
                    if value.isdigit() and len(value) <= 4 and int(value) <= 2400:
                        safe_attrs.append((name, value))
            else:
                if tag == "li" and attr_map.get("data-list") in {"bullet", "ordered"}:
                    safe_attrs.append(("data-list", attr_map["data-list"]))
                classes = (attr_map.get("class") or "").split()
                if classes and all(
                    re.fullmatch(r"ql-(?:align-(?:center|right|justify)|indent-[1-8])", item)
                    for item in classes
                ):
                    safe_attrs.append(("class", " ".join(classes)))
                style = safe_style(attr_map.get("style") or "")
                if style:
                    safe_attrs.append(("style", style))

            attributes = "".join(
                f' {name}="{html.escape(value, quote=True)}"'
                for name, value in safe_attrs
                if value is not None
            )
            self.output.append(f"<{tag}{attributes}>")
            if tag not in void_tags:
                self.stack.append(tag)

        def handle_endtag(self, tag: str) -> None:
            tag = tag.lower()
            if self.blocked_stack:
                if tag == self.blocked_stack[-1]:
                    self.blocked_stack.pop()
                return
            if tag in self.stack:
                while self.stack:
                    open_tag = self.stack.pop()
                    self.output.append(f"</{open_tag}>")
                    if open_tag == tag:
                        break

        def handle_data(self, data: str) -> None:
            if not self.blocked_stack:
                self.output.append(html.escape(data))

    sanitizer = NoticeHTMLSanitizer()
    sanitizer.feed(content)
    while sanitizer.stack:
        sanitizer.output.append(f"</{sanitizer.stack.pop()}>")
    return "".join(sanitizer.output)

def notice_has_invalid_image_source(content: str) -> bool:
    class ImageSourceValidator(HTMLParser):
        invalid = False

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            if tag.lower() != "img":
                return
            src = dict(attrs).get("src") or ""
            if not re.fullmatch(r"/notice-media/[a-f0-9]{32}\.png", src):
                self.invalid = True

    validator = ImageSourceValidator()
    validator.feed(content)
    return validator.invalid

# 데이터베이스 세션 생성
async def get_db():
    async with async_session() as session:
        yield session

async def require_admin(request: Request, user_no: int = Depends(get_current_user)) -> int:
    if request.session.get("user_Role") != "ADMIN":
        raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다.")
    return user_no

# ==========================================
# 1. 파일 업로드 라우터
# ==========================================
@app.post("/uploadmphoto/{memberno}", dependencies=[Depends(get_current_user)])
async def upload_memberimage(request: Request, memberno: int, file: UploadFile = File(...), db: AsyncSession = Depends(get_db)):
    try:
        if not file.content_type.startswith('image/'):
            raise HTTPException(status_code=400, detail="File type not supported.")
        contents = await safe_file_read(file)
        contents = await resize_image_if_needed(contents, max_bytes=102400)
        await save_memberPhoto(contents, memberno)
        await save_thumbnail(contents, memberno, size=(100, 100))
        return RedirectResponse(f"/member_edit/{memberno}", status_code=303)
    except Exception as e:
        print(f"Error: {e}")
        return RedirectResponse(f"/member_edit/{memberno}", status_code=303)


@app.post("/api/upload_photo/{memberno}", dependencies=[Depends(get_current_user)])
async def upload_memberimage(
        request: Request,
        memberno: int,
        photoFile: UploadFile = File(...),
        periodno: int = Form(...),  # 프런트에서 넘어온 기수 정보
        returnUrl: str = Form("/yk55cabhist"),  # 프런트에서 넘어온 돌아갈 주소
        db: AsyncSession = Depends(get_db)
):
    try:
        if not photoFile.content_type.startswith('image/'):
            raise HTTPException(status_code=400, detail="File type not supported.")
        contents = await safe_file_read(photoFile)
        contents = await resize_image_if_needed(contents, max_bytes=102400)
        file_path = f"static/img/members/mphoto_{memberno}_h{periodno}.png"
        with open(file_path, "wb") as f:
            f.write(contents)
        return RedirectResponse(url=returnUrl, status_code=303)
    except Exception as e:
        print(f"Error: {e}")
        return RedirectResponse(url=returnUrl, status_code=303)

@app.post("/uploaddocmphoto/{docno}", dependencies=[Depends(get_current_user)])
async def upload_docimage(request: Request, docno: int, file: UploadFile = File(...), db: AsyncSession = Depends(get_db)):
    try:
        if not file.content_type.startswith('image/'):
            raise HTTPException(status_code=400, detail="File type not supported.")
        contents = await safe_file_read(file)
        contents = await resize_image_if_needed(contents, max_bytes=102400)
        await save_docPhoto(contents, docno)
        return RedirectResponse(f"/yk55greet_edit/{docno}", status_code=303)
    except Exception as e:
        print(f"Error: {e}")
        return RedirectResponse(f"/yk55greet_edit/{docno}", status_code=303)

@app.post("/uploadcephoto/{eventno}", dependencies=[Depends(get_current_user)])
async def upload_eventimage(request: Request, eventno: int, file: UploadFile = File(...), db: AsyncSession = Depends(get_db)):
    try:
        if not file.content_type.startswith('image/'):
            raise HTTPException(status_code=400, detail="File type not supported.")
        contents = await safe_file_read(file)
        contents = await resize_image_if_needed(contents, max_bytes=102400)
        await save_eventPhoto(contents, eventno)
        return RedirectResponse(f"/event_edit/{eventno}", status_code=303)
    except Exception as e:
        print(f"Error: {e}")
        return RedirectResponse(f"/event_edit/{eventno}", status_code=303)

@app.post("/uploadcmphoto/{memberno}", dependencies=[Depends(get_current_user)])
async def upload_cmimage(request: Request, memberno: int, file: UploadFile = File(...), db: AsyncSession = Depends(get_db)):
    try:
        if not file.content_type.startswith('image/'):
            raise HTTPException(status_code=400, detail="File type not supported.")
        contents = await safe_file_read(file)
        contents = await resize_image_if_needed(contents, max_bytes=102400)
        await save_memberPhoto(contents, memberno)
        await save_thumbnail(contents, memberno, size=(100, 100))
        return RedirectResponse(f"/cmember_edit/{memberno}", status_code=303)
    except Exception as e:
        print(f"Error: {e}")
        return RedirectResponse(f"/cmember_edit/{memberno}", status_code=303)

# ==========================================
# 2. 인증 및 메인 라우터
# ==========================================
@app.get("/", response_class=HTMLResponse)
async def login_form(request: Request):
    if not request.session.get("user_No"):
        return templates.TemplateResponse("/login/login.html", {"request": request})
    else:
        return RedirectResponse(url="/mainpage", status_code=303)

@app.post("/login")
async def login_post(request: Request, response: Response, username: str = Form(...), password: str = Form(...), db: AsyncSession = Depends(get_db)):
    query = text("SELECT userNo, userName, userRole, defaultRegion, defaultClub, userPassword FROM yk_user WHERE userId = :username")
    result = await db.execute(query, {"username": username})
    user = result.fetchone()
    if user is None or not verify_password(password, user[5]):
        return templates.TemplateResponse("login/login.html", {"request": request, "error": "Invalid credentials"})
    otp = await generate_otp()
    request.session["user_No"] = user[0]
    request.session["user_Name"] = user[1]
    request.session["user_Role"] = user[2]
    request.session["user_Region"] = user[3]
    request.session["user_Clubno"] = user[4]
    request.session["otp"] = otp
    request.session["cperiod"] = current_period
    return RedirectResponse(url="/success", status_code=303)

@app.get("/success", response_class=HTMLResponse)
async def success_page(request: Request, db: AsyncSession = Depends(get_db)):
    global club_count, devent, cevent
    user_No = request.session.get("user_No")
    user_Role = request.session.get("user_Role")
    user_clubno = request.session.get("user_Clubno")
    otp = request.session.get("otp")
    otpstat = await reg_otp(otp, user_No, db)
    msg = "" if otpstat else "OTP 등록 실패"

    if not user_No:
        return RedirectResponse(url="/")

    club_count = 0
    devent = []
    cevent = []

    if user_Role == 'CUSER':
        clubmember = await get_clubmember(user_clubno, db)
        member_count = sum(1 for m in clubmember if m.get("memberStatus") == "ACTIV")
    else:
        member_count = await get_distmember(db)
        club_count = await get_clubcount(db)
        devent = await get_disteventsthismonth(db)
        cevent = await get_allclubeventsthismonth(current_period, db)

    template_name = "main/indexc.html" if user_Role == "CUSER" else "main/index.html"
    return templates.TemplateResponse(
        template_name,
        {
            "request": request,
            "session": dict(request.session),
            "message": msg,
            "membercnt": member_count,
            "clubcnt": club_count,
            "devents": devent,
            "cperiod": current_period,
            "cevents": cevent,
        },
    )

@app.get("/mainpage", response_class=HTMLResponse)
async def main_page(request: Request, db: AsyncSession = Depends(get_db)):
    user_No = request.session.get("user_No")
    user_Role = request.session.get("user_Role")
    user_clubno = request.session.get("user_Clubno")
    if not user_No:
        return RedirectResponse(url="/")
    clubmember = await get_clubmember(user_clubno, db)
    member_count = sum(1 for m in clubmember if m.get("memberStatus") == "ACTIV")
    template_name = "main/indexc.html" if user_Role == "CUSER" else "main/index.html"
    return templates.TemplateResponse(template_name, {"request": request, "session": dict(request.session), "message": "", "membercnt": member_count})


@app.post("/changeuserpass")
async def change_password(
        data: dict = Body(...),
        db: AsyncSession = Depends(get_db),
        current_user_no: int = Depends(get_current_user)
):
    hashed_password = get_password_hash(data["passwd"])
    sql = text("UPDATE yk_user SET userPassword = :passwd WHERE userNo = :userno")
    await db.execute(sql, {"passwd": hashed_password, "userno": current_user_no})
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
        request.session.clear()
        return RedirectResponse(url="/")
    except Exception:
        return RedirectResponse(url="/", status_code=303)

# ==========================================
# 3. 리포트 라우터
# ==========================================
@app.api_route("/report_event/{clubno}", response_class=HTMLResponse, methods=["GET", "POST"])
async def reportevent(request: Request, clubno: int, db: AsyncSession = Depends(get_db), user_no: int = Depends(get_current_user)):
    ranklist = await get_rank(db)
    periodlist = await getperiod(db)
    memberlist = await get_clubmember(clubno, db)
    eventlist = await get_event_dist_club(clubno, db)
    return templates.TemplateResponse("report/reportevent.html", {"request": request, "session": dict(request.session), "memberlist": memberlist, "ranklist": ranklist, "periodlist": periodlist, "eventlist": eventlist, "periodno": current_period})

@app.api_route("/report_eventedit/{eventno}", response_class=HTMLResponse, methods=["GET", "POST"])
async def reporteventedit(request: Request, eventno: int, db: AsyncSession = Depends(get_db), user_no: int = Depends(get_current_user)):
    clubno = request.session.get("user_Clubno")
    ranklist = await get_rank(db)
    periodlist = await getperiod(db)
    memberlist = await get_clubmember(clubno, db)
    eventdtl = await get_eventdtl(eventno, db)
    joinmember = await get_eventmembers(eventno, db)
    return templates.TemplateResponse("report/reporteventedit.html", {"request": request, "session": dict(request.session), "memberlist": memberlist, "ranklist": ranklist, "periodlist": periodlist, "joinmember": joinmember, "eventdtl": eventdtl})

@app.get("/report_lists_clubevent", response_class=HTMLResponse)
async def reportlistsevnt(request: Request, db: AsyncSession = Depends(get_db), user_no: int = Depends(get_current_user)):
    periodlist = await getperiod(db)
    eventreport = await get_event_reports(db)
    return templates.TemplateResponse("report/reportlist_cevent.html", {"request": request, "session": dict(request.session), "periodlist": periodlist, "eventreports": eventreport, "periodno": current_period})

@app.get("/report_lists_clubmember", response_class=HTMLResponse)
async def reportlistsmem(request: Request, db: AsyncSession = Depends(get_db), user_no: int = Depends(get_current_user)):
    periodlist = await getperiod(db)
    eventreport = await get_event_reports(db)
    return templates.TemplateResponse("report/reportlist_cevent.html", {"request": request, "session": dict(request.session), "periodlist": periodlist, "eventreports": eventreport, "periodno": current_period})

@app.get("/report_eventlist/{clubno}", response_class=HTMLResponse)
async def reporteventlist(request: Request, clubno: int, db: AsyncSession = Depends(get_db), user_no: int = Depends(get_current_user)):
    periodlist = await getperiod(db)
    eventlist = await get_event_dist_club(clubno, db)
    return templates.TemplateResponse("report/reporteventlist.html", {"request": request, "session": dict(request.session), "periodlist": periodlist, "eventlist": eventlist, "periodno": current_period})

@app.get("/report_memberlist/{clubno}", response_class=HTMLResponse)
async def reportmemberlist(request: Request, clubno: int, db: AsyncSession = Depends(get_db), user_no: int = Depends(get_current_user)):
    periodlist = await getperiod(db)
    reportlist = await get_memberreports(clubno, db)
    return templates.TemplateResponse("report/reportmemberlist.html", {"request": request, "session": dict(request.session), "periodlist": periodlist, "reportlist": reportlist})

@app.api_route("/report_member/{clubno}", response_class=HTMLResponse, methods=["GET", "POST"])
async def reportmember(request: Request, clubno: int, db: AsyncSession = Depends(get_db), user_no: int = Depends(get_current_user)):
    memberlist = await get_clubmember(clubno, db)
    periodlist = await getperiod(db)
    return templates.TemplateResponse("report/reportmember.html", {"request": request, "session": dict(request.session), "memberlist": memberlist, "periodlist": periodlist})

@app.post("/insert_clubmember_report")
async def insert_clubmember_report(request: Request, db: AsyncSession = Depends(get_db), user_no: int = Depends(get_current_user)):
    clubno = request.session.get("user_Clubno")
    form = await request.form()
    period_no = to_int(form.get("period"))
    period_month = to_int(form.get("periodmonth"))
    status_type = (form.get("status") or "").strip().upper()
    member_nos = [to_int(x) for x in form.getlist("memberNo") if to_int(x) > 0]
    if period_no <= 0 or period_month <= 0 or status_type == "" or len(member_nos) == 0:
        return RedirectResponse(url=f"/report_memberlist/{clubno}", status_code=303)
    year = to_int(form.get("year"), date.today().year)
    status_from = date(year, period_month, 1) if status_type == "JOIN" else date(year, period_month, calendar.monthrange(year, period_month)[1])
    insert_sql = text("INSERT INTO yk_memberStatus (clubNo, memberNo, statusFrom, statusTo, statusType, periodNo, periodMonth) VALUES (:clubNo, :memberNo, :statusFrom, :statusTo, :statusType, :periodNo, :periodMonth)")
    try:
        for m_no in member_nos:
            await db.execute(insert_sql, {"clubNo": clubno, "memberNo": m_no, "statusFrom": status_from, "statusTo": None, "statusType": status_type, "periodNo": period_no, "periodMonth": period_month})
            await db.commit()
    except Exception:
        await db.rollback()
        raise
    return RedirectResponse(url=f"/report_memberlist/{clubno}", status_code=303)

@app.post("/insert_clubevent/")
async def save_clubevent(request: Request, db: AsyncSession = Depends(get_db), user_no: int = Depends(get_current_user)):
    clubno = request.session.get("user_Clubno")
    form = await request.form()
    event_no = to_int(form.get("event"))
    member_nos = [to_int(x) for x in form.getlist("memberNo") if to_int(x) > 0]
    if not event_no or len(member_nos) == 0:
        return RedirectResponse(url=f"/report_eventlist/{clubno}", status_code=303)
    support_map = {to_int(k[len("supportAmount["):-1], 0): to_int(str(v).replace(",", "").strip(), 0) for k, v in form.items() if k.startswith("supportAmount[") and k.endswith("]") and to_int(k[len("supportAmount["):-1], 0) > 0}
    await db.execute(text("UPDATE yk_eventMember SET attrib = :xxxup WHERE eventNo = :eventNo"), {"xxxup": "XXXUPXXXUP", "eventNo": event_no})
    await db.commit()
    for m_no in member_nos:
        await db.execute(text("INSERT INTO yk_eventMember (eventNo, memberNo, supportAmt) VALUES (:eventNo, :memberNo, :supportAmt)"), {"eventNo": event_no, "memberNo": m_no, "supportAmt": support_map.get(m_no, 0)})
        await db.commit()
    return RedirectResponse(url=f"/report_eventlist/{clubno}", status_code=303)

# ==========================================
# 4. 클럽 및 회원 관리 라우터
# ==========================================
@app.get("/club_memberlist/{clubno}", response_class=HTMLResponse)
async def club_memberlist(request: Request, clubno: int, db: AsyncSession = Depends(get_db), user_no: int = Depends(get_current_user)):
    memberlist = await get_clubmember(clubno, db)
    return templates.TemplateResponse("club/cmemberlist.html", {"request": request, "session": dict(request.session), "memberlist": memberlist})

@app.get("/cmember_edit/{memberno}", response_class=HTMLResponse)
async def cmemberedit(request: Request, memberno: int, db: AsyncSession = Depends(get_db), user_no: int = Depends(get_current_user)):
    clubno = request.session.get("user_Clubno")
    return templates.TemplateResponse("club/cmemberedit.html", {"request": request, "clubs": await get_club(db), "session": dict(request.session), "memberdtl": await get_member_dtl(memberno, db), "spons": await get_clubsponser(clubno, db), "dstaffhist": await get_diststaffmem(clubno, memberno, db), "cstaffhist": await get_clubstaffhist(memberno, db), "catlist": await getcategory("MIDTL", db), "member_detail_list": await get_member_detail_list(memberno, db), "member_prize_list": await get_member_prize_list(memberno, db), "prizelist": await getprizelist(db)})

@app.post("/insert_MIDTL/{memberno}/")
async def insert_midt_detail(request: Request, memberno: int, db: AsyncSession = Depends(get_db)):
    is_ajax = request.headers.get("x-requested-with") == "XMLHttpRequest"
    if not request.session.get("user_No"):
        return JSONResponse({"ok": False, "message": "login required"}, status_code=401) if is_ajax else RedirectResponse(url="/", status_code=303)
    form = await request.form()
    cat_no, detail_info = to_int(form.get("dtlcat"), 0), (form.get("dtlcont") or "").strip()
    if cat_no <= 0 or detail_info == "":
        return JSONResponse({"ok": False, "message": "invalid input"}, status_code=400)
    try:
        async with db.begin():
            await db.execute(text("UPDATE yk_memberDetailinfo SET attrib = :xup, modDate = NOW() WHERE memberNo = :mno AND catNo = :cno AND attrib = :xapp"), {"xup": "XXXUPXXXUP", "mno": memberno, "cno": cat_no, "xapp": "1000010000"})
            result = await db.execute(text("INSERT INTO yk_memberDetailinfo (memberNo, catNo, detailInfo, attrib, regDate) VALUES (:mno, :cno, :info, :xapp, NOW())"), {"mno": memberno, "cno": cat_no, "info": detail_info, "xapp": "1000010000"})
            row = (await db.execute(text("SELECT d.infoNo as id, d.memberNo, d.catNo, c.catTitle, d.detailInfo, DATE_FORMAT(d.regDate, '%Y-%m-%d') AS regDate FROM yk_memberDetailinfo d JOIN yk_category c ON c.catNo = d.catNo WHERE d.infoNo = :id"), {"id": result.lastrowid})).mappings().first()
        return JSONResponse({"ok": True, "row": dict(row) if row else None}) if is_ajax else RedirectResponse(url=request.headers.get("referer", "/"), status_code=303)
    except Exception as e:
        return JSONResponse({"ok": False, "message": str(e)}, status_code=500)

@app.post("/insert_PRIZE/{memberno}/")
async def insert_member_prize(request: Request, memberno: int, db: AsyncSession = Depends(get_db)):
    if not request.session.get("user_No"):
        return JSONResponse({"ok": False, "message": "login required"}, status_code=401) if request.headers.get("x-requested-with") == "XMLHttpRequest" else RedirectResponse(url="/", status_code=303)
    form = await request.form()
    prize_no, prize_info, prize_date = to_int(form.get("prizecat"), 0), (form.get("prizecont") or "").strip(), form.get("prizedate")
    if prize_no <= 0 or prize_info == "":
        return JSONResponse({"ok": False, "message": "invalid input"}, status_code=400)
    try:
        async with db.begin():
            await db.execute(text("UPDATE yk_memberPrize SET attrib = :xup, modDate = NOW() WHERE memberNo = :mno AND prizeNo = :cno AND attrib = :xapp AND prizeDate = :prizedate"), {"xup": "XXXUPXXXUP", "mno": memberno, "cno": prize_no, "xapp": "1000010000", "prizedate": prize_date})
            result = await db.execute(text("INSERT INTO yk_memberPrize (memberNo, prizeNo, prizeMemo, prizeDate) VALUES (:mno, :pno, :memo, :pdate)"), {"mno": memberno, "pno": prize_no, "memo": prize_info, "pdate": prize_date})
            row = (await db.execute(text("SELECT d.mpNo as id, d.memberNo, d.prizeNo, c.prizeTitle, d.prizeMemo, DATE_FORMAT(d.prizeDate, '%Y-%m-%d') AS prizeDate FROM yk_memberPrize d JOIN yk_prize c ON c.prizeNo = d.prizeNo WHERE d.mpNo = :id"), {"id": result.lastrowid})).mappings().first()
        return JSONResponse({"ok": True, "row": dict(row) if row else None})
    except Exception as e:
        return JSONResponse({"ok": False, "message": str(e)}, status_code=500)

@app.get("/api/member/{memberno}/midtl")
async def api_member_midt_list(memberno: int, request: Request, db: AsyncSession = Depends(get_db), user_no: int = Depends(get_current_user)):
    result = await db.execute(text("SELECT d.infoNo as id, d.catNo, c.catTitle, d.detailInfo, DATE_FORMAT(d.regDate, '%Y-%m-%d') AS regDate FROM yk_memberDetailinfo d JOIN yk_category c ON c.catNo = d.catNo WHERE d.memberNo = :mno AND d.attrib = :xapp ORDER BY d.catNo ASC"), {"mno": memberno, "xapp": "1000010000"})
    return {"ok": True, "rows": [dict(r._mapping) for r in result.fetchall()]}

@app.get("/api/member/{memberno}/prize")
async def api_member_prize_list(memberno: int, request: Request, db: AsyncSession = Depends(get_db), user_no: int = Depends(get_current_user)):
    result = await db.execute(text("SELECT d.mpNo as id, d.prizeNo, c.prizeTitle, d.prizeMemo, DATE_FORMAT(d.prizeDate, '%Y-%m-%d') AS prizeDate FROM yk_memberPrize d JOIN yk_prize c ON c.prizeNo = d.prizeNo WHERE d.memberNo = :mno AND d.attrib = :xapp ORDER BY d.prizeDate ASC"), {"mno": memberno, "xapp": "1000010000"})
    return {"ok": True, "rows": [dict(r._mapping) for r in result.fetchall()]}

@app.get("/dist_stafflist/{clubno}", response_class=HTMLResponse)
async def dist_stafflist(request: Request, clubno: int, periodno: int | None = Query(None), db: AsyncSession = Depends(get_db), user_no: int = Depends(get_current_user)):
    return templates.TemplateResponse("club/dstafflist.html", {"request": request, "session": dict(request.session), "memberlist": await get_clubsponser(clubno, db), "stafflist": await get_diststaff(clubno, db), "periods": await getperiod(db), "ranklist": await get_rank(db), "periodno": periodno if periodno is not None else current_period})

@app.get("/club_stafflist/{clubno}", response_class=HTMLResponse)
async def club_stafflist(request: Request, clubno: int, db: AsyncSession = Depends(get_db), user_no: int = Depends(get_current_user)):
    return templates.TemplateResponse("club/cstafflist.html", {"request": request, "session": dict(request.session), "memberlist": await get_clubsponser(clubno, db), "stafflist": await get_clubstaff(clubno, db), "periods": await getperiod(db), "periodno": current_period})

@app.post("/club_staffupdate", response_class=HTMLResponse)
async def updatecstaff(request: Request, db: AsyncSession = Depends(get_db), user_no: int = Depends(get_current_user)):
    form_data = await request.form()
    clubno, periodno = request.session.get("user_Clubno"), _clean_int(form_data.get("speriod"))
    if clubno is None or periodno is None:
        return RedirectResponse("/club_stafflist", status_code=303)
    data = {"clubNo": clubno, "periodNo": periodno, "chairmanNo": _clean_int(form_data.get("staff1")), "vice1stNo": _clean_int(form_data.get("staff2")), "vice2ndNo": _clean_int(form_data.get("staff3")), "vice3rdNo": _clean_int(form_data.get("staff4")), "secretaryNo": _clean_int(form_data.get("staff5")), "treasureNo": _clean_int(form_data.get("staff6")), "lionsteamerNo": _clean_int(form_data.get("staff7")), "tailtNo": _clean_int(form_data.get("staff8")), "slogan": _clean_str(form_data.get("slogan"))}
    update_keys = [k for k in data.keys() if k not in ("clubNo", "periodNo") and data[k] is not None]
    if update_keys:
        await db.execute(text(f"INSERT INTO yk_clubStaff ({', '.join(data.keys())}) VALUES ({', '.join([':'+k for k in data.keys()])}) ON DUPLICATE KEY UPDATE {', '.join([f'{k} = VALUES({k})' for k in update_keys])}"), data)
        await db.commit()
    return RedirectResponse(f"/club_stafflist/{clubno}", status_code=303)

@app.post("/dist_staffupdate", response_class=HTMLResponse)
async def updatedstaff(request: Request, db: AsyncSession = Depends(get_db), user_no: int = Depends(get_current_user)):
    form_data = await request.form()
    clubno, periodno = request.session.get("user_Clubno"), _clean_int(form_data.get("speriod"))
    if clubno is None or periodno is None:
        return RedirectResponse(f"/dist_stafflist/{clubno}?periodno={periodno}", status_code=303)
    data = {"clubNo": clubno, "periodNo": periodno, "rankNo": _clean_int(form_data.get("drank")), "memberNo": _clean_int(form_data.get("dstaff"))}
    update_keys = [k for k in data.keys() if k not in ("clubNo", "periodNo") and data[k] is not None]
    if update_keys:
        await db.execute(text(f"INSERT INTO yk_distStaff ({', '.join(data.keys())}) VALUES ({', '.join([':'+k for k in data.keys()])}) ON DUPLICATE KEY UPDATE {', '.join([f'{k} = VALUES({k})' for k in update_keys])}"), data)
        await db.commit()
    return RedirectResponse(f"/dist_stafflist/{clubno}?periodno={periodno}", status_code=303)

# ==========================================
# 5. 이벤트(행사) 라우터
# ==========================================
@app.get("/club_eventlist/{clubno}", response_class=HTMLResponse)
async def ceventlist(request: Request, clubno: int, db: AsyncSession = Depends(get_db), user_no: int = Depends(get_current_user)):
    locations = await get_locations(db)
    return templates.TemplateResponse("club/club_eventlist.html", {"request": request, "session": dict(request.session), "periodlist": await getperiod(db), "ceventlist": await get_clubeventsperiod(clubno,int(current_period) ,db),"deventlist": await get_disteventsperiod(int(current_period),db), "periodno": current_period,  "locationlist": locations})

@app.get("/dist_eventlist", response_class=HTMLResponse)
async def deventlist(request: Request, db: AsyncSession = Depends(get_db), user_no: int = Depends(get_current_user)):
    locations = await get_locations(db)
    return templates.TemplateResponse("dist/dist_eventlist.html", {"request": request, "session": dict(request.session), "periodlist": await getperiod(db), "deventlist": await get_disteventsperiod(int(current_period),db),"ceventlist":await get_allclubeventsperiod(int(current_period),db) , "periodno": current_period, "locationlist": locations})

@app.get("/dist_eventlist/{periodno}", response_class=HTMLResponse)
async def deventlist_period(request: Request, periodno: int, db: AsyncSession = Depends(get_db), user_no: int = Depends(get_current_user)):
    locations = await get_locations(db)
    return templates.TemplateResponse("dist/dist_eventlist.html", {"request": request, "session": dict(request.session), "periodlist": await getperiod(db), "deventlist": await get_disteventsperiod(periodno,db),"ceventlist":await get_allclubeventsperiod(int(current_period),db) , "periodno": periodno, "locationlist": locations})

@app.get("/club_eventlist/{clubno}/{periodno}", response_class=HTMLResponse)
async def ceventlist_period(request: Request, clubno: int, periodno: int, db: AsyncSession = Depends(get_db), user_no: int = Depends(get_current_user)):
    locations = await get_locations(db)
    return templates.TemplateResponse("club/club_eventlist.html", {"request": request, "session": dict(request.session), "periodlist": await getperiod(db), "ceventlist": await get_clubeventsperiod(clubno, periodno, db),"deventlist": await get_disteventsperiod(periodno,db), "periodno": periodno, "locationlist": locations})

@app.post("/club_eventnew/{clubno}", response_class=HTMLResponse)
async def ceventnew(request: Request, clubno: int, db: AsyncSession = Depends(get_db), user_no: int = Depends(get_current_user)):
    return templates.TemplateResponse("club/club_eventnew.html", {"request": request, "session": dict(request.session), "periodlist": await getperiod(db), "periodno": current_period})

@app.post("/dist_eventnew", response_class=HTMLResponse)
async def deventnew(request: Request, db: AsyncSession = Depends(get_db), user_no: int = Depends(get_current_user)):
    return templates.TemplateResponse("dist/dist_eventnew.html", {"request": request, "session": dict(request.session), "periodlist": await getperiod(db), "periodno": current_period})

@app.get("/club_eventedit/{eventno}", response_class=HTMLResponse)
async def ceventedit(request: Request, eventno: int, db: AsyncSession = Depends(get_db), user_no: int = Depends(get_current_user)):
    return templates.TemplateResponse("club/club_eventedit.html", {"request": request, "session": dict(request.session), "periodlist": await getperiod(db), "eventdtl": await get_eventdtl(eventno, db)})

@app.get("/dist_eventedit/{eventno}", response_class=HTMLResponse)
async def deventedit(request: Request, eventno: int, db: AsyncSession = Depends(get_db), user_no: int = Depends(get_current_user)):
    return templates.TemplateResponse("dist/dist_eventedit.html", {"request": request, "session": dict(request.session), "periodlist": await getperiod(db), "eventdtl": await get_eventdtl(eventno, db)})

async def _validate_district_event_rows(rows: list[dict], db: AsyncSession) -> list[dict]:
    periods_result = await db.execute(
        text("SELECT periodNo FROM yk_period WHERE attrib = :attrib"),
        {"attrib": "1000010000"},
    )
    valid_periods = {int(row[0]) for row in periods_result.fetchall()}
    locations_result = await db.execute(
        text("SELECT locationNo, locationTitle FROM yk_location WHERE attrib = :attrib"),
        {"attrib": "1000010000"},
    )
    location_ids_by_title = {
        str(row.locationTitle).strip().casefold(): int(row.locationNo)
        for row in locations_result.fetchall()
    }
    return validate_event_rows(rows, valid_periods, location_ids_by_title)


async def _district_event_duplicate_keys(
    rows: list[dict], db: AsyncSession
) -> set[tuple[str, str, int]]:
    period_numbers = sorted({int(row["periodNo"]) for row in rows})
    if not period_numbers:
        return set()
    query = text(
        "SELECT eventTitle, eventFrom, periodNo FROM yk_event "
        "WHERE attrib = :attrib AND eventType = 'DISTE' AND periodNo IN :periods"
    ).bindparams(bindparam("periods", expanding=True))
    result = await db.execute(query, {"attrib": "1000010000", "periods": period_numbers})
    existing = set()
    for row in result.mappings().all():
        try:
            existing.add(event_duplicate_key(dict(row)))
        except (KeyError, TypeError, ValueError):
            continue
    return existing


@app.get("/dist_event_import_template")
async def district_event_import_template(
    request: Request, user_no: int = Depends(get_current_user)
):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "행사"
    headers = [header for header, _ in EVENT_IMPORT_COLUMNS]
    worksheet.append(headers)
    worksheet.freeze_panes = "A2"
    worksheet.auto_filter.ref = f"A1:{chr(64 + len(headers))}1"
    for cell in worksheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="4E73DF")
    for column, width in zip("ABCDEFGH", (28, 16, 22, 22, 14, 28, 14, 14)):
        worksheet.column_dimensions[column].width = width

    guide = workbook.create_sheet("안내")
    guide.append(["열 이름", "작성 안내"])
    guide_rows = [
        ("행사명", "필수"),
        ("행사종류", "지구 행사에서는 DISTE 입력. 비우면 DISTE"),
        ("시작일시 / 종료일시", "필수. 엑셀 날짜·시간 또는 YYYY-MM-DD HH:MM"),
        ("회기번호", "필수. 회기 관리 화면의 회기번호 입력"),
        ("장소", "등록된 장소명 입력. 비우면 장소 미정"),
        ("소요비용 / 배치순서", "비우면 각각 10000 / 100으로 등록"),
        ("행 제한", f"한 번에 최대 {MAX_EVENT_IMPORT_ROWS}개 행사"),
    ]
    for row in guide_rows:
        guide.append(row)
    guide.column_dimensions["A"].width = 28
    guide.column_dimensions["B"].width = 70
    guide.freeze_panes = "A2"
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return Response(
        output.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="district_events_template.xlsx"'},
    )


@app.post("/dist_event_import_preview", response_class=HTMLResponse)
async def district_event_import_preview(
    request: Request,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user_no: int = Depends(get_current_user),
):
    if not file.filename or Path(file.filename).suffix.lower() != ".xlsx":
        raise HTTPException(status_code=400, detail=".xlsx 파일만 업로드할 수 있습니다.")
    contents = await file.read(5 * 1024 * 1024 + 1)
    if len(contents) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="파일 크기는 5MB 이하여야 합니다.")
    try:
        source_rows = parse_event_workbook(contents)
    except EventImportError as exc:
        return templates.TemplateResponse(
            "dist/eventimportpreview.html",
            {
                "request": request,
                "session": dict(request.session),
                "rows": [],
                "rows_json": "[]",
                "error_message": str(exc),
                "can_confirm": False,
            },
            status_code=400,
        )

    rows = await _validate_district_event_rows(source_rows, db)
    duplicate_keys = await _district_event_duplicate_keys(
        [row for row in rows if not row["error"]], db
    )
    seen = set(duplicate_keys)
    for row in rows:
        row["duplicate"] = False
        if row["error"]:
            continue
        key = event_duplicate_key(row)
        row["duplicate"] = key in seen
        seen.add(key)

    return templates.TemplateResponse(
        "dist/eventimportpreview.html",
        {
            "request": request,
            "session": dict(request.session),
            "rows": rows,
            "rows_json": json.dumps(rows, ensure_ascii=False),
            "error_message": "",
            "can_confirm": all(not row["error"] for row in rows),
        },
    )


@app.post("/dist_event_import_confirm")
async def district_event_import_confirm(
    request: Request,
    rows_json: str = Form(...),
    db: AsyncSession = Depends(get_db),
    user_no: int = Depends(get_current_user),
):
    try:
        submitted_rows = json.loads(rows_json)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="미리보기 데이터가 올바르지 않습니다.") from exc
    if not isinstance(submitted_rows, list) or not 1 <= len(submitted_rows) <= MAX_EVENT_IMPORT_ROWS:
        raise HTTPException(status_code=400, detail="등록할 행사 행 수가 올바르지 않습니다.")
    if any(not isinstance(row, dict) for row in submitted_rows):
        raise HTTPException(status_code=400, detail="행사 미리보기 데이터 형식이 올바르지 않습니다.")

    inserted = 0
    skipped = 0
    async with db.begin():
        rows = await _validate_district_event_rows(submitted_rows, db)
        if any(row["error"] for row in rows):
            raise HTTPException(status_code=400, detail="행사 정보를 다시 확인한 후 가져오기를 진행해 주세요.")
        duplicate_keys = await _district_event_duplicate_keys(rows, db)
        for row in rows:
            key = event_duplicate_key(row)
            if key in duplicate_keys:
                skipped += 1
                continue
            await db.execute(
                text(
                    "INSERT INTO yk_event "
                    "(eventTitle, eventType, eventFrom, eventTo, periodNo, eventCost, sortNo, locationNo) "
                    "VALUES (:eventTitle, :eventType, :eventFrom, :eventTo, :periodNo, :eventCost, :sortNo, :locationNo)"
                ),
                {
                    field: row[field]
                    for field in (
                        "eventTitle", "eventType", "eventFrom", "eventTo",
                        "periodNo", "eventCost", "sortNo", "locationNo",
                    )
                },
            )
            duplicate_keys.add(key)
            inserted += 1
    return RedirectResponse(
        f"/dist_eventlist?imported={inserted}&skipped={skipped}", status_code=303
    )

@app.post("/club_eventinsert/{clubno}")
async def save_devent(request: Request,clubno:int, db: AsyncSession = Depends(get_db), user_no: int = Depends(get_current_user)):
    form = await request.form()
    event_title = form.get("ceventtitle")
    event_type = form.get("eventtype")
    event_from = form.get("eventfrom")
    event_to = form.get("eventto")
    period_no = to_int(form.get("eventperiod"))
    event_cost = to_int(form.get("eventcost"))
    sort_no = to_int(form.get("sortno"))
    location_no_input = form.get("locationNo")
    custom_location = form.get("customLocation")
    if event_from:
        event_from = event_from.replace("T", " ")
    if event_to:
        event_to = event_to.replace("T", " ")
    final_location_no = 1  # 기본값 (장소미정)
    if location_no_input == "custom" and custom_location:
        result = await db.execute(
            text("INSERT INTO yk_location (locationTitle) VALUES (:title)"),
            {"title": custom_location}
        )
        await db.commit()
        final_location_no = result.lastrowid
    else:
        final_location_no = to_int(location_no_input)
    insert_query = """
                   INSERT INTO yk_event
                   (eventTitle, eventType, eventFrom, eventTo, periodNo, eventCost, sortNo, locationNo, clubNo)
                   VALUES (:eventTitle, :eventType, :eventFrom, :eventTo, :periodNo, :eventCost, :sortNo, :locationNo, :clubNo) \
                   """
    await db.execute(
        text(insert_query),
        {
            "eventTitle": event_title,
            "eventType": event_type,
            "eventFrom": event_from,
            "eventTo": event_to,
            "periodNo": period_no,
            "eventCost": event_cost,
            "sortNo": sort_no,
            "locationNo": final_location_no,
            "clubNo": clubno
        }
    )
    await db.commit()
    return RedirectResponse(url=f"/club_eventlist/{clubno}/{period_no}", status_code=303)


@app.post("/devent_insert")
async def save_devent(request: Request, db: AsyncSession = Depends(get_db), user_no: int = Depends(get_current_user)):
    form = await request.form()
    event_title = form.get("ceventtitle")
    event_type = form.get("eventtype")
    event_from = form.get("eventfrom")
    event_to = form.get("eventto")
    period_no = to_int(form.get("eventperiod"))
    event_cost = to_int(form.get("eventcost"))
    sort_no = to_int(form.get("sortno"))
    location_no_input = form.get("locationNo")
    custom_location = form.get("customLocation")
    if event_from:
        event_from = event_from.replace("T", " ")
    if event_to:
        event_to = event_to.replace("T", " ")
    final_location_no = 1  # 기본값 (장소미정)
    if location_no_input == "custom" and custom_location:
        result = await db.execute(
            text("INSERT INTO yk_location (locationTitle) VALUES (:title)"),
            {"title": custom_location}
        )
        await db.commit()
        final_location_no = result.lastrowid
    else:
        final_location_no = to_int(location_no_input)
    insert_query = """
                   INSERT INTO yk_event
                   (eventTitle, eventType, eventFrom, eventTo, periodNo, eventCost, sortNo, locationNo)
                   VALUES (:eventTitle, :eventType, :eventFrom, :eventTo, :periodNo, :eventCost, :sortNo, :locationNo) \
                   """

    await db.execute(
        text(insert_query),
        {
            "eventTitle": event_title,
            "eventType": event_type,
            "eventFrom": event_from,
            "eventTo": event_to,
            "periodNo": period_no,
            "eventCost": event_cost,
            "sortNo": sort_no,
            "locationNo": final_location_no
        }
    )
    await db.commit()
    return RedirectResponse(url="/dist_eventlist", status_code=303)

@app.post("/cevent_update/{eventno}/{clubno}", response_class=HTMLResponse)
async def updatecevent(request: Request, eventno: int, clubno: int, db: AsyncSession = Depends(get_db), user_no: int = Depends(get_current_user)):
    form_data = await request.form()
    data = {"eventTitle": form_data.get("ceventtitle"), "eventType": form_data.get("eventtype"), "eventFrom": form_data.get("eventfrom"), "eventTo": form_data.get("eventto"), "periodNo": form_data.get("eventperiod"), "eventCost": form_data.get("eventcost"), "sortNo": form_data.get("sortno"), "locationNo": form_data.get("locationNo"), "clubNo": clubno}
    update_fields = {k: v for k, v in data.items() if v is not None}
    if update_fields:
        params = dict(update_fields)
        params["eventNo"] = eventno
        await db.execute(text(f"UPDATE yk_event SET {', '.join([f'{k} = :{k}' for k in update_fields.keys()])} WHERE eventNo = :eventNo"), params)
        await db.commit()
    return RedirectResponse(f"/club_eventlist/{clubno}", status_code=303)

@app.post("/devent_update/{eventno}", response_class=HTMLResponse)
async def updatedevent(request: Request, eventno: int, db: AsyncSession = Depends(get_db), user_no: int = Depends(get_current_user)):
    form_data = await request.form()
    data = {"eventTitle": form_data.get("ceventtitle"), "eventType": form_data.get("eventtype"), "eventFrom": form_data.get("eventfrom"), "eventTo": form_data.get("eventto"), "periodNo": form_data.get("eventperiod"), "eventCost": form_data.get("eventcost"), "sortNo": form_data.get("sortno"), "locationNo": form_data.get("locationNo"), "regionNo": 0}
    update_fields = {k: v for k, v in data.items() if v is not None}
    if update_fields:
        params = dict(update_fields)
        params["eventNo"] = eventno
        await db.execute(text(f"UPDATE yk_event SET {', '.join([f'{k} = :{k}' for k in update_fields.keys()])} WHERE eventNo = :eventNo"), params)
        await db.commit()
    return RedirectResponse(f"/dist_eventlist", status_code=303)

# ==========================================
# 6. 마스터 데이터 관리 라우터
# ==========================================
@app.get("/notices", response_class=HTMLResponse)
async def notice_list(
    request: Request,
    status_filter: str = Query("PUBLISHED", alias="status"),
    db: AsyncSession = Depends(get_db),
    user_no: int = Depends(require_admin),
):
    if status_filter not in {"PUBLISHED", "ARCHIVED"}:
        status_filter = "PUBLISHED"
    result = await db.execute(text(
        "SELECT n.noticeNo, n.noticeTitle, n.noticeContent, n.status, n.rootNoticeNo, "
        "n.previousNoticeNo, u.userName, n.regDate "
        "FROM yk_notice AS n "
        "LEFT JOIN yk_user AS u ON u.userNo = n.userNo "
        "WHERE n.status = :status "
        "ORDER BY n.regDate DESC, n.noticeNo DESC"
    ), {"status": status_filter})
    notices = [dict(notice) for notice in result.mappings().all()]
    if notices:
        for notice in notices:
            notice["noticeContent"] = sanitize_notice_html(notice["noticeContent"] or "")
        notice_ids = [notice["noticeNo"] for notice in notices]
        root_ids = list({notice["rootNoticeNo"] for notice in notices})
        history_result = await db.execute(
            text(
                "SELECT noticeNo, rootNoticeNo, noticeTitle, status, regDate "
                "FROM yk_notice WHERE rootNoticeNo IN :root_ids "
                "ORDER BY regDate, noticeNo"
            ).bindparams(bindparam("root_ids", expanding=True)),
            {"root_ids": root_ids},
        )
        history_by_root: dict[int, list[dict[str, object]]] = {}
        for version in history_result.mappings().all():
            history_by_root.setdefault(version["rootNoticeNo"], []).append(dict(version))
        for notice in notices:
            notice["history"] = history_by_root.get(notice["rootNoticeNo"], [])
        attachment_result = await db.execute(
            text(
                "SELECT fileNo, noticeNo, originalName FROM yk_notice_files "
                "WHERE noticeNo IN :notice_ids ORDER BY fileNo"
            ).bindparams(bindparam("notice_ids", expanding=True)),
            {"notice_ids": notice_ids},
        )
        attachments_by_notice: dict[int, list[dict[str, object]]] = {}
        for attachment in attachment_result.mappings().all():
            attachments_by_notice.setdefault(attachment["noticeNo"], []).append(dict(attachment))
        for notice in notices:
            notice["attachments"] = attachments_by_notice.get(notice["noticeNo"], [])
    return templates.TemplateResponse(
        "master/noticelist.html",
        {
            "request": request,
            "session": dict(request.session),
            "notices": notices,
            "status_filter": status_filter,
        },
    )

@app.get("/announcements", response_class=HTMLResponse)
async def announcement_list(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user_no: int = Depends(get_current_user),
):
    result = await db.execute(
        text(
            "SELECT n.noticeNo, n.noticeTitle, n.regDate, "
            "EXISTS (SELECT 1 FROM yk_notice_views v "
            "WHERE v.noticeNo = n.noticeNo AND v.userNo = :user_no) AS is_read "
            "FROM yk_notice n WHERE n.status = 'PUBLISHED' "
            "ORDER BY n.regDate DESC, n.noticeNo DESC"
        ),
        {"user_no": user_no},
    )
    return templates.TemplateResponse(
        "notice/announcementlist.html",
        {
            "request": request,
            "session": dict(request.session),
            "announcements": result.mappings().all(),
        },
    )

@app.get("/announcements/{notice_no:int}", response_class=HTMLResponse)
async def announcement_detail(
    request: Request,
    notice_no: int,
    db: AsyncSession = Depends(get_db),
    user_no: int = Depends(get_current_user),
):
    result = await db.execute(
        text(
            "SELECT n.noticeNo, n.noticeTitle, n.noticeContent, n.regDate, u.userName "
            "FROM yk_notice n LEFT JOIN yk_user u ON u.userNo = n.userNo "
            "WHERE n.noticeNo = :notice_no AND n.status = 'PUBLISHED'"
        ),
        {"notice_no": notice_no},
    )
    notice = result.mappings().first()
    if notice is None:
        raise HTTPException(status_code=404, detail="게시 중인 공지사항을 찾을 수 없습니다.")

    await db.execute(
        text(
            "INSERT INTO yk_notice_views (noticeNo, userNo) "
            "VALUES (:notice_no, :user_no) "
            "ON DUPLICATE KEY UPDATE "
            "viewCount = viewCount + 1, lastViewedAt = CURRENT_TIMESTAMP"
        ),
        {"notice_no": notice_no, "user_no": user_no},
    )
    attachment_result = await db.execute(
        text(
            "SELECT fileNo, originalName FROM yk_notice_files "
            "WHERE noticeNo = :notice_no ORDER BY fileNo"
        ),
        {"notice_no": notice_no},
    )
    await db.commit()
    return templates.TemplateResponse(
        "notice/announcementdetail.html",
        {
            "request": request,
            "session": dict(request.session),
            "notice": {
                **dict(notice),
                "noticeContent": sanitize_notice_html(notice["noticeContent"] or ""),
            },
            "attachments": attachment_result.mappings().all(),
        },
    )

@app.get("/notices/{notice_no:int}", response_class=HTMLResponse)
async def notice_detail(
    request: Request,
    notice_no: int,
    db: AsyncSession = Depends(get_db),
    user_no: int = Depends(require_admin),
):
    result = await db.execute(
        text(
            "SELECT n.noticeNo, n.noticeTitle, n.noticeContent, n.status, n.rootNoticeNo, "
            "n.previousNoticeNo, u.userName, n.regDate "
            "FROM yk_notice AS n LEFT JOIN yk_user AS u ON u.userNo = n.userNo "
            "WHERE n.noticeNo = :notice_no"
        ),
        {"notice_no": notice_no},
    )
    notice = result.mappings().first()
    if notice is None:
        raise HTTPException(status_code=404, detail="공지사항을 찾을 수 없습니다.")

    history_result = await db.execute(
        text(
            "SELECT noticeNo, noticeTitle, status, regDate FROM yk_notice "
            "WHERE rootNoticeNo = :root_notice_no ORDER BY regDate, noticeNo"
        ),
        {"root_notice_no": notice["rootNoticeNo"]},
    )
    attachment_result = await db.execute(
        text("SELECT fileNo, originalName FROM yk_notice_files WHERE noticeNo = :notice_no ORDER BY fileNo"),
        {"notice_no": notice_no},
    )
    view_result = await db.execute(
        text(
            "SELECT u.userName, u.userId, v.firstViewedAt, v.lastViewedAt, v.viewCount "
            "FROM yk_notice_views v JOIN yk_user u ON u.userNo = v.userNo "
            "WHERE v.noticeNo = :notice_no ORDER BY v.lastViewedAt DESC"
        ),
        {"notice_no": notice_no},
    )
    return templates.TemplateResponse(
        "master/noticedetail.html",
        {
            "request": request,
            "session": dict(request.session),
            "notice": {**dict(notice), "noticeContent": sanitize_notice_html(notice["noticeContent"] or "")},
            "history": history_result.mappings().all(),
            "attachments": attachment_result.mappings().all(),
            "viewers": view_result.mappings().all(),
        },
    )

@app.get("/notices/new", response_class=HTMLResponse)
async def notice_new(request: Request, user_no: int = Depends(require_admin)):
    return templates.TemplateResponse(
        "master/noticereg.html",
        {
            "request": request,
            "session": dict(request.session),
            "title": "",
            "content": "",
            "error": None,
            "form_action": "/notices",
            "attachments": [],
            "edit_mode": False,
        },
    )

@app.get("/notices/{notice_no}/edit", response_class=HTMLResponse)
async def notice_edit(
    request: Request,
    notice_no: int,
    db: AsyncSession = Depends(get_db),
    user_no: int = Depends(require_admin),
):
    result = await db.execute(
        text(
            "SELECT noticeNo, noticeTitle, noticeContent, status "
            "FROM yk_notice WHERE noticeNo = :notice_no"
        ),
        {"notice_no": notice_no},
    )
    notice = result.mappings().first()
    if notice is None:
        raise HTTPException(status_code=404, detail="공지사항을 찾을 수 없습니다.")
    if notice["status"] != "PUBLISHED":
        raise HTTPException(status_code=409, detail="내린 공지사항은 수정할 수 없습니다.")
    attachment_result = await db.execute(
        text("SELECT fileNo, originalName FROM yk_notice_files WHERE noticeNo = :notice_no ORDER BY fileNo"),
        {"notice_no": notice_no},
    )
    return templates.TemplateResponse(
        "master/noticereg.html",
        {
            "request": request,
            "session": dict(request.session),
            "title": notice["noticeTitle"],
            "content": sanitize_notice_html(notice["noticeContent"] or ""),
            "error": None,
            "form_action": f"/notices/{notice_no}/revise",
            "attachments": attachment_result.mappings().all(),
            "edit_mode": True,
        },
    )

@app.post("/notices", response_class=HTMLResponse)
async def notice_create(
    request: Request,
    title: str = Form(...),
    content: str = Form(...),
    files: list[UploadFile] | None = File(default=None),
    db: AsyncSession = Depends(get_db),
    user_no: int = Depends(require_admin),
):
    title = title.strip()
    if notice_has_invalid_image_source(content):
        raise HTTPException(
            status_code=400,
            detail="본문 이미지 업로드가 완료되지 않았습니다. 이미지 삽입 버튼으로 다시 추가해 주세요.",
        )
    content = sanitize_notice_html(content.strip())
    if not title or len(title) > 200 or not content:
        error = "제목은 1~200자, 내용은 비워둘 수 없습니다."
        return templates.TemplateResponse(
            "master/noticereg.html",
            {
                "request": request,
                "session": dict(request.session),
                "title": title,
                "content": content,
                "error": error,
                "form_action": "/notices",
                "attachments": [],
                "edit_mode": False,
            },
            status_code=400,
        )

    selected_files = [upload for upload in files or [] if upload.filename]
    if len(selected_files) > NOTICE_MAX_ATTACHMENTS:
        raise HTTPException(status_code=400, detail=f"첨부파일은 최대 {NOTICE_MAX_ATTACHMENTS}개까지 가능합니다.")

    attachments, stored_paths = await _store_notice_uploads(selected_files)
    NOTICE_ATTACHMENT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        result = await db.execute(
            text(
                "INSERT INTO yk_notice "
                "(noticeTitle, noticeContent, userNo, status, rootNoticeNo) "
                "VALUES (:title, :content, :user_no, 'PUBLISHED', NULL)"
            ),
            {"title": title, "content": content, "user_no": user_no},
        )
        notice_no = result.lastrowid
        await db.execute(
            text("UPDATE yk_notice SET rootNoticeNo = :notice_no WHERE noticeNo = :notice_no"),
            {"notice_no": notice_no},
        )
        await _insert_notice_attachments(db, notice_no, attachments)
        await db.commit()
    except Exception:
        await db.rollback()
        _remove_stored_files(stored_paths)
        raise
    return RedirectResponse(url="/notices", status_code=303)

@app.post("/notices/{notice_no}/revise", response_class=HTMLResponse)
async def notice_revise(
    request: Request,
    notice_no: int,
    title: str = Form(...),
    content: str = Form(...),
    keep_file_ids: list[int] | None = Form(default=None),
    files: list[UploadFile] | None = File(default=None),
    db: AsyncSession = Depends(get_db),
    user_no: int = Depends(require_admin),
):
    title = title.strip()
    if notice_has_invalid_image_source(content):
        raise HTTPException(
            status_code=400,
            detail="본문 이미지 업로드가 완료되지 않았습니다. 이미지 삽입 버튼으로 다시 추가해 주세요.",
        )
    content = sanitize_notice_html(content.strip())
    if not title or len(title) > 200 or not content:
        raise HTTPException(status_code=400, detail="제목은 1~200자, 내용은 비워둘 수 없습니다.")

    selected_files = [upload for upload in files or [] if upload.filename]
    if len(selected_files) > NOTICE_MAX_ATTACHMENTS:
        raise HTTPException(status_code=400, detail=f"새 첨부파일은 최대 {NOTICE_MAX_ATTACHMENTS}개까지 가능합니다.")

    stored_paths: list[Path] = []
    try:
        previous_result = await db.execute(
            text(
                "SELECT noticeNo, status, rootNoticeNo FROM yk_notice "
                "WHERE noticeNo = :notice_no FOR UPDATE"
            ),
            {"notice_no": notice_no},
        )
        previous = previous_result.mappings().first()
        if previous is None:
            raise HTTPException(status_code=404, detail="공지사항을 찾을 수 없습니다.")
        if previous["status"] != "PUBLISHED":
            raise HTTPException(status_code=409, detail="내린 공지사항은 수정할 수 없습니다.")

        old_attachments_result = await db.execute(
            text(
                "SELECT fileNo, originalName, storedName, mediaType, fileSize "
                "FROM yk_notice_files WHERE noticeNo = :notice_no ORDER BY fileNo"
            ),
            {"notice_no": notice_no},
        )
        old_attachments = [dict(row) for row in old_attachments_result.mappings().all()]
        keep_ids = set(keep_file_ids or [])
        retained = [item for item in old_attachments if item["fileNo"] in keep_ids]
        new_attachments, stored_paths = await _store_notice_uploads(selected_files)

        await db.execute(
            text("UPDATE yk_notice SET status = 'ARCHIVED' WHERE noticeNo = :notice_no AND status = 'PUBLISHED'"),
            {"notice_no": notice_no},
        )
        insert_result = await db.execute(
            text(
                "INSERT INTO yk_notice "
                "(noticeTitle, noticeContent, userNo, status, previousNoticeNo, rootNoticeNo) "
                "VALUES (:title, :content, :user_no, 'PUBLISHED', :previous_no, :root_no)"
            ),
            {
                "title": title,
                "content": content,
                "user_no": user_no,
                "previous_no": notice_no,
                "root_no": previous["rootNoticeNo"] or notice_no,
            },
        )
        new_notice_no = insert_result.lastrowid
        for attachment in retained:
            await db.execute(
                text(
                    "INSERT INTO yk_notice_files "
                    "(noticeNo, originalName, storedName, mediaType, fileSize) "
                    "VALUES (:notice_no, :original_name, :stored_name, :media_type, :file_size)"
                ),
                {
                    "notice_no": new_notice_no,
                    "original_name": attachment["originalName"],
                    "stored_name": attachment["storedName"],
                    "media_type": attachment["mediaType"],
                    "file_size": attachment["fileSize"],
                },
            )
        await _insert_notice_attachments(db, new_notice_no, new_attachments)
        await db.commit()
    except Exception:
        await db.rollback()
        _remove_stored_files(stored_paths)
        raise
    return RedirectResponse(url="/notices", status_code=303)

@app.post("/notices/{notice_no}/archive")
async def notice_archive(
    notice_no: int,
    db: AsyncSession = Depends(get_db),
    user_no: int = Depends(require_admin),
):
    try:
        result = await db.execute(
            text("SELECT status FROM yk_notice WHERE noticeNo = :notice_no FOR UPDATE"),
            {"notice_no": notice_no},
        )
        notice = result.mappings().first()
        if notice is None:
            raise HTTPException(status_code=404, detail="공지사항을 찾을 수 없습니다.")
        if notice["status"] != "PUBLISHED":
            raise HTTPException(status_code=409, detail="이미 내린 공지사항은 다시 내릴 수 없습니다.")
        await db.execute(
            text("UPDATE yk_notice SET status = 'ARCHIVED' WHERE noticeNo = :notice_no"),
            {"notice_no": notice_no},
        )
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    return RedirectResponse(url="/notices?status=PUBLISHED", status_code=303)

async def _store_notice_uploads(
    uploads: list[UploadFile],
) -> tuple[list[dict[str, object]], list[Path]]:
    attachments: list[dict[str, object]] = []
    stored_paths: list[Path] = []
    NOTICE_ATTACHMENT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        for upload in uploads:
            extension = Path(upload.filename or "").suffix.lower()
            if extension not in NOTICE_ALLOWED_ATTACHMENT_EXTENSIONS:
                raise HTTPException(status_code=400, detail="PDF, HWP, HWPX 파일만 첨부할 수 있습니다.")

            data = await safe_file_read(upload)
            if not _is_valid_notice_attachment(extension, data):
                raise HTTPException(status_code=400, detail=f"{upload.filename}: 파일 형식을 확인할 수 없습니다.")

            stored_name = f"{secrets.token_hex(16)}{extension}"
            stored_path = NOTICE_ATTACHMENT_DIR / stored_name
            stored_path.write_bytes(data)
            stored_paths.append(stored_path)
            attachments.append({
                "original_name": Path((upload.filename or "").replace("\\", "/")).name[:255],
                "stored_name": stored_name,
                "media_type": _notice_attachment_media_type(extension),
                "file_size": len(data),
            })
    except Exception:
        _remove_stored_files(stored_paths)
        raise
    return attachments, stored_paths

def _remove_stored_files(paths: list[Path]) -> None:
    for path in paths:
        path.unlink(missing_ok=True)

async def _insert_notice_attachments(
    db: AsyncSession,
    notice_no: int,
    attachments: list[dict[str, object]],
) -> None:
    for attachment in attachments:
        await db.execute(
            text(
                "INSERT INTO yk_notice_files "
                "(noticeNo, originalName, storedName, mediaType, fileSize) "
                "VALUES (:notice_no, :original_name, :stored_name, :media_type, :file_size)"
            ),
            {"notice_no": notice_no, **attachment},
        )

def _is_valid_notice_attachment(extension: str, data: bytes) -> bool:
    if extension == ".pdf":
        return data.startswith(b"%PDF-")
    if extension == ".hwp":
        return data.startswith(bytes.fromhex("D0CF11E0A1B11AE1"))
    if extension != ".hwpx" or not data.startswith(b"PK\x03\x04"):
        return False
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            return "[Content_Types].xml" in archive.namelist()
    except zipfile.BadZipFile:
        return False

def _notice_attachment_media_type(extension: str) -> str:
    return {
        ".pdf": "application/pdf",
        ".hwp": "application/x-hwp",
        ".hwpx": "application/octet-stream",
    }[extension]

@app.post("/notices/images", response_class=JSONResponse)
async def notice_image_upload(
    image: UploadFile = File(...),
    user_no: int = Depends(require_admin),
):
    data = await safe_file_read(image, max_size=NOTICE_MAX_IMAGE_SIZE)
    try:
        with Image.open(BytesIO(data)) as uploaded_image:
            if uploaded_image.format not in {"PNG", "JPEG", "GIF", "WEBP"}:
                raise HTTPException(status_code=400, detail="PNG, JPG, GIF, WEBP 이미지만 삽입할 수 있습니다.")
            if uploaded_image.width * uploaded_image.height > 12_000_000:
                raise HTTPException(status_code=400, detail="이미지 해상도가 너무 큽니다.")
            uploaded_image.verify()
        with Image.open(BytesIO(data)) as uploaded_image:
            image_data = uploaded_image.convert("RGBA")
            image_data.thumbnail((2400, 2400))
            normalized_image = BytesIO()
            image_data.save(normalized_image, format="PNG", optimize=True)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, SyntaxError) as exc:
        raise HTTPException(status_code=400, detail="유효한 이미지 파일이 아닙니다.") from exc

    NOTICE_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    stored_name = f"{secrets.token_hex(16)}.png"
    (NOTICE_IMAGE_DIR / stored_name).write_bytes(normalized_image.getvalue())
    return {"url": f"/notice-media/{stored_name}"}

@app.get("/notice-media/{filename}")
async def notice_image_get(filename: str, user_no: int = Depends(get_current_user)):
    if not re.fullmatch(r"[a-f0-9]{32}\.png", filename):
        raise HTTPException(status_code=404, detail="이미지를 찾을 수 없습니다.")
    image_path = NOTICE_IMAGE_DIR / filename
    if not image_path.is_file():
        raise HTTPException(status_code=404, detail="이미지를 찾을 수 없습니다.")
    return FileResponse(image_path, media_type="image/png")

@app.get("/notices/attachments/{file_no:int}")
async def notice_attachment_get(
    request: Request,
    file_no: int,
    db: AsyncSession = Depends(get_db),
    user_no: int = Depends(get_current_user),
):
    result = await db.execute(
        text(
            "SELECT f.originalName, f.storedName, f.mediaType, n.status "
            "FROM yk_notice_files f JOIN yk_notice n ON n.noticeNo = f.noticeNo "
            "WHERE f.fileNo = :file_no"
        ),
        {"file_no": file_no},
    )
    attachment = result.mappings().first()
    if attachment is None:
        raise HTTPException(status_code=404, detail="첨부파일을 찾을 수 없습니다.")
    if attachment["status"] != "PUBLISHED" and request.session.get("user_Role") != "ADMIN":
        raise HTTPException(status_code=404, detail="첨부파일을 찾을 수 없습니다.")
    stored_name = attachment["storedName"]
    if not re.fullmatch(r"[a-f0-9]{32}\.(?:pdf|hwp|hwpx)", stored_name):
        raise HTTPException(status_code=404, detail="첨부파일을 찾을 수 없습니다.")
    attachment_path = NOTICE_ATTACHMENT_DIR / stored_name
    if not attachment_path.is_file():
        raise HTTPException(status_code=404, detail="첨부파일을 찾을 수 없습니다.")
    return FileResponse(
        attachment_path,
        media_type=attachment["mediaType"],
        filename=attachment["originalName"],
        content_disposition_type="attachment",
    )

@app.get("/mst_rank", response_class=HTMLResponse)
async def rankmaster(request: Request, db: AsyncSession = Depends(get_db), user_no: int = Depends(get_current_user)):
    return templates.TemplateResponse("master/ranklist.html", {"request": request, "session": dict(request.session), "ranklist": await get_rank(db)})

@app.post("/rank_reg", response_class=HTMLResponse)
async def rankreg(request: Request, user_no: int = Depends(get_current_user)):
    return templates.TemplateResponse("master/rankreg.html", {"request": request, "session": dict(request.session)})

@app.get("/rank_edit/{rankno}", response_class=HTMLResponse)
async def rankedit(request: Request, rankno: int, db: AsyncSession = Depends(get_db), user_no: int = Depends(get_current_user)):
    return templates.TemplateResponse("master/rankedit.html", {"request": request, "rank": await get_rank_dtl(rankno, db), "session": dict(request.session)})

@app.api_route("/rank_update/{rankno}", response_class=HTMLResponse, methods=["GET", "POST"])
async def updaterank(request: Request, rankno: int, db: AsyncSession = Depends(get_db), user_no: int = Depends(get_current_user)):
    form_data = await request.form()
    await db.execute(text("update yk_rank set rankTitle=:rtitle,rankTitleEng=:rtitleeng, rankTitleCn=:rtitlechn, rankType=:rtype, sortNo=:rsortno, modDate=now() where rankNo=:rankno"), {"rtitle": form_data.get("rtitle"), "rtitleeng": form_data.get("rtitleeng"), "rtitlechn": form_data.get("rtitlechn"), "rtype": form_data.get("rtype"), "rsortno": form_data.get("rsortno"), "rankno": rankno})
    await db.commit()
    return RedirectResponse(f"/mst_rank", status_code=303)

@app.api_route("/rank_insert", response_class=HTMLResponse, methods=["GET", "POST"])
async def insertrank(request: Request, db: AsyncSession = Depends(get_db), user_no: int = Depends(get_current_user)):
    form_data = await request.form()
    await db.execute(text("INSERT INTO yk_rank (rankTitle,rankTitleEng,rankTitleCn, rankType, sortNo) values (:rtitle,:rtitleeng,:rtitlechn,:rtype,:rsortno)"), {"rtitle": form_data.get("rtitle"), "rtitleeng": form_data.get("rtitleeng"), "rtitlechn": form_data.get("rtitlechn"), "rtype": form_data.get("rtype"), "rsortno": form_data.get("rsortno")})
    await db.commit()
    return RedirectResponse(f"/mst_rank", status_code=303)

@app.get("/mst_cat", response_class=HTMLResponse)
async def catgorymaster(request: Request, db: AsyncSession = Depends(get_db), user_no: int = Depends(get_current_user)):
    return templates.TemplateResponse("master/categorylist.html", {"request": request, "session": dict(request.session), "catlist": await get_category(db)})

@app.post("/cat_reg", response_class=HTMLResponse)
async def catgoryreg(request: Request, db: AsyncSession = Depends(get_db), user_no: int = Depends(get_current_user)):
    return templates.TemplateResponse("master/categoryreg.html", {"request": request, "session": dict(request.session)})

@app.get("/cat_edit/{catno}", response_class=HTMLResponse)
async def catgoryedit(request: Request, catno: int, db: AsyncSession = Depends(get_db), user_no: int = Depends(get_current_user)):
    return templates.TemplateResponse("master/categoryedit.html", {"request": request, "session": dict(request.session), "catdtl": await get_cat_detail(catno, db)})

@app.api_route("/cat_insert", response_class=HTMLResponse, methods=["GET", "POST"])
async def insertcat(request: Request, db: AsyncSession = Depends(get_db), user_no: int = Depends(get_current_user)):
    form_data = await request.form()
    await db.execute(text("INSERT INTO yk_category (catTitle,catTitleEng,catTitleCn, catType) values (:rtitle,:rtitleeng,:rtitlechn,:rtype)"), {"rtitle": form_data.get("rtitle"), "rtitleeng": form_data.get("rtitleeng"), "rtitlechn": form_data.get("rtitlechn"), "rtype": form_data.get("rtype")})
    await db.commit()
    return RedirectResponse(f"/mst_cat", status_code=303)

@app.api_route("/cat_update/{catno}", response_class=HTMLResponse, methods=["GET", "POST"])
async def updatecat(request: Request, catno: int, db: AsyncSession = Depends(get_db), user_no: int = Depends(get_current_user)):
    form_data = await request.form()
    await db.execute(text("update yk_category set catTitle=:rtitle,catTitleEng=:rtitleeng, catTitleCn=:rtitlechn, catType=:rtype, modDate=now() where catNo=:catno"), {"rtitle": form_data.get("rtitle"), "rtitleeng": form_data.get("rtitleeng"), "rtitlechn": form_data.get("rtitlechn"), "rtype": form_data.get("rtype"), "catno": catno})
    await db.commit()
    return RedirectResponse(f"/mst_cat", status_code=303)

@app.get("/mst_prize", response_class=HTMLResponse)
async def prizemaster(request: Request, db: AsyncSession = Depends(get_db), user_no: int = Depends(get_current_user)):
    return templates.TemplateResponse("master/prizelist.html", {"request": request, "session": dict(request.session), "prizelist": await get_prize(db)})

@app.post("/prize_reg", response_class=HTMLResponse)
async def prizereg(request: Request, db: AsyncSession = Depends(get_db), user_no: int = Depends(get_current_user)):
    return templates.TemplateResponse("master/prizereg.html", {"request": request, "session": dict(request.session)})

@app.get("/prize_edit/{prizeno}", response_class=HTMLResponse)
async def prizeedit(request: Request, prizeno: int, db: AsyncSession = Depends(get_db), user_no: int = Depends(get_current_user)):
    return templates.TemplateResponse("master/prizeedit.html", {"request": request, "session": dict(request.session), "prizedtl": await get_prize_detail(prizeno, db)})

@app.api_route("/prize_insert", response_class=HTMLResponse, methods=["GET", "POST"])
async def insertprize(request: Request, db: AsyncSession = Depends(get_db), user_no: int = Depends(get_current_user)):
    form_data = await request.form()
    await db.execute(text("INSERT INTO yk_prize (prizeTitle,prizeTitleEng,prizeTitleCn, prizeType, sortNo) values (:rtitle,:rtitleeng,:rtitlechn,:rtype, :sortno)"), {"rtitle": form_data.get("rtitle"), "rtitleeng": form_data.get("rtitleeng"), "rtitlechn": form_data.get("rtitlechn"), "rtype": form_data.get("rtype"), "sortno": form_data.get("sortno")})
    await db.commit()
    return RedirectResponse(f"/mst_prize", status_code=303)

@app.api_route("/prize_update/{prizeno}", response_class=HTMLResponse, methods=["GET", "POST"])
async def updateprize(request: Request, prizeno: int, db: AsyncSession = Depends(get_db), user_no: int = Depends(get_current_user)):
    form_data = await request.form()
    await db.execute(text("update yk_prize set prizeTitle=:rtitle,prizeTitleEng=:rtitleeng, prizeTitleCn=:rtitlechn, prizeType=:rtype, sortNo=:sortno ,modDate=now() where prizeNo=:prizeno"), {"rtitle": form_data.get("rtitle"), "rtitleeng": form_data.get("rtitleeng"), "rtitlechn": form_data.get("rtitlechn"), "rtype": form_data.get("rtype"), "prizeno": prizeno, "sortno": form_data.get("sortno")})
    await db.commit()
    return RedirectResponse(f"/mst_prize", status_code=303)

@app.get("/mst_member", response_class=HTMLResponse)
async def membermaster(request: Request, db: AsyncSession = Depends(get_db), user_no: int = Depends(get_current_user)):
    return templates.TemplateResponse("master/memberlist.html", {"request": request, "session": dict(request.session), "memberlist": await get_member(db)})

@app.get("/member_edit/{memberno}", response_class=HTMLResponse)
async def memberedit(request: Request, memberno: int, db: AsyncSession = Depends(get_db), user_no: int = Depends(get_current_user)):
    return templates.TemplateResponse("master/memberedit.html", {"request": request, "clubs": await get_club(db), "session": dict(request.session), "memberdtl": await get_member_dtl(memberno, db)})

@app.post("/member_reg", response_class=HTMLResponse)
async def memberreg(request: Request, db: AsyncSession = Depends(get_db), user_no: int = Depends(get_current_user)):
    return templates.TemplateResponse("master/memberreg.html", {"request": request, "clubs": await get_club(db), "session": dict(request.session)})

@app.post("/member_insert", response_class=HTMLResponse)
async def insertmember(request: Request, db: AsyncSession = Depends(get_db), user_no: int = Depends(get_current_user)):
    form_data = await request.form()
    data = {"memberName": _clean_str(form_data.get("membername")), "memberNameEng": _clean_str(form_data.get("membernameeng")), "memberNameCn": _clean_str(form_data.get("membernamecn")), "memberBirth": _clean_str(form_data.get("memberbirth")), "memberEntdate": _clean_str(form_data.get("regdate")), "memberMF": _clean_str(form_data.get("membermf")), "memberSponser": _clean_int(form_data.get("memberspon")), "regNo": _clean_int(form_data.get("regno")), "clubNo": _clean_int(form_data.get("memberclub")), "memberStatus": _clean_str(form_data.get("memberstat"))}
    insert_fields = {k: v for k, v in data.items() if v is not None}
    await db.execute(text(f"INSERT INTO yk_members ({', '.join(insert_fields.keys())}) VALUES ({', '.join([':'+k for k in insert_fields.keys()])})"), insert_fields)
    await db.commit()
    return RedirectResponse(f"/mst_member", status_code=303)

@app.post("/cmember_insert", response_class=HTMLResponse)
async def insertcmember(request: Request, db: AsyncSession = Depends(get_db), user_no: int = Depends(get_current_user)):
    form_data = await request.form()
    clubno = _clean_int(form_data.get("memberclub"))
    data = {"memberName": _clean_str(form_data.get("membername")), "memberNameEng": _clean_str(form_data.get("membernameeng")), "memberNameCn": _clean_str(form_data.get("membernamecn")), "memberBirth": _clean_str(form_data.get("memberbirth")), "memberEntdate": _clean_str(form_data.get("regdate")), "memberMF": _clean_str(form_data.get("membermf")), "memberSponser": _clean_int(form_data.get("memberspon")), "regNo": _clean_int(form_data.get("regno")), "clubNo": clubno, "memberStatus": _clean_str(form_data.get("memberstat"))}
    insert_fields = {k: v for k, v in data.items() if v is not None}
    await db.execute(text(f"INSERT INTO yk_members ({', '.join(insert_fields.keys())}) VALUES ({', '.join([':'+k for k in insert_fields.keys()])})"), insert_fields)
    await db.commit()
    return RedirectResponse(f"/club_memberlist/{clubno}", status_code=303)

@app.post("/cmember_reg", response_class=HTMLResponse)
async def cmemberreg(request: Request, db: AsyncSession = Depends(get_db), user_no: int = Depends(get_current_user)):
    clubno = request.session.get("user_Clubno")
    return templates.TemplateResponse("club/cmemberreg.html", {"request": request, "clubs": await get_club(db), "session": dict(request.session), "spons": await get_clubsponser(clubno, db)})

@app.post("/cmember_update/{memberno}", response_class=HTMLResponse)
async def cupdatemember(request: Request, memberno: int, db: AsyncSession = Depends(get_db), user_no: int = Depends(get_current_user)):
    form_data = await request.form()
    clubno = _clean_int(form_data.get("memberclub"))
    data = {"memberName": _clean_str(form_data.get("membername")), "memberNameEng": _clean_str(form_data.get("membernameeng")), "memberNameCn": _clean_str(form_data.get("membernamecn")), "memberBirth": _clean_str(form_data.get("memberbirth")), "memberEntdate": _clean_str(form_data.get("regdate")), "memberMF": _clean_str(form_data.get("membermf")), "memberSponser": _clean_int(form_data.get("memberspon")), "regNo": _clean_str(form_data.get("regno")), "clubNo": clubno, "maskYN": _clean_str(form_data.get("membermask")), "memberStatus": _clean_str(form_data.get("memberstat")), "memberMemo": form_data.get("membermemo", '')}
    update_fields = {k: v for k, v in data.items() if v is not None}
    if update_fields:
        params = dict(update_fields)
        params["memberNo"] = memberno
        await db.execute(text(f"UPDATE yk_members SET {', '.join([f'{k} = :{k}' for k in update_fields.keys()])} WHERE memberNo = :memberNo"), params)
        await db.commit()
    return RedirectResponse(f"/club_memberlist/{clubno}", status_code=303)

@app.post("/member_update/{memberno}", response_class=HTMLResponse)
async def updatemember(request: Request, memberno: int, db: AsyncSession = Depends(get_db), user_no: int = Depends(get_current_user)):
    form_data = await request.form()
    data = {
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
        "memberMemo": form_data.get("membermemo", ''),
    }
    update_fields = {k: v for k, v in data.items() if v is not None}
    if update_fields:
        params = dict(update_fields)
        params["memberNo"] = memberno
        set_clause = ", ".join([f"{k} = :{k}" for k in update_fields.keys()])
        await db.execute(text(f"UPDATE yk_members SET {set_clause} WHERE memberNo = :memberNo"), params)
        await db.commit()
    return RedirectResponse(f"/mst_member", status_code=303)

@app.get("/mst_region", response_class=HTMLResponse)
async def regionmaster(request: Request, db: AsyncSession = Depends(get_db), user_no: int = Depends(get_current_user)):
    regionlist = await getregionList(db)
    return templates.TemplateResponse("master/regionlist.html",
                                          {"request": request, "session": dict(request.session),
                                           "regionlist": regionlist})

@app.get("/mst_club", response_class=HTMLResponse)
async def clubmaster(request: Request, db: AsyncSession = Depends(get_db), user_no: int = Depends(get_current_user)):
    clublist = await get_club(db)
    return templates.TemplateResponse("master/clublist.html",
                                          {"request": request, "session": dict(request.session), "clublist": clublist})

@app.get("/club_edit/{clubno}", response_class=HTMLResponse)
async def clubedit_route(request: Request, clubno: int, db: AsyncSession = Depends(get_db), user_no: int = Depends(get_current_user)):
    club = await get_club_dtl(clubno, db)
    spons = await get_club_spon(clubno, db)
    return templates.TemplateResponse("master/clubedit.html",
                                          {"request": request, "session": dict(request.session), "clubdtl": club,
                                           "spons": spons})

@app.post("/club_reg", response_class=HTMLResponse)
async def clubreg_route(request: Request, db: AsyncSession = Depends(get_db), user_no: int = Depends(get_current_user)):
    club = await get_club(db)
    return templates.TemplateResponse("master/clubreg.html",
                                          {"request": request, "session": dict(request.session), "clubs": club})

@app.post("/club_update/{clubno}", response_class=HTMLResponse)
async def clubupdate(request: Request, clubno: int, db: AsyncSession = Depends(get_db), user_no: int = Depends(get_current_user)):
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
    await db.execute(query,
                     {"clubname": clubname, "clubnameeng": clubnameeng, "clubnamecn": clubnamecn, "estdate": estdate,
                      "charno": charno, "clubtel": clubtel, "clubfax": clubfax, "clubemail": clubemail,
                      "clubspon": clubspon, "clubaddr": clubaddr, "clubno": clubno})
    await db.commit()
    return RedirectResponse(f"/mst_club", status_code=303)

@app.post("/club_insert", response_class=HTMLResponse)
async def clubinsert(request: Request, db: AsyncSession = Depends(get_db), user_no: int = Depends(get_current_user)):
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
    await db.execute(query,
                     {"clubname": clubname, "clubnameeng": clubnameeng, "clubnamecn": clubnamecn, "estdate": estdate,
                      "charno": charno, "clubtel": clubtel, "clubfax": clubfax, "clubemail": clubemail,
                      "clubspon": clubspon, "clubaddr": clubaddr})
    await db.commit()
    return RedirectResponse(f"/mst_club", status_code=303)


# ==========================================
# 7. YK55 라우터
# ==========================================
@app.get("/yk55greet", response_class=HTMLResponse)
async def yk55greet(request: Request, db: AsyncSession = Depends(get_db), user_no: int = Depends(get_current_user)):
    doclist = await getdocList(db)
    return templates.TemplateResponse("yk55/yk55_greetings.html",
                                          {"request": request, "session": dict(request.session), "doclist": doclist})

@app.post("/yk55greet_reg", response_class=HTMLResponse)
async def yk55greet_reg(request: Request, db: AsyncSession = Depends(get_db), user_no: int = Depends(get_current_user)):
    user_Role = request.session.get("user_Role")
    clubno = request.session.get("user_Clubno")
    periodno = current_period
    if user_Role == 'CUSER':
        events = await get_clubeventsperiod(clubno, periodno, db)
    else:
        events = await get_disteventsperiod(periodno, db)
    return templates.TemplateResponse("yk55/yk55_greetings_reg.html",
                                          {"request": request, "session": dict(request.session), "events": events})

@app.get("/yk55greet_edit/{greetno}", response_class=HTMLResponse)
async def yk55greet_edit_route(request: Request, greetno: int, db: AsyncSession = Depends(get_db), user_no: int = Depends(get_current_user)):
    docs = await getdocdetail(greetno, db)
    clubno = request.session.get("user_Clubno")
    periodno = current_period
    events = await get_clubeventsperiod(clubno, periodno, db)
    return templates.TemplateResponse("yk55/yk55_greetings_edit.html",
                                          {"request": request, "session": dict(request.session), "docs": docs,"events": events})

@app.get("/yk55greet_preview/{greetno}", response_class=HTMLResponse)
async def yk55greet_prv(request: Request, greetno: int, type: int = Query(1), db: AsyncSession = Depends(get_db), user_no: int = Depends(get_current_user)):
    docs = await getdocdetail(greetno, db)
    template_name = "tmplets/greet02.html" if type == 2 else "tmplets/greet01.html"
    resp = templates.TemplateResponse(template_name,{"request": request, "session": dict(request.session), "docs": docs})
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp

@app.api_route("/yk55greetupdate/{docno}", response_class=HTMLResponse, methods=["GET", "POST"])
async def updatedoc(request: Request, docno: int, db: AsyncSession = Depends(get_db), user_no: int = Depends(get_current_user)):
    form_data = await request.form()
    doctitle = form_data.get("dtitle")
    docconts = form_data.get("dcontent")
    doctype = form_data.get("dtype")
    docevent = form_data.get("docevent")
    dwriter1 = form_data.get("dwriter1")
    dwriter2 = form_data.get("dwriter2")
    query = text(
        f"update yk_doc set docTitle=:doctitle,docContents=:docconts,memberTitle=:dwriter1,memberName=:dwriter2, docType=:doctype,docEvent=:docevent, modDate=now() where docNo=:docno")
    await db.execute(query, {"docno": docno, "doctitle": doctitle, "docconts": docconts, "doctype": doctype,
                             "dwriter1": dwriter1, "dwriter2": dwriter2, "docevent": docevent})
    await db.commit()
    return RedirectResponse(f"/yk55greet", status_code=303)

@app.api_route("/yk55greetinsert/", response_class=HTMLResponse, methods=["GET", "POST"])
async def insertdoc(request: Request, db: AsyncSession = Depends(get_db), user_no: int = Depends(get_current_user)):
    form_data = await request.form()
    doctitle = form_data.get("dtitle")
    docconts = form_data.get("dcontent")
    doctype = form_data.get("dtype")
    docevent = form_data.get("docevent")
    dwriter1 = form_data.get("dwriter1")
    dwriter2 = form_data.get("dwriter2")
    query = text(
        f"INSERT INTO yk_doc (docTitle, docContents, memberTitle, memberName, userNo, docType, docEvent) values (:doctitle,:docconts,:dwriter1,:dwriter2, :userno, :doctype, :docevent) ")
    await db.execute(query, {"doctitle": doctitle, "docconts": docconts, "userno": request.session.get("user_No"),
                             "doctype": doctype, "dwriter1": dwriter1, "dwriter2": dwriter2, "docevent": docevent})
    await db.commit()
    return RedirectResponse(f"/yk55greet", status_code=303)

@app.get("/yk55cabhist", response_class=HTMLResponse)
async def yk55cabhist(request: Request, db: AsyncSession = Depends(get_db), user_no: int = Depends(get_current_user)):
    periods = await getperiod(db)
    return templates.TemplateResponse("yk55/yk55_cabhist.html",
                                          {"request": request, "session": dict(request.session), "periods": periods})

@app.get("/yk55cabhist_view/{periodno}", response_class=HTMLResponse)
async def yk55cabhist_view(request: Request, periodno: int, db: AsyncSession = Depends(get_db), user_no: int = Depends(get_current_user)):
    cabs = await get_cabhist_wname(periodno, db)
    is_admin = request.session.get("user_Role") == "ADMIN"
    cabinet_ranks = await _get_cabinet_ranks(db) if is_admin else []
    cabinet_edit_ranks = await _get_cabinet_ranks(db, include_previous=True) if is_admin else []
    return templates.TemplateResponse("yk55/yk55_cabhistview.html",
                                          {"request": request, "session": dict(request.session), "membs": cabs, "periodno": periodno, "cabinet_ranks": cabinet_ranks, "cabinet_edit_ranks": cabinet_edit_ranks})

@app.get("/yk55cabhist_tview/{periodno}", response_class=HTMLResponse)
async def yk55cabhisttv(request: Request, periodno: int, db: AsyncSession = Depends(get_db), user_no: int = Depends(get_current_user)):
    cabs = await get_cabhist_wname(periodno, db)
    is_admin = request.session.get("user_Role") == "ADMIN"
    cabinet_ranks = await _get_cabinet_ranks(db) if is_admin else []
    cabinet_edit_ranks = await _get_cabinet_ranks(db, include_previous=True) if is_admin else []
    return templates.TemplateResponse("yk55/yk55_cabhistview_tile.html",
                                          {"request": request, "session": dict(request.session), "membs": cabs, "periodno": periodno, "cabinet_ranks": cabinet_ranks, "cabinet_edit_ranks": cabinet_edit_ranks})

async def _get_cabinet_ranks(
    db: AsyncSession, include_previous: bool = False
) -> list[dict]:
    role_titles = CABINET_EDIT_ROLE_TITLES if include_previous else CABINET_ROLE_TITLES
    result = await db.execute(
        text(
            "SELECT rankNo, rankTitle FROM yk_rank "
            "WHERE attrib = :attrib AND rankTitle IN :titles"
        ).bindparams(bindparam("titles", expanding=True)),
        {"attrib": "1000010000", "titles": list(role_titles)},
    )
    ranks = [
        {
            "rankNo": int(row.rankNo),
            "rankTitle": role_titles[row.rankTitle],
            "rankSourceTitle": row.rankTitle,
        }
        for row in result.fetchall()
    ]
    ranks.sort(key=lambda rank: list(role_titles).index(rank["rankSourceTitle"]))
    return ranks


@app.get("/api/yk55/cabinet-members")
async def yk55_cabinet_members(
    q: str = Query(..., min_length=2, max_length=50),
    db: AsyncSession = Depends(get_db),
    user_no: int = Depends(require_admin),
):
    search_text = q.strip().replace("%", "").replace("_", "")
    if len(search_text) < 2:
        raise HTTPException(status_code=400, detail="회원 이름이나 클럽명을 두 글자 이상 입력해 주세요.")
    result = await db.execute(
        text(
            "SELECT m.memberNo, m.memberName, m.clubNo, c.clubName "
            "FROM yk_members m LEFT JOIN yk_club c ON c.clubNo = m.clubNo "
            "WHERE m.attrib = :attrib AND "
            "(m.memberName LIKE :term OR c.clubName LIKE :term) "
            "ORDER BY m.memberName LIMIT 30"
        ),
        {"attrib": "1000010000", "term": f"%{search_text}%"},
    )
    return {
        "members": [
            {
                "memberNo": row.memberNo,
                "memberName": row.memberName,
                "clubNo": row.clubNo,
                "clubName": row.clubName or "소속 없음",
            }
            for row in result.fetchall()
        ]
    }


@app.post("/yk55cabhist_insert/{periodno}")
async def yk55_cabinet_insert(
    request: Request,
    periodno: int,
    member_no: int = Form(..., alias="memberNo"),
    rank_no: int = Form(..., alias="rankNo"),
    view_type: str = Form("card", alias="viewType"),
    db: AsyncSession = Depends(get_db),
    user_no: int = Depends(require_admin),
):
    if view_type not in {"card", "list"}:
        raise HTTPException(status_code=400, detail="화면 형식이 올바르지 않습니다.")
    previous_added = False
    async with db.begin():
        period_result = await db.execute(
            text("SELECT periodNo FROM yk_period WHERE periodNo = :periodNo AND attrib = :attrib"),
            {"periodNo": periodno, "attrib": "1000010000"},
        )
        if period_result.first() is None:
            raise HTTPException(status_code=404, detail="회기 정보를 찾을 수 없습니다.")

        rank_result = await db.execute(
            text(
                "SELECT rankNo FROM yk_rank "
                "WHERE rankNo = :rankNo AND rankTitle IN :titles AND attrib = :attrib"
            ).bindparams(bindparam("titles", expanding=True)),
            {
                "rankNo": rank_no,
                "titles": list(CABINET_ROLE_TITLES),
                "attrib": "1000010000",
            },
        )
        if rank_result.first() is None:
            raise HTTPException(status_code=400, detail="지구 캐비넷에 등록할 수 없는 직책입니다.")

        member_result = await db.execute(
            text(
                "SELECT memberNo, clubNo FROM yk_members "
                "WHERE memberNo = :memberNo AND attrib = :attrib"
            ),
            {"memberNo": member_no, "attrib": "1000010000"},
        )
        member = member_result.mappings().first()
        if member is None:
            raise HTTPException(status_code=400, detail="회원을 찾을 수 없습니다.")

        await db.execute(
            text(
                "INSERT INTO yk_distStaff (periodNo, clubNo, rankNo, memberNo, attrib) "
                "VALUES (:periodNo, :clubNo, :rankNo, :memberNo, :attrib) "
                "ON DUPLICATE KEY UPDATE dstaffNo = dstaffNo"
            ),
            {
                "periodNo": periodno,
                "clubNo": member["clubNo"],
                "rankNo": rank_no,
                "memberNo": member_no,
                "attrib": "1000010000",
            },
        )

        prior_governor_result = await db.execute(
            text(
                "SELECT s.memberNo, s.clubNo "
                "FROM yk_distStaff s "
                "JOIN yk_rank r ON r.rankNo = s.rankNo AND r.attrib = s.attrib "
                "WHERE s.attrib = :attrib AND s.periodNo = :priorPeriod "
                "AND r.rankTitle = '지구총재' "
                "ORDER BY s.dstaffNo DESC LIMIT 1"
            ),
            {"attrib": "1000010000", "priorPeriod": periodno - 1},
        )
        prior_governor = prior_governor_result.mappings().first()
        if prior_governor:
            former_rank_result = await db.execute(
                text(
                    "SELECT rankNo FROM yk_rank "
                    "WHERE rankTitle = :rankTitle AND attrib = :attrib"
                ),
                {
                    "rankTitle": CABINET_PREVIOUS_GOVERNOR_TITLE,
                    "attrib": "1000010000",
                },
            )
            former_rank = former_rank_result.first()
            if former_rank is None:
                raise HTTPException(
                    status_code=500, detail="직전지구총재 직책 정보를 찾을 수 없습니다."
                )
            existing_former_result = await db.execute(
                text(
                    "SELECT dstaffNo FROM yk_distStaff "
                    "WHERE attrib = :attrib AND periodNo = :periodNo "
                    "AND rankNo = :rankNo LIMIT 1"
                ),
                {
                    "attrib": "1000010000",
                    "periodNo": periodno,
                    "rankNo": former_rank[0],
                },
            )
            if existing_former_result.first() is None:
                await db.execute(
                    text(
                        "INSERT INTO yk_distStaff "
                        "(periodNo, clubNo, rankNo, memberNo, attrib) "
                        "VALUES (:periodNo, :clubNo, :rankNo, :memberNo, :attrib) "
                        "ON DUPLICATE KEY UPDATE dstaffNo = dstaffNo"
                    ),
                    {
                        "periodNo": periodno,
                        "clubNo": prior_governor["clubNo"],
                        "rankNo": former_rank[0],
                        "memberNo": prior_governor["memberNo"],
                        "attrib": "1000010000",
                    },
                )
                previous_added = True

    destination = "tview" if view_type == "card" else "view"
    return RedirectResponse(
        f"/yk55cabhist_{destination}/{periodno}"
        f"?added=1&previous_added={1 if previous_added else 0}",
        status_code=303,
    )


@app.post("/yk55cabhist_update/{periodno}/{dstaffno}")
async def yk55_cabinet_update(
    request: Request,
    periodno: int,
    dstaffno: int,
    member_no: int = Form(..., alias="memberNo"),
    rank_no: int = Form(..., alias="rankNo"),
    view_type: str = Form("card", alias="viewType"),
    db: AsyncSession = Depends(get_db),
    user_no: int = Depends(require_admin),
):
    if view_type not in {"card", "list"}:
        raise HTTPException(status_code=400, detail="화면 형식이 올바르지 않습니다.")
    destination = "tview" if view_type == "card" else "view"
    async with db.begin():
        cabinet_result = await db.execute(
            text(
                "SELECT dstaffNo FROM yk_distStaff "
                "WHERE dstaffNo = :dstaffNo AND periodNo = :periodNo AND attrib = :attrib "
                "FOR UPDATE"
            ),
            {
                "dstaffNo": dstaffno,
                "periodNo": periodno,
                "attrib": "1000010000",
            },
        )
        if cabinet_result.first() is None:
            raise HTTPException(status_code=404, detail="수정할 캐비넷 등록을 찾을 수 없습니다.")

        rank_result = await db.execute(
            text(
                "SELECT rankNo FROM yk_rank "
                "WHERE rankNo = :rankNo AND rankTitle IN :titles AND attrib = :attrib"
            ).bindparams(bindparam("titles", expanding=True)),
            {
                "rankNo": rank_no,
                "titles": list(CABINET_EDIT_ROLE_TITLES),
                "attrib": "1000010000",
            },
        )
        if rank_result.first() is None:
            raise HTTPException(status_code=400, detail="지구 캐비넷에 사용할 수 없는 직책입니다.")

        member_result = await db.execute(
            text(
                "SELECT memberNo, clubNo FROM yk_members "
                "WHERE memberNo = :memberNo AND attrib = :attrib"
            ),
            {"memberNo": member_no, "attrib": "1000010000"},
        )
        member = member_result.mappings().first()
        if member is None:
            raise HTTPException(status_code=400, detail="회원을 찾을 수 없습니다.")

        duplicate_result = await db.execute(
            text(
                "SELECT dstaffNo FROM yk_distStaff "
                "WHERE memberNo = :memberNo AND rankNo = :rankNo AND clubNo <=> :clubNo "
                "AND periodNo = :periodNo AND attrib = :attrib AND dstaffNo <> :dstaffNo "
                "LIMIT 1"
            ),
            {
                "memberNo": member_no,
                "rankNo": rank_no,
                "clubNo": member["clubNo"],
                "periodNo": periodno,
                "attrib": "1000010000",
                "dstaffNo": dstaffno,
            },
        )
        if duplicate_result.first() is not None:
            raise HTTPException(
                status_code=409, detail="같은 회원과 직책이 해당 회기에 이미 등록되어 있습니다."
            )

        await db.execute(
            text(
                "UPDATE yk_distStaff "
                "SET memberNo = :memberNo, clubNo = :clubNo, rankNo = :rankNo, modDate = NOW() "
                "WHERE dstaffNo = :dstaffNo AND periodNo = :periodNo AND attrib = :attrib"
            ),
            {
                "memberNo": member_no,
                "clubNo": member["clubNo"],
                "rankNo": rank_no,
                "dstaffNo": dstaffno,
                "periodNo": periodno,
                "attrib": "1000010000",
            },
        )

    return RedirectResponse(
        f"/yk55cabhist_{destination}/{periodno}?updated=1",
        status_code=303,
    )

@app.get("/yk55servhist", response_class=HTMLResponse)
async def yk55servhist(request: Request, db: AsyncSession = Depends(get_db), user_no: int = Depends(get_current_user)):
    periods = await getperiod(db)
    return templates.TemplateResponse("yk55/yk55_servhist.html",
                                          {"request": request, "session": dict(request.session), "periods": periods})

@app.get("/edit_servhist/{servno}", response_class=HTMLResponse)
async def yk55servhist(request: Request,servno: int, db: AsyncSession = Depends(get_db), user_no: int = Depends(get_current_user)):
    periods = await getperiod(db)
    servdtl = await get_servdtl(servno,db)
    return templates.TemplateResponse("yk55/yk55_edit_servhist.html",
                                          {"request": request, "session": dict(request.session), "periods": periods, "servdtl": servdtl})


@app.get("/yk55servhist_view/{period}", response_class=HTMLResponse)
async def yk55servhist_view(request: Request, period: int, db: AsyncSession = Depends(get_db), user_no: int = Depends(get_current_user)):
    svrs = await getperiod(db)
    servlist = await get_servlist(period, db)
    return templates.TemplateResponse("yk55/yk55_servhistview.html",
                                          {"request": request, "session": dict(request.session), "svrs": svrs, "period": period, "servlist": servlist})

@app.get("/yk55membhist", response_class=HTMLResponse)
async def yk55membhist(request: Request, db: AsyncSession = Depends(get_db), user_no: int = Depends(get_current_user)):
    periods = await getperiod(db)
    return templates.TemplateResponse("yk55/yk55_memberhist.html",
                                          {"request": request, "session": dict(request.session), "periods": periods})

@app.get("/yk55membhist_view/{periodno}", response_class=HTMLResponse)
async def yk55membhist_view(request: Request, periodno: int, db: AsyncSession = Depends(get_db), user_no: int = Depends(get_current_user)):
    membs = await get_dmemberhist_wname(periodno, db)
    periods = await getperiod(db)
    return templates.TemplateResponse("yk55/yk55_memberhistview.html",
                                          {"request": request, "session": dict(request.session), "membs": membs,
                                           "periodno": periodno, "periods": periods})

@app.get("/yk55membhist_tview/{periodno}", response_class=HTMLResponse)
async def yk55membhist_tview(request: Request, periodno: int, db: AsyncSession = Depends(get_db), user_no: int = Depends(get_current_user)):
    membs = await get_dmemberhist_wname(periodno, db)
    periods = await getperiod(db)
    return templates.TemplateResponse("yk55/yk55_memberhistview_tile.html",
                                          {"request": request, "session": dict(request.session), "membs": membs,
                                           "periodno": periodno, "periods": periods})

@app.get("/yk55mjfhist", response_class=HTMLResponse)
async def yk55mjfhist(request: Request, user_no: int = Depends(get_current_user)):
    return templates.TemplateResponse("yk55/yk55_mjfhist.html",
                                          {"request": request, "session": dict(request.session)})


@app.get("/privacy", response_class=HTMLResponse)
async def privacy(request: Request):
    return templates.TemplateResponse("privacy/privacy.htm", {"request": request})


# ---------------------------------------------------------
# 1. [신규] 회원 사진 제공 라우터 (Fallback 로직 포함)
# ---------------------------------------------------------
@app.get("/api/member_photo/{memberno}/{periodno}")
async def get_member_photo(memberno: int, periodno: int):
    versioned_path = f"static/img/members/mphoto_{memberno}_h{periodno}.png"
    if os.path.exists(versioned_path):
        return FileResponse(versioned_path)

    base_path = f"static/img/members/mphoto_{memberno}.png"
    if os.path.exists(base_path):
        return FileResponse(base_path)

    return FileResponse("static/img/defaultphoto.png")


@app.get("/main_dash", response_class=HTMLResponse)
async def maindash(request: Request,db: AsyncSession = Depends(get_db)):
    clublist = await funchub.get_board002(db)
    topdata = await funchub.get_boarddata2001(db)
    return templates.TemplateResponse("board/main_dash.html",
                                          {"request": request, "session": dict(request.session), "clublist": clublist , "topdata": topdata})


@app.get("/edit_dashdata", response_class=HTMLResponse)
async def editdashdata(request: Request,db: AsyncSession = Depends(get_db)):
    clublist = await funchub.get_board002(db)
    topdata = await funchub.get_boarddata2001(db)
    return templates.TemplateResponse("board/main_dash_edit.html",
                                          {"request": request, "session": dict(request.session), "clublist": clublist, "topdata": topdata})


@app.get("/dash_lcif", response_class=HTMLResponse)
async def dashlcif(request: Request,db: AsyncSession = Depends(get_db) ,user_no: int = Depends(get_current_user)):
    clublist = await funchub.get_board001(db)
    return templates.TemplateResponse("board/dash_lcif.html",
                                          {"request": request, "session": dict(request.session), "clublist": clublist})


@app.get("/userEdit", response_class=HTMLResponse)
async def useredit(request: Request,db: AsyncSession = Depends(get_db) ,user_no: int = Depends(get_current_user)):
    return templates.TemplateResponse("login/changePass.html",
                                          {"request": request, "session": dict(request.session)})


# ==========================================
# 8. 대시보드 데이터 업데이트 라우터
# ==========================================
@app.post("/api/update_dashdata")
async def update_dashdata(
        data: dict = Body(...),
        db: AsyncSession = Depends(get_db),
        user_no: int = Depends(get_current_user)
):
    try:
        club_no = int(data.get("clubNo", 0))
        period_no = str(data.get("periodNo", ""))
        pres_mcnt = int(data.get("presMcnt", 0))
        family_mcnt = int(data.get("familyMcnt", 0))

        if not club_no or not period_no:
            return JSONResponse({"ok": False, "message": "Invalid clubNo or periodNo"}, status_code=400)

        # 1. 해당 기수(periodNo)와 클럽(clubNo)의 데이터가 이미 있는지 확인
        check_sql = text("SELECT logNo FROM yk_dashboarddata WHERE clubNo = :clubNo AND periodNo = :periodNo")
        result = await db.execute(check_sql, {"clubNo": club_no, "periodNo": period_no})
        row = result.fetchone()

        if row:
            # 2. 데이터가 존재하면 UPDATE
            update_sql = text("""
                UPDATE yk_dashboarddata 
                SET presMcnt = :presMcnt, 
                    familyMcnt = :familyMcnt, 
                    modDate = NOW() 
                WHERE clubNo = :clubNo AND periodNo = :periodNo
            """)
            await db.execute(update_sql, {
                "presMcnt": pres_mcnt,
                "familyMcnt": family_mcnt,
                "clubNo": club_no,
                "periodNo": period_no
            })
        else:
            # 3. 데이터가 없으면 새로 INSERT (초기 prevMcnt는 0으로 세팅, 필요시 수정 가능)
            insert_sql = text("""
                INSERT INTO yk_dashboarddata 
                (periodNo, clubNo, prevMcnt, presMcnt, familyMcnt, regDate, modDate, attrib) 
                VALUES (:periodNo, :clubNo, 0, :presMcnt, :familyMcnt, NOW(), NOW(), '1000010000')
            """)
            await db.execute(insert_sql, {
                "periodNo": period_no,
                "clubNo": club_no,
                "presMcnt": pres_mcnt,
                "familyMcnt": family_mcnt
            })

        await db.commit()
        return {"ok": True, "message": "Dashboard data updated successfully"}

    except Exception as e:
        await db.rollback()
        print(f"Update Dashboard Error: {e}")
        return JSONResponse({"ok": False, "message": str(e)}, status_code=500)


@app.post("/api/update_topdata")
async def update_topdata(
        data: dict = Body(...),
        db: AsyncSession = Depends(get_db),
        user_no: int = Depends(get_current_user)
):
    try:
        # 1. 기존 유효한 데이터 무효화 (기간 조건 없이 현재 유효한 모든 데이터를 무효화)
        update_sql = text("""
                          UPDATE yk_dashboarddata2
                          SET attrib  = 'xxxupxxxup',
                              modDate = NOW()
                          WHERE attrib != 'xxxupxxxup'
                          """)
        await db.execute(update_sql)

        # 2. 새로운 데이터 INSERT (periodNo 제외, data01 ~ data14만 삽입)
        insert_sql = text("""
                          INSERT INTO yk_dashboarddata2
                          (data01, data02, data03, data04, data05, data06, data07,
                           data08, data09, data10, data11, data12, data13, data14,data15, data16, data17, data18,
                           regDate, attrib)
                          VALUES (:data01, :data02, :data03, :data04, :data05, :data06, :data07,
                                  :data08, :data09, :data10, :data11, :data12, :data13, :data14,:data15, :data16, :data17, :data18,
                                  NOW(), '1000010000')
                          """)

        # 파라미터 바인딩 (data01 ~ data14)
        params = {}
        for i in range(1, 19):
            col_name = f"data{i:02d}"
            # 프론트에서 값이 안 넘어왔을 경우 빈 문자열로 처리
            params[col_name] = str(data.get(col_name, ""))

        await db.execute(insert_sql, params)
        await db.commit()

        return {"ok": True, "message": "Top dashboard data updated successfully"}

    except Exception as e:
        await db.rollback()
        print(f"Update Top Dashboard Error: {e}")
        return JSONResponse({"ok": False, "message": str(e)}, status_code=500)

# ==========================================
# 봉사실적 저장 라우터 (/save_servhist)
# ==========================================
@app.post("/save_servhist", response_class=HTMLResponse)
async def save_servhist(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user_no: int = Depends(get_current_user)  # 인증 세션 확인
):
    form_data = await request.form()

    # 필수값 및 숫자형 데이터 정제
    period_no = _clean_int(form_data.get("periodNo"))
    serve_type = _clean_str(form_data.get("serveType")) or "DISTM"
    serve_title = _clean_str(form_data.get("serveTitle"))
    serve_content = _clean_str(form_data.get("serveContent"))
    serve_cost = _clean_int(form_data.get("serveCost")) or 0
    serve_timefrom = _clean_str(form_data.get("serveTimefrom")) or None
    serve_timeto = _clean_str(form_data.get("serveTimeto")) or None
    attrib = _clean_str(form_data.get("attrib")) or "1000010000"

    # 유효성 검사: 봉사명 누락 방지
    if not serve_title:
        raise HTTPException(status_code=400, detail="봉사명(serveTitle)은 필수 항목입니다.")

    insert_query = text("""
        INSERT INTO yk_serveHist (
            periodNo,
            serveType,
            serveTitle,
            serveContent,
            serveCost,
            serveTimefrom,
            serveTimeto,
            regDate,
            attrib
        ) VALUES (
            :periodNo,
            :serveType,
            :serveTitle,
            :serveContent,
            :serveCost,
            :serveTimefrom,
            :serveTimeto,
            NOW(),
            :attrib
        )
    """)

    params = {
        "periodNo": period_no,
        "serveType": serve_type[:5] if serve_type else "DISTM",
        "serveTitle": serve_title,
        "serveContent": serve_content,
        "serveCost": serve_cost,
        "serveTimefrom": serve_timefrom,
        "serveTimeto": serve_timeto,
        "attrib": attrib
    }

    try:
        await db.execute(insert_query, params)
        await db.commit()
    except Exception as e:
        await db.rollback()
        print(f"Error saving serveHist: {e}")
        raise HTTPException(status_code=500, detail="봉사실적 저장 중 오류가 발생했습니다.")

    # 저장이 완료되면 봉사실적 목록 화면으로 리다이렉트 (회기 정보가 있으면 해당 회기 뷰로 이동 가능)
    redirect_url = f"/yk55servhist_view/{period_no}" if period_no else "/yk55servhist"
    return RedirectResponse(url=redirect_url, status_code=303)

@app.post("/update_servhist/{servno}", response_class=HTMLResponse)
async def update_servhist(
    request: Request,
    servno: int,
    db: AsyncSession = Depends(get_db),
    user_no: int = Depends(get_current_user)
):
    form_data = await request.form()

    period_no = _clean_int(form_data.get("periodNo"))
    serve_type = _clean_str(form_data.get("serveType")) or "DISTM"
    serve_title = _clean_str(form_data.get("serveTitle"))
    serve_content = _clean_str(form_data.get("serveContent"))
    serve_cost = _clean_int(form_data.get("serveCost")) or 0
    serve_timefrom = _clean_str(form_data.get("serveTimefrom")) or None
    serve_timeto = _clean_str(form_data.get("serveTimeto")) or None

    update_query = text("""
        UPDATE yk_serveHist
        SET periodNo = :periodNo,
            serveType = :serveType,
            serveTitle = :serveTitle,
            serveContent = :serveContent,
            serveCost = :serveCost,
            serveTimefrom = :serveTimefrom,
            serveTimeto = :serveTimeto,
            modDate = NOW()
        WHERE serveNo = :serveNo
    """)

    await db.execute(update_query, {
        "serveNo": servno,
        "periodNo": period_no,
        "serveType": serve_type[:5],
        "serveTitle": serve_title,
        "serveContent": serve_content,
        "serveCost": serve_cost,
        "serveTimefrom": serve_timefrom,
        "serveTimeto": serve_timeto
    })
    await db.commit()

    return RedirectResponse(url=f"/yk55servhist_view/{period_no}", status_code=303)

# ==========================================
# 봉사실적 삭제(논리 삭제) 라우터 (/delete_servhist/{servno})
# ==========================================
@app.post("/delete_servhist/{servno}")
async def delete_servhist(
    request: Request,
    servno: int,
    db: AsyncSession = Depends(get_db),
    user_no: int = Depends(get_current_user)
):
    form_data = await request.form()
    period_no = _clean_int(form_data.get("periodNo"))

    delete_query = text("""
        UPDATE yk_serveHist
        SET attrib = 'XXXUPXXXUP',
            modDate = NOW()
        WHERE serveNo = :serveNo
    """)

    try:
        await db.execute(delete_query, {"serveNo": servno})
        await db.commit()
    except Exception as e:
        await db.rollback()
        print(f"Error deleting serveHist: {e}")
        raise HTTPException(status_code=500, detail="봉사실적 삭제 중 오류가 발생했습니다.")

    # 삭제 후 원래 보고 있던 회기 목록 페이지로 리다이렉트
    redirect_url = f"/yk55servhist_view/{period_no}" if period_no else "/yk55servhist"
    return RedirectResponse(url=redirect_url, status_code=303)

@app.get("/add_servhist", response_class=HTMLResponse)
async def addservhist(request: Request,db: AsyncSession = Depends(get_db) ,user_no: int = Depends(get_current_user)):
    return templates.TemplateResponse("yk55/yk55_add_servhist.html",
                                          {"request": request, "session": dict(request.session)})

# ==========================================
# 🚀 웹하드 라우터 연결
app.include_router(webhard.router)       # 일반 유저용: /webhard/...
app.include_router(webhard.admin_router) # 관리자용: /admin/webhard/...
