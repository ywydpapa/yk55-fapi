import os
import unittest
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from PIL import Image
from starlette.datastructures import UploadFile

os.environ.setdefault("dburl", "mysql+asyncmy://test:test@localhost/test")
os.environ.setdefault("cperiod", "56")
import main


class Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeInsertResult:
    lastrowid = 501


class FakeDB:
    def __init__(self):
        self.inserted = None

    def begin(self):
        return Transaction()

    async def execute(self, statement, params):
        self.inserted = params
        return FakeInsertResult()


class FakeUpdateDB:
    def __init__(self):
        self.updated = None

    async def execute(self, statement, params):
        self.updated = params

    async def commit(self):
        pass


def build_png():
    output = BytesIO()
    Image.new("RGB", (4, 4), color="blue").save(output, format="PNG")
    return output.getvalue()


class GreetingPhotoTests(unittest.IsolatedAsyncioTestCase):
    async def test_document_creation_saves_uploaded_photo_for_new_document(self):
        photo = UploadFile(
            filename="greeting.png",
            file=BytesIO(build_png()),
            headers={"content-type": "image/png"},
        )
        form_data = {
            "dtitle": "축사 제목",
            "dcontent": "<p>축사 내용</p>",
            "dtype": "GREET",
            "docevent": "12",
            "dwriter1": "총재",
            "dwriter2": "홍길동",
            "docPhoto": photo,
        }
        request = SimpleNamespace(
            form=AsyncMock(return_value=form_data),
            session={"user_No": 7},
        )
        db = FakeDB()

        with patch.object(main, "save_docPhoto", new_callable=AsyncMock) as save_photo:
            response = await main.insertdoc(request, db, 7)

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/yk55greet")
        self.assertEqual(db.inserted["doctitle"], "축사 제목")
        self.assertEqual(db.inserted["userno"], 7)
        save_photo.assert_awaited_once()
        self.assertEqual(save_photo.await_args.args[1], 501)

    async def test_document_creation_requires_an_uploaded_photo(self):
        request = SimpleNamespace(
            form=AsyncMock(return_value={
                "dtitle": "회고사",
                "dcontent": "<p>내용</p>",
                "dtype": "MEMOR",
                "docevent": "0",
                "dwriter1": "직책",
                "dwriter2": "작성자",
            }),
            session={"user_No": 7},
        )

        with self.assertRaises(HTTPException) as raised:
            await main.insertdoc(request, FakeDB(), 7)

        self.assertEqual(raised.exception.status_code, 400)

    async def test_document_creation_rejects_non_image_upload(self):
        photo = UploadFile(
            filename="notes.txt",
            file=BytesIO(b"not an image"),
            headers={"content-type": "text/plain"},
        )
        request = SimpleNamespace(
            form=AsyncMock(return_value={"docPhoto": photo}),
            session={"user_No": 7},
        )

        with self.assertRaises(HTTPException) as raised:
            await main.insertdoc(request, FakeDB(), 7)

        self.assertEqual(raised.exception.status_code, 400)

    async def test_document_update_saves_replacement_photo_with_document(self):
        photo = UploadFile(
            filename="updated.png",
            file=BytesIO(build_png()),
            headers={"content-type": "image/png"},
        )
        request = SimpleNamespace(
            form=AsyncMock(return_value={
                "dtitle": "수정된 제목",
                "dcontent": "<p>수정된 내용</p>",
                "dtype": "MEMOR",
                "docevent": "12",
                "dwriter1": "직책",
                "dwriter2": "작성자",
                "docPhoto": photo,
            }),
        )
        db = FakeUpdateDB()

        with patch.object(main, "save_docPhoto", new_callable=AsyncMock) as save_photo:
            response = await main.updatedoc(request, 501, db, 7)

        self.assertEqual(response.status_code, 303)
        self.assertEqual(db.updated["doctitle"], "수정된 제목")
        self.assertEqual(db.updated["doctype"], "MEMOR")
        save_photo.assert_awaited_once()
        self.assertEqual(save_photo.await_args.args[1], 501)

    async def test_document_update_preserves_photo_when_no_replacement_is_selected(self):
        request = SimpleNamespace(
            form=AsyncMock(return_value={
                "dtitle": "수정된 제목",
                "dcontent": "<p>수정된 내용</p>",
                "dtype": "GREET",
                "docevent": "0",
                "dwriter1": "직책",
                "dwriter2": "작성자",
            }),
        )

        with patch.object(main, "save_docPhoto", new_callable=AsyncMock) as save_photo:
            response = await main.updatedoc(request, 501, FakeUpdateDB(), 7)

        self.assertEqual(response.status_code, 303)
        save_photo.assert_not_awaited()

    async def test_document_update_rejects_invalid_replacement_photo_before_update(self):
        photo = UploadFile(
            filename="notes.txt",
            file=BytesIO(b"not an image"),
            headers={"content-type": "text/plain"},
        )
        request = SimpleNamespace(
            form=AsyncMock(return_value={"docPhoto": photo}),
        )
        db = FakeUpdateDB()

        with self.assertRaises(HTTPException) as raised:
            await main.updatedoc(request, 501, db, 7)

        self.assertEqual(raised.exception.status_code, 400)
        self.assertIsNone(db.updated)
