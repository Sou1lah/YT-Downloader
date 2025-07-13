# ================================
# 🚀 IMPORTS & FLASK SETUP
# ================================
from flask import Flask, request, render_template, jsonify
import yt_dlp
import re

app = Flask(__name__)

# ================================
# 📦 GLOBAL PROGRESS STATE
# ================================
progress_data = {
    "status": "",
    "progress": "0%",
    "title": "",
    "current": 0,
    "total": 0
}

def strip_ansi(text):
    return re.sub(r'\x1b\[[0-9;]*m', '', text)

def progress_hook(d):
    if d['status'] == 'downloading':
        raw = strip_ansi(d.get('_percent_str', '0%')).strip()
        title = d.get('info_dict', {}).get('title', '')
        progress_data.update({
            "status": "downloading",
            "progress": raw,
            "title": title
        })
    elif d['status'] == 'finished':
        progress_data["status"] = "finished"
        progress_data["progress"] = "100%"

# ================================
# 🌐 ROUTE: Home Page
# ================================
@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")

# ================================
# 🔽 ROUTE: Download Handler
# ================================
@app.route("/download", methods=["POST"])
def download():
    # 🔁 Reset state
    progress_data.update({
        "status": "downloading",
        "progress": "0%",
        "title": "",
        "current": 0,
        "total": 0
    })

    url = request.form.get("url")
    download_type = request.form.get("download_type")
    quality = request.form.get("quality")

    if not url:
        return "Missing URL", 408

    # 🧠 Pre-fetch metadata
    try:
        with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
            info = ydl.extract_info(url, download=False)
            progress_data["total"] = len(info['entries']) if 'entries' in info else 1
    except Exception as e:
        return f"❌ Could not fetch info: {e}", 400

    def wrapped_hook(d):
        if d['status'] == 'finished':
            progress_data["current"] += 1
        progress_hook(d)

    if download_type == "audio":
        ydl_opts = {
            'format': 'bestaudio',
            'outtmpl': '~/Music/YT-Downloader/%(title)s.%(ext)s',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': quality
            }],
            'progress_hooks': [wrapped_hook]
        }
    else:
        fmt = f"bestvideo[height<={quality}]+bestaudio/best[height<={quality}]"
        ydl_opts = {
            'format': fmt,
            'outtmpl': '~/Music/%(title)s.%(ext)s',
            'merge_output_format': 'mp4',
            'progress_hooks': [wrapped_hook]
        }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        return "✅ Download complete"
    except Exception as e:
        return f"❌ Error: {e}", 500

# ================================
# 📊 ROUTE: Progress Polling API
# ================================
@app.route("/progress", methods=["GET"])
def progress():
    return jsonify(progress_data)

# ===============================
# 🏁 RUN FLASK APP
# ================================
if __name__ == "__main__":
    app.run(debug=True)
