import os
import unittest
from unittest.mock import patch

os.environ["dburl"] = "mysql+asyncmy://test:test@localhost/test"
os.environ.setdefault("cperiod", "56")
import main


class MemberPhotoTests(unittest.IsolatedAsyncioTestCase):
    async def test_uses_requested_period_photo_when_available(self):
        expected_path = "static/img/members/mphoto_101_h56.png"
        with (
            patch.object(main.os.path, "exists", side_effect=lambda path: path == expected_path),
            patch.object(main, "FileResponse", side_effect=lambda path: path),
        ):
            result = await main.get_member_photo(101, 56)

        self.assertEqual(result, expected_path)

    async def test_uses_base_photo_when_requested_period_photo_is_missing(self):
        base_path = "static/img/members/mphoto_101.png"
        existing_paths = {
            "static/img/members/mphoto_101_h55.png",
            base_path,
        }
        with (
            patch.object(main.os.path, "exists", side_effect=lambda path: path in existing_paths),
            patch.object(main, "FileResponse", side_effect=lambda path: path),
        ):
            result = await main.get_member_photo(101, 56)

        self.assertEqual(result, base_path)

    async def test_uses_global_default_when_member_photos_are_missing(self):
        with (
            patch.object(main.os.path, "exists", return_value=False),
            patch.object(main, "FileResponse", side_effect=lambda path: path),
        ):
            result = await main.get_member_photo(101, 56)

        self.assertEqual(result, "static/img/defaultphoto.png")
