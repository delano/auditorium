"""Storage checks independent of the HTTP viewer and any working ledger."""
from pathlib import Path
import sqlite3
import tempfile
import unittest
from database_files import create_database

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

if __name__=='__main__': unittest.main()
