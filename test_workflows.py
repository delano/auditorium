"""Manual handoff checks using only synthetic findings."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from database_files import create_database
from discovery import discovery_prompt
from submission import batch_plan, connection, create_batch, select_batch

class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.database=Path(self.temp.name)/'audit.sqlite'
        self.repository=Path(self.temp.name)/'docs'
        create_database(str(self.database),self.repository)
        with connection(self.database) as conn:
            conn.execute("""INSERT INTO findings(id,severity,confidence,category,path,title,issue,reader_impact,evidence,verification,page_branch,target_branch,fix_notes)
                VALUES('EX1','low','high','wording','src/content/docs/en/example.md','Example','Example issue','Example impact','Synthetic evidence','Synthetic verification','main','main','Use the operator wording')""")
            conn.execute("UPDATE findings SET in_batch=1 WHERE id='EX1'")

    def test_plan_preserves_operator_scope_and_manual_launch(self):
        with connection(self.database) as conn,patch('submission.setup_check',return_value=''):
            plan=batch_plan(conn,self.repository)
        self.assertEqual(plan['finding_ids'],['EX1'])
        self.assertEqual(plan['snapshot'][0]['fix_notes'],'Use the operator wording')
        self.assertEqual(plan['mode'],'operator_handoff')
        self.assertIn('does not\nstart or monitor an agent',plan['prompt'])
        self.assertIn(str(self.database),plan['prompt'])

    def test_finding_targets_default_from_domain_and_preserve_explicit_choices(self):
        cases = [
            ('COM','https://docs.onetimesecret.com/en/a/',None,'main'),
            ('DEV','https://docs.onetimesecret.dev/en/a/',None,'develop'),
            ('ROOT','https://docs.onetimesecret.dev',None,'develop'),
            ('OVERRIDE','https://docs.onetimesecret.com/en/a/','develop','develop'),
            ('UNKNOWN','https://docs.example.test/en/a/',None,None),
            ('LOOKALIKE','https://docs.onetimesecret.com.example.test/a/',None,None),
        ]
        with connection(self.database) as conn:
            for ident,url,target,expected in cases:
                conn.execute("""INSERT INTO findings(id,severity,confidence,category,path,title,issue,reader_impact,evidence,verification,page_url,page_branch,target_branch)
                    VALUES(?,'low','high','wording','a.md','Example','Issue','Impact','Evidence','Checked',?,'main',?)""",(ident,url,target))
                self.assertEqual(conn.execute('SELECT target_branch FROM findings WHERE id=?',(ident,)).fetchone()[0],expected)
            conn.execute("UPDATE findings SET target_branch='develop',modified_at='2000-01-01' WHERE id='COM'")
            conn.execute("UPDATE findings SET target_branch='main' WHERE id='COM'")
            row=conn.execute("SELECT page_url,page_branch,target_branch,modified_at FROM findings WHERE id='COM'").fetchone()
            self.assertEqual(tuple(row)[:3],('https://docs.onetimesecret.com/en/a/','main','main'))
            self.assertNotEqual(row['modified_at'],'2000-01-01')

    def test_plan_uses_finding_target_not_source_or_draft_base(self):
        with connection(self.database) as conn,patch('submission.setup_check',return_value=''):
            original=batch_plan(conn,self.repository)
            conn.execute("UPDATE findings SET target_branch='develop' WHERE id='EX1'")
            conn.execute("UPDATE batches SET base_branch='main' WHERE id=1")
            changed=batch_plan(conn,self.repository)
            self.assertEqual(changed['base_branch'],'develop')
            self.assertEqual(changed['snapshot'][0]['page_branch'],'main')
            self.assertEqual(changed['snapshot'][0]['target_branch'],'develop')
            self.assertIn('PR base: develop',changed['prompt'])
            self.assertNotEqual(original['fingerprint'],changed['fingerprint'])
            conn.execute("UPDATE batches SET status='in_progress',base_branch='main' WHERE id=1")
            self.assertEqual(batch_plan(conn,self.repository,batch_id=1)['base_branch'],'main')

    def test_multiple_drafts_preserve_their_membership(self):
        second=create_batch(self.database)['batch_id']
        select_batch(self.database,1)
        with connection(self.database) as conn,patch('submission.setup_check',return_value=''):
            self.assertEqual(batch_plan(conn,self.repository)['finding_ids'],['EX1'])
        select_batch(self.database,second)
        with connection(self.database) as conn,patch('submission.setup_check',return_value=''):
            self.assertEqual(batch_plan(conn,self.repository)['finding_ids'],[])

    def test_research_prompt_names_the_file_and_current_schema(self):
        with connection(self.database) as conn:
            prompt=discovery_prompt(conn,self.database,self.repository)
        self.assertIn(str(self.database),prompt)
        self.assertIn('CREATE TABLE findings',prompt)
        self.assertIn('exploration only',prompt)
        self.assertIn('manually',prompt)
        self.assertIn('CREATE TABLE discovery_prompts',prompt)
        self.assertIn('prompt verbatim',prompt)
        self.assertIn('including any operator research',prompt)
        self.assertIn('INSERT INTO discovery_prompts(prompt) VALUES (?)',prompt)
        self.assertIn('zero findings',prompt)
        self.assertIn('Determine target_branch now',prompt)
        self.assertIn('one shared target',prompt)

    def test_discovery_prompt_storage_preserves_verbatim_text_and_timestamps(self):
        text = 'Research focus: café\n\nExact quotation: \"test\"\n'
        with connection(self.database) as conn:
            cursor=conn.execute('INSERT INTO discovery_prompts(prompt) VALUES (?)',(text,))
            row=conn.execute('SELECT prompt,created_at,modified_at FROM discovery_prompts WHERE id=?',(cursor.lastrowid,)).fetchone()
            self.assertEqual(row['prompt'],text)
            self.assertTrue(row['created_at'])
            self.assertTrue(row['modified_at'])
            self.assertEqual(list(conn.execute('PRAGMA foreign_key_list(discovery_prompts)')),[])
            conn.execute("UPDATE discovery_prompts SET modified_at='2000-01-01' WHERE id=?",(cursor.lastrowid,))
            conn.execute('UPDATE discovery_prompts SET prompt=? WHERE id=?',(text+'more',cursor.lastrowid))
            updated=conn.execute('SELECT created_at,modified_at FROM discovery_prompts WHERE id=?',(cursor.lastrowid,)).fetchone()
            self.assertEqual(updated['created_at'],row['created_at'])
            self.assertNotEqual(updated['modified_at'],'2000-01-01')

if __name__=='__main__': unittest.main()
