from dotenv import load_dotenv
load_dotenv()

import os
import json
import shutil
import threading
import traceback
from typing import Optional
from uuid import uuid4
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel
import providers
import select_clips
import burn_subtitles


# ─────────────────────────────────────────────────────────────
# App setup
# ─────────────────────────────────────────────────────────────

app = FastAPI(title="Viral Clip Pipeline API")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
ENV_PATH = os.path.join(BASE_DIR, ".env")
JOBS_INDEX = os.path.join(OUTPUT_DIR, "_jobs.json")

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

# job_id -> status dict. Mirrored to JOBS_INDEX on every write so a server restart
# doesn't lose finished jobs and their downloadable clips.
JOBS = {}
JOBS_LOCK = threading.Lock()

# Fields that are large or meaningless after a restart — never persisted.
_TRANSIENT_JOB_FIELDS = ("raw_clips", "highlights", "error")


def _persist_jobs_locked():
    """Write the job index to disk. Caller must already hold JOBS_LOCK."""
    try:
        slim = {
            jid: {k: v for k, v in job.items() if k not in _TRANSIENT_JOB_FIELDS}
            for jid, job in JOBS.items()
        }
        tmp = JOBS_INDEX + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(slim, f, ensure_ascii=False)
        os.replace(tmp, JOBS_INDEX)   # atomic: a crash mid-write can't corrupt the index
    except OSError:
        pass   # persistence is best-effort; never break a running job over it


def _load_jobs():
    """Restore the job index at startup. Jobs that were mid-flight when the server
    stopped are marked failed — their worker threads are gone, so they can never
    progress and would otherwise show a spinner forever."""
    if not os.path.exists(JOBS_INDEX):
        return
    try:
        with open(JOBS_INDEX, "r", encoding="utf-8") as f:
            saved = json.load(f)
    except (OSError, json.JSONDecodeError):
        return
    for jid, job in saved.items():
        if job.get("status") in ("queued", "running", "selected"):
            job["status"] = "failed"
            job["message"] = "Interrupted — the server restarted while this job was running."
        JOBS[jid] = job


def _set_job(job_id: str, **kwargs):
    with JOBS_LOCK:
        JOBS.setdefault(job_id, {})
        JOBS[job_id].update(kwargs)
        _persist_jobs_locked()


def _get_job(job_id: str) -> Optional[dict]:
    with JOBS_LOCK:
        return JOBS.get(job_id)


_load_jobs()


@app.get("/", tags=["UI"])
def serve_frontend():
    """Serves the main frontend UI."""
    return FileResponse(os.path.join(BASE_DIR, "templates", "index.html"))


# ─────────────────────────────────────────────────────────────
# Request/response models
# ─────────────────────────────────────────────────────────────

class SelectClipsURLRequest(BaseModel):
    url: str
    options: Optional[dict] = None


class JobResponse(BaseModel):
    job_id: str
    status: str


# ─────────────────────────────────────────────────────────────
# Background workers
# ─────────────────────────────────────────────────────────────

def _run_selection(job_id: str, url: Optional[str], local_file_path: Optional[str], options: Optional[dict]):
    try:
        _set_job(job_id, status="running", stage="selection")

        def status_cb(msg: str):
            _set_job(job_id, message=msg)

        raw_clips, highlights, log_path = select_clips.execute_selection_workflow(
            url=url,
            local_file_path=local_file_path,
            options=options,
            status_callback=status_cb,
        )

        if not raw_clips:
            _set_job(job_id, status="failed", message="No clips were produced.", log_path=log_path)
            return

        # raw_path is now the SOURCE video, so derive job_dir from the log path
        # (output/<job_id>/DIAGNOSTIC_REPORT.txt -> output/<job_id>).
        job_dir = os.path.dirname(log_path)

        _set_job(
            job_id,
            status="selected",
            stage="selection_complete",
            job_dir=job_dir,
            highlights=highlights,
            raw_clips=raw_clips,
            total_clips=len(raw_clips),
            ready_clips=[],
            log_path=log_path,
            message="Clip selection complete. Ready for subtitle burning.",
        )
    except Exception as e:
        _set_job(job_id, status="failed", message=f"Selection crashed: {e}", error=traceback.format_exc())


