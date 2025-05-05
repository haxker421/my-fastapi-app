import os
import io
import tempfile
import shutil
import zipfile
import logging
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

from flask import Flask, request, send_file, jsonify
from flask_sqlalchemy import SQLAlchemy
import yt_dlp

app = Flask(__name__, static_folder='static')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///downloads.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

logging.basicConfig(level=logging.INFO)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/94.0.4606.81 Safari/537.36"
)
MAX_DOWNLOAD_RETRIES = 2
MAX_WORKERS = 4

class DownloadHistory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    url = db.Column(db.String(500), nullable=False)
    file_format = db.Column(db.String(10), nullable=False)
    quality = db.Column(db.String(20))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

    def as_dict(self):
        return {
            'id': self.id,
            'url': self.url,
            'file_format': self.file_format,
            'quality': self.quality,
            'timestamp': self.timestamp.isoformat() + 'Z'
        }

with app.app_context():
    db.create_all()

def get_ydl_opts(file_format, quality, output_template):
    # Choose best quality for all platforms (YT shorts, Reels, TikTok, FB, etc.)
    if file_format == 'mp4':
        # 'best' means bestvideo+bestaudio; users always get highest resolution
        fmt = 'bestvideo+bestaudio/best'
        return {
            'format': fmt,
            'outtmpl': output_template,
            'quiet': True,
            'ignoreerrors': False,
            'nocheckcertificate': True,
            'user_agent': DEFAULT_USER_AGENT,
            'merge_output_format': 'mp4',
            'retries': MAX_DOWNLOAD_RETRIES,
            'concurrent_fragment_downloads': 5,
        }
    elif file_format == 'mp3':
        return {
            'format': 'bestaudio/best',
            'outtmpl': output_template,
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'quiet': True,
            'ignoreerrors': False,
            'nocheckcertificate': True,
            'user_agent': DEFAULT_USER_AGENT,
            'retries': MAX_DOWNLOAD_RETRIES,
        }
    elif file_format in ['jpg', 'png']:
        return {
            'skip_download': True,
            'write_thumbnail': True,
            'outtmpl': output_template,
            'quiet': True,
            'ignoreerrors': False,
            'nocheckcertificate': True,
        }
    else:
        raise ValueError(f'Unsupported format: {file_format}')

def download_single(url, file_format, quality, output_template):
    opts = get_ydl_opts(file_format, quality, output_template)
    for attempt in range(MAX_DOWNLOAD_RETRIES):
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.extract_info(url, download=True)
            break
        except Exception as e:
            logging.warning(f"Attempt {attempt+1} failed: {e}")
            if attempt == MAX_DOWNLOAD_RETRIES - 1:
                raise

    # Find the downloaded file
    directory = os.path.dirname(output_template)
    prefix = os.path.basename(output_template).split('.')[0]
    for f in os.listdir(directory):
        if f.startswith(prefix) and f.lower().endswith(file_format):
            return os.path.join(directory, f)
    raise FileNotFoundError(f"No .{file_format} found for {prefix}")

@app.route('/')
def index():
    return "JustPaste Backend Running!"

@app.route('/download_get', methods=['GET'])
def download_get():
    url = request.args.get('url')
    fmt = request.args.get('format')
    quality = request.args.get('quality') or 'best'
    if not url or not fmt:
        return jsonify({'error': 'Missing parameters'}), 400

    tmp = tempfile.mkdtemp()
    try:
        out = os.path.join(tmp, 'dl.%(ext)s')
        path = download_single(url, fmt, quality, out)
        # record history
        db.session.add(DownloadHistory(url=url, file_format=fmt, quality=quality))
        db.session.commit()
        return send_file(path, as_attachment=True,
                         download_name=f"JustPaste.{fmt}")
    except Exception as e:
        logging.exception("Download error")
        return jsonify({'error': str(e)}), 500
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

@app.route('/history', methods=['GET'])
def history():
    recs = DownloadHistory.query.order_by(DownloadHistory.timestamp.desc()).all()
    return jsonify([r.as_dict() for r in recs])

if __name__ == '__main__':
    app.run(host='0.0.0.0', debug=True)