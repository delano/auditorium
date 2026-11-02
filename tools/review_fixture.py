"""Generate a disposable brenda usability fixture; never reads an existing audit."""

import argparse
import json
import struct
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import core


def tall_png():
    width, height = 180, 13702

    def chunk(kind, content):
        return (
            struct.pack("!I", len(content))
            + kind
            + content
            + struct.pack("!I", zlib.crc32(kind + content))
        )

    pixels = b"".join(
        b"\x00" + bytes([215, 230, 240] if y % 70 < 60 else [45, 80, 100]) * width
        for y in range(height)
    )
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack("!2I5B", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(pixels))
        + chunk(b"IEND", b"")
    )


def build(path):
    core.create(path, core.HERE, "example/docs")
    with core.connection(path, True) as c:
        for fid, title in [
            ("QA1", "Example: clarify configuration precedence"),
            ("QA2", "Example: a report newer than PR metadata"),
            ("QA3", "Example: a routine wording correction"),
            ("QA4", "Example: another routine correction"),
        ]:
            c.execute(
                "INSERT INTO findings(id,title,quotation,reason,proposed,path,page_url,target,severity,evidence) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    fid,
                    title,
                    "Read the first file and then the second file.",
                    "The example does not state which value takes precedence.",
                    "The second file overrides values from the first.",
                    fid + ".md",
                    "https://example.invalid/docs/" + fid,
                    "main",
                    "medium",
                    "Synthetic QA fixture; not an audit finding.",
                ),
            )
        c.execute(
            "INSERT INTO batches(id,title,target,branch,worktree,submitted_at) VALUES(1,'QA fixture · not an audit','main','wip/qa-fixture','/tmp/qa-fixture',?)",
            (core.now(),),
        )
        c.executemany(
            "INSERT INTO batch_findings(batch_id,finding_id) VALUES(1,?)",
            [(fid,) for fid in ("QA1", "QA2", "QA3", "QA4")],
        )
        meta = {
            "title": "Synthetic PR summary for usability checks",
            "head_sha": "a" * 40,
            "head_branch": "wip/qa-fixture",
            "base_branch": "main",
            "state": "open",
            "merged_at": None,
            "draft": True,
            "comments": 4,
            "review_comments": 2,
            "commits": 3,
            "changed_files": 4,
            "additions": 12,
            "deletions": 8,
        }
        pid = c.execute(
            "INSERT INTO pull_requests(batch_id,url,metadata,tree,patches,observed_at) VALUES(1,?,?,?,?,?)",
            (
                "https://github.com/example/docs/pull/1",
                json.dumps(meta),
                json.dumps(
                    {fid + ".md": "blob-" + fid for fid in ("QA1", "QA2", "QA3", "QA4")}
                ),
                json.dumps(
                    {
                        fid
                        + ".md": "-Read the first file and then the second file.\n+The second file overrides values from the first."
                        for fid in ("QA1", "QA2", "QA3", "QA4")
                    }
                ),
                core.now(),
            ),
        ).lastrowid
        hid = core.make_handoff(c, path, "validation", 1, ["QA1", "QA2", "QA3", "QA4"])
        for fid in ("QA1", "QA2", "QA3", "QA4"):
            cid = c.execute(
                "INSERT INTO checks(batch_id,finding_id,handoff_id,pr_id,revision,source_blobs,proposed,outcome,recommendation,summary,target_url,reporter,created_at) VALUES(1,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    fid,
                    hid,
                    pid,
                    "b" * 40 if fid == "QA2" else "a" * 40,
                    json.dumps({fid + ".md": "blob-" + fid}),
                    "The second file overrides values from the first.",
                    "needs_work" if fid == "QA1" else "pass",
                    "needs_work" if fid == "QA1" else "accept",
                    "Clarify that the second file overrides duplicate values."
                    if fid == "QA1"
                    else "The proposed passage resolves the ambiguity.",
                    "https://example.invalid/docs/" + fid,
                    "Agent",
                    core.now(),
                ),
            ).lastrowid
            if fid == "QA1":
                c.execute(
                    "INSERT INTO evidence(check_id,label,mime_type,content) VALUES(?,?,?,?)",
                    (
                        cid,
                        "Synthetic tall image · QA fixture, not page evidence",
                        "image/png",
                        tall_png(),
                    ),
                )
    print(path)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("database")
    args = p.parse_args()
    build(Path(args.database).resolve())
