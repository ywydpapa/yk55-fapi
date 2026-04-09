from fastapi import APIRouter, File, UploadFile, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse
import shutil
from pathlib import Path

# 🌟 기존 funchub.py에서 작성하신 의존성 함수를 임포트합니다.
from funchub import get_current_user

# 라우터 생성
router = APIRouter(prefix="/webhard", tags=["Webhard"])
admin_router = APIRouter(prefix="/admin/webhard", tags=["Admin Webhard"])

# 저장소 최상위 경로 설정
BASE_STORAGE_DIR = Path("./user_webhard_data")


# ==========================================
# 🔐 인증 및 권한 확인 로직 (수정됨)
# ==========================================
async def get_current_user_id(request: Request, user_no: int = Depends(get_current_user)) -> str:
    """
    로그인 검증(get_current_user) 통과 후,
    세션에서 'user_Clubno'를 가져와서 'club###' 형태로 변환합니다.
    """
    club_no = request.session.get("user_Clubno")

    if club_no is None:
        # 만약 세션에 클럽 번호가 없다면 임시로 DB 고유번호를 사용
        return f"user_{user_no}"

    try:
        # club_no가 42라면 -> "club042" 로 변환 (3자리 0 채움)
        formatted_club_id = f"club{int(club_no):03d}"
    except ValueError:
        # 숫자로 변환할 수 없는 예외적인 값이 들어온 경우
        formatted_club_id = f"club_{club_no}"

    return formatted_club_id


async def get_current_admin(request: Request, user_no: int = Depends(get_current_user)):
    user_role = request.session.get("user_Role")
    if user_role != "ADMIN":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="관리자 권한이 없습니다."
        )
    return user_no


# ==========================================
# 🛠️ 공통 유틸리티 함수
# ==========================================
def get_user_storage_path(user_id: str) -> Path:
    user_dir = BASE_STORAGE_DIR / user_id
    user_dir.mkdir(parents=True, exist_ok=True)
    return user_dir


def get_safe_file_path(user_dir: Path, filename: str) -> Path:
    file_path = (user_dir / filename).resolve()
    if not file_path.is_relative_to(user_dir.resolve()):
        raise HTTPException(status_code=403, detail="잘못된 접근입니다.")
    return file_path


# ==========================================
# 👤 일반 유저용 API (/webhard/...)
# ==========================================
@router.get("/", response_class=HTMLResponse)
async def my_webhard_page(user_id: str = Depends(get_current_user_id)):
    user_dir = get_user_storage_path(user_id)
    files = [f.name for f in user_dir.iterdir() if f.is_file()]

    file_list_html = "".join([
        f"""<li style="margin-bottom: 10px; padding: 10px; border: 1px solid #eee; border-radius: 5px;">
            📄 {f} 
            <a href='/webhard/download/{f}' style="float: right; margin-left: 10px; padding: 5px 10px; background: #007bff; color: white; text-decoration: none; border-radius: 4px; font-size: 12px;">다운로드</a>
            <a href='/webhard/delete/{f}' style="float: right; padding: 5px 10px; background: #dc3545; color: white; text-decoration: none; border-radius: 4px; font-size: 12px;">삭제</a>
            <div style="clear: both;"></div>
        </li>""" for f in files
    ]) or "<li>제출된 파일이 없습니다.</li>"

    html_content = f"""
    <html>
        <head><title>{user_id}님의 파일 제출함</title></head>
        <body style="font-family: sans-serif; max-width: 600px; margin: 40px auto; padding: 20px;">
            <h2>☁️ {user_id}님의 파일 제출함</h2>
            <a href="/">홈으로 이동</a>
            <div style="background: #f8f9fa; padding: 20px; border-radius: 8px; margin-bottom: 30px;">
                <form action="/webhard/upload/" enctype="multipart/form-data" method="post" style="margin: 0;">
                    <input name="files" type="file" multiple required>
                    <button type="submit" style="padding: 5px 15px; background: #28a745; color: white; border: none; border-radius: 4px; cursor: pointer;">제출하기</button>
                </form>
                <p style="font-size: 12px; color: #666; margin-top: 10px;">* Shift 키나 Ctrl(Cmd) 키를 누른 상태로 여러 파일을 선택할 수 있습니다.</p>
            </div>
            <h3>📂 제출한 파일 목록</h3>
            <ul style="list-style-type: none; padding: 0;">{file_list_html}</ul>
        </body>
    </html>
    """
    return HTMLResponse(content=html_content)


