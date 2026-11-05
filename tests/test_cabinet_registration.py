import unittest
import os

from fastapi import HTTPException
from starlette.requests import Request

os.environ.setdefault("dburl", "mysql+asyncmy://test:test@127.0.0.1/test")
os.environ.setdefault("cperiod", "56")
import main


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


class FakeDB:
    def __init__(self, former_exists=False, allowed_rank=True):
        self.former_exists = former_exists
        self.allowed_rank = allowed_rank
        self.inserts = []

    def begin(self):
        return Transaction()

    async def execute(self, statement, params=None):
        query = str(statement)
        if "SELECT periodNo FROM yk_period" in query:
            return Result([(56,)])
        if "SELECT rankNo FROM yk_rank" in query and "rankTitle IN" in query:
            return Result([(params["rankNo"],)] if self.allowed_rank else [])
        if "SELECT memberNo, clubNo FROM yk_members" in query:
            return Result([{"memberNo": 101, "clubNo": 9}])
        if "SELECT s.memberNo, s.clubNo" in query:
            return Result([{"memberNo": 202, "clubNo": 10}])
        if "SELECT rankNo FROM yk_rank" in query:
            return Result([(12,)])
        if "SELECT dstaffNo FROM yk_distStaff" in query:
            return Result([(1,)] if self.former_exists else [])
        if "INSERT INTO yk_distStaff" in query:
            self.inserts.append(params)
            return Result([])
        raise AssertionError(f"Unexpected query: {query}")


class FakeUpdateDB:
    def __init__(self, record_exists=True, duplicate=False, allowed_rank=True):
        self.record_exists = record_exists
        self.duplicate = duplicate
        self.allowed_rank = allowed_rank
        self.updates = []

    def begin(self):
        return Transaction()

    async def execute(self, statement, params=None):
        query = str(statement)
        if "SELECT dstaffNo FROM yk_distStaff" in query and "FOR UPDATE" in query:
            return Result([(params["dstaffNo"],)] if self.record_exists else [])
        if "SELECT rankNo FROM yk_rank" in query:
            return Result([(params["rankNo"],)] if self.allowed_rank else [])
        if "SELECT memberNo, clubNo FROM yk_members" in query:
            return Result([{"memberNo": params["memberNo"], "clubNo": 9}])
        if "SELECT dstaffNo FROM yk_distStaff" in query:
            return Result([(1,)] if self.duplicate else [])
        if "UPDATE yk_distStaff" in query:
            self.updates.append(params)
            return Result([])
        raise AssertionError(f"Unexpected query: {query}")


class CabinetRegistrationTests(unittest.IsolatedAsyncioTestCase):
    def test_card_view_renders_cabinet_in_hierarchy_order(self):
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/yk55cabhist_tview/56",
            "raw_path": b"/yk55cabhist_tview/56",
            "query_string": b"",
            "root_path": "",
            "headers": [],
            "client": ("test", 1),
            "server": ("test", 80),
            "router": main.app.router,
        }
        request = Request(scope)
        ranks = [
            "재무부총장", "재무총장", "지구 제2부총재", "사무부총장",
            "직전지구총재", "지구총재", "기획총장", "수석부총장",
            "사무총장", "지구 제1부총재",
        ]
        members = [
            {
                "rankTitle": rank,
                "memberNo": index,
                "memberName": f"회원{index}",
                "clubName": "클럽",
            }
            for index, rank in enumerate(ranks, start=1)
        ]

        response = main.templates.TemplateResponse(
            "yk55/yk55_cabhistview_tile.html",
            {
                "request": request,
                "session": {"user_Role": "ADMIN"},
                "membs": members,
                "periodno": 56,
                "cabinet_ranks": [],
                "cabinet_edit_ranks": [
                    {"rankNo": 12, "rankTitle": "직전지구총재", "rankSourceTitle": "직전지구총재"}
                ],
            },
        )
        body = response.body
        role_positions = [
            body.index(role.encode("utf-8"))
            for role in (
                "지구총재", "직전지구총재",
                "지구 제1부총재", "지구 제2부총재",
                "사무총장", "재무총장", "기획총장",
                "수석부총장", "사무부총장", "재무부총장",
            )
        ]

        self.assertEqual(role_positions, sorted(role_positions))
        self.assertLess(
            body.index("수석부총장".encode("utf-8")),
            body.index("사무부총장".encode("utf-8")),
        )
        self.assertLess(
            body.index("재무총장".encode("utf-8")),
            body.index("기획총장".encode("utf-8")),
        )
        self.assertIn(b'data-mode="edit"', body)
        self.assertIn(b'data-target="#cabinetMemberModal"', body)
        self.assertLess(
            body.index(b"/static/vendor/jquery/jquery.min.js"),
            body.index(b"searchButton.addEventListener"),
        )

    async def test_update_changes_existing_cabinet_member_and_role(self):
        db = FakeUpdateDB()

        response = await main.yk55_cabinet_update(
            None, 56, 321, 202, 30, "card", db, 1
        )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/yk55cabhist_tview/56?updated=1")
        self.assertEqual(
            db.updates,
            [{
                "memberNo": 202,
                "clubNo": 9,
                "rankNo": 30,
                "dstaffNo": 321,
                "periodNo": 56,
                "attrib": "1000010000",
            }],
        )

    async def test_update_rejects_duplicate_role_entry(self):
        db = FakeUpdateDB(duplicate=True)

        with self.assertRaises(HTTPException) as raised:
            await main.yk55_cabinet_update(None, 56, 321, 202, 30, "list", db, 1)

        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(db.updates, [])

    async def test_update_rejects_record_from_another_period(self):
        db = FakeUpdateDB(record_exists=False)

        with self.assertRaises(HTTPException) as raised:
            await main.yk55_cabinet_update(None, 56, 321, 202, 30, "card", db, 1)

        self.assertEqual(raised.exception.status_code, 404)
        self.assertEqual(db.updates, [])

    async def test_registering_cabinet_member_adds_previous_governor(self):
        db = FakeDB()

        response = await main.yk55_cabinet_insert(
            None, 56, 101, 4, "card", db, 1
        )

        self.assertEqual(response.status_code, 303)
        self.assertTrue(response.headers["location"].startswith("/yk55cabhist_tview/56?"))
        self.assertIn("previous_added=1", response.headers["location"])
        self.assertEqual([row["rankNo"] for row in db.inserts], [4, 12])
        self.assertEqual(db.inserts[1]["periodNo"], 56)
        self.assertEqual(db.inserts[1]["memberNo"], 202)

    async def test_existing_previous_governor_is_not_added_again(self):
        db = FakeDB(former_exists=True)

        response = await main.yk55_cabinet_insert(
            None, 56, 101, 4, "list", db, 1
        )

        self.assertTrue(response.headers["location"].startswith("/yk55cabhist_view/56?"))
        self.assertIn("previous_added=0", response.headers["location"])
        self.assertEqual(len(db.inserts), 1)

    async def test_cannot_register_unapproved_role(self):
        db = FakeDB(allowed_rank=False)

        with self.assertRaises(HTTPException) as raised:
            await main.yk55_cabinet_insert(None, 56, 101, 14, "card", db, 1)

        self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(db.inserts, [])


if __name__ == "__main__":
    unittest.main()
