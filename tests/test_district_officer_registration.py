import os
import re
import unittest
from types import SimpleNamespace

from fastapi import HTTPException
from starlette.requests import Request

os.environ.setdefault("dburl", "mysql+asyncmy://test:test@localhost/test")
os.environ.setdefault("cperiod", "56")
import main


class Result:
    def __init__(self, rows):
        self.rows = rows

    def first(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows

    def mappings(self):
        return self


class Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeDistrictOfficerDB:
    def __init__(self, period_exists=True, rank_allowed=True, member_exists=True):
        self.period_exists = period_exists
        self.rank_allowed = rank_allowed
        self.member_exists = member_exists
        self.inserts = []

    def begin(self):
        return Transaction()

    async def execute(self, statement, params=None):
        query = str(statement)
        if "SELECT periodNo FROM yk_period" in query:
            return Result([(56,)] if self.period_exists else [])
        if "SELECT rankNo FROM yk_rank" in query:
            return Result([(params["rankNo"],)] if self.rank_allowed else [])
        if "SELECT memberNo, clubNo FROM yk_members" in query:
            rows = [{"memberNo": 101, "clubNo": 9}] if self.member_exists else []
            return Result(rows)
        if "INSERT INTO yk_distStaff" in query:
            self.inserts.append(params)
            return Result([])
        raise AssertionError(f"Unexpected query: {query}")


class FakeDistrictOfficerUpdateDB:
    def __init__(self, record_exists=True, duplicate=False, rank_allowed=True):
        self.record_exists = record_exists
        self.duplicate = duplicate
        self.rank_allowed = rank_allowed
        self.updates = []
        self.deletes = []

    def begin(self):
        return Transaction()

    async def execute(self, statement, params=None):
        query = str(statement)
        if "SELECT dstaffNo FROM yk_distStaff" in query and "FOR UPDATE" in query:
            return Result([(params["dstaffNo"],)] if self.record_exists else [])
        if "SELECT rankNo FROM yk_rank" in query:
            return Result([(params["rankNo"],)] if self.rank_allowed else [])
        if "SELECT memberNo, clubNo FROM yk_members" in query:
            return Result([{"memberNo": params["memberNo"], "clubNo": 9}])
        if "SELECT dstaffNo FROM yk_distStaff" in query:
            return Result([(1,)] if self.duplicate else [])
        if "UPDATE yk_distStaff" in query and "attrib = :deletedAttrib" in query:
            self.deletes.append(params)
            return Result([])
        if "UPDATE yk_distStaff" in query:
            self.updates.append(params)
            return Result([])
        raise AssertionError(f"Unexpected query: {query}")


class DistrictOfficerRegistrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_registration_adds_selected_member_to_period_and_redirects(self):
        db = FakeDistrictOfficerDB()

        response = await main.yk55_district_officer_insert(
            56, 101, 9, "card", db, 1
        )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(
            response.headers["location"],
            "/yk55membhist_tview/56?added=1",
        )
        self.assertEqual(
            db.inserts,
            [{
                "periodNo": 56,
                "clubNo": 9,
                "rankNo": 9,
                "memberNo": 101,
                "attrib": "1000010000",
            }],
        )

    async def test_registration_rejects_non_district_officer_role(self):
        db = FakeDistrictOfficerDB(rank_allowed=False)

        with self.assertRaises(HTTPException) as raised:
            await main.yk55_district_officer_insert(56, 101, 1, "list", db, 1)

        self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(db.inserts, [])

    async def test_update_changes_member_and_role_and_redirects(self):
        db = FakeDistrictOfficerUpdateDB()

        response = await main.yk55_district_officer_update(
            56, 321, 202, 15, "list", db, 1
        )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(
            response.headers["location"],
            "/yk55membhist_view/56?updated=1",
        )
        self.assertEqual(
            db.updates,
            [{
                "memberNo": 202,
                "clubNo": 9,
                "rankNo": 15,
                "dstaffNo": 321,
                "periodNo": 56,
                "attrib": "1000010000",
            }],
        )

    async def test_update_rejects_record_from_another_period(self):
        db = FakeDistrictOfficerUpdateDB(record_exists=False)

        with self.assertRaises(HTTPException) as raised:
            await main.yk55_district_officer_update(56, 321, 202, 15, "card", db, 1)

        self.assertEqual(raised.exception.status_code, 404)
        self.assertEqual(db.updates, [])

    async def test_update_rejects_duplicate_member_and_role(self):
        db = FakeDistrictOfficerUpdateDB(duplicate=True)

        with self.assertRaises(HTTPException) as raised:
            await main.yk55_district_officer_update(56, 321, 202, 15, "card", db, 1)

        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(db.updates, [])

    async def test_delete_marks_record_inactive_and_redirects(self):
        db = FakeDistrictOfficerUpdateDB()

        response = await main.yk55_district_officer_delete(56, 321, "card", db, 1)

        self.assertEqual(response.status_code, 303)
        self.assertEqual(
            response.headers["location"],
            "/yk55membhist_tview/56?deleted=1",
        )
        self.assertEqual(
            db.deletes,
            [{
                "deletedAttrib": "XXXUPXXXUP",
                "dstaffNo": 321,
                "periodNo": 56,
                "attrib": "1000010000",
            }],
        )

    def test_role_options_follow_requested_order_and_display_labels(self):
        source_titles = list(main.DISTRICT_OFFICER_ROLE_TITLES)
        rows = [
            SimpleNamespace(rankNo=index + 1, rankTitle=title)
            for index, title in reversed(list(enumerate(source_titles)))
        ]

        class FakeRankDB:
            async def execute(self, statement, params):
                return Result(rows)

        ranks = self._run(main._get_district_officer_ranks(FakeRankDB()))
        self.assertEqual(
            [rank["rankTitle"] for rank in ranks],
            list(main.DISTRICT_OFFICER_ROLE_TITLES.values()),
        )
        self.assertEqual(ranks[0]["rankTitle"], "전총재")
        self.assertEqual(
            ranks[4]["rankTitle"],
            "국제재단부이사장책임비서/총재특보/총재대외홍보특임",
        )

    def test_district_officers_query_uses_requested_display_order(self):
        class FakeDistrictStaffDB:
            query = None

            async def execute(self, statement, params):
                self.query = str(statement)
                return Result([])

        db = FakeDistrictStaffDB()
        self._run(main.funchub.get_dmemberhist_wname(56, db))

        ordered_titles = re.findall(
            r"WHEN '([^']+)' THEN (\d+)",
            db.query.split("order by CASE b.rankTitle", 1)[1],
        )
        self.assertEqual(
            [title for title, _ in ordered_titles],
            [
                "지구총재",
                "직전지구총재",
                "지구1부총재",
                "지구2부총재",
                "전지구총재",
                "지구감사",
                "전총장",
                "전감사",
                "사무총장",
                "재무총장",
                "총재대외홍보특임",
                "총재상임고문",
                "연수부원장",
                "총재고문",
                "지역위원장",
                "기획위원장",
                "사무부총장",
                "재무부총장",
                "공인가이딩",
                "지대위원장",
                "명예위원장",
            ],
        )

    def test_both_views_render_admin_registration_modal(self):
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/yk55membhist_tview/56",
            "raw_path": b"/yk55membhist_tview/56",
            "query_string": b"",
            "root_path": "",
            "headers": [],
            "client": ("test", 1),
            "server": ("test", 80),
            "router": main.app.router,
        }
        request = Request(scope)
        member = {
            "dstaffNo": 321,
            "rankNo": 15,
            "rankTitle": "지구감사",
            "memberNo": 202,
            "memberName": "테스트 회원",
            "clubName": "테스트 클럽",
        }
        for template_name, view_type in (
            ("yk55/yk55_memberhistview.html", "list"),
            ("yk55/yk55_memberhistview_tile.html", "card"),
        ):
            body = main.templates.TemplateResponse(
                template_name,
                {
                    "request": request,
                    "session": {"user_Role": "ADMIN"},
                    "membs": [member],
                    "periodno": 56,
                    "periods": [],
                    "district_officer_ranks": [
                        {"rankNo": 8, "rankTitle": "총재고문"}
                    ],
                    "view_type": view_type,
                },
            ).body
            self.assertIn(b'data-target="#districtOfficerModal"', body)
            self.assertIn(b'id="districtOfficerSearchButton"', body)
            self.assertIn(f'name="viewType" value="{view_type}"'.encode(), body)
            self.assertIn(b'data-mode="edit"', body)
            self.assertIn(b"yk55membhist_delete/56/321", body)
            self.assertIn(b"yk55membhist_update/56/", body)

    def test_district_officer_card_uses_shared_versioned_member_photo_api(self):
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/yk55membhist_tview/56",
            "raw_path": b"/yk55membhist_tview/56",
            "query_string": b"",
            "root_path": "",
            "headers": [],
            "client": ("test", 1),
            "server": ("test", 80),
            "router": main.app.router,
        }
        response = main.templates.TemplateResponse(
            "yk55/yk55_memberhistview_tile.html",
            {
                "request": Request(scope),
                "session": {},
                "membs": [{
                    "rankTitle": "전총재",
                    "memberNo": 101,
                    "memberName": "테스트 회원",
                    "clubName": "테스트 클럽",
                }],
                "periodno": 56,
                "periods": [],
                "district_officer_ranks": [],
                "view_type": "card",
            },
        )

        self.assertIn(b"/api/member_photo/101/56", response.body)

    @staticmethod
    def _run(coroutine):
        import asyncio

        return asyncio.run(coroutine)
