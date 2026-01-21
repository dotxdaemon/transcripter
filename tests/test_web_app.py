# ABOUTME: Tests the HTML website for searching stored transcripts.
# ABOUTME: Uses a temporary SQLite database to validate rendered output.

import sqlite3

import app
import web_app


def test_index_shows_search_results(tmp_path):
    db_path = tmp_path / "videos.db"
    connection = sqlite3.connect(db_path)
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
    connection.close()

    website = web_app.create_app(str(db_path))
    client = website.test_client()

    response = client.get("/?q=Hello")

    assert response.status_code == 200
    assert b"Sample" in response.data
    assert b"Hello world" in response.data
