from datetime import date
from io import BytesIO
import json
import os
import unittest

from fastapi import HTTPException
from openpyxl import Workbook, load_workbook

os.environ.setdefault("dburl", "mysql+asyncmy://test:test@localhost/test")
os.environ.setdefault("cperiod", "56")
import main
from servhist_import import (
    SERVHIST_IMPORT_COLUMNS,
    ServHistImportError,
    parse_servhist_workbook,
    servhist_duplicate_key,
    validate_servhist_rows,
)


class Result:
    def __init__(self, rows):
        self.rows = rows

    def first(self):
        return self.rows[0] if self.rows else None

    def mappings(self):
        return self

    def all(self):
        return self.rows


class Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeImportDB:
    def __init__(self, period_exists=True, existing_rows=None):
        self.period_exists = period_exists
        self.existing_rows = existing_rows or []
        self.inserts = []

    def begin(self):
        return Transaction()

    async def execute(self, statement, params=None):
        query = str(statement)
        if "SELECT periodNo FROM yk_period" in query:
            return Result([(56,)] if self.period_exists else [])
        if "SELECT periodNo, serveType, serveTitle" in query:
            return Result(self.existing_rows)
        if "INSERT INTO yk_serveHist" in query:
            self.inserts.append(params)
            return Result([])
        raise AssertionError(f"Unexpected query: {query}")


def build_workbook(rows, headers=None):
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(headers or [header for header, _ in SERVHIST_IMPORT_COLUMNS])
    for row in rows:
        worksheet.append(row)
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


class ServHistImportTests(unittest.TestCase):
    def test_parse_and_validate_service_overview_row(self):
        parsed = parse_servhist_workbook(build_workbook([[
            "무료 급식 지원",
            "REGIM",
            date(2026, 10, 1),
            date(2026, 10, 2),
            125000,
            "지역 복지관 지원",
        ]]))

        rows = validate_servhist_rows(parsed, period_no=56)

        self.assertEqual(rows[0]["error"], "")
        self.assertEqual(rows[0]["periodNo"], 56)
        self.assertEqual(rows[0]["serveType"], "REGIM")
        self.assertEqual(rows[0]["serveTimefrom"], "2026-10-01")
        self.assertEqual(rows[0]["serveTimeto"], "2026-10-02")
        self.assertEqual(rows[0]["serveCost"], 125000)

    def test_optional_values_use_defaults(self):
        rows = validate_servhist_rows(
            [{"serveTitle": "기본 봉사"}],
            period_no=56,
        )

        self.assertEqual(rows[0]["error"], "")
        self.assertEqual(rows[0]["serveType"], "DISTM")
        self.assertIsNone(rows[0]["serveTimefrom"])
        self.assertIsNone(rows[0]["serveTimeto"])
        self.assertEqual(rows[0]["serveCost"], 0)
        self.assertEqual(rows[0]["serveContent"], "")

    def test_missing_title_invalid_type_dates_and_negative_cost_are_reported(self):
        rows = validate_servhist_rows(
            [{
                "serveTitle": "",
                "serveType": "OTHER",
                "serveTimefrom": "not-a-date",
                "serveCost": -1,
            }],
            period_no=56,
        )

        self.assertIn("봉사명이 비어 있습니다.", rows[0]["error"])
        self.assertIn("DISTM, REGIM, CLUBM", rows[0]["error"])
        self.assertIn("시작일", rows[0]["error"])
        self.assertIn("0 이상의 정수", rows[0]["error"])

    def test_title_column_is_required(self):
        with self.assertRaisesRegex(ServHistImportError, "필수 열"):
            parse_servhist_workbook(
                build_workbook([], headers=["봉사구분", "시작일"])
            )

    def test_template_download_contains_expected_columns(self):
        response = self._run(
            main.servhist_import_template(56, FakeImportDB(), 1)
        )
        workbook = load_workbook(BytesIO(response.body), read_only=True, data_only=True)
        headers = next(workbook.active.iter_rows(min_row=1, max_row=1, values_only=True))
        workbook.close()

        self.assertEqual(headers, tuple(header for header, _ in SERVHIST_IMPORT_COLUMNS))

    def test_exact_duplicate_key_matches_normalized_date_and_defaults(self):
        base = {
            "periodNo": 56,
            "serveTitle": "  무료 급식 지원 ",
            "serveType": "distm",
            "serveTimefrom": date(2026, 10, 1),
            "serveTimeto": None,
            "serveCost": 1000,
            "serveContent": "내용",
        }
        same = {
            **base,
            "serveTitle": "무료 급식 지원",
            "serveType": "DISTM",
            "serveTimefrom": "2026-10-01",
        }
        different = {**same, "serveCost": 1001}

        self.assertEqual(servhist_duplicate_key(base), servhist_duplicate_key(same))
        self.assertNotEqual(
            servhist_duplicate_key(same), servhist_duplicate_key(different)
        )

    def test_confirm_skips_exact_duplicates_and_inserts_new_rows(self):
        duplicate = {
            "periodNo": 56,
            "serveType": "DISTM",
            "serveTitle": "기존 사업",
            "serveTimefrom": date(2026, 10, 1),
            "serveTimeto": None,
            "serveCost": 1000,
            "serveContent": "내용",
        }
        new_row = {
            "serveTitle": "신규 사업",
            "serveType": "REGIM",
            "serveTimefrom": "2026-10-02",
            "serveTimeto": "",
            "serveCost": 0,
            "serveContent": "",
        }
        db = FakeImportDB(existing_rows=[duplicate])
        submitted_duplicate = {
            **duplicate,
            "serveTimefrom": "2026-10-01",
        }

        response = self._run(
            main.servhist_import_confirm(
                56, json.dumps([submitted_duplicate, new_row]), db, 1
            )
        )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(
            response.headers["location"],
            "/yk55servhist_view/56?imported=1&skipped=1",
        )
        self.assertEqual(len(db.inserts), 1)
        self.assertEqual(db.inserts[0]["serveTitle"], "신규 사업")

    def test_confirm_rejects_nonexistent_period(self):
        db = FakeImportDB(period_exists=False)

        with self.assertRaises(HTTPException) as raised:
            self._run(
                main.servhist_import_confirm(
                    56, json.dumps([{"serveTitle": "사업"}]), db, 1
                )
            )

        self.assertEqual(raised.exception.status_code, 404)
        self.assertEqual(db.inserts, [])

    @staticmethod
    def _run(coroutine):
        import asyncio

        return asyncio.run(coroutine)


if __name__ == "__main__":
    unittest.main()
