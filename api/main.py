from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
import requests
import random
import os

app = FastAPI()

# Vercel環境では、カレントディレクトリからの相対パスが不安定になるため、
# 絶対パスを取得して静的ファイルを指定します。
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(BASE_DIR, "statics")

# ===============================
# Static Files Routing
# ===============================
# UIから /static/filename でアクセスできるようにマウント
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
# API Base Lists
# ===============================
# 稼働率の高いインスタンスに厳選
VIDEO_APIS = [
    "https://inv.tux.pizza",
    "https://invidious.flokinet.to",
    "https://invidious.io.lol",
    "https://iv.melmac.space",
    "https://invidious.perennialte.ch",
    "https://yt.artemislena.eu"
]

SEARCH_APIS = VIDEO_APIS.copy()

# 重複排除
VIDEO_APIS = list(dict.fromkeys(VIDEO_APIS))
SEARCH_APIS = list(dict.fromkeys(SEARCH_APIS))

STREAM_YTDL_API_BASE_URL = "https://yudlp.vercel.app/stream/"
HLS_API_BASE_URL = "https://yudlp.vercel.app/m3u8/"

# 各リクエストのタイムアウトを短くして、Vercel全体の10秒制限を超えないようにする
TIMEOUT = 3 
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"}

# ===============================
# Utils
# ===============================
def try_json(url, params=None):
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print(f"Request error: {e}")
    return None

# ===============================
# Music Search API
# ===============================
@app.get("/api/search")
def api_search(q: str):
    results = []
    random.shuffle(SEARCH_APIS)
    
    music_query = f"{q} topic audio"

    for base in SEARCH_APIS:
        data = try_json(
            f"{base}/api/v1/search",
            {"q": music_query, "type": "video"}
        )
        if not isinstance(data, list):
            continue

        for v in data:
            if not v.get("videoId"):
                continue

            results.append({
                "videoId": v.get("videoId"),
                "title": v.get("title"),
                "author": v.get("author"),
                "lengthSeconds": v.get("lengthSeconds") or 0,
                "thumbnail": f"https://img.youtube.com/vi/{v.get('videoId')}/mqdefault.jpg"
            })

        if results:
            return {"results": results, "source": base}

    raise HTTPException(status_code=503, detail="Search unavailable")

# ===============================
# Audio Stream URL API
# ===============================
@app.get("/api/streamurl")
def api_streamurl(video_id: str):
    # 1. Invidious Adaptive Formats (音声のみ) を最優先（これが一番確実）
    random.shuffle(VIDEO_APIS)
    for base in VIDEO_APIS:
        try:
            res = requests.get(f"{base}/api/v1/videos/{video_id}", headers=HEADERS, timeout=TIMEOUT)
            if res.status_code != 200:
                continue
            data = res.json()

            audio_streams = [
                f for f in data.get("adaptiveFormats", [])
                if "audio/" in f.get("type", "")
            ]
            
            if audio_streams:
                # ビットレート 128k (m4a) 周辺を狙う
                best_audio = sorted(audio_streams, key=lambda x: x.get("bitrate", 0), reverse=True)[0]
                stream_url = best_audio["url"]
                # 相対パスの場合はベースURLを付加
                if stream_url.startswith("/"):
                    stream_url = base + stream_url
                return RedirectResponse(stream_url)

        except:
            continue

    # 2. HLS (m3u8) 試行
    try:
        data = try_json(f"{HLS_API_BASE_URL}{video_id}")
        if data and "m3u8_formats" in data:
            m3u8s = [f for f in data.get("m3u8_formats", []) if f.get("url")]
            if m3u8s:
                best = sorted(m3u8s, key=lambda f: int((f.get("resolution") or "0x0").split("x")[-1]), reverse=True)[0]
                return RedirectResponse(best["url"])
    except:
        pass

    # 3. 外部プロキシ
    try:
        return RedirectResponse(f"{STREAM_YTDL_API_BASE_URL}{video_id}")
    except:
        pass

    raise HTTPException(status_code=503, detail="Stream unavailable")
