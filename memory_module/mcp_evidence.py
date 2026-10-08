"""Receipt-verified output of MCP writes for a check whose plan already names mcp: paths.

A live workflow sits outside the checked folder. When the plan paths include an
mcp: target, the check stores the output of matching completed writes after the
transcript matches the receipt hash, and carries those sources in the snapshot.
A call that does not verify is left out, so one missing transcript does not
refuse the check. The manual evidence operation still refuses a bad batch. This
path reuses that cap and does not read an n8n export.
"""
from . import guards, sessions
from .core import InvalidRecord


def patterns(paths):
    """Plan paths that name an MCP server or one of its write tools."""
    return [path for path in paths or [] if isinstance(path, str) and path.startswith(guards.MCP_TARGET_PREFIX)]


def matching_receipts(memory, episode_id, paths):
    """PostToolUse receipts of MCP writes on this work whose targets match paths, latest first within the cap."""
    selected = patterns(paths)
    if not selected:
        return []
    rows = memory.db.execute(
        """SELECT post.id, post.tool_name FROM host_receipts AS post
           WHERE post.event_name='PostToolUse' AND (
             post.episode_id=? OR EXISTS (
               SELECT 1 FROM host_receipts AS pre
               WHERE pre.event_name='PreToolUse' AND pre.session_id=post.session_id AND pre.tool_use_id=post.tool_use_id
                 AND (pre.episode_id=? OR json_extract(pre.payload,'$.work_item')=?)))
           ORDER BY post.rowid""",
        (episode_id, episode_id, episode_id)).fetchall()
    matched = []
    for row in rows:
        target = guards.mcp_write_target(row['tool_name'])
        if target and guards.match_path(target, selected):
            matched.append(row['id'])
    if len(matched) > sessions.RECEIPT_EVIDENCE_LIMIT:
        matched = matched[-sessions.RECEIPT_EVIDENCE_LIMIT:]
    return matched


def attach(memory, episode_id, paths, *, found=None):
    """Store and return verified sources for the MCP writes the plan already allows.

    Receipts that do not match their transcript are skipped. Sources already stored for the same
    receipt are reused.
    """
    sources = []
    seen = set()
    for receipt_id in matching_receipts(memory, episode_id, paths):
        try:
            result = sessions.receipt_evidence(memory, [receipt_id], 'mcp-receipt:' + receipt_id, found=found)
        except InvalidRecord:
            continue
        for item in result['evidence']:
            if item['source_id'] in seen:
                continue
            seen.add(item['source_id'])
            sources.append(memory.read(item['source_id'], detail=True))
    return sources
