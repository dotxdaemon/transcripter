# ABOUTME: Streamlit app for ingesting YouTube transcripts into a local search index.
# ABOUTME: Provides parsing, indexing, and search helpers for the knowledge base.

import os
import re
import sqlite3
import tempfile
from datetime import datetime
from functools import lru_cache
from typing import Dict, Iterable, List, Optional, Tuple


DB_PATH = "video_search.db"


def init_db(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS videos (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            channel TEXT,
            thumbnail_url TEXT,
            publish_date TEXT
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS transcripts (
            video_id TEXT NOT NULL,
            start_time REAL NOT NULL,
            text TEXT NOT NULL,
            FOREIGN KEY (video_id) REFERENCES videos(id)
        )
        """
    )
    connection.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS transcripts_fts
        USING fts5(text, content='transcripts', content_rowid='rowid')
        """
    )
    connection.execute(
        """
        CREATE TRIGGER IF NOT EXISTS transcripts_ai
        AFTER INSERT ON transcripts
        BEGIN
            INSERT INTO transcripts_fts(rowid, text)
            VALUES (new.rowid, new.text);
        END
        """
    )
    connection.execute(
        """
        CREATE TRIGGER IF NOT EXISTS transcripts_ad
        AFTER DELETE ON transcripts
        BEGIN
            INSERT INTO transcripts_fts(transcripts_fts, rowid, text)
            VALUES('delete', old.rowid, old.text);
        END
        """
    )
    connection.execute(
        """
        CREATE TRIGGER IF NOT EXISTS transcripts_au
        AFTER UPDATE ON transcripts
        BEGIN
            INSERT INTO transcripts_fts(transcripts_fts, rowid, text)
            VALUES('delete', old.rowid, old.text);
            INSERT INTO transcripts_fts(rowid, text)
            VALUES (new.rowid, new.text);
        END
        """
    )
    connection.commit()


def insert_video(connection: sqlite3.Connection, video: Dict[str, Optional[str]]) -> bool:
    cursor = connection.execute("SELECT 1 FROM videos WHERE id = ?", (video["id"],))
    if cursor.fetchone():
        return False
    connection.execute(
        """
        INSERT INTO videos (id, title, channel, thumbnail_url, publish_date)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            video["id"],
            video["title"],
            video.get("channel"),
            video.get("thumbnail_url"),
            video.get("publish_date"),
        ),
    )
    connection.commit()
    return True


def insert_transcripts(
    connection: sqlite3.Connection,
    video_id: str,
    transcripts: Iterable[Tuple[float, str]],
) -> None:
    connection.executemany(
        """
        INSERT INTO transcripts (video_id, start_time, text)
        VALUES (?, ?, ?)
        """,
        ((video_id, start_time, text) for start_time, text in transcripts),
    )
    connection.commit()


def parse_subtitle_text(content: str) -> List[Tuple[float, str]]:
    cues: List[Tuple[float, str]] = []
    current_start: Optional[float] = None
    current_text: List[str] = []
    timing_pattern = re.compile(
        r"(?P<start>\d{2}:\d{2}:\d{2}[\.,]\d{3})\s*-->\s*\d{2}:\d{2}:\d{2}[\.,]\d{3}"
    )
    for raw_line in content.splitlines():
        line = raw_line.strip("\ufeff").strip()
        if not line:
            if current_start is not None and current_text:
                cues.append((current_start, " ".join(current_text).strip()))
            current_start = None
            current_text = []
            continue
        if line.upper() == "WEBVTT":
            continue
        timing_match = timing_pattern.match(line)
        if timing_match:
            if current_start is not None and current_text:
                cues.append((current_start, " ".join(current_text).strip()))
                current_text = []
            current_start = parse_timestamp(timing_match.group("start"))
            continue
        if current_start is None:
            continue
        current_text.append(line)
    if current_start is not None and current_text:
        cues.append((current_start, " ".join(current_text).strip()))
    return cues


def parse_timestamp(value: str) -> float:
    clean_value = value.replace(",", ".")
    hours, minutes, seconds = clean_value.split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def format_timestamp(seconds: float) -> str:
    total_seconds = int(seconds)
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    remaining_seconds = total_seconds % 60
    if hours:
        return f"{hours}:{minutes:02d}:{remaining_seconds:02d}"
    return f"{minutes:02d}:{remaining_seconds:02d}"


def search_transcripts(
    connection: sqlite3.Connection, query: str
) -> Dict[str, Dict[str, object]]:
    if not query.strip():
        return {}
    cursor = connection.execute(
        """
        SELECT
            t.video_id,
            t.start_time,
            snippet(transcripts_fts, 0, '', '', '...', 10) AS snippet,
            v.title,
            v.channel,
            v.thumbnail_url,
            v.publish_date
        FROM transcripts_fts
        JOIN transcripts t ON t.rowid = transcripts_fts.rowid
        JOIN videos v ON v.id = t.video_id
        WHERE transcripts_fts MATCH ?
        ORDER BY bm25(transcripts_fts)
        """,
        (query,),
    )
    grouped: Dict[str, Dict[str, object]] = {}
    for row in cursor.fetchall():
        video_id = row[0]
        grouped.setdefault(
            video_id,
            {
                "video": {
                    "id": video_id,
                    "title": row[3],
                    "channel": row[4],
                    "thumbnail_url": row[5],
                    "publish_date": row[6],
                },
                "matches": [],
            },
        )
        grouped[video_id]["matches"].append(
            {"start_time": row[1], "text": row[2]}
        )
    return grouped


def parse_publish_date(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y%m%d").strftime("%Y-%m-%d")
    except ValueError:
        return value


def load_video_urls(url: str) -> List[str]:
    import yt_dlp

    options = {
        "quiet": True,
        "skip_download": True,
        "extract_flat": "in_playlist",
    }
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=False)
    entries = info.get("entries") if isinstance(info, dict) else None
    if not entries:
        return [url]
    urls: List[str] = []
    for entry in entries:
        if not entry:
            continue
        entry_url = entry.get("url") or entry.get("id")
        if not entry_url:
            continue
        if entry_url.startswith("http"):
            urls.append(entry_url)
        else:
            urls.append(f"https://www.youtube.com/watch?v={entry_url}")
    return urls


def fetch_video_info(url: str) -> Dict[str, object]:
    import yt_dlp

    options = {"quiet": True, "skip_download": True}
    with yt_dlp.YoutubeDL(options) as ydl:
        return ydl.extract_info(url, download=False)


def download_subtitles(video_url: str, target_dir: str) -> Optional[str]:
    import yt_dlp

    options = {
        "quiet": True,
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": ["en"],
        "subtitlesformat": "vtt/srt",
        "outtmpl": os.path.join(target_dir, "%(id)s.%(ext)s"),
    }
    with yt_dlp.YoutubeDL(options) as ydl:
        ydl.download([video_url])
    for extension in ("vtt", "srt"):
        matches = [
            path
            for path in os.listdir(target_dir)
            if path.lower().endswith(f".{extension}")
        ]
        if matches:
            return os.path.join(target_dir, matches[0])
    return None


@lru_cache(maxsize=1)
def load_whisper_model():
    import whisper

    return whisper.load_model("base")


def download_audio(video_url: str, target_dir: str) -> Optional[str]:
    import yt_dlp

    options = {
        "quiet": True,
        "format": "bestaudio/best",
        "outtmpl": os.path.join(target_dir, "audio.%(ext)s"),
        "postprocessors": [
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3"}
        ],
    }
    with yt_dlp.YoutubeDL(options) as ydl:
        ydl.download([video_url])
    for filename in os.listdir(target_dir):
        if filename.endswith(".mp3"):
            return os.path.join(target_dir, filename)
    return None


def transcribe_audio(audio_path: str) -> List[Tuple[float, str]]:
    model = load_whisper_model()
    result = model.transcribe(audio_path)
    segments = result.get("segments", [])
    return [
        (segment["start"], segment["text"].strip())
        for segment in segments
        if segment.get("text")
    ]


def ingest_video(
    connection: sqlite3.Connection,
    video_url: str,
    progress_callback,
) -> None:
    info = fetch_video_info(video_url)
    video_id = info.get("id")
    if not video_id:
        return
    video_data = {
        "id": video_id,
        "title": info.get("title") or "Untitled",
        "channel": info.get("channel"),
        "thumbnail_url": info.get("thumbnail"),
        "publish_date": parse_publish_date(info.get("upload_date")),
    }
    if not insert_video(connection, video_data):
        progress_callback("Skipping existing video.", 1.0)
        return
    subtitles = info.get("subtitles") or {}
    automatic = info.get("automatic_captions") or {}
    has_subtitles = "en" in subtitles or "en" in automatic

    if has_subtitles:
        progress_callback("Downloading subtitles...", 0.3)
        with tempfile.TemporaryDirectory() as temp_dir:
            subtitle_path = download_subtitles(video_url, temp_dir)
            if not subtitle_path:
                raise RuntimeError("No subtitle file found after download.")
            with open(subtitle_path, "r", encoding="utf-8") as handle:
                cues = parse_subtitle_text(handle.read())
        progress_callback("Indexing transcripts...", 0.9)
        insert_transcripts(connection, video_id, cues)
        return

    progress_callback("Downloading audio...", 0.3)
    with tempfile.TemporaryDirectory() as temp_dir:
        audio_path = download_audio(video_url, temp_dir)
        if not audio_path:
            raise RuntimeError("Audio download failed.")
        progress_callback("Transcribing audio...", 0.7)
        cues = transcribe_audio(audio_path)
    progress_callback("Indexing transcripts...", 0.9)
    insert_transcripts(connection, video_id, cues)


def ingest_url(
    connection: sqlite3.Connection, url: str, progress_callback
) -> Optional[str]:
    try:
        video_urls = load_video_urls(url)
        total = len(video_urls)
        if total == 0:
            return "No videos found for the provided URL."
        for index, video_url in enumerate(video_urls, start=1):
            base_progress = (index - 1) / total

            def scoped_progress(message: str, step_progress: float) -> None:
                progress_callback(
                    message,
                    min(1.0, base_progress + (step_progress / total)),
                )

            progress_callback(
                f"Processing video {index} of {total}...",
                base_progress,
            )
            ingest_video(connection, video_url, scoped_progress)
        progress_callback("Ingestion complete.", 1.0)
        return None
    except Exception as exc:  # noqa: BLE001
        message = str(exc)
        if "cookie" in message.lower():
            return (
                "yt-dlp reported a cookies error. "
                "Export your YouTube cookies and pass them via yt-dlp config."
            )
        return message


def run_app() -> None:
    import streamlit as st

    st.set_page_config(page_title="Knowledge Base", layout="wide")
    st.title("Knowledge Base")

    connection = sqlite3.connect(DB_PATH)
    init_db(connection)

    with st.sidebar:
        st.header("Ingest")
        url = st.text_input("YouTube URL")
        ingest_button = st.button("Ingest")
        progress_bar = st.progress(0)
        status = st.empty()

    def progress_callback(message: str, progress: float) -> None:
        status.write(message)
        progress_bar.progress(progress)

    if ingest_button and url:
        error = ingest_url(
            connection,
            url,
            progress_callback,
        )
        if error:
            st.sidebar.error(error)
        else:
            st.sidebar.success("Ingestion complete.")

    st.subheader("Search")
    query = st.text_input("Search your library...", key="search")
    results = search_transcripts(connection, query)

    for group in results.values():
        video = group["video"]
        col1, col2 = st.columns([1, 4])
        with col1:
            if video.get("thumbnail_url"):
                st.image(video["thumbnail_url"], width=160)
        with col2:
            st.markdown(f"**{video['title']}**")
            if video.get("channel"):
                st.write(video["channel"])
            if video.get("publish_date"):
                st.write(video["publish_date"])
            for match in group["matches"]:
                start_time = int(match["start_time"])
                link = (
                    "https://www.youtube.com/watch?v="
                    f"{video['id']}&t={start_time}"
                )
                timestamp_label = format_timestamp(match["start_time"])
                st.markdown(
                    f"[{timestamp_label}]({link}) {match['text']}",
                    unsafe_allow_html=True,
                )


if __name__ == "__main__":
    run_app()
