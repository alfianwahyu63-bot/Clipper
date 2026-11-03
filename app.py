"""ViralCut Studio — self-hosted clipping app. Use only source media you own/have permission to process."""
from __future__ import annotations

import array
import json
import math
import os
import re
import shutil
import statistics
import subprocess
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

BASE = Path(__file__).resolve().parent
STATIC = BASE / "static"
WORK = BASE / "workspace"
WORK.mkdir(exist_ok=True)
MAX_FILE_MB = int(os.getenv("MAX_FILE_MB", "500"))
MAX_DURATION = int(os.getenv("MAX_DURATION_SEC", "7200"))
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "tiny")
JOBS: dict[str, dict] = {}
LOCK = threading.Lock()
MODEL_LOCK = threading.Lock()
MODEL = None

app = FastAPI(title="ViralCut Studio", version="1.0.0")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


class UrlRequest(BaseModel):
    url: str
    permission: bool = False
    clip_count: int = Field(3, ge=1, le=8)
    clip_duration: int = Field(30, ge=15, le=60)
    aspect: str = "9:16"
    captions: bool = True
    use_ai: bool = True


def update(job_id: str, **changes):
    with LOCK:
        if job_id in JOBS:
            JOBS[job_id].update(changes)


def ensure_tools():
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            raise RuntimeError(f"{tool} belum terpasang. Pasang FFmpeg terlebih dahulu (termasuk ffprobe).")


def clean_options(count, duration, aspect):
    count, duration = int(count), int(duration)
    if not 1 <= count <= 8 or duration not in (15, 30, 45, 60):
        raise ValueError("Jumlah klip 1–8 dan durasi harus 15, 30, 45, atau 60 detik.")
    if aspect not in ("9:16", "1:1", "16:9"):
        raise ValueError("Rasio video tidak valid.")
    return count, duration, aspect


def youtube_id(link: str) -> str:
    try:
        u = urlsplit(link.strip())
        host = (u.hostname or "").lower()
        if u.scheme not in ("http", "https"):
            raise ValueError()
        if host in ("youtu.be", "www.youtu.be"):
            v = u.path.strip("/").split("/")[0]
        elif host in ("youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"):
            parts = u.path.strip("/").split("/")
            if u.path == "/watch":
                v = parse_qs(u.query).get("v", [""])[0]
            elif parts[0] in ("shorts", "live", "embed") and len(parts) >= 2:
                v = parts[1]
            else:
                v = ""
        else:
            v = ""
        if re.fullmatch(r"[A-Za-z0-9_-]{11}", v):
            return v
    except Exception:
        pass
    raise ValueError("Link YouTube tidak valid. Gunakan link video, Shorts, atau live YouTube.")


def probe(path: Path):
    proc = subprocess.run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height",
        "-of", "json", str(path)
    ], text=True, capture_output=True, timeout=40)
    if proc.returncode:
        raise RuntimeError("Video tidak bisa dibaca oleh FFmpeg: " + proc.stderr[-300:])
    info = json.loads(proc.stdout)
    duration = float(info.get("format", {}).get("duration") or 0)
    streams = info.get("streams", [])
    if not any(s.get("codec_type") == "video" for s in streams):
        raise RuntimeError("Berkas tidak mengandung video.")
    if not 5 <= duration <= MAX_DURATION:
        raise RuntimeError(f"Durasi harus 5 detik sampai {MAX_DURATION // 60} menit.")
    return duration, streams


def extract_energy(src: Path, duration: float):
    """Read a mono PCM stream; compute 1-second RMS blocks (no large in-memory audio)."""
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le", "pipe:1"]
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    values = []
    try:
        while True:
            buf = process.stdout.read(16000)
            if not buf:
                break
            samples = array.array("h")
            samples.frombytes(buf[:len(buf) - len(buf) % 2])
            if not samples:
                break
            sub = samples[::8]
            energy = math.sqrt(sum(x*x for x in sub) / max(1, len(sub))) / 32768
            values.append(energy)
        process.wait(timeout=15)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        if process.stdout:
            process.stdout.close()
    if not values:
        return [0.0] * max(1, int(math.ceil(duration)))
    return values


