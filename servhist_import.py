from datetime import date, datetime
from io import BytesIO
from typing import Any
from zipfile import BadZipFile

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException


MAX_SERVHIST_IMPORT_ROWS = 500
SERVHIST_IMPORT_COLUMNS = (
    ("봉사명", "serveTitle"),
    ("봉사구분", "serveType"),
    ("시작일", "serveTimefrom"),
    ("종료일", "serveTimeto"),
    ("봉사비용", "serveCost"),
    ("봉사내용", "serveContent"),
)
SERVHIST_TYPES = {"DISTM", "REGIM", "CLUBM"}
MAX_SERVHIST_COST = 9_223_372_036_854_775_807


class ServHistImportError(ValueError):
    pass


def parse_servhist_workbook(contents: bytes) -> list[dict[str, Any]]:
    try:
        workbook = load_workbook(BytesIO(contents), read_only=True, data_only=True)
    except (BadZipFile, InvalidFileException, OSError, ValueError) as exc:
        raise ServHistImportError("올바른 .xlsx 파일을 업로드해 주세요.") from exc

    try:
        worksheet = workbook.active
        if (
            worksheet.max_row > MAX_SERVHIST_IMPORT_ROWS + 1
            or worksheet.max_column > 64
        ):
            raise ServHistImportError(
                f"엑셀은 최대 {MAX_SERVHIST_IMPORT_ROWS}개 사업까지 포함할 수 있습니다."
            )

        header_cells = next(
            worksheet.iter_rows(min_row=1, max_row=1, values_only=True), ()
        )
        headers = [
            str(value).strip() if value is not None else ""
            for value in header_cells
        ]
        while headers and not headers[-1]:
            headers.pop()
        if "봉사명" not in headers:
            raise ServHistImportError("필수 열이 없습니다: 봉사명")
        named_headers = [header for header in headers if header]
        if len(named_headers) != len(set(named_headers)):
            raise ServHistImportError("엑셀 첫 행에 중복된 열 이름이 있습니다.")

        column_indexes = {
            field: headers.index(header)
            for header, field in SERVHIST_IMPORT_COLUMNS
            if header in headers
        }
        rows: list[dict[str, Any]] = []
        for row_number, values in enumerate(
            worksheet.iter_rows(min_row=2, values_only=True), start=2
        ):
            if not any(value is not None and str(value).strip() for value in values):
                continue
            if len(rows) >= MAX_SERVHIST_IMPORT_ROWS:
                raise ServHistImportError(
                    f"엑셀은 최대 {MAX_SERVHIST_IMPORT_ROWS}개 사업까지 포함할 수 있습니다."
                )
            rows.append({
                "rowNumber": row_number,
                **{
                    field: values[index] if index < len(values) else None
                    for field, index in column_indexes.items()
                },
            })
        if not rows:
            raise ServHistImportError("엑셀에 등록할 사업 행이 없습니다.")
        return rows
    finally:
        workbook.close()


def _parse_servhist_date(value: Any) -> date | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip().replace("/", "-"))
        except ValueError as exc:
            raise ValueError("날짜는 YYYY-MM-DD 형식이어야 합니다.") from exc
    raise ValueError("날짜가 비어 있거나 형식이 올바르지 않습니다.")


def _parse_cost(value: Any) -> int:
    if value is None or (isinstance(value, str) and not value.strip()):
        return 0
    if isinstance(value, bool):
        raise ValueError("봉사비용은 0 이상의 정수여야 합니다.")
    if isinstance(value, int):
        cost = value
    elif isinstance(value, float) and value.is_integer():
        cost = int(value)
    else:
        try:
            cost = int(str(value).strip().replace(",", ""))
        except ValueError as exc:
            raise ValueError("봉사비용은 0 이상의 정수여야 합니다.") from exc
    if cost < 0:
        raise ValueError("봉사비용은 0 이상의 정수여야 합니다.")
    if cost > MAX_SERVHIST_COST:
        raise ValueError("봉사비용이 저장 가능한 최대 금액을 초과했습니다.")
    return cost


def validate_servhist_rows(
    rows: list[dict[str, Any]], period_no: int
) -> list[dict[str, Any]]:
    validated: list[dict[str, Any]] = []
    for row_index, row in enumerate(rows):
        errors: list[str] = []
        title = str(row.get("serveTitle") or "").strip()
        if not title:
            errors.append("봉사명이 비어 있습니다.")
        elif len(title) > 1000:
            errors.append("봉사명은 1,000자 이하여야 합니다.")

        serve_type = str(row.get("serveType") or "DISTM").strip().upper()
        if serve_type not in SERVHIST_TYPES:
            errors.append("봉사구분은 DISTM, REGIM, CLUBM 중 하나여야 합니다.")

        parsed_dates: dict[str, date | None] = {}
        for field, label in (
            ("serveTimefrom", "시작일"),
            ("serveTimeto", "종료일"),
        ):
            try:
                parsed_dates[field] = _parse_servhist_date(row.get(field))
            except ValueError as exc:
                parsed_dates[field] = None
                errors.append(f"{label}: {exc}")

        date_from = parsed_dates["serveTimefrom"]
        date_to = parsed_dates["serveTimeto"]
        if date_from and date_to and date_to < date_from:
            errors.append("종료일은 시작일보다 빠를 수 없습니다.")

        try:
            serve_cost = _parse_cost(row.get("serveCost"))
        except ValueError as exc:
            serve_cost = 0
            errors.append(str(exc))

        content = str(row.get("serveContent") or "").strip()
        if len(content) > 2000:
            errors.append("봉사내용은 2,000자 이하여야 합니다.")

        source_row_number = row.get("rowNumber")
        row_number = (
            source_row_number
            if isinstance(source_row_number, int)
            and not isinstance(source_row_number, bool)
            else row_index + 2
        )
        validated.append({
            "rowNumber": row_number,
            "periodNo": period_no,
            "serveTitle": title,
            "serveType": serve_type,
            "serveTimefrom": date_from.isoformat() if date_from else None,
            "serveTimeto": date_to.isoformat() if date_to else None,
            "serveCost": serve_cost,
            "serveContent": content,
            "error": " ".join(errors),
        })
    return validated


def servhist_duplicate_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        int(row["periodNo"]),
        str(row.get("serveType") or "DISTM").strip().upper(),
        str(row.get("serveTitle") or "").strip(),
        _parse_servhist_date(row.get("serveTimefrom")),
        _parse_servhist_date(row.get("serveTimeto")),
        _parse_cost(row.get("serveCost")),
        str(row.get("serveContent") or "").strip(),
    )
