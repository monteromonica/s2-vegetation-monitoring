from datetime import datetime, timezone

import pystac

from s2veg.stac import deduplicate_acquisitions


def _item(item_id, day, baseline, platform="sentinel-2a", updated="2022-01-01T00:00:00Z"):
    return pystac.Item(
        item_id, None, None, datetime(2021, 3, day, 11, 0, tzinfo=timezone.utc),
        {"platform": platform, "s2:processing_baseline": baseline, "updated": updated},
    )


def test_keeps_latest_baseline_when_same_acquisition_was_reprocessed():
    original = _item("S2A_30TVK_20210302_0_L2A", 2, "03.00")
    reprocessed = _item("S2A_30TVK_20210302_1_L2A", 2, "05.00")
    other_day = _item("S2A_30TVK_20210307_0_L2A", 7, "03.00")
    kept = deduplicate_acquisitions([original, reprocessed, other_day])
    assert [i.id for i in kept] == ["S2A_30TVK_20210302_1_L2A", "S2A_30TVK_20210307_0_L2A"]


def test_different_satellites_on_same_day_are_not_duplicates():
    a = _item("A", 2, "05.00", platform="sentinel-2a")
    b = _item("B", 2, "05.00", platform="sentinel-2b")
    assert len(deduplicate_acquisitions([a, b])) == 2


def test_output_is_sorted_by_time():
    items = [_item("late", 20, "05.00"), _item("early", 3, "05.00")]
    assert [i.id for i in deduplicate_acquisitions(items)] == ["early", "late"]
