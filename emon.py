from flask import Flask, render_template, request, send_file
from pytube import YouTube
import os

app = Flask(__name__)

DOWNLOAD_FOLDER = "downloads"
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

@app.route('/')
def index():
    return render_template('emon.html')

@app.route('/download', methods=['POST'])
def download():
    url = request.form.get('url')

    try:
        yt = YouTube(url)
        stream = yt.streams.get_highest_resolution()

        file_path = stream.download(output_path=DOWNLOAD_FOLDER)

        return send_file(file_path, as_attachment=True)

    except Exception as e:
        return f"Error: {str(e)}"

if __name__ == "__main__":
    app.run(debug=True)
