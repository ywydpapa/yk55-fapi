import os
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
        for template_name, view_type in (
            ("yk55/yk55_memberhistview.html", "list"),
            ("yk55/yk55_memberhistview_tile.html", "card"),
        ):
            body = main.templates.TemplateResponse(
                template_name,
                {
                    "request": request,
                    "session": {"user_Role": "ADMIN"},
                    "membs": [],
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
