"""Evidence persistence, PR freshness, and operator completion boundaries."""
import base64
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from database_files import create_database
from server import audit_database
import review
import submission

URL='https://github.com/onetimesecret/docs.onetimesecret.com/pull/1'
SHA='a'*40
PNG=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6tVEAAAAASUVORK5CYII=')

class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.db=Path(self.temp.name)/'audit.sqlite'
        create_database(str(self.db),Path(self.temp.name))
        audit_database(str(self.db))
        with submission.connection(self.db) as conn:
            conn.execute("INSERT INTO findings(id,severity,confidence,category,path,title,issue,reader_impact,evidence,verification) VALUES('X','low','high','writing','page.md','Title','Quote','Impact','Evidence','Checked')")
            conn.execute("INSERT INTO batch_findings(batch_id,finding_id) VALUES(1,'X')")
            conn.execute("UPDATE batches SET status='in_progress',is_current=0 WHERE id=1")
        submission.save_pr_url(self.db,1,URL)
        self.raw={'title':'Fix documentation','state':'open','draft':True,'merged_at':None,'additions':12,'deletions':3,'commits':2,'changed_files':1,'comments':4,'review_comments':5,'head':{'sha':SHA,'ref':'fix/docs'},'base':{'ref':'main'}}
        self.mock=patch('review.run_command',side_effect=lambda *a,**kw:json.dumps(self.raw));self.mock.start();self.addCleanup(self.mock.stop)
        review.refresh_prs(self.db,1)

    def decision(self,action,notes='',**changes):
        with submission.connection(self.db) as conn:
            state=review.review_queue(conn,1)[0]
        return review.decide_finding(self.db,{'batch_id':1,'finding_id':'X','validation_id':state['validation_id'],
            'reviewed_at':state['reviewed_at'],'action':action,'notes':notes,**changes})

    def accept(self,validation_id,accepted):
        return self.decision('accept' if accepted else 'clear',validation_id=validation_id)

    def report(self,**changes):
        return {'batch_id':1,'finding_id':'X','pr_url':URL,'revision':SHA,'outcome':'pass','summary':'Inspected the corrected paragraph in the local preview.','target_url':'http://localhost:4321/page/','images':[{'label':'After','content':base64.b64encode(PNG).decode()}],**changes}

    def test_add_pr_is_additive_and_does_not_remove_evidence(self):
        id=review.record_validation(self.db,self.report())['validation_id']
        submission.save_pr_url(self.db,1,URL.replace('/1','/2'))
        submission.save_pr_url(self.db,1,URL)
        with submission.connection(self.db) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM batch_pull_requests').fetchone()[0],2)
            self.assertEqual(conn.execute('SELECT content FROM validation_evidence WHERE validation_id=?',(id,)).fetchone()[0],PNG)
        with self.assertRaisesRegex(ValueError,'validation evidence'):review.remove_pr(self.db,1,URL)

    def test_agent_evidence_requires_acceptance_before_completion(self):
        id=review.record_validation(self.db,self.report(recommendation='accept'),reporter='Agent')['validation_id']
        with self.assertRaisesRegex(ValueError,'accepted evidence'):review.complete_batch(self.db,1)
        self.accept(id,True)
        review.complete_batch(self.db,1)
        with self.assertRaisesRegex(ValueError,'Reopen'):review.record_validation(self.db,self.report())
        with submission.connection(self.db) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM batch_completions').fetchone()[0],1)
            self.assertEqual(conn.execute("SELECT status FROM findings WHERE id='X'").fetchone()[0],'open')
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(),[])
        review.reopen_batch(self.db,1)
        self.accept(id,False)
        with self.assertRaises(ValueError):review.complete_batch(self.db,1)

    def test_new_commits_and_failed_refresh_block_stale_acceptance(self):
        id=review.record_validation(self.db,self.report())['validation_id']
        self.accept(id,True)
        self.raw['head']['sha']='b'*40
        with self.assertRaisesRegex(ValueError,'current PR commit'):review.complete_batch(self.db,1)
        with self.assertRaisesRegex(ValueError,'current PR commit'):self.accept(id,True)
        with patch('review.run_command',side_effect=ValueError('Offline')):
            self.assertFalse(review.refresh_prs(self.db,1)['ok'])
            with self.assertRaisesRegex(ValueError,'Could not check'):self.accept(id,True)
        with submission.connection(self.db) as conn:
            cached=conn.execute('SELECT metadata_json,error FROM pull_request_details').fetchone()
            self.assertEqual(json.loads(cached[0])['title'],'Fix documentation')
            self.assertEqual(cached[1],'Offline')
            self.assertEqual(conn.execute('SELECT count(*) FROM batch_completions').fetchone()[0],0)

    def test_latest_result_controls_completion_and_history_survives(self):
        first=review.record_validation(self.db,self.report())['validation_id']
        self.accept(first,True)
        second=review.record_validation(self.db,self.report(outcome='needs_work'))['validation_id']
        with self.assertRaises(ValueError):self.accept(first,True)
        with self.assertRaises(ValueError):self.accept(second,True)
        with self.assertRaises(ValueError):review.complete_batch(self.db,1)
        with submission.connection(self.db) as conn:self.assertEqual(conn.execute('SELECT count(*) FROM finding_validations').fetchone()[0],2)

    def test_images_and_findings_are_validated_before_writing(self):
        for report in [self.report(finding_id='wrong'),self.report(revision='main'),self.report(target_url='javascript:alert(1)'),self.report(images=[{'label':'SVG','content':base64.b64encode(b'<svg/>').decode()}])]:
            with self.assertRaises(ValueError):review.record_validation(self.db,report)
        with submission.connection(self.db) as conn:self.assertEqual(conn.execute('SELECT count(*) FROM finding_validations').fetchone()[0],0)

    def test_change_requests_work_without_evidence_and_survive_agent_checks(self):
        self.decision('needs_work','Use the correct host in the example.')
        with submission.connection(self.db) as conn:
            self.assertEqual(review.review_queue(conn,1)[0]['state'],'needs_work')
            export=review.outstanding_findings(conn,self.db,1)
            self.assertIn('Use the correct host',export)
            self.assertIn('1 not accepted',export)
            self.assertIn(URL,export)
        id=review.record_validation(self.db,self.report(recommendation='accept'),reporter='Agent')['validation_id']
        with submission.connection(self.db) as conn:
            state=review.review_queue(conn,1)[0]
            self.assertEqual(state['state'],'needs_work')
            self.assertTrue(state['can_accept'])
        with self.assertRaisesRegex(ValueError,'Resolve every'):review.complete_batch(self.db,1)
        self.accept(id,True)
        with submission.connection(self.db) as conn:
            self.assertEqual(review.review_queue(conn,1)[0]['state'],'accepted')
            self.assertIn('0 not accepted',review.outstanding_findings(conn,self.db,1))
        self.decision('revert','Undo only the changed host, keep the typo fix.')
        with submission.connection(self.db) as conn:
            state=review.review_queue(conn,1)[0]
            self.assertEqual(state['state'],'revert')
            self.assertIsNone(conn.execute('SELECT accepted_at FROM finding_validations').fetchone()[0])
            export=review.outstanding_findings(conn,self.db,1)
            self.assertIn('Revert requested',export)
            self.assertIn('keep the typo fix',export)
            self.assertIn(SHA,export)
        self.decision('clear')
        with submission.connection(self.db) as conn:self.assertEqual(review.review_queue(conn,1)[0]['state'],'ready')

    def test_decisions_require_reasons_and_reject_concurrent_edits(self):
        for action in ('revert','needs_work'):
            with self.assertRaisesRegex(ValueError,'Describe'):self.decision(action,'   ')
        with self.assertRaises(ValueError):self.decision('accept')
        self.decision('revert','Undo this change.')
        with self.assertRaisesRegex(ValueError,'changed while'):
            self.decision('needs_work','Stale form must not override the other operator.',reviewed_at=None)
        id=review.record_validation(self.db,self.report())['validation_id']
        with self.assertRaisesRegex(ValueError,'changed while'):
            self.decision('accept',validation_id=None)
        self.accept(id,True)
        self.raw['head']['sha']='b'*40
        review.refresh_prs(self.db,1)
        with submission.connection(self.db) as conn:
            self.assertEqual(review.review_queue(conn,1)[0]['state'],'stale')
            self.assertIn('Recheck revision',review.outstanding_findings(conn,self.db,1))

    def test_agent_recommendations_with_zero_or_multiple_images_are_not_decisions(self):
        for recommendation,outcome,count in [('accept','pass',0),('needs_work','needs_work',2),('revert','pass',1)]:
            report=self.report(recommendation=recommendation,outcome=outcome)
            report['images']*=count
            result=review.record_validation(self.db,report,reporter='Agent')
            with submission.connection(self.db) as conn:
                saved=conn.execute('SELECT * FROM finding_validations WHERE id=?',(result['validation_id'],)).fetchone()
                self.assertEqual(saved['recommendation'],recommendation)
                self.assertEqual(saved['summary'],report['summary'])
                self.assertIsNone(saved['accepted_at'])
                self.assertEqual(conn.execute('SELECT count(*) FROM validation_evidence WHERE validation_id=?',(saved['id'],)).fetchone()[0],count)
                self.assertIsNone(review.review_queue(conn,1)[0]['review_action'])
                self.assertIn('Agent recommendation: '+recommendation.replace('_',' '),review.outstanding_findings(conn,self.db,1))
        with self.assertRaisesRegex(ValueError,'accepted evidence'):review.complete_batch(self.db,1)

    def test_agent_recommendation_validation_is_atomic(self):
        for report in [self.report(),self.report(recommendation='approved'),self.report(recommendation='accept',outcome='blocked')]:
            with self.assertRaises(ValueError):review.record_validation(self.db,report,reporter='Agent')
        with submission.connection(self.db) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM finding_validations').fetchone()[0],0)
            self.assertEqual(conn.execute('SELECT count(*) FROM validation_evidence').fetchone()[0],0)

    def test_batch_prompt_references_database_instead_of_copying_record_details(self):
        with submission.connection(self.db) as conn:
            conn.execute("UPDATE batches SET branch='private-branch-marker',worktree='/private/worktree-marker' WHERE id=1")
            conn.execute("UPDATE findings SET title='unique-finding-marker',fix_notes='private-notes-marker' WHERE id='X'")
            prompt=review.validation_prompt(conn,self.db,1)
            for detail in ('private-branch-marker','/private/worktree-marker','unique-finding-marker','private-notes-marker',URL,SHA):
                self.assertNotIn(detail,prompt)
            conn.execute("UPDATE findings SET fix_notes='changed since prompt' WHERE id='X'")
            self.assertEqual(prompt,review.validation_prompt(conn,self.db,1))
        for text in (str(self.db),'every finding','batch_findings JOIN findings','batch_pull_requests','finding_validations','validation_evidence','recommendation','revert','screenshots: []','one sentence','real factual errors','not operator decisions','Never set accepted_at'):
            self.assertIn(text,prompt)

    def test_re_review_prompt_scopes_one_member_and_preserves_review_history(self):
        review.record_validation(self.db,self.report(recommendation='accept'),reporter='Agent')
        with submission.connection(self.db) as conn:
            conn.execute("INSERT INTO findings(id,severity,confidence,category,path,title,issue,reader_impact,evidence,verification) VALUES('Y','low','high','writing','other.md','Unrelated title','Quote','Impact','Evidence','Checked')")
            conn.execute("UPDATE batch_findings SET review_action='needs_work',review_notes='operator-instruction-marker' WHERE batch_id=1 AND finding_id='X'")
            before=list(conn.iterdump())
            prompt=review.validation_prompt(conn,self.db,1,finding_id='X')
            self.assertIn('Re-review only finding "X" in Auditorium Batch #1',prompt)
            self.assertIn(str(self.db),prompt)
            self.assertIn('"finding_id": "X"',prompt)
            self.assertIn('Do not review other findings or submit reports for them',prompt)
            self.assertIn('Append a new report and preserve previous checks and evidence',prompt)
            for unrelated in ('every finding in Auditorium','Unrelated title','operator-instruction-marker'):
                self.assertNotIn(unrelated,prompt)
            self.assertEqual(list(conn.iterdump()),before)
            with self.assertRaisesRegex(ValueError,'not in the selected batch'):
                review.validation_prompt(conn,self.db,1,finding_id='Y')
            for invalid in ('',42):
                with self.assertRaisesRegex(ValueError,'finding ID'):
                    review.validation_prompt(conn,self.db,1,finding_id=invalid)

    def test_cli_imports_agent_notes_and_optional_screenshots(self):
        image=Path(self.temp.name)/'after.png';image.write_bytes(PNG)
        for screenshots in ([],[{'label':'After','path':str(image)},{'label':'Before','path':str(image)}]):
            report=self.report(recommendation='accept');report.pop('images')
            report['screenshots']=screenshots
            path=Path(self.temp.name)/'report.json';path.write_text(json.dumps(report))
            result=subprocess.run([sys.executable,str(Path(review.__file__)),'record','--database',str(self.db),'--report',str(path)],text=True,capture_output=True,check=True)
            id=json.loads(result.stdout)['validation_id']
            with submission.connection(self.db) as conn:
                row=conn.execute('SELECT reporter,recommendation,accepted_at FROM finding_validations WHERE id=?',(id,)).fetchone()
                self.assertEqual(tuple(row),('Agent','accept',None))
                self.assertEqual(conn.execute('SELECT count(*) FROM validation_evidence WHERE validation_id=?',(id,)).fetchone()[0],len(screenshots))

    def test_prompt_and_read_model_keep_manual_acceptance_and_hide_image_bytes(self):
        review.record_validation(self.db,self.report())
        with submission.connection(self.db) as conn:
            data=review.read_review(conn)
            self.assertNotIn('content',data['validation_evidence'][0])
            prompt=review.validation_prompt(conn,self.db,1)
        self.assertIn(str(self.db),prompt)
        self.assertIn('Never set accepted_at',prompt)
        self.assertIn('Playwright',prompt)
        self.assertIn('revision',prompt)

if __name__=='__main__':unittest.main()