def transcript(src: Path):
    """Optional local speech recognition. Works offline after the model is cached."""
    global MODEL
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return [], "Model transkripsi belum diinstal; ranking berdasarkan dinamika audio."
    with MODEL_LOCK:
        if MODEL is None:
            MODEL = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8", download_root=str(BASE / "model_cache"))
        segments, _ = MODEL.transcribe(str(src), beam_size=3, vad_filter=True, word_timestamps=True)
        result = []
        for s in segments:
            words = [{"start": float(w.start), "end": float(w.end), "text": w.word.strip()} for w in (s.words or [])]
            result.append({"start": float(s.start), "end": float(s.end), "text": s.text.strip(), "words": words})
        return result, None


HOOKS = re.compile(r"\b(?:ternyata|rahasia|kenapa|mengapa|bagaimana|jangan|wajib|kesalahan|fakta|bahaya|serius|penting|gila|bayangkan|pernah|terbaik|terburuk|bongkar|terbukti|akhirnya|masalahnya|solusi|cuma|coba|tidak mungkin|gak nyangka|nggak nyangka|tips|trik|cara|how|why|secret|never|stop|mistake|truth|shocking|imagine|actually|must|best|worst)\b", re.I)


def evaluate_windows(duration, energy, spoken, count, wanted_length):
    length = min(float(wanted_length), duration)
    if length < 8:
        raise ValueError("Video terlalu pendek untuk dipotong.")
    highest = max(max(energy), 0.002)
    n = len(energy)
    stride = max(3, int(length / 5))
    starts = [float(x) for x in range(0, max(1, int(duration - length) + 1), stride)]
    starts.extend(max(0.0, min(duration-length, float(s["start"]) - 1.5)) for s in spoken)
    starts.append(max(0.0, duration - length))
    starts = sorted(set(round(x, 1) for x in starts))
    candidates = []
    for start in starts[:3500]:
        end = min(duration, start + length)
        subset = energy[min(n-1, int(start)):min(n, max(int(start)+1, int(math.ceil(end))))]
        if not subset:
            continue
        mean_energy = statistics.fmean(subset) / highest
        jitter = statistics.pstdev(subset) / highest if len(subset) > 1 else 0
        vocal_activity = sum(e > (highest * 0.18) for e in subset) / len(subset)
        statements = [s for s in spoken if s["end"] > start and s["start"] < end]
        text = " ".join(s["text"] for s in statements)
        opening = " ".join(s["text"] for s in statements if s["start"] < start + 9)
        hook_count = len(HOOKS.findall(opening))
        questions = (opening.count("?") > 0)
        words = len(text.split())
        density = min(1.0, words / max(1, length * 2.0)) if spoken else vocal_activity
        # This is a heuristic editorial score, NOT a views prediction.
        score = (34 + 27 * min(1, mean_energy) + 14 * min(1, jitter * 3)
                 + 9 * vocal_activity + 8 * density + 7 * min(2, hook_count)
                 + (6 if questions else 0))
        if not spoken:
            score = min(78, score)
        else:
            score = min(98, score)
        excerpt = text.strip()
        if excerpt:
            title = re.sub(r"\s+", " ", excerpt)[:77].rstrip(" ,.;:")
            if len(excerpt) > 77:
                title += "…"
        else:
            title = "Momen menarik berdasarkan perubahan energi audio"
        desc = []
        if hook_count:
            desc.append("Hook kuat di awal")
        if questions:
            desc.append("Memancing rasa penasaran")
        if mean_energy > .47:
            desc.append("Energi suara tinggi")
        if jitter > .11:
            desc.append("Dinamika audio menonjol")
        if not desc:
            desc.append("Ritme audio relatif aktif" if not spoken else "Percakapan cukup padat")
        candidates.append({"start": start, "end": end, "score": round(score), "title": title,
                           "reason": " • ".join(desc), "excerpt": excerpt[:260]})
    candidates.sort(key=lambda x: (-x["score"], x["start"]))
    selected = []
    for c in candidates:
        if all(c["end"] <= x["start"] + 1 or c["start"] >= x["end"] - 1 for x in selected):
            selected.append(c)
            if len(selected) >= count:
                break
    if not selected and candidates:
        selected = [candidates[0]]
    return selected


