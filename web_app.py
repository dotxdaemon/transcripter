# ABOUTME: Serves the knowledge base as a Flask website for ingestion and search.
# ABOUTME: Renders HTML pages backed by the same SQLite transcript storage.

import sqlite3
from typing import Callable, Optional

from flask import Flask, g, redirect, render_template, request, url_for

import app


def create_app(db_path: str = app.DB_PATH) -> Flask:
    website = Flask(__name__)
    website.config["DB_PATH"] = db_path

    def get_connection() -> sqlite3.Connection:
        if "db" not in g:
            connection = sqlite3.connect(website.config["DB_PATH"])
            connection.row_factory = sqlite3.Row
            app.init_db(connection)
            g.db = connection
        return g.db

    @website.teardown_appcontext
    def close_connection(error: Optional[BaseException]) -> None:
        connection = g.pop("db", None)
        if connection is not None:
            connection.close()

    @website.get("/")
    def index() -> str:
        query = request.args.get("q", "").strip()
        message = request.args.get("message")
        error = request.args.get("error")
        results = app.search_transcripts(get_connection(), query)
        return render_template(
            "index.html",
            query=query,
            results=results.values(),
            message=message,
            error=error,
            format_timestamp=app.format_timestamp,
        )

    @website.post("/ingest")
    def ingest() -> str:
        url = request.form.get("url", "").strip()
        if not url:
            return redirect(url_for("index", error="Enter a YouTube URL."))

        def progress_callback(message: str, progress: float) -> None:
            _ = message
            _ = progress

        error = app.ingest_url(get_connection(), url, progress_callback)
        if error:
            return redirect(url_for("index", error=error))
        return redirect(url_for("index", message="Ingestion complete."))

    return website


def run() -> None:
    website = create_app()
    website.run(host="0.0.0.0", port=8000, debug=False)


if __name__ == "__main__":
    run()