def _run_subtitles(job_id: str, job_dir: str):
    try:
        _set_job(job_id, status="running", stage="subtitles")

        def status_cb(msg: str):
            _set_job(job_id, message=msg)

        # Called by the burn workflow the instant each clip is finished, so the UI
        # can show clips as they complete instead of waiting for the whole batch.
        def clip_cb(output_path: str, reason: str):
            with JOBS_LOCK:
                JOBS.setdefault(job_id, {})
                ready = JOBS[job_id].setdefault("ready_clips", [])
                fname = os.path.basename(output_path)
                if not any(c["filename"] == fname for c in ready):
                    ready.append({
                        "filename": fname,
                        "download_url": f"/jobs/{job_id}/clips/{fname}",
                        "reason": reason,
                    })
                    _persist_jobs_locked()

        final_clips, log_path = burn_subtitles.execute_subtitle_workflow(
            job_dir=job_dir,
            clip_callback=clip_cb,
            status_callback=status_cb,
        )

        if not final_clips:
            _set_job(job_id, status="failed", message="No subtitled clips were produced.", subtitle_log_path=log_path)
            return

        _set_job(
            job_id,
            status="done",
            stage="subtitles_complete",
            final_clips=final_clips,
            subtitle_log_path=log_path,
            message="Subtitle burning complete.",
        )
    except Exception as e:
        _set_job(job_id, status="failed", message=f"Subtitle burning crashed: {e}", error=traceback.format_exc())


def _run_full_pipeline(job_id: str, url: Optional[str], local_file_path: Optional[str], options: Optional[dict]):
    _run_selection(job_id, url, local_file_path, options)
    job = _get_job(job_id)
    if job and job.get("status") == "selected":
        _run_subtitles(job_id, job["job_dir"])


# ─────────────────────────────────────────────────────────────
# Endpoints: clip selection only
# ─────────────────────────────────────────────────────────────



@app.post("/select-clips/url", response_model=JobResponse)
def select_clips_from_url(payload: SelectClipsURLRequest):
    """Start clip selection from a video URL (yt-dlp supported source, or Google Drive link)."""
    job_id = str(uuid4())
    _set_job(job_id, status="queued", message="Job queued")

    thread = threading.Thread(
        target=_run_selection,
        args=(job_id, payload.url, None, payload.options),
        daemon=True,
    )
    thread.start()

    return JobResponse(job_id=job_id, status="queued")


@app.post("/select-clips/upload", response_model=JobResponse)
def select_clips_from_upload(
    file: UploadFile = File(...),
    options: Optional[str] = Form(None),
):
    """Start clip selection from an uploaded local video file.
    `options` (optional) should be a JSON string, e.g.
    '{"viral": true, "num_clips": "auto"}'. `num_clips` controls how many clips
    are cut: "auto" (default) scales to video length (about one per minute, up to
    ~50), or pass an integer (capped at 80)."""
    job_id = str(uuid4())

    upload_path = os.path.join(UPLOAD_DIR, f"{job_id}_{file.filename}")
    with open(upload_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    parsed_options = None
    if options:
        try:
            parsed_options = json.loads(options)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="`options` must be valid JSON")

    _set_job(job_id, status="queued", message="Job queued", uploaded_path=upload_path)

    thread = threading.Thread(
        target=_run_selection,
        args=(job_id, None, upload_path, parsed_options),
        daemon=True,
    )
    thread.start()

    return JobResponse(job_id=job_id, status="queued")


# ─────────────────────────────────────────────────────────────
# Endpoints: subtitle burning only (for an already-selected job)
# ─────────────────────────────────────────────────────────────