def srt_time(t):
    t = max(0, float(t))
    hr = int(t//3600); mins=int(t//60)%60; secs=int(t)%60; ms=int((t-int(t))*1000)
    return f"{hr:02d}:{mins:02d}:{secs:02d},{ms:03d}"


def write_srt(segments, start, end, output: Path):
    chunks = []
    for seg in segments:
        words = [w for w in seg.get("words", []) if w["end"] > start and w["start"] < end]
        if words:
            for ix in range(0, len(words), 5):
                grp = words[ix:ix+5]
                ts = max(0, grp[0]["start"] - start)
                te = min(end-start, grp[-1]["end"] - start)
                text = " ".join(w["text"] for w in grp).strip()
                if te > ts and text:
                    chunks.append((ts, te, text))
        elif seg["end"] > start and seg["start"] < end and seg["text"]:
            ts, te = max(0,seg["start"]-start), min(end-start,seg["end"]-start)
            if te > ts:
                chunks.append((ts,te,seg["text"]))
    if chunks:
        content = "\n\n".join(f"{i}\n{srt_time(a)} --> {srt_time(b)}\n{txt}" for i,(a,b,txt) in enumerate(chunks,1)) + "\n"
        output.write_text(content, encoding="utf-8")
    return bool(chunks)


def video_filter(aspect: str):
    w,h = {"9:16":(720,1280), "1:1":(720,720), "16:9":(1280,720)}[aspect]
    return f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1"


def render_clip(src, folder, clip, index, aspect, captions, spoken):
    name = f"viralcut_{index:02d}.mp4"
    output = folder / name
    filters = video_filter(aspect)
    subtitle_name = f"viralcut_{index:02d}.srt"
    has_srt = write_srt(spoken, clip["start"], clip["end"], folder / subtitle_name) if spoken else False
    if captions and has_srt:
        # Paths remain relative to the job directory, for Windows and Linux compatibility.
        filters += f",subtitles={subtitle_name}:force_style='FontName=Arial,FontSize=19,Bold=1,Outline=2,Shadow=1,Alignment=2,MarginV=110'"
    cmd = ["ffmpeg", "-y", "-nostdin", "-v", "error", "-ss", str(clip["start"]), "-i", str(src),
           "-t", str(max(0.1, clip["end"] - clip["start"])), "-map", "0:v:0", "-map", "0:a:0?",
           "-vf", filters, "-c:v", "libx264", "-preset", "veryfast", "-crf", "25",
           "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
           "-threads", "2", name]
    result = subprocess.run(cmd, cwd=folder, capture_output=True, text=True)
    warning = None
    if result.returncode and captions and has_srt:
        # Build environments without libass should still produce playable clips.
        warning = "Subtitle burn-in tidak tersedia pada FFmpeg ini; file .srt tetap disiapkan."
        cmd[cmd.index("-vf") + 1] = video_filter(aspect)
        result = subprocess.run(cmd, cwd=folder, capture_output=True, text=True)
    if result.returncode or not output.exists():
        raise RuntimeError("Gagal memotong video. " + (result.stderr or "")[-430:])
    thumb = f"thumb_{index:02d}.jpg"
    subprocess.run(["ffmpeg", "-y", "-nostdin", "-v", "error", "-ss", str(clip["start"]+0.7),
                    "-i", str(src), "-frames:v", "1", "-vf", video_filter(aspect)+",scale=270:-1",
                    thumb], cwd=folder, capture_output=True, timeout=80)
    return name, (subtitle_name if has_srt else None), (thumb if (folder/thumb).exists() else None), warning


def download_source(url: str, folder: Path):
    try:
        from yt_dlp import YoutubeDL
    except ImportError:
        raise RuntimeError("Modul yt-dlp belum tersedia. Jalankan: pip install -r requirements.txt")
    video_id = youtube_id(url)
    opts = {"format": "bv*[height<=1080]+ba/b[height<=1080]/best", "outtmpl": str(folder / "source.%(ext)s"),
            "merge_output_format": "mp4", "noplaylist": True, "quiet": True, "no_warnings": True,
            "socket_timeout": 20, "retries": 2, "overwrites": True, "max_filesize": MAX_FILE_MB * 1024 * 1024}
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=True)
    possible = [p for p in folder.glob("source.*") if p.suffix.lower() in (".mp4", ".mkv", ".webm", ".mov")]
    if not possible:
        raise RuntimeError("Video dari link tidak dapat diambil. Coba unggah file asli milik Anda. YouTube dapat membatasi akses.")
    return max(possible, key=lambda f: f.stat().st_size), (info or {}).get("title", "Video YouTube")


