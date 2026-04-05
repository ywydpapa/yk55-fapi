import os
import calendar
from datetime import date
import dotenv
from pathlib import Path
from fastapi import Depends, FastAPI, Request, Query, Form, Response, HTTPException, File, UploadFile, Body
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, FileResponse
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from starlette.middleware.sessions import SessionMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

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

# 데이터베이스 세션 생성
async def get_db():
    async with async_session() as session:
        yield session

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
    user_No = request.session.get("user_No")
    user_Role = request.session.get("user_Role")
    user_clubno = request.session.get("user_Clubno")
    otp = request.session.get("otp")
    otpstat = await reg_otp(otp, user_No, db)
    msg = "" if otpstat else "OTP 등록 실패"
    if not user_No:
        return RedirectResponse(url="/")
    if user_Role == 'CUSER':
        clubmember = await get_clubmember(user_clubno, db)
        member_count = sum(1 for m in clubmember if m.get("memberStatus") == "ACTIV")
    else:
        member_count = await get_distmember(db)
    template_name = "main/indexc.html" if user_Role == "CUSER" else "main/index.html"
    return templates.TemplateResponse(template_name, {"request": request, "session": dict(request.session), "message": msg, "membercnt": member_count})

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

@app.post("/changeuserpass", dependencies=[Depends(get_current_user)])
async def change_password(data: dict = Body(...), db: AsyncSession = Depends(get_db)):
    hashed_password = get_password_hash(data["passwd"])
    sql = text("UPDATE yk_user SET userPassword = :passwd WHERE userNo = :userno")
    await db.execute(sql, {"passwd": hashed_password, "userno": data["uno"]})
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
    return templates.TemplateResponse("yk55/yk55_cabhistview.html",
                                          {"request": request, "session": dict(request.session), "membs": cabs, "periodno": periodno,})

@app.get("/yk55cabhist_tview/{periodno}", response_class=HTMLResponse)
async def yk55cabhisttv(request: Request, periodno: int, db: AsyncSession = Depends(get_db), user_no: int = Depends(get_current_user)):
    cabs = await get_cabhist_wname(periodno, db)
    return templates.TemplateResponse("yk55/yk55_cabhistview_tile.html",
                                          {"request": request, "session": dict(request.session), "membs": cabs, "periodno": periodno})

@app.get("/yk55servhist", response_class=HTMLResponse)
async def yk55servhist(request: Request, db: AsyncSession = Depends(get_db), user_no: int = Depends(get_current_user)):
    periods = await getperiod(db)
    return templates.TemplateResponse("yk55/yk55_servhist.html",
                                          {"request": request, "session": dict(request.session), "periods": periods})

@app.get("/yk55servhist_view/{period}", response_class=HTMLResponse)
async def yk55servhist_view(request: Request, period: int, db: AsyncSession = Depends(get_db), user_no: int = Depends(get_current_user)):
    svrs = await getperiod(db)
    return templates.TemplateResponse("yk55/yk55_servhistview.html",
                                          {"request": request, "session": dict(request.session), "svrs": svrs})

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


# ---------------------------------------------------------
# 1. [신규] 회원 사진 제공 라우터 (Fallback 로직 포함)
# ---------------------------------------------------------
@app.get("/api/member_photo/{memberno}/{periodno}")
async def get_member_photo(memberno: int, periodno: int):
    # 1. 현재 기수부터 1기까지 역순으로 사진이 있는지 확인
    for p in range(periodno, 0, -1):
        path = f"static/img/members/mphoto_{memberno}_h{p}.png"
        if os.path.exists(path):
            return FileResponse(path)
    # 2. 기수별 사진이 아예 없으면 기본 사진(mphoto_{memberno}.png) 확인
    base_path = f"static/img/members/mphoto_{memberno}.png"
    if os.path.exists(base_path):
        return FileResponse(base_path)
    # 3. 그것마저 없으면 디폴트 이미지 반환
    return FileResponse("static/img/defaultphoto.png")


@app.get("/main_dash", response_class=HTMLResponse)
async def maindash(request: Request,db: AsyncSession = Depends(get_db) ,user_no: int = Depends(get_current_user)):
    clublist = await funchub.get_board001(db)
    return templates.TemplateResponse("board/main_dash.html",
                                          {"request": request, "session": dict(request.session), "clublist": clublist})