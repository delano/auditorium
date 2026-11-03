"""Storage checks independent of the HTTP viewer and any working ledger."""
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from database_files import create_database, file_dialog, DIALOG_LOCK

class DatabaseFilesTests(unittest.TestCase):
    def test_new_file_has_one_draft_and_no_findings(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'audit.sqlite'
            create_database(str(path),Path(directory)/'docs')
            with sqlite3.connect(path) as conn:
                self.assertEqual(conn.execute('SELECT count(*) FROM findings').fetchone()[0],0)
                self.assertEqual(conn.execute('SELECT status,is_current FROM batches').fetchall(),[('draft',1)])
                conn.execute("INSERT INTO metadata(key,value) VALUES('example','created')")
                created=conn.execute("SELECT created_at FROM metadata WHERE key='example'").fetchone()[0]
                conn.execute("UPDATE metadata SET modified_at='2000-01-01T00:00:00Z' WHERE key='example'")
                conn.execute("UPDATE metadata SET value='changed' WHERE key='example'")
                self.assertEqual(conn.execute("SELECT created_at FROM metadata WHERE key='example'").fetchone()[0],created)
                self.assertNotEqual(conn.execute("SELECT modified_at FROM metadata WHERE key='example'").fetchone()[0],'2000-01-01T00:00:00Z')
                self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_creation_refuses_to_replace_an_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'existing.sqlite'
            path.write_bytes(b'existing user data')
            with self.assertRaisesRegex(ValueError,'already exists'):
                create_database(str(path),Path(directory))
            self.assertEqual(path.read_bytes(),b'existing user data')

    @patch('database_files.sys.platform','darwin')
    def test_picker_errors_release_the_dialog_lock(self):
        failures = [
            (FileNotFoundError(), 'make native-picker'),
            (subprocess.TimeoutExpired('picker',300), 'timed out'),
        ]
        for error,message in failures:
            with self.subTest(error=error), patch('database_files.subprocess.run',side_effect=error):
                with self.assertRaisesRegex(ValueError,message):
                    file_dialog('load',Path('/tmp'))
                self.assertFalse(DIALOG_LOCK.locked())
        for output in ('not json','null','123'):
            result=subprocess.CompletedProcess([],0,stdout=output,stderr='')
            with self.subTest(output=output), patch('database_files.subprocess.run',return_value=result):
                with self.assertRaisesRegex(ValueError,'invalid path'):
                    file_dialog('load',Path('/tmp'))
                self.assertFalse(DIALOG_LOCK.locked())

    @patch('database_files.sys.platform','linux')
    def test_other_platforms_explain_the_path_entry_option(self):
        with patch('database_files.subprocess.run') as run:
            with self.assertRaisesRegex(ValueError,'Enter a full file path'):
                file_dialog('load',Path('/tmp'))
            run.assert_not_called()

if __name__=='__main__': unittest.main()