def process(job_id, source, options):
    folder = WORK / job_id
    try:
        ensure_tools()
        update(job_id, status="working", progress=8, step="Mempersiapkan video…")
        if source["type"] == "url":
            update(job_id, step="Mengimpor video dari YouTube…")
            src, title = download_source(source["url"], folder)
        else:
            src = folder / "upload_source"
            title = source["name"]
        duration, streams = probe(src)
        update(job_id, progress=25, step="Menganalisis ritme dan energi audio…", title=title, duration=round(duration))
        energy = extract_energy(src, duration)
        spoken, warn = [], None
        if options["use_ai"]:
            update(job_id, progress=43, step="Mendeteksi kata kunci dan momen penting (AI)…")
            try:
                spoken, warn = transcript(src)
            except Exception as e:
                warn = "Transkripsi AI tidak tersedia; analisis tetap dilanjutkan berbasis audio. " + str(e)[:130]
        update(job_id, progress=55, step="Merangking potongan paling potensial…", warning=warn)
        picks = evaluate_windows(duration, energy, spoken, options["clip_count"], options["clip_duration"])
        items = []
        warnings = [warn] if warn else []
        for i, clip in enumerate(picks, 1):
            update(job_id, progress=55 + round((i-1)/len(picks)*42), step=f"Merender video {i} dari {len(picks)}…")
            file, srt, thumb, render_warn = render_clip(src, folder, clip, i, options["aspect"], options["captions"], spoken)
            if render_warn and render_warn not in warnings:
                warnings.append(render_warn)
            items.append({**clip, "id": i, "url": f"/api/files/{job_id}/{file}",
                          "subtitle_url": f"/api/files/{job_id}/{srt}" if srt else None,
                          "thumbnail_url": f"/api/files/{job_id}/{thumb}" if thumb else None})
            update(job_id, progress=55 + round(i/len(picks)*42))
        update(job_id, status="done", progress=100, step="Selesai", clips=items,
               warning=" ".join(w for w in warnings if w) or None, transcript_ready=bool(spoken))
    except Exception as e:
        update(job_id, status="error", step="Gagal memproses", error=str(e)[:700])


def new_job(source, options):
    job_id = uuid.uuid4().hex[:16]
    folder = WORK / job_id
    folder.mkdir(exist_ok=False)
    with LOCK:
        JOBS[job_id] = {"id": job_id, "status": "queued", "step": "Dalam antrean…", "progress": 0,
                        "clips": [], "error": None, "warning": None, "created": int(time.time()),
                        "title": "Video baru", "duration": None}
    return job_id, folder


def start_processing(jid, source, options):
    threading.Thread(target=process, args=(jid, source, options), daemon=True).start()


@app.get("/")
def homepage():
    return FileResponse(STATIC / "index.html")


@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/sw.js")
def serviceworker():
    return FileResponse(STATIC / "sw.js", media_type="application/javascript", headers={"Service-Worker-Allowed": "/"})


