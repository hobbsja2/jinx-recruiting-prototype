from types import SimpleNamespace

from app.main import dedupe_school_rows, major_group_for_value


def test_business_details_map_to_business_group():
    assert major_group_for_value("Business Administration") == "Business"
    assert major_group_for_value("Financial Planning") == "Business"
    assert major_group_for_value("Marketing") == "Business"
    assert major_group_for_value("Computer Science") != "Business"


def test_dedupe_keeps_one_row_per_college():
    college = SimpleNamespace(id=42, name="Sample U")
    rows = [
        (college, SimpleNamespace(position="IF", class_year=2026), 100),
        (college, SimpleNamespace(position="OF", class_year=2026), 120),
    ]

    deduped = dedupe_school_rows(rows)

    assert len(deduped) == 1
    assert deduped[0][0].id == 42
    assert deduped[0][2] == 120
