from flask import Flask, render_template, request, jsonify, send_file
import os
import yt_dlp
import time
import threading

app = Flask(__name__)

DOWNLOAD_FOLDER = "downloads"
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

# Cookies file — place cookies.txt in project root (same folder as emon.py)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
COOKIES_FILE = os.path.join(BASE_DIR, "cookies.txt")

# Track download progress per task
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
# BUILD YDL OPTS
# =========================
def build_ydl_opts(fmt, file_path, progress_hook, platform):
    base = {
        'quiet': True,
        'noplaylist': True,
        'progress_hooks': [progress_hook],
        'socket_timeout': 30,
        'retries': 5,
        'http_headers': {
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/124.0.0.0 Safari/537.36'
            )
        },
    }

    # ── YouTube specific ──────────────────────────────────────
    if platform == "youtube":
        # Always attach cookies if file exists
        if os.path.exists(COOKIES_FILE):
            base['cookiefile'] = COOKIES_FILE
            print(f"[COOKIES] Loaded: {COOKIES_FILE}")
        else:
            print(f"[COOKIES] WARNING: cookies.txt not found at {COOKIES_FILE}")

        base['extractor_args'] = {
            'youtube': {
                # tv_embedded is least-restricted on server IPs
                'player_client': ['tv_embedded', 'web', 'mweb'],
            }
        }

    # ── Format ───────────────────────────────────────────────
    if fmt == "mp3":
        base['format'] = 'bestaudio/best'
        base['outtmpl'] = file_path.replace('.mp3', '.%(ext)s')
        base['postprocessors'] = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }]
    else:
        base['format'] = 'best[ext=mp4]/best[ext=webm]/best'
        base['outtmpl'] = file_path

    return base


# =========================
# DOWNLOAD VIDEO/AUDIO
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

        # Init progress
        progress_store[task_id] = {"percent": 0, "status": "starting"}

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

        ydl_opts = build_ydl_opts(fmt, file_path, progress_hook, platform)

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get("title", "Video")

        # mp3: yt-dlp renames the output — find the actual .mp3 file
        if fmt == "mp3":
            for f in os.listdir(DOWNLOAD_FOLDER):
                if f.startswith(f"video_{task_id}") and f.endswith(".mp3"):
                    file_path = os.path.join(DOWNLOAD_FOLDER, f)
                    safe_name = f
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
                title    = lines[0] if len(lines) > 0 else "Unknown"
                safe_name = lines[1] if len(lines) > 1 else ""
                fmt      = lines[2] if len(lines) > 2 else "mp4"
                platform = lines[3] if len(lines) > 3 else "unknown"
                ts       = float(lines[4]) if len(lines) > 4 else 0

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