@router.post("/upload/")
async def upload_files(files: list[UploadFile] = File(...), user_id: str = Depends(get_current_user_id)):
    user_dir = get_user_storage_path(user_id)

    for file in files:
        if not file.filename:
            continue
        safe_file_path = get_safe_file_path(user_dir, file.filename)
        with open(safe_file_path, "wb+") as file_object:
            shutil.copyfileobj(file.file, file_object)

    return RedirectResponse(url="/webhard", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/download/{filename}")
async def download_file(filename: str, user_id: str = Depends(get_current_user_id)):
    user_dir = get_user_storage_path(user_id)
    safe_file_path = get_safe_file_path(user_dir, filename)
    if safe_file_path.exists() and safe_file_path.is_file():
        return FileResponse(path=safe_file_path, filename=filename)
    raise HTTPException(status_code=404, detail="파일을 찾을 수 없습니다.")


@router.get("/delete/{filename}")
async def delete_file(filename: str, user_id: str = Depends(get_current_user_id)):
    user_dir = get_user_storage_path(user_id)
    safe_file_path = get_safe_file_path(user_dir, filename)
    if safe_file_path.exists() and safe_file_path.is_file():
        safe_file_path.unlink()
        return RedirectResponse(url="/webhard", status_code=status.HTTP_303_SEE_OTHER)
    raise HTTPException(status_code=404, detail="파일을 찾을 수 없습니다.")


# ==========================================
# 👑 관리자용 API (/admin/webhard/...)
# ==========================================
@admin_router.get("/", response_class=HTMLResponse)
async def admin_dashboard(admin_no: int = Depends(get_current_admin)):
    if not BASE_STORAGE_DIR.exists():
        BASE_STORAGE_DIR.mkdir()

    folders = [d.name for d in BASE_STORAGE_DIR.iterdir() if d.is_dir()]
    folders.sort()

    user_list_html = ""
    for folder_name in folders:
        user_dir = BASE_STORAGE_DIR / folder_name
        file_count = len([f for f in user_dir.iterdir() if f.is_file()])
        user_list_html += f"""
        <li style="margin-bottom: 10px; padding: 10px; background: #f8f9fa; border-radius: 5px;">
            👤 <strong>{folder_name}</strong> (제출된 파일: {file_count}개)
            <a href='/admin/webhard/user/{folder_name}' style="float: right; padding: 5px 10px; background: #17a2b8; color: white; text-decoration: none; border-radius: 4px; font-size: 12px;">파일 보기</a>
            <div style="clear: both;"></div>
        </li>
        """

    if not folders:
        user_list_html = "<li>파일을 제출한 유저가 없습니다.</li>"

    html_content = f"""
    <html>
        <head><title>관리자 대시보드</title></head>
        <body style="font-family: sans-serif; max-width: 600px; margin: 40px auto; padding: 20px;">
            <h2>👑 관리자 대시보드 (유저별 제출 현황)</h2>
            <a href="/">홈으로 이동</a>
            <ul style="list-style-type: none; padding: 0;">{user_list_html}</ul>
        </body>
    </html>
    """
    return HTMLResponse(content=html_content)


@admin_router.get("/user/{target_folder}", response_class=HTMLResponse)
async def admin_view_user_files(target_folder: str, admin_no: int = Depends(get_current_admin)):
    user_dir = BASE_STORAGE_DIR / target_folder

    if not user_dir.exists() or not user_dir.is_dir():
        raise HTTPException(status_code=404, detail="해당 유저의 폴더가 없습니다.")

    files = [f.name for f in user_dir.iterdir() if f.is_file()]

    file_list_html = "".join([
        f"""<li style="margin-bottom: 10px; padding: 10px; border: 1px solid #eee; border-radius: 5px;">
            📄 {f} 
            <a href='/admin/webhard/download/{target_folder}/{f}' style="float: right; padding: 5px 10px; background: #007bff; color: white; text-decoration: none; border-radius: 4px; font-size: 12px;">다운로드</a>
            <div style="clear: both;"></div>
        </li>""" for f in files
    ]) or "<li>제출된 파일이 없습니다.</li>"

    html_content = f"""
    <html>
        <head><title>{target_folder} 제출 파일</title></head>
        <body style="font-family: sans-serif; max-width: 600px; margin: 40px auto; padding: 20px;">
            <a href="/admin/webhard" style="color: #6c757d; text-decoration: none;">← 목록으로 돌아가기</a>
            <h2>📂 {target_folder}님이 제출한 파일</h2>
            <ul style="list-style-type: none; padding: 0;">{file_list_html}</ul>
        </body>
    </html>
    """
    return HTMLResponse(content=html_content)


@admin_router.get("/download/{target_folder}/{filename}")
async def admin_download_file(target_folder: str, filename: str, admin_no: int = Depends(get_current_admin)):
    user_dir = BASE_STORAGE_DIR / target_folder
    safe_file_path = get_safe_file_path(user_dir, filename)

    if safe_file_path.exists() and safe_file_path.is_file():
        return FileResponse(path=safe_file_path, filename=f"[{target_folder}]_{filename}")

    raise HTTPException(status_code=404, detail="파일을 찾을 수 없습니다.")
