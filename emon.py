from flask import Flask, render_template, request, jsonify, send_file
import os
import yt_dlp
import time
import threading
import requests as req

app = Flask(__name__)

DOWNLOAD_FOLDER = "downloads"
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
COOKIES_FILE = os.path.join(BASE_DIR, "cookies.txt")

# ─────────────────────────────────────────────
# Cobalt public API instances (auto-rotated)
# These are community-hosted, no bot detection
# ─────────────────────────────────────────────
COBALT_INSTANCES = [
    "https://cobalt-api.kwiatekmiki.com",
    "https://cobalt.api.timelessnesses.me",
    "https://cobalt.ggtyler.dev",
    "https://cobalt-backend.canine.tools",
    "https://api.cobalt.tools",
]

progress_store = {}


# =========================
# AUTO DELETE FUNCTION
# =========================
def delete_file_after_delay(path, delay=120):
    def worker():
        time.sleep(delay)
        try:
            if os.path.exists(path):
                os.remove(path)
                print(f"[AUTO-DELETE] Removed: {path}")
        except Exception as e:
            print("Delete error:", e)
    threading.Thread(target=worker, daemon=True).start()


# =========================
# HOME ROUTE
# =========================
@app.route('/')
def home():
    return render_template("emon.html")


# =========================
# DETECT PLATFORM
# =========================
def detect_platform(url):
    url_lower = url.lower()
    if "youtube.com" in url_lower or "youtu.be" in url_lower:
        return "youtube"
    elif "facebook.com" in url_lower or "fb.watch" in url_lower or "fb.com" in url_lower:
        return "facebook"
    else:
        return "unknown"


# =========================
# COBALT API DOWNLOAD
# Tries each instance in order until one works
# =========================
def cobalt_download(url, fmt, file_path, task_id):
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    payload = {
        "url": url,
        "videoQuality": "720",
        "filenameStyle": "basic",
    }

    if fmt == "mp3":
        payload["downloadMode"] = "audio"
        payload["audioFormat"] = "mp3"
        payload["audioBitrate"] = "192"
    else:
        payload["downloadMode"] = "auto"
        payload["videoFormat"] = "mp4"

    last_error = "All Cobalt instances failed"

    for instance in COBALT_INSTANCES:
        try:
            print(f"[COBALT] Trying: {instance}")
            progress_store[task_id] = {"percent": 10, "status": "downloading"}

            resp = req.post(
                f"{instance}/",
                json=payload,
                headers=headers,
                timeout=15
            )

            if resp.status_code != 200:
                print(f"[COBALT] {instance} returned {resp.status_code}")
                continue

            data = resp.json()
            status = data.get("status", "")

            # Cobalt returns a direct stream URL or redirect URL
            if status in ("stream", "redirect", "tunnel"):
                download_url = data.get("url")
                if not download_url:
                    continue

                progress_store[task_id] = {"percent": 40, "status": "downloading"}

                # Stream the file to disk
                file_resp = req.get(download_url, stream=True, timeout=60)
                file_resp.raise_for_status()

                total = int(file_resp.headers.get("content-length", 0))
                downloaded = 0

                with open(file_path, "wb") as f:
                    for chunk in file_resp.iter_content(chunk_size=65536):
                        if chunk:
                            f.write(chunk)
                            downloaded += len(chunk)
                            if total > 0:
                                pct = min(95, 40 + int((downloaded / total) * 55))
                                progress_store[task_id] = {
                                    "percent": pct,
                                    "status": "downloading"
                                }

                print(f"[COBALT] Success via {instance}")
                return True, None

            elif status == "picker":
                # Multiple streams — pick the first video
                items = data.get("picker", [])
                if items:
                    download_url = items[0].get("url")
                    if download_url:
                        file_resp = req.get(download_url, stream=True, timeout=60)
                        file_resp.raise_for_status()
                        with open(file_path, "wb") as f:
                            for chunk in file_resp.iter_content(chunk_size=65536):
                                if chunk:
                                    f.write(chunk)
                        return True, None

            elif status == "error":
                last_error = data.get("error", {}).get("code", "unknown cobalt error")
                print(f"[COBALT] {instance} error: {last_error}")
                continue
            else:
                print(f"[COBALT] {instance} unknown status: {status}")
                continue

        except Exception as e:
            print(f"[COBALT] {instance} exception: {e}")
            last_error = str(e)
            continue

    return False, last_error


