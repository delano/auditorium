#!/usr/bin/env python3
"""Local documentation-audit viewer with persistent PR batch selection."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import argparse
from datetime import datetime, timezone
import json
import sqlite3
from urllib.parse import unquote, urlsplit, parse_qs
from discovery import discovery_prompt
import review
from database_files import create_database, file_dialog
from submission import (DEFAULT_REPO, MODE, batch_plan, submit_batch,
    create_batch, select_batch, dismiss_handoff, save_pr_url)

HERE = Path(__file__).resolve().parent
DEFAULT_DB = HERE / 'data' / 'auditorium.sqlite'
TABLES = ('findings', 'coverage', 'metadata', 'batches', 'batch_findings', 'batch_pull_requests', 'discovery_prompts')
REQUIRED_COLUMNS = {
    'discovery_prompts': {'id','prompt','created_at','modified_at'},
    'findings': set('id severity confidence category locale path line title issue reader_impact evidence suggested_correction verification status page_url page_branch target_branch verbatim_content reason in_batch fix_notes created_at modified_at'.split()),
    'coverage': set('path locale publication review_status review_basis notes created_at modified_at'.split()),
    'metadata': {'key', 'value', 'created_at', 'modified_at'},
    'batches': set('id name branch worktree is_current created_at modified_at status base_branch base_revision agent_prompt finding_snapshot submitted_at finished_at error result_summary handoff_dismissed'.split()),
    'batch_findings': {'batch_id','finding_id','created_at','modified_at','review_action','review_notes','reviewed_at'},
    'batch_pull_requests': {'batch_id','url','created_at','modified_at'},
    'pull_request_details': set('batch_id url metadata_json fetched_at error created_at modified_at'.split()),
    'finding_validations': set('id batch_id finding_id pr_url revision outcome summary target_url reporter recommendation accepted_at created_at modified_at'.split()),
    'validation_evidence': set('id validation_id label mime_type content created_at modified_at'.split()),
    'batch_completions': set('batch_id completed_at created_at modified_at'.split()),
}

def audit_database(value):
    """Validate the current audit schema without modifying the selected file."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError('Enter the full path to an existing audit SQLite file.')
    path = Path(value.strip()).expanduser()
    if not path.is_absolute():
        raise ValueError('Use an absolute file path, starting with / or ~/.')
    path = path.resolve()
    if not path.is_file():
        raise ValueError('Database file not found: '+str(path))
    try:
        with sqlite3.connect(path.as_uri()+'?mode=ro', uri=True) as conn:
            for table, required in REQUIRED_COLUMNS.items():
                columns = {row[1] for row in conn.execute('SELECT * FROM pragma_table_info(?)', (table,))}
                if not required.issubset(columns):
                    raise ValueError('This file does not match the current audit schema. Open it with its matching application snapshot.')
            triggers = {row[0] for row in conn.execute("SELECT name FROM sqlite_schema WHERE type='trigger'")}
            required = {table+'_timestamps_'+event for table in (*TABLES, *review.REVIEW_TABLES) for event in ('insert','update')}
            required.add('findings_target_default')
            if not required.issubset(triggers):
                raise ValueError('This file does not match the current audit schema. Required triggers are missing.')
    except sqlite3.Error as exc:
        raise ValueError('Unable to read an audit SQLite database: '+str(exc)) from exc
    return path

def database_choices(database):
    paths = {database}
    for pattern in ('*.sqlite','*.sqlite3','*.db'):
        for path in database.parent.glob(pattern):
            try:
                paths.add(audit_database(str(path)))
            except (ValueError, OSError):
                continue
    return [str(path) for path in sorted(paths)]