@app.post("/burn-subtitles/{job_id}", response_model=JobResponse)
def burn_subtitles_for_job(job_id: str):
    """Start subtitle burning for a job that has already completed clip selection
    (status must be 'selected')."""
    job = _get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.get("status") != "selected":
        raise HTTPException(
            status_code=400,
            detail=(
                f"Job is not ready for subtitle burning (status='{job.get('status')}'). "
                f"Run /select-clips first and wait for status='selected'."
            ),
        )

    job_dir = job["job_dir"]
    _set_job(job_id, status="queued", message="Subtitle job queued")

    thread = threading.Thread(
        target=_run_subtitles,
        args=(job_id, job_dir),
        daemon=True,
    )
    thread.start()

    return JobResponse(job_id=job_id, status="queued")


# ─────────────────────────────────────────────────────────────
# Endpoints: combined pipeline (selection + subtitles, end-to-end)
# ─────────────────────────────────────────────────────────────

@app.post("/process/url", response_model=JobResponse)
def process_from_url(payload: SelectClipsURLRequest):
    """Run the full pipeline (selection + subtitle burning) for a video URL."""
    job_id = str(uuid4())
    _set_job(job_id, status="queued", message="Job queued")

    thread = threading.Thread(
        target=_run_full_pipeline,
        args=(job_id, payload.url, None, payload.options),
        daemon=True,
    )
    thread.start()

    return JobResponse(job_id=job_id, status="queued")


@app.post("/process/upload", response_model=JobResponse)
def process_from_upload(
    file: UploadFile = File(...),
    options: Optional[str] = Form(None),
):
    """Run the full pipeline (selection + subtitle burning) for an uploaded video file."""
    job_id = str(uuid4())

    upload_path = os.path.join(UPLOAD_DIR, f"{job_id}_{file.filename}")
    with open(upload_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    parsed_options = None
    if options:
        try:
            parsed_options = json.loads(options)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="`options` must be valid JSON")

    _set_job(job_id, status="queued", message="Job queued", uploaded_path=upload_path)

    thread = threading.Thread(
        target=_run_full_pipeline,
        args=(job_id, None, upload_path, parsed_options),
        daemon=True,
    )
    thread.start()

    return JobResponse(job_id=job_id, status="queued")


# ─────────────────────────────────────────────────────────────
# Endpoints: status + results
# ─────────────────────────────────────────────────────────────

@app.get("/jobs/{job_id}")
def get_job_status(job_id: str):
    job = _get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Avoid dumping huge transcript/word-level objects back to the client
    safe_job = {k: v for k, v in job.items() if k != "raw_clips"}
    if "raw_clips" in job:
        safe_job["clips"] = [
            {
                "index": c["index"],
                "start": c["start"],
                "end": c["end"],
                "score": c.get("score"),
                "reason": c.get("reason"),
            }
            for c in job["raw_clips"]
        ]
    return JSONResponse(content=safe_job)


@app.get("/jobs/{job_id}/clips")
def list_clips(job_id: str):
    """List downloadable clips. Returns clips AS THEY FINISH (partial) while the job
    is still rendering, plus a `complete` flag and progress counts. The frontend
    polls this to show clips the moment each one is ready."""
    job = _get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    final_clips = job.get("final_clips")
    ready = job.get("ready_clips", [])
    total = job.get("total_clips")

    if final_clips:
        clips = [
            {"filename": os.path.basename(c),
             "download_url": f"/jobs/{job_id}/clips/{os.path.basename(c)}"}
            for c in final_clips
        ]
        return {"job_id": job_id, "complete": True,
                "ready": len(clips), "total": total or len(clips), "clips": clips}

    # Still rendering — hand back whatever has finished so far.
    return {"job_id": job_id, "complete": False,
            "ready": len(ready), "total": total,
            "clips": [{"filename": c["filename"], "download_url": c["download_url"]} for c in ready]}


@app.get("/jobs/{job_id}/clips.zip")
def download_all_clips(job_id: str):
    """Bundle every finished clip for a job into a single ZIP and stream it back.
    Powers the frontend 'Download all' button."""
    import io, zipfile
    job = _get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    final_clips = job.get("final_clips")
    if not final_clips:
        raise HTTPException(status_code=400, detail="Subtitled clips not ready yet")

    # Build the zip in memory (clips are small; for very large batches switch to a
    # temp file on disk). Each clip is added once under its basename.
    buf = io.BytesIO()
    added = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        # ZIP_STORED (no recompression) — mp4 is already compressed, so this is
        # fast and avoids burning CPU re-zipping video.
        for c in final_clips:
            if os.path.exists(c):
                zf.write(c, arcname=os.path.basename(c))
                added += 1

    if added == 0:
        raise HTTPException(status_code=404, detail="No clip files found on disk")

    buf.seek(0)
    headers = {"Content-Disposition": f'attachment; filename="{job_id}_clips.zip"'}
    return StreamingResponse(buf, media_type="application/zip", headers=headers)