# =========================
# YT-DLP FALLBACK
# Used if all Cobalt instances fail
# =========================
def ytdlp_download(url, fmt, file_path, progress_hook, platform):
    base = {
        'quiet': True,
        'noplaylist': True,
        'progress_hooks': [progress_hook],
        'socket_timeout': 30,
        'retries': 3,
        'http_headers': {
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/124.0.0.0 Safari/537.36'
            )
        },
    }

    if platform == "youtube":
        if os.path.exists(COOKIES_FILE):
            base['cookiefile'] = COOKIES_FILE
        base['extractor_args'] = {
            'youtube': {
                'player_client': ['tv_embedded', 'web', 'mweb'],
            }
        }

    if fmt == "mp3":
        base['format'] = 'bestaudio/best'
        base['outtmpl'] = file_path.replace('.mp3', '.%(ext)s')
        base['postprocessors'] = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }]
    else:
        base['format'] = (
            'bestvideo[ext=mp4]+bestaudio[ext=m4a]'
            '/bestvideo+bestaudio'
            '/best[ext=mp4]'
            '/best'
        )
        base['outtmpl'] = file_path
        base['merge_output_format'] = 'mp4'

    with yt_dlp.YoutubeDL(base) as ydl:
        info = ydl.extract_info(url, download=True)
        return info.get("title", "Video")


# =========================
# DOWNLOAD ROUTE
# =========================
@app.route('/download', methods=['POST'])
def download():
    try:
        data = request.get_json(force=True)
        url = data.get("url")
        fmt = data.get("format", "mp4")
        task_id = data.get("task_id", str(int(time.time())))

        if not url:
            return jsonify({"error": "No URL provided"})

        platform = detect_platform(url)
        if platform == "unknown":
            return jsonify({"error": "Only YouTube and Facebook URLs are supported."})

        ext = "mp3" if fmt == "mp3" else "mp4"
        safe_name = f"video_{task_id}.{ext}"
        file_path = os.path.join(DOWNLOAD_FOLDER, safe_name)

        progress_store[task_id] = {"percent": 0, "status": "starting"}
        title = "Video"

        # ── 1. Try Cobalt API first (no bot issues) ──
        print(f"[DOWNLOAD] Trying Cobalt API for: {url}")
        cobalt_ok, cobalt_err = cobalt_download(url, fmt, file_path, task_id)

        if cobalt_ok and os.path.exists(file_path) and os.path.getsize(file_path) > 1024:
            print("[DOWNLOAD] Cobalt succeeded")
            # Try to get title via yt-dlp info extraction (no download)
            try:
                with yt_dlp.YoutubeDL({'quiet': True, 'skip_download': True}) as ydl:
                    info = ydl.extract_info(url, download=False)
                    title = info.get("title", "Video")
            except Exception:
                title = "Video"

        else:
            # ── 2. Fallback to yt-dlp ──
            print(f"[DOWNLOAD] Cobalt failed ({cobalt_err}), falling back to yt-dlp")
            progress_store[task_id] = {"percent": 5, "status": "downloading"}

            def progress_hook(d):
                if d['status'] == 'downloading':
                    pct = d.get('_percent_str', '0%').strip().replace('%', '')
                    try:
                        progress_store[task_id] = {
                            "percent": float(pct),
                            "status": "downloading"
                        }
                    except Exception:
                        pass
                elif d['status'] == 'finished':
                    progress_store[task_id] = {"percent": 100, "status": "finished"}

            title = ytdlp_download(url, fmt, file_path, progress_hook, platform)

        # mp3: find the renamed file
        if fmt == "mp3" and not os.path.exists(file_path):
            for f in os.listdir(DOWNLOAD_FOLDER):
                if f.startswith(f"video_{task_id}") and f.endswith(".mp3"):
                    file_path = os.path.join(DOWNLOAD_FOLDER, f)
                    safe_name = f
                    break

        # mp4: yt-dlp may have saved as .webm — rename it
        if fmt == "mp4" and not os.path.exists(file_path):
            for f in os.listdir(DOWNLOAD_FOLDER):
                if f.startswith(f"video_{task_id}") and not f.endswith(".meta"):
                    actual = os.path.join(DOWNLOAD_FOLDER, f)
                    os.rename(actual, file_path)
                    break

        # Write metadata sidecar
        meta_path = os.path.join(DOWNLOAD_FOLDER, f"video_{task_id}.meta")
        with open(meta_path, 'w', encoding='utf-8') as mf:
            mf.write(f"{title}\n{safe_name}\n{fmt}\n{platform}\n{time.time()}")

        delete_file_after_delay(file_path, delay=120)
        delete_file_after_delay(meta_path, delay=125)

        progress_store[task_id] = {"percent": 100, "status": "done"}

        return jsonify({
            "title": title,
            "download_url": f"/get-video?file={safe_name}",
            "file": safe_name,
            "format": fmt,
            "platform": platform,
            "task_id": task_id
        })

    except Exception as e:
        if 'task_id' in locals():
            progress_store[task_id] = {"percent": 0, "status": "error", "message": str(e)}
        return jsonify({"error": str(e)})


