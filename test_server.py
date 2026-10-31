"""Exercise the viewer against synthetic audit data and temporary Git repos."""
import json
import subprocess
from unittest.mock import patch
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from urllib.parse import quote
from http.server import ThreadingHTTPServer
from server import Handler, audit_database, TABLES
import submission
import database_files

class ViewerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = (Path(self.temp.name) / 'test.sqlite').resolve()
        self.repository = (Path(self.temp.name)/'repo').resolve()
        database_files.create_database(str(self.db), self.repository)
        with sqlite3.connect(self.db) as conn:
            for finding in ('H1','H2'):
                conn.execute('''INSERT INTO findings
                    (id,severity,confidence,category,locale,path,line,title,issue,reader_impact,
                     evidence,verification,page_url,page_branch,verbatim_content)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (finding,'medium','high','clarity','en',
                     'src/content/docs/en/example-'+finding.lower()+'.md',1,
                     'Example finding '+finding,'Example issue','Example impact',
                     'Synthetic test evidence','Synthetic test verification',
                     'https://docs.example.test/en/example/','main',
                     'Example wording about a temporary subdomain.'))
            conn.execute("INSERT INTO coverage(path,locale,publication,review_status,review_basis) VALUES('example.md','en','source page','surveyed','Synthetic test coverage')")
        audit_database(str(self.db))
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.db = self.db
        self.repository = (Path(self.temp.name)/'repo').resolve()
        self.repository.mkdir()
        self.remote = Path(self.temp.name)/'origin.git'
        subprocess.run(['git','init','--bare',str(self.remote)],check=True,capture_output=True)
        subprocess.run(['git','init','-b','main',str(self.repository)],check=True,capture_output=True)
        subprocess.run(['git','-c','user.name=Viewer Test','-c','user.email=viewer@example.test','-c','commit.gpgsign=false','commit','--allow-empty','-m','Initial'],cwd=self.repository,check=True,capture_output=True)
        self.git('remote','add','origin',str(self.remote))
        self.git('push','origin','main')
        self.server.repository = self.repository
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.origin = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def git(self,*args):
        return subprocess.run(['git',*args],cwd=self.repository,text=True,capture_output=True,check=True).stdout.strip()

    def post(self, route, payload, origin=None, database=None):
        headers={'Content-Type':'application/json', 'Origin':origin or self.origin}
        if database is not None: headers['X-Audit-Database']=quote(str(database),safe='')
        request = Request(self.origin+route, data=json.dumps(payload).encode(), headers=headers, method='POST')
        try:
            with urlopen(request) as response: return response.status, json.load(response)
        except HTTPError as exc: return exc.code, json.load(exc)

    def get(self, route, database=None):
        headers={'X-Audit-Database':quote(str(database),safe='')} if database is not None else {}
        try:
            with urlopen(Request(self.origin+route,headers=headers)) as response:
                return response.status,json.load(response)
        except HTTPError as exc: return exc.code,json.load(exc)

    def copy_database(self, name='second audit 日本語.sqlite'):
        path=(Path(self.temp.name)/name).resolve()
        with sqlite3.connect(self.db) as source,sqlite3.connect(path) as target:
            source.backup(target)
        return path

    def test_load_database_pins_reads_and_writes_without_changing_other_tabs(self):
        other=self.copy_database()
        with sqlite3.connect(other) as conn:
            conn.execute("UPDATE findings SET title='Second database only' WHERE id='H1'")
        self.assertEqual(self.post('/api/load-database',{'path':str(other)})[0],200)
        code,data=self.get('/api/data',other)
        self.assertEqual(code,200)
        self.assertEqual(data['schema']['database'],str(other))
        self.assertEqual(next(r['title'] for r in data['findings'] if r['id']=='H1'),'Second database only')
        self.assertNotEqual(next(r['title'] for r in self.get('/api/data')[1]['findings'] if r['id']=='H1'),'Second database only')
        self.assertEqual(self.post('/api/notes',{'id':'H1','fix_notes':'Only in the second file'},database=other)[0],200)
        self.assertEqual(self.post('/api/batch',{'id':'H1','in_batch':True},database=other)[0],200)
        with sqlite3.connect(other) as conn:
            self.assertEqual(conn.execute("SELECT fix_notes,in_batch FROM findings WHERE id='H1'").fetchone(),('Only in the second file',1))
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT fix_notes,in_batch FROM findings WHERE id='H1'").fetchone(),('',0))
        self.assertEqual(self.get('/api/schema',other)[1]['schema']['database'],str(other))
        self.assertEqual(self.get('/api/batches',other)[1]['submission']['plan']['finding_ids'],['H1'])
        self.assertEqual(self.get('/api/batches')[1]['submission']['plan']['finding_ids'],[])

    def test_create_database_starts_empty_with_current_schema_and_one_draft(self):
        path=Path(self.temp.name).resolve()/'New audit 日本語.sqlite'
        before=self.db.read_bytes()
        code,result=self.post('/api/create-database',{'path':str(path)})
        self.assertEqual(code,200,result)
        self.assertEqual(result['database'],str(path))
        self.assertEqual(audit_database(str(path)),path)
        code,data=self.get('/api/data',path)
        self.assertEqual(code,200,data)
        self.assertEqual(data['findings'],[])
        self.assertEqual(data['coverage'],[])
        self.assertEqual(data['batch_findings'],[])
        self.assertEqual(data['batch_pull_requests'],[])
        self.assertEqual(len(data['batches']),1)
        self.assertEqual(data['batches'][0]['status'],'draft')
        self.assertEqual(data['batches'][0]['is_current'],1)
        self.assertIsNotNone(data['batches'][0]['modified_at'])
        self.assertEqual(dict((row['key'],row['value']) for row in data['metadata'])['docs_repository'],str(self.repository))
        self.assertIn(str(path),self.get('/api/discovery',path)[1]['prompt'])
        self.assertEqual(self.db.read_bytes(),before)
        with sqlite3.connect(path) as conn:
            self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_create_database_never_overwrites_existing_files(self):
        existing=self.copy_database()
        before=existing.read_bytes()
        code,result=self.post('/api/create-database',{'path':str(existing)})
        self.assertEqual(code,400)
        self.assertIn('already exists',result['error'])
        self.assertEqual(existing.read_bytes(),before)
        for path in ('relative.sqlite','',None,str(Path(self.temp.name)/'missing'/'new.sqlite')):
            self.assertEqual(self.post('/api/create-database',{'path':path})[0],400)
        self.assertEqual(self.post('/api/create-database',{'path':str(existing)},origin='https://example.com')[0],403)

    def test_system_dialog_returns_path_or_cancellation_without_loading_files(self):
        with patch('server.file_dialog',return_value=str(self.db)) as picker:
            code,result=self.post('/api/file-dialog',{'operation':'load'})
            self.assertEqual(code,200)
            self.assertEqual(result,{'path':str(self.db),'cancelled':False})
            picker.assert_called_once_with('load',self.db.parent)
        with patch('server.file_dialog',return_value=''):
            self.assertEqual(self.post('/api/file-dialog',{'operation':'create'})[1],{'path':'','cancelled':True})
        with patch('server.file_dialog') as picker:
            self.assertEqual(self.post('/api/file-dialog',{'operation':'load'},origin='https://example.com')[0],403)
            picker.assert_not_called()
        self.assertEqual(self.post('/api/file-dialog',{'operation':'invalid'})[0],400)

    def test_native_dialog_uses_arguments_and_handles_cancel_failure_and_busy(self):
        result=subprocess.CompletedProcess([],0,stdout=str(self.db)+'\n',stderr='')
        with patch('database_files.subprocess.run',return_value=result) as run:
            self.assertEqual(database_files.file_dialog('load',self.db.parent),str(self.db))
            args=run.call_args.args[0]
            self.assertEqual(args[:2],['osascript','-e'])
            self.assertEqual(args[3:5],['load',str(self.db.parent)])
        with patch('database_files.subprocess.run',return_value=subprocess.CompletedProcess([],0,stdout='\n',stderr='')):
            self.assertEqual(database_files.file_dialog('create',self.db.parent),'')
        with patch('database_files.subprocess.run',return_value=subprocess.CompletedProcess([],1,stdout='',stderr='Dialog error')):
            with self.assertRaisesRegex(ValueError,'Dialog error'):
                database_files.file_dialog('load',self.db.parent)
        self.assertTrue(database_files.DIALOG_LOCK.acquire(blocking=False))
        try:
            with self.assertRaisesRegex(ValueError,'already open'):
                database_files.file_dialog('load',self.db.parent)
        finally:
            database_files.DIALOG_LOCK.release()

    def test_invalid_files_are_rejected_without_creating_or_modifying_them(self):
        unrelated=Path(self.temp.name)/'unrelated.sqlite'
        with sqlite3.connect(unrelated) as conn: conn.execute('CREATE TABLE example(value TEXT)')
        before=unrelated.read_bytes()
        missing=Path(self.temp.name)/'missing.sqlite'
        garbage=Path(self.temp.name)/'not-sqlite.db'
        garbage.write_text('not a SQLite database')
        for path in (str(unrelated),str(missing),str(garbage),'relative.sqlite','',None):
            with self.subTest(path=path):
                self.assertEqual(self.post('/api/load-database',{'path':path})[0],400)
        self.assertFalse(missing.exists())
        self.assertEqual(unrelated.read_bytes(),before)
        self.assertEqual(self.post('/api/notes',{'id':'H1','fix_notes':'Wrong file'},database=unrelated)[0],400)
        self.assertEqual(self.get('/api/data',missing)[0],400)
        self.assertFalse(missing.exists())
        self.assertEqual(self.get('/api/data')[0],200)

    def test_file_choices_list_compatible_neighboring_databases(self):
        other=self.copy_database()
        (Path(self.temp.name)/'bad.sqlite').write_text('unrelated')
        choices=self.get('/api/databases')[1]
        self.assertEqual(choices['database'],str(self.db))
        self.assertEqual(set(choices['files']),{str(self.db),str(other)})
        self.assertEqual(self.post('/api/load-database',{'path':str(other)},origin='https://example.com')[0],403)

    def test_discovery_prompt_targets_selected_file_and_uses_live_schema(self):
        other=self.copy_database()
        with sqlite3.connect(other) as conn:
            conn.execute('ALTER TABLE findings ADD COLUMN researcher_context TEXT')
        code,result=self.get('/api/discovery',other)
        self.assertEqual(code,200)
        self.assertEqual(result['database'],str(other))
        prompt=result['prompt']
        self.assertIn(str(other),prompt)
        self.assertNotIn(str(self.db),prompt)
        self.assertIn('researcher_context TEXT',prompt)
        self.assertIn('manually',prompt)
        self.assertIn('exploration only',prompt)
        self.assertIn('Do not delete or alter existing findings',prompt)
        self.assertIn('including rejected items',prompt)
        self.assertIn('Reload',prompt)
        self.assertIn('PRAGMA foreign_keys=ON',prompt)
        self.assertNotIn('researcher_context TEXT',self.get('/api/discovery')[1]['prompt'])

    def test_read_includes_quotes_links_and_flags(self):
        with urlopen(self.origin+'/api/data') as response: data=json.load(response)
        row=next(r for r in data['findings'] if r['id']=='H1')
        self.assertIn('temporary subdomain', row['verbatim_content'])
        self.assertTrue(row['page_url'].startswith('https://docs.example.test/'))
        self.assertEqual(row['in_batch'],0)

    def test_batch_persists_and_is_queryable(self):
        self.assertEqual(self.post('/api/batch',{'id':'H1','in_batch':True})[0],200)
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute("select in_batch from findings where id='H1'").fetchone()[0],1)
            self.assertEqual(conn.execute('select id from batched_findings').fetchall(),[('H1',)])
        self.assertEqual(self.post('/api/batch',{'id':'H1','in_batch':False})[0],200)
        with sqlite3.connect(self.db) as conn: self.assertEqual(conn.execute('select count(*) from batched_findings').fetchone()[0],0)

    def test_delete_removes_only_selected_finding(self):
        with sqlite3.connect(self.db) as conn: before=conn.execute('select count(*) from findings').fetchone()[0]
        self.assertEqual(self.post('/api/delete',{'id':'H1'})[0],200)
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute("select count(*) from findings where id='H1'").fetchone()[0],0)
            self.assertEqual(conn.execute('select count(*) from findings').fetchone()[0],before-1)
        self.assertEqual(self.post('/api/delete',{'id':'H1'})[0],404)

    def test_notes_preserve_free_text_and_survive_batch_removal(self):
        notes = 'Keep the temporary domain.\nCheck "receipts" & links — 日本語\n\'Do not delete\'; <draft>'
        self.assertEqual(self.post('/api/notes',{'id':'H1','fix_notes':notes})[0],200)
        self.assertEqual(self.post('/api/batch',{'id':'H1','in_batch':True})[0],200)
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute("select fix_notes from batched_findings where id='H1'").fetchone()[0],notes)
        self.assertEqual(self.post('/api/batch',{'id':'H1','in_batch':False})[0],200)
        audit_database(str(self.db))
        with urlopen(self.origin+'/api/data') as response: data=json.load(response)
        row=next(r for r in data['findings'] if r['id']=='H1')
        self.assertEqual(row['fix_notes'],notes)
        self.assertEqual(row['in_batch'],0)
        self.assertEqual(self.post('/api/notes',{'id':'H1','fix_notes':''})[0],200)
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute("select fix_notes from findings where id='H1'").fetchone()[0],'')

    def test_notes_validate_payload_and_unknown_findings(self):
        self.assertEqual(self.post('/api/notes',{'id':'H1','fix_notes':None})[0],400)
        self.assertEqual(self.post('/api/notes',{'id':'not-a-finding','fix_notes':'text'})[0],404)
        self.assertEqual(self.post('/api/notes',{'id':'H1','fix_notes':'text'},origin='https://example.com')[0],403)

    def test_loading_a_different_schema_rejects_it_without_upgrading(self):
        other=self.copy_database('different-schema.sqlite')
        with sqlite3.connect(other) as conn:
            conn.execute('DROP TRIGGER findings_timestamps_insert')
            conn.execute('DROP TRIGGER findings_timestamps_update')
            conn.execute('ALTER TABLE findings DROP COLUMN modified_at')
        before=other.read_bytes()
        code,result=self.post('/api/load-database',{'path':str(other)})
        self.assertEqual(code,400)
        self.assertIn('current audit schema',result['error'])
        self.assertEqual(other.read_bytes(),before)

    def test_timestamps_cover_all_tables_and_direct_sql_writes(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute('PRAGMA foreign_keys=ON')
            conn.execute('PRAGMA recursive_triggers=ON')
            for table in TABLES:
                columns={r[1]:r for r in conn.execute('PRAGMA table_info('+table+')')}
                self.assertIn('created_at',columns)
                self.assertEqual(columns['modified_at'][3],0)
                if table!='batches': self.assertEqual(columns['created_at'][3],0)
            source=conn.execute("SELECT * FROM findings WHERE id='H1'")
            row=dict(zip([col[0] for col in source.description],source.fetchone()))
            row.update(id='N-TIMESTAMP-TEST',in_batch=0,fix_notes='')
            del row['created_at'],row['modified_at']
            conn.execute('INSERT INTO findings ('+','.join(row)+') VALUES ('+','.join('?' for _ in row)+')',list(row.values()))
            conn.execute("INSERT INTO coverage(path,locale,publication,review_status,review_basis) VALUES('timestamp-test','en','source page','reviewed','Test')")
            conn.execute("INSERT INTO metadata(key,value) VALUES('timestamp-test','Test')")
            batch_id=conn.execute("INSERT INTO batches(name) VALUES('Timestamp test')").lastrowid
            conn.execute("INSERT INTO batch_findings(batch_id,finding_id) VALUES(?,'N-TIMESTAMP-TEST')",(batch_id,))
            conn.execute("INSERT INTO batch_pull_requests(batch_id,url) VALUES(?,'https://example.test/1')",(batch_id,))
            cases=[
                ('findings',"id='N-TIMESTAMP-TEST'","title='Updated title'"),
                ('coverage',"path='timestamp-test'","notes='Updated notes'"),
                ('metadata',"key='timestamp-test'","value='Updated value'"),
                ('batches',f'id={batch_id}',"name='Updated batch'"),
                ('batch_findings',f'batch_id={batch_id}',"finding_id='H2'"),
                ('batch_pull_requests',f'batch_id={batch_id}',"url='https://example.test/2'"),
            ]
            for table,where,change in cases:
                with self.subTest(table=table):
                    created,modified=conn.execute(f'SELECT created_at,modified_at FROM {table} WHERE {where}').fetchone()
                    self.assertIsNotNone(created)
                    self.assertRegex(modified,r'^\d{4}-\d{2}-\d{2}T.*Z$')
                    conn.execute(f"UPDATE {table} SET modified_at='2000-01-01T00:00:00Z' WHERE {where}")
                    conn.execute(f'UPDATE {table} SET {change} WHERE {where}')
                    after=conn.execute(f'SELECT created_at,modified_at FROM {table} WHERE {where}').fetchone()
                    self.assertEqual(after[0],created)
                    self.assertNotEqual(after[1],'2000-01-01T00:00:00Z')
            conn.execute("UPDATE findings SET created_at=NULL,modified_at=NULL WHERE id='H1'")
            conn.execute("UPDATE findings SET fix_notes='A new edit' WHERE id='H1'")
            created,modified=conn.execute("SELECT created_at,modified_at FROM findings WHERE id='H1'").fetchone()
            self.assertIsNone(created)
            self.assertIsNotNone(modified)
            conn.execute("UPDATE findings SET modified_at='2000-01-01T00:00:00Z' WHERE id='H1'")
            conn.execute("UPDATE findings SET title=title WHERE id='H1'")
            self.assertEqual(conn.execute("SELECT modified_at FROM findings WHERE id='H1'").fetchone()[0],'2000-01-01T00:00:00Z')
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_rejects_bad_origin_and_nonboolean_batch(self):
        self.assertEqual(self.post('/api/delete',{'id':'H1'},origin='https://example.com')[0],403)
        self.assertEqual(self.post('/api/batch',{'id':'H1','in_batch':'false'})[0],400)
        with sqlite3.connect(self.db) as conn: self.assertEqual(conn.execute("select count(*) from findings where id='H1'").fetchone()[0],1)

    def test_schema_is_derived_from_the_database_on_each_request(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("ALTER TABLE findings ADD COLUMN future_note TEXT DEFAULT 'new'")
            conn.execute('CREATE TABLE future_table (id INTEGER PRIMARY KEY, finding_id TEXT REFERENCES findings(id))')
            expected_version=conn.execute('PRAGMA schema_version').fetchone()[0]
        with urlopen(self.origin+'/api/schema') as response: schema=json.load(response)['schema']
        self.assertEqual(schema['version'],expected_version)
        self.assertEqual(schema['database'],str(self.db))
        finding=next(o for o in schema['objects'] if o['name']=='findings')
        column=next(c for c in finding['columns'] if c['name']=='future_note')
        self.assertEqual(column['type'],'TEXT')
        self.assertEqual(column['dflt_value'],"'new'")
        future=next(o for o in schema['objects'] if o['name']=='future_table')
        self.assertEqual(future['foreign_keys'][0]['table'],'findings')
        self.assertEqual(future['foreign_keys'][0]['from'],'finding_id')
        self.assertIn('future_note',finding['sql'])
        self.assertTrue(any(o['name']=='batched_findings' and o['type']=='view' for o in schema['objects']))
        self.assertTrue(any(o['name']=='one_current_batch' for o in schema['indexes']))

    def submission_state(self):
        with urlopen(self.origin+'/api/submission') as response: return json.load(response)['submission']

    def payload(self,state):
        return {'batch_id':state['plan']['batch_id'],'fingerprint':state['plan']['fingerprint'],'mode':submission.MODE}

    def test_submission_prepares_real_worktree_without_starting_an_agent(self):
        self.post('/api/batch',{'id':'H1','in_batch':True})
        self.post('/api/notes',{'id':'H1','fix_notes':'Keep receipt links working.'})
        state=self.submission_state()
        self.assertTrue(state['plan']['ready'],state['plan']['blocker'])
        self.assertEqual(state['plan']['mode'],'operator_handoff')
        self.assertIn('Keep receipt links working.',state['plan']['prompt'])
        self.assertIn('does not\nstart or monitor an agent',state['plan']['prompt'])
        submitted_id=state['batch']['id']
        process_commands=[]
        original_popen=subprocess.Popen
        def capture_process(command,**kwargs):
            process_commands.append(command)
            return original_popen(command,**kwargs)
        with patch('submission.subprocess.Popen',side_effect=capture_process):
            self.assertEqual(self.post('/api/submit-batch',self.payload(state))[0],200)
            after_first=len(process_commands)
            self.assertTrue(self.post('/api/submit-batch',self.payload(state))[1]['already_submitted'])
            self.assertEqual(len(process_commands),after_first)
        self.assertTrue(process_commands)
        self.assertTrue(all(command[0]=='git' for command in process_commands),process_commands)
        with urlopen(self.origin+'/api/batches') as response: history=json.load(response)
        batch=next(b for b in history['batches'] if b['id']==submitted_id)
        self.assertEqual(batch['status'],'in_progress')
        self.assertEqual(batch['handoff_dismissed'],0)
        self.assertEqual(history['submission']['batch']['status'],'draft')
        self.assertEqual(history['submission']['plan']['finding_ids'],[])
        self.assertIsNone(batch['agent_thread_id'])
        self.assertEqual(state['pull_requests'],[])
        worktree=Path(batch['worktree'])
        self.assertTrue(worktree.is_dir())
        self.assertEqual(submission.run_command(['git','branch','--show-current'],worktree),batch['branch'])
        self.assertEqual(submission.run_command(['git','rev-parse','HEAD'],worktree),batch['base_revision'])
        self.assertEqual(self.git('branch','--show-current'),'main')
        self.assertEqual(json.loads(batch['finding_snapshot'])[0]['fix_notes'],'Keep receipt links working.')
        self.assertIn(str(worktree),batch['agent_prompt'])
        self.assertIn('record-pr',batch['agent_prompt'])
        self.assertIn('Use the existing worktree and branch',batch['agent_prompt'])
        self.assertEqual(self.post('/api/batch',{'id':'H1','in_batch':False})[0],409)
        self.assertEqual(self.post('/api/notes',{'id':'H1','fix_notes':'Changed'})[0],409)
        self.assertEqual(self.post('/api/delete',{'id':'H1'})[0],409)
        self.assertEqual(self.post('/api/submit-batch',self.payload(state),origin='https://example.com')[0],403)

    def test_submission_rejects_empty_stale_or_invalid_mode_requests(self):
        state=self.submission_state()
        self.assertEqual(self.post('/api/submit-batch',self.payload(state))[0],400)
        self.post('/api/batch',{'id':'H1','in_batch':True})
        self.assertEqual(self.post('/api/submit-batch',self.payload(state))[0],400)
        state=self.submission_state()
        old_payload=self.payload(state)
        del old_payload['mode']
        code,result=self.post('/api/submit-batch',old_payload)
        self.assertEqual(code,400)
        self.assertIn('current operator handoff',result['error'])
        self.assertEqual(self.post('/api/batch-details',{'id':state['plan']['batch_id'],'pr_urls':['https://example.com/fake']})[0],404)
        self.assertFalse(Path(state['plan']['worktree']).exists())
        self.assertEqual(self.submission_state()['batch']['status'],'draft')

    def test_fetch_failure_keeps_draft_notes_and_source_checkout(self):
        self.post('/api/batch',{'id':'H1','in_batch':True})
        self.post('/api/notes',{'id':'H1','fix_notes':'Keep this note'})
        state=self.submission_state()
        self.git('remote','set-url','origin',str(Path(self.temp.name)/'missing.git'))
        self.assertEqual(self.post('/api/submit-batch',self.payload(state))[0],400)
        state=self.submission_state()
        self.assertEqual(state['batch']['status'],'draft')
        self.assertFalse(Path(state['plan']['worktree']).exists())
        self.assertEqual(self.git('branch','--show-current'),'main')
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT fix_notes,in_batch FROM findings WHERE id='H1'").fetchone(),('Keep this note',1))

    def test_separately_started_agent_can_record_one_verified_pr(self):
        self.post('/api/batch',{'id':'H1','in_batch':True})
        state=self.submission_state()
        self.assertEqual(self.post('/api/submit-batch',self.payload(state))[0],200)
        with urlopen(self.origin+'/api/batches') as response: history=json.load(response)
        batch=next(b for b in history['batches'] if b['id']==state['batch']['id'])
        url='https://github.com/onetimesecret/docs.onetimesecret.com/pull/9999'
        real_command=submission.run_command
        def commands(args,cwd,**kwargs):
            if args[0]=='git': return real_command(args,cwd,**kwargs)
            if args[1:3]==['pr','list']: return json.dumps([{'url':url}])
            if args[1:3]==['pr','view']: return json.dumps({'url':url,'headRefName':batch['branch'],'baseRefName':'main'})
            raise AssertionError(args)
        # Worktree verification is real; only the GitHub responses are simulated.
        with patch('submission.run_command',side_effect=commands):
            submission.record_pr(self.db,self.repository,batch['id'])
            submission.record_pr(self.db,self.repository,batch['id'])
        with urlopen(self.origin+'/api/batches') as response: batches=json.load(response)
        recorded=next(b for b in batches['batches'] if b['id']==batch['id'])
        self.assertEqual(recorded['status'],'pr_open')
        self.assertEqual([pr['url'] for pr in batches['batch_pull_requests'] if pr['batch_id']==batch['id']],[url])
        self.assertEqual(batches['submission']['batch']['status'],'draft')
        with patch('submission.run_command',side_effect=[real_command(['git','worktree','list','--porcelain'],self.repository),json.dumps([{'url':url},{'url':url+'0'}])]):
            with self.assertRaisesRegex(ValueError,'exactly one PR'):
                submission.record_pr(self.db,self.repository,batch['id'])

    def test_multiple_drafts_keep_membership_when_switching(self):
        first=self.submission_state()['batch']['id']
        self.post('/api/batch',{'id':'H1','in_batch':True})
        self.post('/api/notes',{'id':'H1','fix_notes':'First batch instructions'})
        code,created=self.post('/api/new-batch',{})
        self.assertEqual(code,200)
        second=created['batch_id']
        self.assertNotEqual(first,second)
        self.assertEqual(self.submission_state()['plan']['finding_ids'],[])
        self.post('/api/batch',{'id':'H2','in_batch':True})
        self.assertEqual(self.post('/api/batch',{'id':'H1','in_batch':True})[0],409)
        self.assertEqual(self.post('/api/select-batch',{'batch_id':first})[0],200)
        self.assertEqual(self.submission_state()['plan']['finding_ids'],['H1'])
        self.assertEqual(self.post('/api/select-batch',{'batch_id':second})[0],200)
        self.assertEqual(self.submission_state()['plan']['finding_ids'],['H2'])
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute('SELECT batch_id,finding_id FROM batch_findings ORDER BY batch_id').fetchall(),[(first,'H1'),(second,'H2')])
            self.assertEqual(conn.execute("SELECT fix_notes FROM findings WHERE id='H1'").fetchone()[0],'First batch instructions')

    def test_multiple_batches_can_be_in_progress_with_a_fresh_draft(self):
        ids=[]
        for finding in ('H1','H2'):
            self.assertEqual(self.post('/api/batch',{'id':finding,'in_batch':True})[0],200)
            state=self.submission_state()
            ids.append(state['batch']['id'])
            self.assertEqual(self.post('/api/submit-batch',self.payload(state))[0],200)
        current=self.submission_state()
        self.assertEqual(current['batch']['status'],'draft')
        self.assertEqual(current['plan']['finding_ids'],[])
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT id FROM batches WHERE status='in_progress' ORDER BY id").fetchall(),[(id,) for id in ids])
            self.assertEqual(conn.execute('SELECT count(*) FROM batch_findings').fetchone()[0],2)
            self.assertEqual(conn.execute('SELECT count(*) FROM findings WHERE in_batch=1').fetchone()[0],0)
        self.assertEqual(self.post('/api/select-batch',{'batch_id':ids[0]})[0],400)
        self.assertEqual(self.post('/api/batch',{'id':'H1','in_batch':True})[0],409)
        self.assertTrue(self.post('/api/submit-batch',{'batch_id':ids[0],'fingerprint':'old','mode':submission.MODE})[1]['already_submitted'])

    def test_manual_pr_url_is_saved_without_network_validation(self):
        self.post('/api/batch',{'id':'H1','in_batch':True})
        state=self.submission_state()
        batch_id=state['batch']['id']
        self.post('/api/submit-batch',self.payload(state))
        url='https://github.com/onetimesecret/docs.onetimesecret.com/pull/9876'
        with patch('submission.run_command',side_effect=AssertionError('Manual entry must not fetch GitHub')):
            self.assertEqual(self.post('/api/batch-pr',{'batch_id':batch_id,'url':url+'?diff=split#discussion'})[0],200)
            self.assertEqual(self.post('/api/batch-pr',{'batch_id':batch_id,'url':'javascript:alert(1)'})[0],400)
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute('SELECT url FROM batch_pull_requests WHERE batch_id=?',(batch_id,)).fetchall(),[(url,)])
        self.assertEqual(self.post('/api/batch-pr',{'batch_id':batch_id,'url':''})[0],200)
        self.assertEqual(self.post('/api/batch-pr',{'batch_id':batch_id,'url':url},origin='https://example.com')[0],403)
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM batch_pull_requests WHERE batch_id=?',(batch_id,)).fetchone()[0],0)
            self.assertEqual(conn.execute('SELECT status FROM batches WHERE id=?',(batch_id,)).fetchone()[0],'in_progress')

    def test_dismissing_handoff_keeps_batch_in_progress_and_preserves_scope(self):
        self.post('/api/batch',{'id':'H1','in_batch':True})
        self.post('/api/notes',{'id':'H1','fix_notes':'Preserve this instruction'})
        state=self.submission_state()
        batch_id=state['batch']['id']
        self.assertEqual(self.post('/api/submit-batch',self.payload(state))[0],200)
        with sqlite3.connect(self.db) as conn:
            before=conn.execute('SELECT branch,worktree,finding_snapshot,agent_prompt FROM batches WHERE id=?',(batch_id,)).fetchone()
            self.assertEqual(conn.execute('SELECT status,handoff_dismissed,is_current FROM batches WHERE id=?',(batch_id,)).fetchone(),('in_progress',0,0))
        self.assertEqual(self.post('/api/start-batch',{'batch_id':batch_id})[0],404)
        self.assertEqual(self.post('/api/dismiss-handoff',{'batch_id':batch_id},origin='https://example.com')[0],403)
        self.assertEqual(self.post('/api/dismiss-handoff',{'batch_id':batch_id})[0],200)
        self.assertEqual(self.post('/api/dismiss-handoff',{'batch_id':batch_id})[0],200)
        audit_database(str(self.db))
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute('SELECT status,handoff_dismissed FROM batches WHERE id=?',(batch_id,)).fetchone(),('in_progress',1))
            self.assertEqual(conn.execute('SELECT branch,worktree,finding_snapshot,agent_prompt FROM batches WHERE id=?',(batch_id,)).fetchone(),before)
            self.assertEqual(conn.execute('SELECT finding_id FROM batch_findings WHERE batch_id=?',(batch_id,)).fetchall(),[('H1',)])
            self.assertTrue(Path(before[1]).is_dir())
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_batch_memberships_flags_and_history_stay_consistent(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute('PRAGMA foreign_keys=ON')
            current=conn.execute('SELECT id FROM batches WHERE is_current=1').fetchone()[0]
            conn.execute("UPDATE findings SET in_batch=1 WHERE id='H1'")
            self.assertEqual(conn.execute('SELECT finding_id FROM batch_findings WHERE batch_id=?',(current,)).fetchall(),[('H1',)])
            other=conn.execute("INSERT INTO batches(name) VALUES('Next batch')").lastrowid
            conn.execute("INSERT INTO batch_findings(batch_id,finding_id) VALUES(?,'H2')",(other,))
            conn.execute('UPDATE batches SET is_current=0 WHERE id=?',(current,))
            conn.execute('UPDATE batches SET is_current=1 WHERE id=?',(other,))
            self.assertEqual(conn.execute('SELECT id FROM findings WHERE in_batch=1').fetchall(),[('H2',)])
            self.assertEqual(conn.execute('SELECT id FROM batched_findings').fetchall(),[('H2',)])
            self.assertEqual(conn.execute('SELECT finding_id FROM batch_findings WHERE batch_id=?',(current,)).fetchall(),[('H1',)])
            conn.execute("DELETE FROM batch_findings WHERE batch_id=? AND finding_id='H2'",(other,))
            self.assertEqual(conn.execute("SELECT in_batch FROM findings WHERE id='H2'").fetchone()[0],0)
            conn.execute("INSERT INTO batch_findings(batch_id,finding_id) VALUES(?,'H2')",(other,))
        self.assertEqual(self.post('/api/delete',{'id':'H2'})[0],200)
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM batch_findings WHERE finding_id='H2'").fetchone()[0],0)
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(),[])

if __name__=='__main__': unittest.main()