@app.get("/jobs/{job_id}/clips/{filename}")
def download_clip(job_id: str, filename: str):
    """Download a specific clip file by name."""
    job = _get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    job_dir = job.get("job_dir")
    if not job_dir:
        raise HTTPException(status_code=400, detail="Job directory not available yet")

    # Prevent path traversal - only allow plain filenames
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    clip_path = os.path.join(job_dir, "clips", filename)
    if not os.path.exists(clip_path):
        raise HTTPException(status_code=404, detail="Clip not found")

    return FileResponse(clip_path, media_type="video/mp4", filename=filename)


@app.get("/jobs/{job_id}/highlights")
def get_highlights(job_id: str):
    """Return the AI-selected highlight metadata (start/end/score/reason) for a job."""
    job = _get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    highlights = job.get("highlights")
    if highlights is None:
        raise HTTPException(status_code=400, detail="Highlights not ready yet")

    return {"job_id": job_id, "highlights": highlights}


@app.delete("/jobs/{job_id}")
def delete_job(job_id: str, keep_files: bool = False):
    """Remove a job when the user clicks 'remove'.

    Opt-in cleanup: nothing is deleted automatically anywhere else, so downloaded
    videos and generated clips persist on disk until this endpoint is called.

    Deletes:
      - the job's output directory (raw_video, audio, transcripts, clips, logs)
      - the uploaded source file (if the job came from an upload)
      - the in-memory job entry
    Pass ?keep_files=true to only forget the job in memory but leave files on disk.
    """
    job = _get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    removed = {"job_dir": None, "uploaded_path": None, "memory": False}
    errors = []

    if not keep_files:
        job_dir = job.get("job_dir")
        if job_dir and os.path.isdir(job_dir):
            try:
                shutil.rmtree(job_dir)
                removed["job_dir"] = job_dir
            except OSError as e:
                errors.append(f"job_dir: {e}")

        uploaded_path = job.get("uploaded_path")
        if uploaded_path and os.path.exists(uploaded_path):
            try:
                os.remove(uploaded_path)
                removed["uploaded_path"] = uploaded_path
            except OSError as e:
                errors.append(f"uploaded_path: {e}")

    with JOBS_LOCK:
        if job_id in JOBS:
            del JOBS[job_id]
            removed["memory"] = True

    if errors:
        return JSONResponse(
            status_code=207,
            content={"job_id": job_id, "removed": removed,
                     "message": "Job removed with some errors.", "errors": errors},
        )
    return {"job_id": job_id, "removed": removed,
            "message": "Job removed." if not keep_files else "Job forgotten (files kept on disk)."}


@app.get("/health")
def health():
    return {"status": "ok"}


# ─────────────────────────────────────────────────────────────
# Setup: provider status + API keys
# ─────────────────────────────────────────────────────────────

# Keys the setup screen can manage. label/help drive the UI; `required` marks the
# ones without which nothing works at all.
MANAGED_KEYS = [
    {"key": "GROQ_API_KEY", "label": "Groq", "required": True,
     "help": "Free. Powers transcription + AI selection.", "url": "https://console.groq.com/keys"},
    {"key": "DEEPGRAM_API_KEY", "label": "Deepgram", "required": False,
     "help": "Optional. Best Hindi caption accuracy.", "url": "https://console.deepgram.com/signup"},
    {"key": "GEMINI_API_KEY", "label": "Google Gemini", "required": False,
     "help": "Optional fallback when Groq is rate-limited.", "url": "https://aistudio.google.com/apikey"},
    {"key": "OPENROUTER_API_KEY", "label": "OpenRouter", "required": False,
     "help": "Optional last-resort text fallback.", "url": "https://openrouter.ai/keys"},
]
_MANAGED_KEY_NAMES = {k["key"] for k in MANAGED_KEYS}


