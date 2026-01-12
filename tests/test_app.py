# ABOUTME: Tests core parsing, formatting, and search behaviors.
# ABOUTME: Uses a temporary database to validate query flows.

import sqlite3

import app


def test_parse_subtitle_text_vtt():
    content = """WEBVTT

00:00:01.000 --> 00:00:02.000
Hello world.

00:00:03.500 --> 00:00:05.000
Second line.
"""
    cues = app.parse_subtitle_text(content)
    assert cues == [(1.0, "Hello world."), (3.5, "Second line.")]


def test_parse_subtitle_text_srt():
    content = """1
00:00:01,000 --> 00:00:02,000
Hello again.

2
00:00:03,500 --> 00:00:05,000
Second cue.
"""
    cues = app.parse_subtitle_text(content)
    assert cues == [(1.0, "Hello again."), (3.5, "Second cue.")]


def test_format_timestamp():
    assert app.format_timestamp(872.0) == "14:32"
    assert app.format_timestamp(3723.0) == "1:02:03"


def test_search_transcripts_groups_by_video():
    connection = sqlite3.connect(":memory:")
    app.init_db(connection)
    app.insert_video(
        connection,
        {
            "id": "video-1",
            "title": "Sample",
            "channel": "Channel",
            "thumbnail_url": "http://example.com/thumb.jpg",
            "publish_date": "2024-01-01",
        },
    )
    app.insert_transcripts(
        connection,
        "video-1",
        [(12.0, "Hello world"), (45.0, "Another line")],
    )

    results = app.search_transcripts(connection, "Hello")

    assert "video-1" in results
    assert results["video-1"]["video"]["title"] == "Sample"
    assert results["video-1"]["matches"][0]["start_time"] == 12.0
