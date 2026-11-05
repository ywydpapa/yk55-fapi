from datetime import date, datetime
from io import BytesIO
from typing import Any
from zipfile import BadZipFile

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException


MAX_EVENT_IMPORT_ROWS = 500
EVENT_IMPORT_COLUMNS = (
    ("행사명", "eventTitle"),
    ("행사종류", "eventType"),
    ("시작일시", "eventFrom"),
    ("종료일시", "eventTo"),
    ("회기번호", "periodNo"),
    ("장소", "locationTitle"),
    ("소요비용", "eventCost"),
    ("배치순서", "sortNo"),
)
EVENT_TYPES = {"DISTE"}


class EventImportError(ValueError):
    pass


def parse_event_workbook(contents: bytes) -> list[dict[str, Any]]:
    try:
        workbook = load_workbook(BytesIO(contents), read_only=True, data_only=True)
    except (BadZipFile, InvalidFileException, OSError, ValueError) as exc:
        raise EventImportError("올바른 .xlsx 파일을 업로드해 주세요.") from exc

    try:
        worksheet = workbook.active
        if worksheet.max_row > MAX_EVENT_IMPORT_ROWS + 1 or worksheet.max_column > 64:
            raise EventImportError(f"엑셀은 최대 {MAX_EVENT_IMPORT_ROWS}개 행사까지 포함할 수 있습니다.")

        header_cells = next(worksheet.iter_rows(min_row=1, max_row=1, values_only=True), ())
        headers = [str(value).strip() if value is not None else "" for value in header_cells]
        while headers and not headers[-1]:
            headers.pop()
        required = {"행사명", "시작일시", "종료일시", "회기번호"}
        missing = sorted(required - set(headers))
        if missing:
            raise EventImportError(f"필수 열이 없습니다: {', '.join(missing)}")
        named_headers = [header for header in headers if header]
        if len(named_headers) != len(set(named_headers)):
            raise EventImportError("엑셀 첫 행에 중복된 열 이름이 있습니다.")

        column_indexes = {
            field: headers.index(header)
            for header, field in EVENT_IMPORT_COLUMNS
            if header in headers
        }
        rows: list[dict[str, Any]] = []
        for row_number, values in enumerate(
            worksheet.iter_rows(min_row=2, values_only=True), start=2
        ):
            if not any(value is not None and str(value).strip() for value in values):
                continue
            if len(rows) >= MAX_EVENT_IMPORT_ROWS:
                raise EventImportError(f"엑셀은 최대 {MAX_EVENT_IMPORT_ROWS}개 행사까지 포함할 수 있습니다.")
            rows.append({
                "rowNumber": row_number,
                **{
                    field: values[index] if index < len(values) else None
                    for field, index in column_indexes.items()
                },
            })
        if not rows:
            raise EventImportError("엑셀에 등록할 행사 행이 없습니다.")
        return rows
    finally:
        workbook.close()


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.min.time())
    elif isinstance(value, str):
        raw = value.strip().replace("/", "-")
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError as exc:
            raise ValueError("날짜·시간은 YYYY-MM-DD HH:MM 형식이어야 합니다.") from exc
    else:
        raise ValueError("날짜·시간이 비어 있거나 형식이 올바르지 않습니다.")
    if parsed.tzinfo is not None:
        raise ValueError("날짜·시간에는 시간대를 입력할 수 없습니다.")
    return parsed.replace(microsecond=0)


def _parse_integer(value: Any, label: str, default: int | None = None) -> int:
    if value is None or (isinstance(value, str) and not value.strip()):
        if default is not None:
            return default
        raise ValueError(f"{label}이(가) 비어 있습니다.")
    if isinstance(value, bool):
        raise ValueError(f"{label}은(는) 정수여야 합니다.")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    raw = str(value).strip().replace(",", "")
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{label}은(는) 정수여야 합니다.") from exc


def normalize_event_datetime(value: Any) -> str:
    return _parse_datetime(value).isoformat(sep=" ", timespec="seconds")


def event_duplicate_key(row: dict[str, Any]) -> tuple[str, str, int]:
    return (
        str(row["eventTitle"]).strip().casefold(),
        normalize_event_datetime(row["eventFrom"]),
        int(row["periodNo"]),
    )


def validate_event_rows(
    rows: list[dict[str, Any]],
    valid_periods: set[int],
    location_ids_by_title: dict[str, int],
) -> list[dict[str, Any]]:
    validated: list[dict[str, Any]] = []
    for row_index, row in enumerate(rows):
        errors: list[str] = []
        title = str(row.get("eventTitle") or "").strip()
        if not title:
            errors.append("행사명이 비어 있습니다.")

        event_type = str(row.get("eventType") or "DISTE").strip().upper()
        if event_type not in EVENT_TYPES:
            errors.append("지구 행사 종류는 DISTE만 가져올 수 있습니다.")

        try:
            event_from = _parse_datetime(row.get("eventFrom"))
        except ValueError as exc:
            errors.append(f"시작일시: {exc}")
            event_from = None
        try:
            event_to = _parse_datetime(row.get("eventTo"))
        except ValueError as exc:
            errors.append(f"종료일시: {exc}")
            event_to = None
        if event_from and event_to and event_to <= event_from:
            errors.append("종료일시는 시작일시보다 늦어야 합니다.")

        try:
            period_no = _parse_integer(row.get("periodNo"), "회기번호")
            if period_no not in valid_periods:
                errors.append("존재하지 않거나 사용할 수 없는 회기번호입니다.")
        except ValueError as exc:
            errors.append(str(exc))
            period_no = 0

        location_title = str(row.get("locationTitle") or "").strip()
        if location_title:
            location_no = location_ids_by_title.get(location_title.casefold())
            if location_no is None:
                errors.append(f"등록되지 않은 장소입니다: {location_title}")
        else:
            location_no = 1
            if location_no not in set(location_ids_by_title.values()):
                errors.append("장소 미정(장소번호 1) 항목을 찾을 수 없습니다.")

        try:
            event_cost = _parse_integer(row.get("eventCost"), "소요비용", default=10000)
        except ValueError as exc:
            errors.append(str(exc))
            event_cost = 10000
        try:
            sort_no = _parse_integer(row.get("sortNo"), "배치순서", default=100)
        except ValueError as exc:
            errors.append(str(exc))
            sort_no = 100

        source_row_number = row.get("rowNumber")
        row_number = (
            source_row_number
            if isinstance(source_row_number, int) and not isinstance(source_row_number, bool)
            else row_index + 2
        )
        normalized = {
            "rowNumber": row_number,
            "eventTitle": title,
            "eventType": event_type,
            "eventFrom": event_from.isoformat(sep=" ", timespec="seconds") if event_from else "",
            "eventTo": event_to.isoformat(sep=" ", timespec="seconds") if event_to else "",
            "periodNo": period_no,
            "locationTitle": location_title,
            "locationNo": location_no,
            "eventCost": event_cost,
            "sortNo": sort_no,
            "error": " ".join(errors),
        }
        validated.append(normalized)
    return validated