@app.get("/api/health")
def health():
    ai = False
    try:
        import faster_whisper  # noqa
        ai = True
    except ImportError:
        pass
    return {"ok": True, "ffmpeg": bool(shutil.which("ffmpeg") and shutil.which("ffprobe")), "ai_installed": ai,
            "model": WHISPER_MODEL, "youtube_import": True}


@app.post("/api/jobs/url", status_code=202)
def new_from_url(req: UrlRequest):
    if not req.permission:
        raise HTTPException(400, "Centang bahwa video milik Anda atau Anda memiliki izin untuk memprosesnya.")
    try:
        youtube_id(req.url)
        count, duration, aspect = clean_options(req.clip_count, req.clip_duration, req.aspect)
    except ValueError as e:
        raise HTTPException(422, str(e))
    options = {"clip_count":count,"clip_duration":duration,"aspect":aspect,"captions":req.captions,"use_ai":req.use_ai}
    jid, _ = new_job({"type": "url", "url": req.url}, options)
    start_processing(jid, {"type":"url", "url":req.url}, options)
    return {"job_id": jid}


@app.post("/api/jobs/upload", status_code=202)
async def new_from_upload(file: UploadFile = File(...), permission: bool = Form(False),
                          clip_count: int = Form(3), clip_duration: int = Form(30),
                          aspect: str = Form("9:16"), captions: bool = Form(True), use_ai: bool = Form(True)):
    if not permission:
        raise HTTPException(400, "Anda harus mempunyai hak untuk menggunakan video.")
    try:
        count, duration, aspect = clean_options(clip_count, clip_duration, aspect)
    except ValueError as e:
        raise HTTPException(422, str(e))
    ext = Path(file.filename or "").suffix.lower()
    if ext not in (".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"):
        raise HTTPException(400, "Tipe video harus MP4, MOV, MKV, WEBM, AVI, atau M4V.")
    jid, folder = new_job({"type": "upload"}, {})
    dest = folder / "upload_source"
    size = 0
    try:
        with dest.open("wb") as fh:
            while chunk := await file.read(1024*1024):
                size += len(chunk)
                if size > MAX_FILE_MB * 1024 * 1024:
                    raise ValueError(f"Ukuran file maksimal {MAX_FILE_MB} MB.")
                fh.write(chunk)
    except Exception as e:
        shutil.rmtree(folder, ignore_errors=True)
        with LOCK:
            JOBS.pop(jid, None)
        raise HTTPException(413, str(e))
    finally:
        await file.close()
    if not size:
        shutil.rmtree(folder, ignore_errors=True)
        with LOCK:
            JOBS.pop(jid, None)
        raise HTTPException(400, "File kosong.")
    options = {"clip_count":count,"clip_duration":duration,"aspect":aspect,"captions":captions,"use_ai":use_ai}
    source = {"type":"upload", "name": Path(file.filename or "Video baru").name}
    start_processing(jid, source, options)
    return {"job_id": jid}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    with LOCK:
        job = JOBS.get(job_id)
        if not job:
            raise HTTPException(404, "Job tidak ditemukan (job lama akan hilang setelah server restart).")
        return dict(job)


@app.get("/api/files/{job_id}/{filename}")
def get_file(job_id: str, filename: str):
    if not re.fullmatch(r"[a-f0-9]{16}", job_id):
        raise HTTPException(404, "Berkas tidak ditemukan.")
    if not re.fullmatch(r"(?:viralcut_\d{2}\.(?:mp4|srt)|thumb_\d{2}\.jpg)", filename):
        raise HTTPException(404, "Berkas tidak ditemukan.")
    path = WORK / job_id / filename
    if not path.is_file():
        raise HTTPException(404, "Berkas tidak ditemukan.")
    return FileResponse(path, filename=filename if filename.endswith(".srt") else None)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=os.getenv("HOST", "0.0.0.0"), port=int(os.getenv("PORT", "8000")))
