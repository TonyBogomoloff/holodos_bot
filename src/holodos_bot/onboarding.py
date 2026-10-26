"""The shared demo and its persistent, bot-specific Telegram upload cache."""
import hashlib
import sqlite3
from contextlib import closing
from pathlib import Path

from .providers import ProviderError

VIDEO = Path(__file__).resolve().parents[2] / 'output/remotion/holodos-v1/holodos-intro-v1-ru.mp4'
CACHE_NAME = 'telegram-video-cache.sqlite3'
CAPTION = 'Что умеет Холодос — за 30 секунд 🧡'


def video_payload(owner, file_id):
    return {'chat_id': owner, 'video': file_id, 'caption': CAPTION,
            'supports_streaming': True, 'duration': 30, 'width': 720, 'height': 1280}


class VideoCache:
    def __init__(self, path, bot_id):
        self.path, self.bot_id = Path(path), str(bot_id)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS videos ('
                       'bot_id TEXT NOT NULL, sha256 TEXT NOT NULL, file_id TEXT NOT NULL, '
                       'PRIMARY KEY(bot_id,sha256))')
        self.path.chmod(0o600)

    @staticmethod
    def digest(path):
        with Path(path).open('rb') as handle:
            return hashlib.file_digest(handle, 'sha256').hexdigest()

    def get(self, digest):
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            row = db.execute('SELECT file_id FROM videos WHERE bot_id=? AND sha256=?',
                             (self.bot_id, digest)).fetchone()
        return row[0] if row else None

    def put(self, digest, file_id):
        if not isinstance(file_id, str) or not file_id:
            raise ProviderError('telegram_video_file_id_missing')
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            db.execute('INSERT INTO videos VALUES (?,?,?) ON CONFLICT(bot_id,sha256) '
                       'DO UPDATE SET file_id=excluded.file_id', (self.bot_id, digest, file_id))

    def discard(self, digest, file_id):
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            db.execute('DELETE FROM videos WHERE bot_id=? AND sha256=? AND file_id=?',
                       (self.bot_id, digest, file_id))


async def prepare_video(telegram, cache, owner=None, *, check_only=False, path=VIDEO):
    """Preload only at deployment; runtime never uploads the file to a user."""
    digest = cache.digest(path)
    file_id = cache.get(digest)
    if file_id:
        try:
            await telegram.call('getFile', file_id=file_id)
        except ProviderError as error:
            if str(error) != 'telegram_file_invalid':
                raise
            if not check_only:
                cache.discard(digest, file_id)
            file_id = None
    uploaded = not file_id
    if not file_id:
        if check_only:
            raise ProviderError('video_cache_not_ready')
        if type(owner) is not int or owner <= 0:
            raise ProviderError('video_cache_chat_required')
        # This private preparation message is removed as soon as its file_id is saved.
        # An ambiguous transport failure is never retried blindly.
        result = await telegram.video(owner, path, caption='Подготовка видео', silent=True)
        try:
            file_id = result.get('video', {}).get('file_id')
            cache.put(digest, file_id)
        finally:
            await telegram.call('deleteMessage', chat_id=owner, message_id=result['message_id'])
        await telegram.call('getFile', file_id=file_id)
    return {'ready': True, 'sha256': digest, 'uploaded': uploaded}
