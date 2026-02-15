from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
import httpx
import asyncio
import os
import time
from typing import Dict

app = FastAPI()

# ===============================
# Paths
# ===============================
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(BASE_DIR, "statics")

if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.get("/")
def root():
    index_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"status": "index.html not found", "path": index_path}

@app.get("/playlist")
def playlist_page():
    playlist_path = os.path.join(STATIC_DIR, "playlist.html")
    if os.path.exists(playlist_path):
        return FileResponse(playlist_path)
    return {"status": "playlist.html not found"}

# ===============================
# API Lists
# ===============================
VIDEO_APIS = list(dict.fromkeys([
    "https://iv.melmac.space",
    "https://pol1.iv.ggtyler.dev",
    "https://cal1.iv.ggtyler.dev",
    "https://invidious.0011.lt",
    "https://yt.omada.cafe",
    "https://invidious.exma.de",
    "https://invidious.f5.si",
    "https://siawaseok-wakame-server2.glitch.me",
    "https://lekker.gay",
    "https://id.420129.xyz"
]))

SEARCH_APIS = VIDEO_APIS.copy()

STREAM_YTDL_API_BASE_URL = "https://yudlp.vercel.app/stream/"
HLS_API_BASE_URL = "https://yudlp.vercel.app/m3u8/"

TIMEOUT = 5

# ===============================
# 優先度スコア
# ===============================
api_scores: Dict[str, int] = {base: 0 for base in VIDEO_APIS}

def sorted_apis(api_list):
    return sorted(api_list, key=lambda x: api_scores.get(x, 0), reverse=True)

def update_score(base, success, elapsed):
    if success:
        api_scores[base] += 3
        if elapsed > 2:
            api_scores[base] -= 1
    else:
        api_scores[base] -= 2

# ===============================
# Async Client
# ===============================
client = httpx.AsyncClient(
    timeout=TIMEOUT,
    headers={"User-Agent": "Mozilla/5.0"},
    limits=httpx.Limits(max_keepalive_connections=20, max_connections=50)
)

async def try_json(url, params=None):
    try:
        r = await client.get(url, params=params)
        if r.status_code == 200:
            return r.json()
    except:
        return None
    return None

# ===============================
# Music Search
# ===============================
@app.get("/api/search")
async def api_search(q: str):
    music_query = f"{q} topic audio"

    async def fetch(base):
        start = time.time()
        data = await try_json(
            f"{base}/api/v1/search",
            {"q": music_query, "type": "video"}
        )
        elapsed = time.time() - start

        if not isinstance(data, list):
            update_score(base, False, elapsed)
            return None

        results = []
        for v in data:
            vid = v.get("videoId")
            if not vid:
                continue
            results.append({
                "videoId": vid,
                "title": v.get("title"),
                "author": v.get("author"),
                "lengthSeconds": v.get("lengthSeconds") or 0,
                "thumbnail": f"https://img.youtube.com/vi/{vid}/mqdefault.jpg"
            })

        if results:
            update_score(base, True, elapsed)
            return {"results": results, "source": base}

        update_score(base, False, elapsed)
        return None

    tasks = [fetch(base) for base in sorted_apis(SEARCH_APIS)]

    for coro in asyncio.as_completed(tasks):
        result = await coro
        if result:
            return result

    raise HTTPException(status_code=503, detail="Search unavailable")

# ===============================
# Stream URL
# ===============================
@app.get("/api/streamurl")
async def api_streamurl(video_id: str):

    # 1. HLS優先
    data = await try_json(f"{HLS_API_BASE_URL}{video_id}")
    if data:
        m3u8s = [f for f in data.get("m3u8_formats", []) if f.get("url")]
        if m3u8s:
            best = max(
                m3u8s,
                key=lambda f: int((f.get("resolution") or "0x0").split("x")[-1])
            )
            return RedirectResponse(best["url"])

    # 2. Invidious 並列
    async def fetch_video(base):
        start = time.time()
        data = await try_json(f"{base}/api/v1/videos/{video_id}")
        elapsed = time.time() - start

        if not data:
            update_score(base, False, elapsed)
            return None

        audio_streams = [
            f for f in data.get("adaptiveFormats", [])
            if "audio/" in f.get("type", "")
        ]
        if audio_streams:
            best_audio = max(audio_streams, key=lambda x: x.get("bitrate", 0))
            update_score(base, True, elapsed)
            return best_audio.get("url")

        for f in data.get("formatStreams", []):
            if f.get("url"):
                update_score(base, True, elapsed)
                return f["url"]

        update_score(base, False, elapsed)
        return None

    tasks = [fetch_video(base) for base in sorted_apis(VIDEO_APIS)]

    for coro in asyncio.as_completed(tasks):
        url = await coro
        if url:
            return RedirectResponse(url)

    # 3. fallback
    data = await try_json(f"{STREAM_YTDL_API_BASE_URL}{video_id}")
    if data:
        for f in data.get("formats", []):
            if f.get("itag") in ["140", "18"] and f.get("url"):
                return RedirectResponse(f["url"])

    raise HTTPException(status_code=503, detail="Stream unavailable")
