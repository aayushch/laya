# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Tests for GET /events/day — the timeline view's per-day event shape."""

import json

import pytest
from httpx import ASGITransport, AsyncClient


async def _insert_event(db, event_id: str, timestamp: str, platform: str = "jira",
                        space_id: str | None = None, subject_type: str = "ticket",
                        subject_id: str = "BUG-1", subject_title: str = "Test",
                        raw_event_type: str = "issue_assigned",
                        metadata: dict | None = None):
    await db.execute(
        """INSERT INTO events
           (event_id, timestamp, source_platform, source_raw_event_type,
            subject_type, subject_id, subject_title, actor_name, actor_email,
            content_body, content_metadata, raw_json, processed, filtered, space_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (event_id, timestamp, platform, raw_event_type, subject_type, subject_id,
         subject_title, "Sarah", "sarah@company.com", "body",
         json.dumps(metadata or {}), "{}", True, False, space_id),
    )
    await db.commit()


async def _get(path: str):
    from laya.main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path)


@pytest.mark.asyncio
class TestDayEventBuckets:
    async def test_empty_day(self, db):
        resp = await _get("/events/day?date=2026-05-02")
        assert resp.status_code == 200
        data = resp.json()
        assert data == {
            "date": "2026-05-02",
            "total": 0,
            "bucket_minutes": 30,
            "platforms": {},
            "buckets": [],
            "meetings": [],
        }

    async def test_counts_and_buckets(self, db):
        # 09:10 and 09:25 share the 09:00 bucket; 09:40 opens the 09:30 one.
        await _insert_event(db, "e1", "2026-05-02 09:10:00", platform="github")
        await _insert_event(db, "e2", "2026-05-02 09:25:00", platform="github")
        await _insert_event(db, "e3", "2026-05-02 09:40:00", platform="gmail")

        data = (await _get("/events/day?date=2026-05-02")).json()

        assert data["total"] == 3
        assert data["platforms"] == {"github": 2, "gmail": 1}
        assert data["buckets"] == [
            {"start_minute": 540, "count": 2},
            {"start_minute": 570, "count": 1},
        ]

    async def test_excludes_other_days(self, db):
        await _insert_event(db, "e1", "2026-05-01 23:59:00")
        await _insert_event(db, "e2", "2026-05-02 00:01:00")
        await _insert_event(db, "e3", "2026-05-03 00:00:00")

        data = (await _get("/events/day?date=2026-05-02")).json()
        assert data["total"] == 1
        assert data["buckets"] == [{"start_minute": 0, "count": 1}]

    async def test_counts_filtered_and_unprocessed_events(self, db):
        """The heat rail's whole point is the mass that never became a card."""
        await _insert_event(db, "e1", "2026-05-02 10:00:00")
        await db.execute(
            "UPDATE events SET processing_status = 'filtered', filtered = TRUE, "
            "processed = FALSE WHERE event_id = 'e1'"
        )
        await db.commit()

        data = (await _get("/events/day?date=2026-05-02")).json()
        assert data["total"] == 1

    async def test_space_filter(self, db):
        await _insert_event(db, "e1", "2026-05-02 09:00:00", space_id="default")
        await _insert_event(db, "e2", "2026-05-02 09:00:00", space_id="work")

        data = (await _get("/events/day?date=2026-05-02&space_id=work")).json()
        assert data["total"] == 1

        both = (await _get("/events/day?date=2026-05-02&space_id=work,default")).json()
        assert both["total"] == 2

    async def test_custom_bucket_size(self, db):
        await _insert_event(db, "e1", "2026-05-02 09:10:00")
        await _insert_event(db, "e2", "2026-05-02 09:50:00")

        data = (await _get("/events/day?date=2026-05-02&bucket_minutes=60")).json()
        assert data["bucket_minutes"] == 60
        assert data["buckets"] == [{"start_minute": 540, "count": 2}]

    async def test_timezone_shifts_day_window_and_buckets(self, db):
        """With tz, the day is the LOCAL day and buckets are local clock time."""
        # 2026-05-02 03:00 UTC == 2026-05-01 20:00 in Los Angeles (UTC-7).
        await _insert_event(db, "e1", "2026-05-02 03:00:00")
        # 2026-05-02 20:00 UTC == 2026-05-02 13:00 local.
        await _insert_event(db, "e2", "2026-05-02 20:00:00")

        data = (await _get("/events/day?date=2026-05-02&tz=America/Los_Angeles")).json()
        assert data["total"] == 1
        assert data["buckets"] == [{"start_minute": 780, "count": 1}]  # 13:00

    async def test_invalid_timezone_falls_back_to_utc(self, db):
        await _insert_event(db, "e1", "2026-05-02 09:00:00")
        data = (await _get("/events/day?date=2026-05-02&tz=Not/AZone")).json()
        assert data["total"] == 1
        assert data["buckets"] == [{"start_minute": 540, "count": 1}]


@pytest.mark.asyncio
class TestDayMeetings:
    async def test_meeting_positioned_by_cal_start_not_ingest_time(self, db):
        """Calendar workflows stamp events at sync time — the meeting is at 14:00."""
        await _insert_event(
            db, "evt_cal_1", "2026-05-02 06:00:00", platform="calendar",
            subject_type="meeting", subject_id="cal-1", subject_title="1:1 Sarah",
            raw_event_type="event_created",
            metadata={
                "cal_start_time": "2026-05-02T14:00:00+00:00",
                "cal_end_time": "2026-05-02T14:30:00+00:00",
                "cal_attendees": ["a@x.com", "b@x.com"],
                "cal_location": "Zoom",
            },
        )

        data = (await _get("/events/day?date=2026-05-02")).json()
        assert len(data["meetings"]) == 1
        meeting = data["meetings"][0]
        assert meeting["title"] == "1:1 Sarah"
        assert meeting["start"] == "2026-05-02T14:00:00+00:00"
        assert meeting["end"] == "2026-05-02T14:30:00+00:00"
        assert meeting["platform"] == "calendar"
        assert meeting["all_day"] is False
        assert meeting["cancelled"] is False
        assert meeting["attendee_count"] == 2
        assert meeting["location"] == "Zoom"

    async def test_meeting_on_another_day_excluded(self, db):
        await _insert_event(
            db, "evt_cal_1", "2026-05-02 06:00:00", platform="calendar",
            subject_type="meeting", subject_id="cal-1", subject_title="Tomorrow",
            metadata={"cal_start_time": "2026-05-03T09:00:00+00:00"},
        )
        data = (await _get("/events/day?date=2026-05-02")).json()
        assert data["meetings"] == []

    async def test_latest_event_wins_per_calendar_entry(self, db):
        """An updated meeting renders once, in its newest state."""
        await _insert_event(
            db, "evt_cal_1", "2026-05-02 06:00:00", platform="calendar",
            subject_type="meeting", subject_id="cal-1", subject_title="Standup",
            raw_event_type="event_created",
            metadata={"cal_start_time": "2026-05-02T09:30:00+00:00"},
        )
        await _insert_event(
            db, "evt_cal_2", "2026-05-02 08:00:00", platform="calendar",
            subject_type="meeting", subject_id="cal-1", subject_title="Standup (moved)",
            raw_event_type="event_cancelled",
            metadata={"cal_start_time": "2026-05-02T09:30:00+00:00"},
        )

        data = (await _get("/events/day?date=2026-05-02")).json()
        assert len(data["meetings"]) == 1
        assert data["meetings"][0]["title"] == "Standup (moved)"
        assert data["meetings"][0]["cancelled"] is True

    async def test_all_day_meeting_flagged(self, db):
        await _insert_event(
            db, "evt_cal_1", "2026-05-02 06:00:00", platform="outlook",
            subject_type="meeting", subject_id="cal-1", subject_title="Company offsite",
            metadata={"cal_start_time": "2026-05-02", "cal_end_time": "2026-05-03"},
        )
        data = (await _get("/events/day?date=2026-05-02")).json()
        assert data["meetings"][0]["all_day"] is True

    async def test_meeting_matched_in_local_timezone(self, db):
        """A 2026-05-03T02:00Z meeting is still 'May 2' in Los Angeles."""
        await _insert_event(
            db, "evt_cal_1", "2026-05-02 06:00:00", platform="calendar",
            subject_type="meeting", subject_id="cal-1", subject_title="Late sync",
            metadata={"cal_start_time": "2026-05-03T02:00:00+00:00"},
        )
        data = (await _get("/events/day?date=2026-05-02&tz=America/Los_Angeles")).json()
        assert len(data["meetings"]) == 1
        assert data["meetings"][0]["title"] == "Late sync"

    async def test_meeting_space_filter(self, db):
        await _insert_event(
            db, "evt_cal_1", "2026-05-02 06:00:00", platform="calendar",
            subject_type="meeting", subject_id="cal-1", subject_title="Work sync",
            space_id="work", metadata={"cal_start_time": "2026-05-02T10:00:00+00:00"},
        )
        data = (await _get("/events/day?date=2026-05-02&space_id=default")).json()
        assert data["meetings"] == []

    async def test_malformed_metadata_is_skipped(self, db):
        await _insert_event(
            db, "evt_cal_1", "2026-05-02 06:00:00", platform="calendar",
            subject_type="meeting", subject_id="cal-1", subject_title="Broken",
            metadata={"cal_start_time": "not-a-timestamp-2026-05-02"},
        )
        resp = await _get("/events/day?date=2026-05-02")
        assert resp.status_code == 200
        assert resp.json()["meetings"] == []
