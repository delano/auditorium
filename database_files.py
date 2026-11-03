"""Current-format database creation and macOS system file dialogs."""
from datetime import datetime
from pathlib import Path
import json
import os
import sqlite3
import subprocess
import sys
import threading

HERE = Path(__file__).resolve().parent
DIALOG_LOCK = threading.Lock()
NATIVE_PICKER = HERE / '.build' / 'Auditorium Picker.app' / 'Contents' / 'MacOS' / 'AuditoriumPicker'


def file_dialog(operation, directory):
    if operation not in ('load', 'create'):
        raise ValueError('Choose load or create for the file dialog.')
    if sys.platform != 'darwin':
        raise ValueError('Native file dialogs require macOS. Enter a full file path instead.')
    if not DIALOG_LOCK.acquire(blocking=False):
        raise ValueError('A system file dialog is already open. Finish or cancel it first.')
    try:
        result = subprocess.run(
            [str(NATIVE_PICKER), operation, str(directory),
             'docs-content-audit-'+datetime.now().strftime('%Y-%m-%d')+'.sqlite'],
            capture_output=True, text=True, timeout=300, check=False,
        )
        if result.returncode:
            raise ValueError('The system file dialog could not complete: '+result.stderr.strip())
        try:
            path = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise ValueError('The native file picker returned an invalid path.') from exc
        if not isinstance(path, str):
            raise ValueError('The native file picker returned an invalid path.')
        return path
    except FileNotFoundError as exc:
        raise ValueError('Build the native file picker with "make native-picker" in the Auditorium directory.') from exc
    except subprocess.TimeoutExpired as exc:
        raise ValueError('The system file dialog timed out. Try again.') from exc
    finally:
        DIALOG_LOCK.release()


def create_database(value, repository):
    """Initialize a fresh current-format file; never overwrite or copy audit data."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError('Choose a destination for the new SQLite file.')
    path = Path(value.strip()).expanduser()
    if not path.is_absolute():
        raise ValueError('Use an absolute file path, starting with / or ~/.')
    path = path.resolve()
    if not path.parent.is_dir():
        raise ValueError('The destination folder does not exist.')
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise ValueError('A file already exists at that path. Choose a new filename.') from exc
    os.close(descriptor)
    try:
        with sqlite3.connect(path.as_uri()+'?mode=rw', uri=True) as conn:
            conn.execute('PRAGMA foreign_keys=ON')
            conn.executescript('BEGIN IMMEDIATE;\n'+(HERE/'schema.sql').read_text())
            conn.executemany('INSERT INTO metadata(key,value) VALUES(?,?)', [
                ('purpose', 'Documentation content research and review.'),
                ('docs_repository', str(repository)),
            ])
            conn.execute('INSERT INTO batches(is_current) VALUES(1)')
        return path
    except Exception:
        path.unlink()
        raise
