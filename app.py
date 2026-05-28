from __future__ import annotations

from pathlib import Path

from fastapi import BackgroundTasks, Cookie, FastAPI, File, Form, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from separator import KeysIsolationService
from storage import AppStorage


BASE_DIR = Path(__file__).resolve().parent
SESSION_COOKIE = "chord_expo_session"

app = FastAPI(title="Chord Expo Keys Isolation")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
service = KeysIsolationService(BASE_DIR)
storage = AppStorage(BASE_DIR)


def persist_job_snapshot(job_id: str) -> None:
    job = service.get_job(job_id)
    if job is not None and job.track_id:
        storage.update_track_from_job(job.track_id, job)


service.set_on_change(persist_job_snapshot)


def require_user(session_token: str | None) -> dict:
    user = storage.get_user_by_session(session_token)
    if user is None:
        raise HTTPException(status_code=401, detail="You must be signed in.")
    return user


def job_payload_for_user(user_id: int, job_id: str) -> dict:
    track = storage.get_track_by_job_id(job_id)
    if track is None or track["user_id"] != user_id:
        raise HTTPException(status_code=404, detail="Job not found.")

    job = service.get_job(job_id)
    if job is not None:
        if job.track_id:
            storage.update_track_from_job(job.track_id, job)
        track = storage.get_track_by_job_id(job_id) or track
    return track


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(BASE_DIR / "templates" / "index.html")


@app.get("/auth")
async def auth_page() -> FileResponse:
    return FileResponse(BASE_DIR / "templates" / "auth.html")


@app.post("/api/auth/register")
async def register(response: Response, email: str = Form(...), password: str = Form(...)) -> dict:
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters.")
    try:
        user = storage.create_user(email, password)
    except Exception as error:
        raise HTTPException(status_code=400, detail="That email is already in use.") from error
    token = storage.create_session(user["id"])
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 30)
    return {"user": user}


@app.post("/api/auth/login")
async def login(response: Response, email: str = Form(...), password: str = Form(...)) -> dict:
    user = storage.authenticate_user(email, password)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    token = storage.create_session(user["id"])
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 30)
    return {"user": user}


@app.post("/api/auth/logout")
async def logout(response: Response, session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE)) -> dict:
    storage.delete_session(session_token)
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


@app.get("/api/auth/me")
async def me(session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE)) -> dict:
    user = storage.get_user_by_session(session_token)
    return {"user": user}


@app.get("/api/library")
async def library(session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE)) -> dict:
    user = require_user(session_token)
    return {"tracks": storage.list_tracks_for_user(user["id"])}


@app.get("/api/tracks/{track_id}")
async def track_detail(track_id: int, session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE)) -> dict:
    user = require_user(session_token)
    track = storage.get_track_for_user(user["id"], track_id)
    if track is None:
        raise HTTPException(status_code=404, detail="Track not found.")
    job = service.get_job(track["job_id"])
    if job is not None and job.track_id:
        storage.update_track_from_job(job.track_id, job)
        track = storage.get_track_for_user(user["id"], track_id) or track
    return track


@app.delete("/api/tracks/{track_id}")
async def delete_track(track_id: int, session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE)) -> dict:
    user = require_user(session_token)
    track = storage.delete_track_for_user(user["id"], track_id)
    if track is None:
      raise HTTPException(status_code=404, detail="Track not found.")
    service.cleanup_job(track["job_id"])
    return {"ok": True, "track_id": track_id}


@app.post("/api/jobs")
async def create_job(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    model: str = Form("htdemucs_6s"),
    shifts: int = Form(4),
    overlap: float = Form(0.5),
    segment: int = Form(7),
    jobs: int = Form(0),
    aggressive_refine: bool = Form(True),
    session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> dict:
    user = require_user(session_token)
    payload = await file.read()
    if not payload:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")

    settings = {
        "model": model,
        "shifts": shifts,
        "overlap": overlap,
        "segment": segment,
        "jobs": jobs,
        "aggressive_refine": aggressive_refine,
    }
    job = service.create_job(source_name=file.filename or "audio.wav", settings=settings, kind="separation")
    input_path = service.save_upload(job.job_id, file.filename or "audio.wav", payload)
    title = Path(file.filename or "audio.wav").stem
    track_id = storage.create_track(
        user_id=user["id"],
        kind="separation",
        title=title,
        source_filename=file.filename or "audio.wav",
        source_path=str(input_path),
        job_id=job.job_id,
        settings=settings,
    )
    service._update(job.job_id, track_id=track_id)
    job = service.get_job(job.job_id)
    if job is not None and job.track_id:
        storage.update_track_from_job(job.track_id, job)
    background_tasks.add_task(service.process_job, job.job_id, input_path)
    return {"job_id": job.job_id, "track_id": track_id}


@app.post("/api/analysis-jobs")
async def create_analysis_job(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> dict:
    user = require_user(session_token)
    payload = await file.read()
    if not payload:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")

    settings: dict[str, str] = {}
    job = service.create_job(source_name=file.filename or "audio.wav", settings=settings, kind="analysis")
    input_path = service.save_upload(job.job_id, file.filename or "audio.wav", payload)
    title = Path(file.filename or "audio.wav").stem
    track_id = storage.create_track(
        user_id=user["id"],
        kind="analysis",
        title=title,
        source_filename=file.filename or "audio.wav",
        source_path=str(input_path),
        job_id=job.job_id,
        settings=settings,
    )
    service._update(job.job_id, track_id=track_id)
    job = service.get_job(job.job_id)
    if job is not None and job.track_id:
        storage.update_track_from_job(job.track_id, job)
    background_tasks.add_task(service.process_job, job.job_id, input_path)
    return {"job_id": job.job_id, "track_id": track_id}


@app.get("/api/jobs/{job_id}")
async def job_status(job_id: str, session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE)) -> dict:
    user = require_user(session_token)
    return job_payload_for_user(user["id"], job_id)


@app.get("/downloads/tracks/{track_id}/{filename}")
async def download_track_file(
    track_id: int,
    filename: str,
    session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> FileResponse:
    user = require_user(session_token)
    track = storage.get_track_for_user(user["id"], track_id)
    if track is None:
        raise HTTPException(status_code=404, detail="Track not found.")
    try:
        path = service.resolve_download(track["job_id"], filename)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return FileResponse(path, filename=path.name, media_type="audio/wav")