class SettingsRequest(BaseModel):
    keys: dict


def _mask(value: str) -> str:
    """Show only enough of a key to recognise it — never echo a secret back in full."""
    v = (value or "").strip()
    if not v:
        return ""
    return f"{v[:4]}…{v[-4:]}" if len(v) > 12 else "…" * len(v)


def _require_local(request: Request):
    """Reject key management from anywhere but this machine.

    The server is often started with --host 0.0.0.0 to reach it from a phone, which
    would otherwise expose an unauthenticated write-secrets-to-disk endpoint to the
    whole network. Set ALLOW_REMOTE_SETTINGS=1 only on a trusted network."""
    if (os.environ.get("ALLOW_REMOTE_SETTINGS") or "").strip() == "1":
        return
    host = (request.client.host if request.client else "") or ""
    if host not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(
            status_code=403,
            detail="API keys can only be changed from the machine running ShortsAI. "
                   "Open http://localhost:8000 there, or edit the .env file directly.",
        )


def _read_env_file() -> dict:
    """Parse .env into a dict, preserving nothing else about the file."""
    out = {}
    if not os.path.exists(ENV_PATH):
        return out
    try:
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    except OSError:
        pass
    return out


def _write_env_file(values: dict):
    """Rewrite .env with `values` merged in, keeping any unmanaged keys the user added."""
    existing = _read_env_file()
    existing.update(values)
    lines = ["# ShortsAI configuration — managed by the in-app setup screen.\n"]
    for entry in MANAGED_KEYS:
        lines.append(f"{entry['key']}={existing.get(entry['key'], '')}\n")
    for k, v in existing.items():
        if k not in _MANAGED_KEY_NAMES:
            lines.append(f"{k}={v}\n")
    tmp = ENV_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.writelines(lines)
    os.replace(tmp, ENV_PATH)
    try:
        os.chmod(ENV_PATH, 0o600)   # the file holds secrets — keep it owner-only
    except OSError:
        pass


@app.get("/api/status")
def api_status():
    """What is configured and working right now — drives the UI's setup panel."""
    status = providers.provider_status()
    env = _read_env_file()
    keys = [{
        "key": e["key"],
        "label": e["label"],
        "required": e["required"],
        "help": e["help"],
        "url": e["url"],
        "set": bool((os.environ.get(e["key"]) or env.get(e["key"]) or "").strip()),
        "masked": _mask(os.environ.get(e["key"]) or env.get(e["key"]) or ""),
    } for e in MANAGED_KEYS]

    fonts_dir = os.path.join(BASE_DIR, "fonts")
    font_count = len([f for f in os.listdir(fonts_dir)
                      if f.lower().endswith(".ttf")]) if os.path.isdir(fonts_dir) else 0

    return {
        "ready": status["chat_ready"] and status["transcribe_ready"] and status["ffmpeg"],
        "providers": status,
        "keys": keys,
        "fonts": font_count,
    }


@app.post("/api/settings")
def api_save_settings(payload: SettingsRequest, request: Request):
    """Save API keys to .env and apply them to the running process immediately, so
    the user never has to restart the server or touch a text editor."""
    _require_local(request)

    updates = {}
    for k, v in (payload.keys or {}).items():
        if k not in _MANAGED_KEY_NAMES:
            continue
        value = (v or "").strip()
        # The UI sends back the masked placeholder for untouched fields; ignore those
        # so redisplaying the form can't overwrite a real key with its own mask.
        if "…" in value:
            continue
        updates[k] = value

    if not updates:
        raise HTTPException(status_code=400, detail="No recognised keys to update")

    try:
        _write_env_file(updates)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Could not write .env: {e}")

    for k, v in updates.items():
        if v:
            os.environ[k] = v
        else:
            os.environ.pop(k, None)

    return {"saved": sorted(updates.keys()), "status": api_status()}