def read_schema(conn, database):
    """Generate the About model from the database, never from a copied schema."""
    rows = [dict(row) for row in conn.execute("SELECT type,name,tbl_name,sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name")]
    objects = []
    for row in rows:
        if row['type'] not in ('table', 'view'):
            continue
        row['columns'] = [dict(column) for column in conn.execute('SELECT * FROM pragma_table_xinfo(?)', (row['name'],))]
        row['foreign_keys'] = [dict(key) for key in conn.execute('SELECT * FROM pragma_foreign_key_list(?)', (row['name'],))]
        objects.append(row)
    return {
        'database': str(database),
        'version': conn.execute('PRAGMA schema_version').fetchone()[0],
        'read_at': datetime.now(timezone.utc).isoformat(),
        'objects': objects,
        'indexes': [row for row in rows if row['type']=='index'],
        'triggers': [row for row in rows if row['type']=='trigger'],
    }

class Handler(BaseHTTPRequestHandler):
    @property
    def database(self):
        # Pin every request to its tab's file; opening another file is never global.
        if not hasattr(self, '_database'):
            value = self.headers.get('X-Audit-Database')
            self._database = audit_database(unquote(value, errors='strict')) if value is not None else self.server.db
        return self._database

    def do_GET(self):
        route = self.path.split('?', 1)[0]
        if route == '/':
            self.send_data(200, 'text/html; charset=utf-8', (HERE / 'viewer.html').read_bytes())
        elif route in ('/review.js','/review.css'):
            self.send_data(200, 'text/javascript' if route.endswith('.js') else 'text/css', (HERE/route[1:]).read_bytes())
        elif route == '/api/evidence':
            try:
                query=parse_qs(urlsplit(self.path).query)
                database=audit_database(query['database'][0])
                with sqlite3.connect(database.as_uri()+'?mode=ro',uri=True) as conn:
                    row=conn.execute('SELECT mime_type,content FROM validation_evidence WHERE id=?',(int(query['id'][0]),)).fetchone()
                if not row: raise ValueError('Screenshot not found.')
                self.send_data(200,row[0],row[1])
            except (KeyError,ValueError,sqlite3.Error) as exc:
                self.send_json(400,{'error':str(exc)})
        elif route in ('/api/data', '/api/schema', '/api/submission', '/api/batches', '/api/databases', '/api/discovery'):
            try:
                database = self.database
                if route == '/api/databases':
                    self.send_json(200, {'database':str(database), 'files':database_choices(database)})
                    return
                with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as conn:
                    conn.row_factory = sqlite3.Row
                    conn.execute('BEGIN')
                    repository = getattr(self.server,'repository',DEFAULT_REPO)
                    if route == '/api/discovery':
                        self.send_json(200, {'database':str(database),'prompt':discovery_prompt(conn,database,repository)})
                        return
                    data = {} if route in ('/api/submission','/api/batches') else {'schema': read_schema(conn, database)}
                    if route != '/api/schema':
                        plan = batch_plan(conn,repository)
                        batch = conn.execute('SELECT * FROM batches WHERE is_current=1').fetchone()
                        data['submission'] = {'plan':plan,'batch':dict(batch) if batch else None,
                            'pull_requests':[dict(row) for row in conn.execute('SELECT * FROM batch_pull_requests WHERE batch_id=?',(batch['id'],))] if batch else []}
                    if route == '/api/data':
                        data.update({table: [dict(row) for row in conn.execute('SELECT * FROM ' + table)] for table in TABLES})
                    elif route == '/api/batches':
                        data.update({table:[dict(row) for row in conn.execute('SELECT * FROM '+table)] for table in ('batches','batch_findings','batch_pull_requests')})
                    if route in ('/api/data','/api/batches'):
                        data.update(review.read_review(conn))
                self.send_json(200, data)
            except ValueError as exc:
                self.send_json(400, {'error': str(exc)})
            except (sqlite3.Error, OSError) as exc:
                self.send_json(500, {'error': str(exc)})
        else:
            self.send_data(404, 'text/plain; charset=utf-8', b'Not found')

    def do_POST(self):
        origin = self.headers.get('Origin')
        expected = {f'http://127.0.0.1:{self.server.server_port}', f'http://localhost:{self.server.server_port}'}
        if origin not in expected:
            self.send_json(403, {'error': 'Changes must be made from this local viewer.'})
            return
        if self.path not in ('/api/batch', '/api/notes', '/api/finding-target', '/api/delete', '/api/submit-batch',
                             '/api/new-batch','/api/select-batch','/api/dismiss-handoff','/api/batch-pr',
                             '/api/load-database','/api/create-database','/api/file-dialog',
                             '/api/refresh-prs','/api/remove-pr','/api/validation-prompt','/api/re-review-prompt','/api/validation',
                             '/api/finding-decision','/api/complete-batch','/api/reopen-batch','/api/outstanding-findings'):
            self.send_json(404, {'error': 'Not found'})
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= (29*1024*1024 if self.path=='/api/validation' else 262144):
                raise ValueError('Invalid request size')
            payload = json.loads(self.rfile.read(length))
            if self.path in ('/api/load-database','/api/create-database','/api/file-dialog'):
                if not isinstance(payload, dict):
                    raise ValueError('Expected a database path.')
                if self.path == '/api/file-dialog':
                    selected = file_dialog(payload.get('operation'),self.database.parent)
                    self.send_json(200, {'path':selected, 'cancelled':not bool(selected)})
                    return
                database = create_database(payload.get('path'),getattr(self.server,'repository',DEFAULT_REPO)) if self.path == '/api/create-database' else audit_database(payload.get('path'))
                self.send_json(200, {'database':str(database)})
                return
            database = self.database
            if self.path in ('/api/refresh-prs','/api/remove-pr','/api/validation-prompt','/api/re-review-prompt','/api/validation','/api/finding-decision','/api/complete-batch','/api/reopen-batch','/api/outstanding-findings'):
                if not isinstance(payload,dict): raise ValueError('Expected a review request.')
                if self.path == '/api/finding-decision':
                    result=review.decide_finding(database,payload)
                else:
                    if type(payload.get('batch_id')) is not int: raise ValueError('A batch ID is required.')
                    if self.path == '/api/refresh-prs': result=review.refresh_prs(database,payload['batch_id'])
                    elif self.path == '/api/remove-pr': result=review.remove_pr(database,payload['batch_id'],payload.get('url',''))
                    elif self.path == '/api/validation': result=review.record_validation(database,payload)
                    elif self.path == '/api/complete-batch': result=review.complete_batch(database,payload['batch_id'])
                    elif self.path == '/api/reopen-batch': result=review.reopen_batch(database,payload['batch_id'])
                    elif self.path == '/api/outstanding-findings':
                        with review.connection(database) as conn:
                            result={'text':review.outstanding_findings(conn,database,payload['batch_id'])}
                    elif self.path == '/api/re-review-prompt':
                        if set(payload) != {'batch_id','finding_id'} or not isinstance(payload['finding_id'],str) or not payload['finding_id'].strip():
                            raise ValueError('Provide a batch ID and a finding ID for re-review.')
                        with review.connection(database) as conn:
                            result={'prompt':review.validation_prompt(conn,database,payload['batch_id'],finding_id=payload['finding_id'])}
                    else:
                        with review.connection(database) as conn:
                            if set(payload) != {'batch_id'}: raise ValueError('Provide only a batch ID for validation.')
                            result={'prompt':review.validation_prompt(conn,database,payload['batch_id'])}
                self.send_json(200,result)
                return
            if self.path=='/api/new-batch':
                if not isinstance(payload,dict):
                    raise ValueError('Expected a batch request')
                self.send_json(200,create_batch(database))
                return
            if self.path in ('/api/select-batch','/api/dismiss-handoff','/api/batch-pr'):
                if not isinstance(payload,dict) or type(payload.get('batch_id')) is not int:
                    raise ValueError('A batch ID is required')
                if self.path=='/api/batch-pr':
                    if not isinstance(payload.get('url'),str):
                        raise ValueError('A PR URL is required')
                    result=save_pr_url(database,payload['batch_id'],payload['url'])
                else:
                    action=select_batch if self.path=='/api/select-batch' else dismiss_handoff
                    result=action(database,payload['batch_id'])
                self.send_json(200,result)
                return
            if self.path == '/api/submit-batch':
                if not isinstance(payload,dict) or payload.get('mode')!=MODE:
                    raise ValueError('Use the current operator handoff request. Reload the viewer and try again.')
                if not isinstance(payload,dict) or type(payload.get('batch_id')) is not int or not isinstance(payload.get('fingerprint'),str):
                    raise ValueError('A batch ID and reviewed scope are required')
                result = submit_batch(database,getattr(self.server,'repository',DEFAULT_REPO),payload['batch_id'],payload['fingerprint'])
                self.send_json(200,result)
                return
            if not isinstance(payload, dict) or not isinstance(payload.get('id'), str) or not payload['id']:
                raise ValueError('A finding ID is required')
            if self.path == '/api/batch' and type(payload.get('in_batch')) is not bool:
                raise ValueError('in_batch must be a boolean')
            if self.path == '/api/notes' and not isinstance(payload.get('fix_notes'), str):
                raise ValueError('fix_notes must be text')
            if self.path == '/api/finding-target' and payload.get('target_branch') not in ('main','develop'):
                raise ValueError('Choose main or develop as the target branch.')
            with sqlite3.connect(database) as conn:
                conn.execute('PRAGMA foreign_keys=ON')
                conn.execute('BEGIN IMMEDIATE')
                sealed = conn.execute('''SELECT 1 FROM batch_findings bf JOIN batches b ON b.id=bf.batch_id
                    WHERE bf.finding_id=? AND b.status<>'draft' LIMIT 1''',(payload['id'],)).fetchone()
                current_sealed = conn.execute("SELECT 1 FROM batches WHERE is_current=1 AND status<>'draft'").fetchone()
                if sealed or (self.path=='/api/batch' and current_sealed):
                    self.send_json(409,{'error':'This batch has been submitted. Its target, scope and notes are preserved for the agent.'})
                    return
                if self.path=='/api/batch' and payload['in_batch']:
                    other=conn.execute('''SELECT bf.batch_id FROM batch_findings bf JOIN batches b ON b.id=bf.batch_id
                        WHERE bf.finding_id=? AND b.is_current=0 LIMIT 1''',(payload['id'],)).fetchone()
                    if other:
                        self.send_json(409,{'error':f'This finding already belongs to Batch #{other[0]}.'})
                        return
                if self.path == '/api/batch':
                    result = conn.execute('UPDATE findings SET in_batch=? WHERE id=?', (int(payload['in_batch']), payload['id']))
                elif self.path == '/api/notes':
                    result = conn.execute('UPDATE findings SET fix_notes=? WHERE id=?', (payload['fix_notes'], payload['id']))
                elif self.path == '/api/finding-target':
                    result = conn.execute('UPDATE findings SET target_branch=? WHERE id=?', (payload['target_branch'], payload['id']))
                else:
                    result = conn.execute('DELETE FROM findings WHERE id=?', (payload['id'],))
                if not result.rowcount:
                    self.send_json(404, {'error': 'Finding no longer exists. Reload the database.'})
                    return
            self.send_json(200, {'ok': True})
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            self.send_json(400, {'error': str(exc)})
        except (sqlite3.Error, OSError) as exc:
            self.send_json(500, {'error': str(exc)})

    def send_json(self, status, data):
        self.send_data(status, 'application/json; charset=utf-8', json.dumps(data, ensure_ascii=False).encode())

    def send_data(self, status, content_type, body):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, default=DEFAULT_DB, help='Path to the audit SQLite database')
    parser.add_argument('--port', type=int, default=8766, help='Local port (default: 8766)')
    parser.add_argument('--create', action='store_true', help='Create a new empty audit database before starting')
    parser.add_argument('--repository', type=Path, default=DEFAULT_REPO, help='Documentation checkout used for research and batch worktrees')
    args = parser.parse_args()
    try:
        if args.create:
            create_database(str(args.database.expanduser().resolve()), args.repository.expanduser().resolve())
        database = audit_database(str(args.database.expanduser().resolve()))
    except ValueError as exc:
        parser.error(str(exc))
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    server.db = database
    server.repository = args.repository.expanduser().resolve()
    print(f'Viewer ready at http://127.0.0.1:{server.server_port}/', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
