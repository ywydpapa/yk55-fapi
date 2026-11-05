from datetime import datetime
from io import BytesIO
import unittest

from openpyxl import Workbook

from event_import import (
    EVENT_IMPORT_COLUMNS,
    EventImportError,
    event_duplicate_key,
    parse_event_workbook,
    validate_event_rows,
)


def build_workbook(rows):
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append([header for header, _ in EVENT_IMPORT_COLUMNS])
    for row in rows:
        worksheet.append(row)
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


class EventImportTests(unittest.TestCase):
    def test_parse_and_validate_event_workbook_row(self):
        contents = build_workbook([[
            "정기 행사", "DISTE", datetime(2026, 11, 5, 9), datetime(2026, 11, 5, 18),
            12, "본부", 15000, 20,
        ]])

        parsed = parse_event_workbook(contents)
        rows = validate_event_rows(
            parsed,
            valid_periods={12},
            location_ids_by_title={"본부": 3, "장소 미정": 1},
        )

        self.assertEqual(rows[0]["error"], "")
        self.assertEqual(rows[0]["eventFrom"], "2026-11-05 09:00:00")
        self.assertEqual(rows[0]["periodNo"], 12)
        self.assertEqual(rows[0]["locationNo"], 3)
        self.assertEqual(rows[0]["eventCost"], 15000)

    def test_missing_required_column_is_rejected(self):
        workbook = Workbook()
        workbook.active.append(["행사명", "시작일시"])
        output = BytesIO()
        workbook.save(output)
        workbook.close()

        with self.assertRaisesRegex(EventImportError, "필수 열"):
            parse_event_workbook(output.getvalue())

    def test_invalid_period_and_location_are_reported(self):
        rows = validate_event_rows(
            [{
                "rowNumber": 2,
                "eventTitle": "행사",
                "eventType": "DISTE",
                "eventFrom": "2026-11-05 09:00",
                "eventTo": "2026-11-05 18:00",
                "periodNo": 99,
                "locationTitle": "없는 장소",
            }],
            valid_periods={12},
            location_ids_by_title={"장소 미정": 1},
        )

        self.assertIn("회기번호", rows[0]["error"])
        self.assertIn("등록되지 않은 장소", rows[0]["error"])

    def test_non_district_event_type_is_rejected(self):
        rows = validate_event_rows(
            [{
                "eventTitle": "행사",
                "eventType": "CLUBE",
                "eventFrom": "2026-11-05 09:00",
                "eventTo": "2026-11-05 18:00",
                "periodNo": 12,
            }],
            valid_periods={12},
            location_ids_by_title={"장소 미정": 1},
        )

        self.assertIn("DISTE만", rows[0]["error"])

    def test_duplicate_key_ignores_title_case_and_datetime_format(self):
        self.assertEqual(
            event_duplicate_key({
                "eventTitle": "  Annual Meeting ",
                "eventFrom": "2026-11-05T09:00",
                "periodNo": 12,
            }),
            event_duplicate_key({
                "eventTitle": "annual meeting",
                "eventFrom": datetime(2026, 11, 5, 9),
                "periodNo": 12,
            }),
        )


if __name__ == "__main__":
    unittest.main()