# =========================
# PROGRESS ENDPOINT
# =========================
@app.route('/progress')
def progress():
    task_id = request.args.get("task_id")
    info = progress_store.get(task_id, {"percent": 0, "status": "unknown"})
    return jsonify(info)


# =========================
# LIST STORED VIDEOS
# =========================
@app.route('/list-videos')
def list_videos():
    files = []
    try:
        for f in os.listdir(DOWNLOAD_FOLDER):
            if f.endswith(".meta"):
                meta_path = os.path.join(DOWNLOAD_FOLDER, f)
                with open(meta_path, 'r', encoding='utf-8') as mf:
                    lines = mf.read().splitlines()
                title     = lines[0] if len(lines) > 0 else "Unknown"
                safe_name = lines[1] if len(lines) > 1 else ""
                fmt       = lines[2] if len(lines) > 2 else "mp4"
                platform  = lines[3] if len(lines) > 3 else "unknown"
                ts        = float(lines[4]) if len(lines) > 4 else 0

                file_path = os.path.join(DOWNLOAD_FOLDER, safe_name)
                if os.path.exists(file_path):
                    size = os.path.getsize(file_path)
                    expires_in = max(0, int(120 - (time.time() - ts)))
                    files.append({
                        "title": title,
                        "file": safe_name,
                        "format": fmt,
                        "platform": platform,
                        "size_mb": round(size / (1024 * 1024), 2),
                        "expires_in": expires_in,
                        "download_url": f"/get-video?file={safe_name}"
                    })
    except Exception as e:
        return jsonify({"error": str(e), "files": []})

    files.sort(key=lambda x: x["expires_in"])
    return jsonify({"files": files})


# =========================
# DELETE FILE MANUALLY
# =========================
@app.route('/delete-video', methods=['POST'])
def delete_video():
    try:
        data = request.get_json(force=True)
        file = data.get("file")
        if not file or ".." in file or "/" in file:
            return jsonify({"error": "Invalid file"})

        file_path = os.path.join(DOWNLOAD_FOLDER, file)
        task_id = file.replace("video_", "").rsplit(".", 1)[0]
        meta_path = os.path.join(DOWNLOAD_FOLDER, f"video_{task_id}.meta")

        for p in [file_path, meta_path]:
            if os.path.exists(p):
                os.remove(p)

        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)})


# =========================
# SERVE DOWNLOADED FILE
# =========================
@app.route('/get-video')
def get_video():
    try:
        file = request.args.get("file")
        if not file or ".." in file:
            return "Invalid file", 400
        path = os.path.join(DOWNLOAD_FOLDER, file)
        if not os.path.exists(path):
            return "File not found or expired", 404
        return send_file(path, as_attachment=True)
    except Exception as e:
        return f"Error: {str(e)}", 500


# =========================
# RUN APP
# =========================
if __name__ == "__main__":
    app.run(debug=True, port=8081)
