-- Current audit database schema. Used only when creating a new file.

CREATE TABLE batch_findings (
            batch_id INTEGER NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
            finding_id TEXT NOT NULL REFERENCES findings(id) ON DELETE CASCADE, created_at TEXT, modified_at TEXT,
            PRIMARY KEY (batch_id,finding_id)
        );

CREATE TABLE batch_pull_requests (
            batch_id INTEGER NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
            url TEXT NOT NULL CHECK (length(trim(url))>0), created_at TEXT, modified_at TEXT,
            PRIMARY KEY (batch_id,url)
        );

CREATE TABLE batches (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL DEFAULT 'Documentation corrections',
            branch TEXT NOT NULL DEFAULT '',
            worktree TEXT NOT NULL DEFAULT '',
            is_current INTEGER NOT NULL DEFAULT 0 CHECK (is_current IN (0,1)),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        , base_branch TEXT NOT NULL DEFAULT '', agent_prompt TEXT NOT NULL DEFAULT '', finding_snapshot TEXT NOT NULL DEFAULT '', run_id TEXT, agent_thread_id TEXT, submitted_at TEXT, finished_at TEXT, error TEXT NOT NULL DEFAULT '', log_path TEXT, result_summary TEXT NOT NULL DEFAULT '', base_revision TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','handoff_ready','in_progress','queued','running','pr_open','needs_attention')), handoff_dismissed INTEGER NOT NULL DEFAULT 1 CHECK(handoff_dismissed IN (0,1)), modified_at TEXT);

CREATE TABLE coverage (path TEXT PRIMARY KEY, locale TEXT NOT NULL, publication TEXT NOT NULL, review_status TEXT NOT NULL, review_basis TEXT NOT NULL, notes TEXT, created_at TEXT, modified_at TEXT);

CREATE TABLE findings (id TEXT PRIMARY KEY, severity TEXT NOT NULL, confidence TEXT NOT NULL, category TEXT NOT NULL, locale TEXT NOT NULL DEFAULT 'en', path TEXT NOT NULL, line INTEGER, title TEXT NOT NULL, issue TEXT NOT NULL, reader_impact TEXT NOT NULL, evidence TEXT NOT NULL, suggested_correction TEXT, verification TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open', page_url TEXT, page_branch TEXT, target_branch TEXT CHECK(target_branch IN ('main','develop')), verbatim_content TEXT, reason TEXT, in_batch INTEGER NOT NULL DEFAULT 0 CHECK (in_batch IN (0,1)), fix_notes TEXT NOT NULL DEFAULT '', created_at TEXT, modified_at TEXT);

CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL, created_at TEXT, modified_at TEXT);

CREATE UNIQUE INDEX one_current_batch ON batches(is_current) WHERE is_current=1;

CREATE VIEW batched_findings AS
            SELECT f.id,f.title,f.page_url,f.verbatim_content,f.reason,f.suggested_correction,
                   f.fix_notes,f.path,f.line,b.id AS batch_id
            FROM findings f JOIN batch_findings bf ON bf.finding_id=f.id
            JOIN batches b ON b.id=bf.batch_id
            WHERE b.is_current=1 ORDER BY f.path,f.line,f.id;

CREATE VIEW priority_findings as select * from findings where status = 'open' order by case severity when 'high' then 1 when 'medium' then 2 else 3 end, id;

CREATE TRIGGER batch_finding_added AFTER INSERT ON batch_findings WHEN NEW.batch_id IN (SELECT id FROM batches WHERE is_current=1) BEGIN UPDATE findings SET in_batch=1 WHERE id=NEW.finding_id AND in_batch<>1; END;

CREATE TRIGGER batch_finding_removed AFTER DELETE ON batch_findings WHEN OLD.batch_id IN (SELECT id FROM batches WHERE is_current=1) BEGIN UPDATE findings SET in_batch=0 WHERE id=OLD.finding_id AND in_batch<>0; END;

CREATE TRIGGER batch_findings_timestamps_insert AFTER INSERT ON batch_findings
BEGIN
    UPDATE batch_findings SET created_at=COALESCE(NEW.created_at,strftime('%Y-%m-%dT%H:%M:%fZ','now')),
        modified_at=COALESCE(NEW.modified_at,strftime('%Y-%m-%dT%H:%M:%fZ','now'))
    WHERE rowid=NEW.rowid;
END;

CREATE TRIGGER batch_findings_timestamps_update AFTER UPDATE ON batch_findings
WHEN OLD."batch_id" IS NOT NEW."batch_id" OR OLD."finding_id" IS NOT NEW."finding_id"
BEGIN
    UPDATE batch_findings SET modified_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE rowid=NEW.rowid;
END;

CREATE TRIGGER batch_pull_requests_timestamps_insert AFTER INSERT ON batch_pull_requests
BEGIN
    UPDATE batch_pull_requests SET created_at=COALESCE(NEW.created_at,strftime('%Y-%m-%dT%H:%M:%fZ','now')),
        modified_at=COALESCE(NEW.modified_at,strftime('%Y-%m-%dT%H:%M:%fZ','now'))
    WHERE rowid=NEW.rowid;
END;

CREATE TRIGGER batch_pull_requests_timestamps_update AFTER UPDATE ON batch_pull_requests
WHEN OLD."batch_id" IS NOT NEW."batch_id" OR OLD."url" IS NOT NEW."url"
BEGIN
    UPDATE batch_pull_requests SET modified_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE rowid=NEW.rowid;
END;

CREATE TRIGGER batches_timestamps_insert AFTER INSERT ON batches
BEGIN
    UPDATE batches SET created_at=COALESCE(NEW.created_at,strftime('%Y-%m-%dT%H:%M:%fZ','now')),
        modified_at=COALESCE(NEW.modified_at,strftime('%Y-%m-%dT%H:%M:%fZ','now'))
    WHERE rowid=NEW.rowid;
END;

CREATE TRIGGER batches_timestamps_update AFTER UPDATE ON batches
WHEN OLD."id" IS NOT NEW."id" OR OLD."name" IS NOT NEW."name" OR OLD."branch" IS NOT NEW."branch" OR OLD."worktree" IS NOT NEW."worktree" OR OLD."is_current" IS NOT NEW."is_current" OR OLD."base_branch" IS NOT NEW."base_branch" OR OLD."agent_prompt" IS NOT NEW."agent_prompt" OR OLD."finding_snapshot" IS NOT NEW."finding_snapshot" OR OLD."run_id" IS NOT NEW."run_id" OR OLD."agent_thread_id" IS NOT NEW."agent_thread_id" OR OLD."submitted_at" IS NOT NEW."submitted_at" OR OLD."finished_at" IS NOT NEW."finished_at" OR OLD."error" IS NOT NEW."error" OR OLD."log_path" IS NOT NEW."log_path" OR OLD."result_summary" IS NOT NEW."result_summary" OR OLD."base_revision" IS NOT NEW."base_revision" OR OLD."status" IS NOT NEW."status" OR OLD."handoff_dismissed" IS NOT NEW."handoff_dismissed"
BEGIN
    UPDATE batches SET modified_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE rowid=NEW.rowid;
END;

CREATE TRIGGER coverage_timestamps_insert AFTER INSERT ON coverage
BEGIN
    UPDATE coverage SET created_at=COALESCE(NEW.created_at,strftime('%Y-%m-%dT%H:%M:%fZ','now')),
        modified_at=COALESCE(NEW.modified_at,strftime('%Y-%m-%dT%H:%M:%fZ','now'))
    WHERE rowid=NEW.rowid;
END;

CREATE TRIGGER coverage_timestamps_update AFTER UPDATE ON coverage
WHEN OLD."path" IS NOT NEW."path" OR OLD."locale" IS NOT NEW."locale" OR OLD."publication" IS NOT NEW."publication" OR OLD."review_status" IS NOT NEW."review_status" OR OLD."review_basis" IS NOT NEW."review_basis" OR OLD."notes" IS NOT NEW."notes"
BEGIN
    UPDATE coverage SET modified_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE rowid=NEW.rowid;
END;

CREATE TRIGGER current_batch_changed AFTER UPDATE OF is_current ON batches BEGIN UPDATE findings SET in_batch=EXISTS (
            SELECT 1 FROM batch_findings bf JOIN batches b ON b.id=bf.batch_id
            WHERE b.is_current=1 AND bf.finding_id=findings.id); END;

CREATE TRIGGER current_batch_created AFTER INSERT ON batches WHEN NEW.is_current=1 BEGIN UPDATE findings SET in_batch=EXISTS (
            SELECT 1 FROM batch_findings bf JOIN batches b ON b.id=bf.batch_id
            WHERE b.is_current=1 AND bf.finding_id=findings.id); END;

CREATE TRIGGER current_batch_deleted AFTER DELETE ON batches WHEN OLD.is_current=1 BEGIN UPDATE findings SET in_batch=EXISTS (
            SELECT 1 FROM batch_findings bf JOIN batches b ON b.id=bf.batch_id
            WHERE b.is_current=1 AND bf.finding_id=findings.id); END;

CREATE TRIGGER finding_selected AFTER UPDATE OF in_batch ON findings WHEN NEW.in_batch=1
BEGIN
    INSERT OR IGNORE INTO batch_findings(batch_id,finding_id) SELECT id,NEW.id FROM batches WHERE is_current=1;
END;

CREATE TRIGGER finding_unselected AFTER UPDATE OF in_batch ON findings WHEN NEW.in_batch=0 BEGIN DELETE FROM batch_findings WHERE finding_id=NEW.id AND batch_id IN (SELECT id FROM batches WHERE is_current=1); END;

-- Set the finding's initial target from its public page; explicit choices win.
CREATE TRIGGER findings_target_default AFTER INSERT ON findings
WHEN NEW.target_branch IS NULL
BEGIN
    UPDATE findings SET target_branch=CASE
        WHEN lower(NEW.page_url) IN ('https://docs.onetimesecret.com','http://docs.onetimesecret.com')
          OR lower(NEW.page_url) LIKE 'https://docs.onetimesecret.com/%'
          OR lower(NEW.page_url) LIKE 'http://docs.onetimesecret.com/%' THEN 'main'
        WHEN lower(NEW.page_url) IN ('https://docs.onetimesecret.dev','http://docs.onetimesecret.dev')
          OR lower(NEW.page_url) LIKE 'https://docs.onetimesecret.dev/%'
          OR lower(NEW.page_url) LIKE 'http://docs.onetimesecret.dev/%' THEN 'develop'
        END
    WHERE rowid=NEW.rowid;
END;

CREATE TRIGGER findings_timestamps_insert AFTER INSERT ON findings
BEGIN
    UPDATE findings SET created_at=COALESCE(NEW.created_at,strftime('%Y-%m-%dT%H:%M:%fZ','now')),
        modified_at=COALESCE(NEW.modified_at,strftime('%Y-%m-%dT%H:%M:%fZ','now'))
    WHERE rowid=NEW.rowid;
END;

CREATE TRIGGER findings_timestamps_update AFTER UPDATE ON findings
WHEN OLD."id" IS NOT NEW."id" OR OLD."severity" IS NOT NEW."severity" OR OLD."confidence" IS NOT NEW."confidence" OR OLD."category" IS NOT NEW."category" OR OLD."locale" IS NOT NEW."locale" OR OLD."path" IS NOT NEW."path" OR OLD."line" IS NOT NEW."line" OR OLD."title" IS NOT NEW."title" OR OLD."issue" IS NOT NEW."issue" OR OLD."reader_impact" IS NOT NEW."reader_impact" OR OLD."evidence" IS NOT NEW."evidence" OR OLD."suggested_correction" IS NOT NEW."suggested_correction" OR OLD."verification" IS NOT NEW."verification" OR OLD."status" IS NOT NEW."status" OR OLD."page_url" IS NOT NEW."page_url" OR OLD."page_branch" IS NOT NEW."page_branch" OR OLD."target_branch" IS NOT NEW."target_branch" OR OLD."verbatim_content" IS NOT NEW."verbatim_content" OR OLD."reason" IS NOT NEW."reason" OR OLD."in_batch" IS NOT NEW."in_batch" OR OLD."fix_notes" IS NOT NEW."fix_notes"
BEGIN
    UPDATE findings SET modified_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE rowid=NEW.rowid;
END;

CREATE TRIGGER metadata_timestamps_insert AFTER INSERT ON metadata
BEGIN
    UPDATE metadata SET created_at=COALESCE(NEW.created_at,strftime('%Y-%m-%dT%H:%M:%fZ','now')),
        modified_at=COALESCE(NEW.modified_at,strftime('%Y-%m-%dT%H:%M:%fZ','now'))
    WHERE rowid=NEW.rowid;
END;

CREATE TRIGGER metadata_timestamps_update AFTER UPDATE ON metadata
WHEN OLD."key" IS NOT NEW."key" OR OLD."value" IS NOT NEW."value"
BEGIN
    UPDATE metadata SET modified_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE rowid=NEW.rowid;
END;

CREATE TABLE discovery_prompts (
    id INTEGER PRIMARY KEY,
    prompt TEXT NOT NULL CHECK (length(trim(prompt)) > 0),
    created_at TEXT,
    modified_at TEXT
);

CREATE TRIGGER discovery_prompts_timestamps_insert AFTER INSERT ON discovery_prompts
BEGIN
    UPDATE discovery_prompts SET created_at=COALESCE(NEW.created_at,strftime('%Y-%m-%dT%H:%M:%fZ','now')),
        modified_at=COALESCE(NEW.modified_at,strftime('%Y-%m-%dT%H:%M:%fZ','now'))
    WHERE id=NEW.id;
END;

CREATE TRIGGER discovery_prompts_timestamps_update AFTER UPDATE ON discovery_prompts
WHEN OLD.id IS NOT NEW.id OR OLD.prompt IS NOT NEW.prompt
BEGIN
    UPDATE discovery_prompts SET modified_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=NEW.id;
END;
