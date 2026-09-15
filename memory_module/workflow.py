"""Record validation and follow-up queries; no scheduler or model calls."""

FIELDS = {
    'review': ({'target', 'revision', 'summary', 'findings'}, set()),
    'correction': ({'before', 'after', 'reason', 'scope'}, set()),
    'action_result': ({'execution_status', 'summary'}, {'duration_ms', 'artifact'}),
    'follow_up': ({'review_after', 'owner', 'reason'}, set()),
    'episode_status': ({'status', 'reason'}, set()),
    'lesson_review': ({'lesson_id', 'status', 'reason'}, set()),
}


def validate_payload(kind, payload):
    from .core import InvalidRecord, _text, _time
    if kind not in FIELDS:
        return False
    required, optional = FIELDS[kind]
    if not isinstance(payload, dict) or required - payload.keys() or payload.keys() - required - optional:
        raise InvalidRecord(f'{kind} requires {sorted(required)}; optional: {sorted(optional)}.')
    for key, value in payload.items():
        if key == 'findings':
            if not isinstance(value, list) or len(value) > 100:
                raise InvalidRecord('findings must be a list of at most 100 objects.')
            for item in value:
                if not isinstance(item, dict) or set(item) != {'location', 'issue', 'severity'}:
                    raise InvalidRecord('Each finding needs location, issue and severity.')
                for field, text in item.items():
                    _text(text, field, 2000)
                if item['severity'] not in {'minor', 'major', 'unknown'}:
                    raise InvalidRecord('Finding severity must be minor, major or unknown.')
        elif key == 'duration_ms':
            if type(value) is not int or value < 0:
                raise InvalidRecord('duration_ms must be a nonnegative integer.')
        elif key == 'review_after':
            _time(value)
        else:
            _text(value, key)
    allowed = {'action_result': ('execution_status', {'completed', 'failed', 'unknown'}),
               'episode_status': ('status', {'active', 'settled', 'reopened', 'abandoned'}),
               'lesson_review': ('status', {'accepted', 'rejected', 'retired'})}
    if kind in allowed:
        key, values = allowed[kind]
        if payload[key] not in values:
            raise InvalidRecord(f'{key} must be one of {sorted(values)}.')
    return True


def validate_event(memory, episode, kind, payload, evidence, decision_id, links):
    from .core import InvalidRecord, Conflict
    if episode['status'] in {'settled', 'abandoned'} and kind not in {'episode_status', 'outcome', 'follow_up', 'action_result'}:
        raise InvalidRecord('Reopen this episode before adding new work.')
    if kind == 'review' and episode['subject'] != 'code':
        raise InvalidRecord('Code reviews belong in a code episode.')
    if kind == 'research' and episode['subject'] not in {'research', 'general'}:
        raise InvalidRecord('Research belongs in a research episode; link it from other work.')
    if kind in {'review', 'correction', 'lesson_review'} and not evidence:
        raise InvalidRecord(f'{kind} needs evidence references.')
    if kind == 'lesson' and episode['subject'] != 'general' and not evidence:
        raise InvalidRecord('A scoped lesson needs evidence.')
    if kind in {'action_result', 'follow_up'}:
        decision = memory._event(decision_id)
        if decision['kind'] != 'decision' or decision['episode_id'] != episode['id']:
            raise InvalidRecord('The decision must belong to this episode.')
    if kind == 'action_result':
        if not memory.db.execute("SELECT 1 FROM events WHERE kind='action' AND decision_id=?", (decision_id,)).fetchone():
            raise InvalidRecord('Record the action before its execution result.')
        if memory.db.execute("SELECT 1 FROM events WHERE kind='action_result' AND decision_id=?", (decision_id,)).fetchone():
            raise Conflict('Execution result already exists. Retry using the original request key.')
    if kind == 'lesson_review':
        lesson = memory._event(payload['lesson_id'])
        if lesson['kind'] != 'lesson' or lesson['episode_id'] != episode['id']:
            raise InvalidRecord('Review a lesson from this episode.')
        if payload['lesson_id'] not in {ref['event_id'] for ref in links}:
            raise InvalidRecord('Link the lesson being reviewed and explain why.')
        if payload['status'] == 'accepted' and memory._needs_review(payload['lesson_id']):
            raise InvalidRecord('Lesson evidence needs review; propose a corrected lesson first.')
    if kind == 'episode_status':
        allowed = {'active': {'settled', 'abandoned'}, 'reopened': {'settled', 'abandoned'},
                   'settled': {'reopened'}, 'abandoned': {'reopened'}}
        if payload['status'] not in allowed[episode['status']]:
            raise InvalidRecord('Invalid episode status transition.')
        if payload['status'] == 'settled' and memory.pending(episode['id'], limit=1)['decisions']:
            raise InvalidRecord('Record outstanding consequences or leave the episode open.')


class Workflow:
    @staticmethod
    def _subject(subject):
        from .core import SUBJECTS, InvalidRecord
        if subject not in SUBJECTS:
            raise InvalidRecord(f'subject must be one of {sorted(SUBJECTS)}.')

    def _validate_links(self, links):
        from .core import InvalidRecord, _text
        links = [] if links is None else links
        if not isinstance(links, list) or len(links) > 100:
            raise InvalidRecord('links must be a list of at most 100 references.')
        for ref in links:
            if not isinstance(ref, dict) or set(ref) != {'event_id', 'reason'}:
                raise InvalidRecord('Each event link needs event_id and reason.')
            _text(ref['event_id'], 'event_id', 200)
            _text(ref['reason'], 'link reason', 2000)
        if len({r['event_id'] for r in links}) != len(links):
            raise InvalidRecord('Event links must be unique.')
        return sorted(links, key=lambda ref: ref['event_id'])

    def _needs_review(self, event_id):
        return bool(self.review_reasons(event_id))

    def review_reasons(self, event_id):
        reasons=[]
        # UNION deduplicates dependencies. Traversal is iterative in SQLite,
        # rather than recursive Python reads of an ever-growing history.
        ancestors = '''WITH RECURSIVE ancestors(id) AS (
            SELECT ? UNION SELECT l.prior_event_id FROM event_links l JOIN ancestors a ON l.event_id=a.id
            UNION SELECT e.decision_id FROM events e JOIN ancestors a ON e.id=a.id WHERE e.decision_id IS NOT NULL
          ) '''
        # A failed attempt can support a later recovery. Revising the choice
        # does not itself invalidate the observed failure of that choice.
        explicit_ancestors = '''WITH RECURSIVE ancestors(id) AS (
            SELECT ? UNION SELECT l.prior_event_id FROM event_links l JOIN ancestors a ON l.event_id=a.id
          ) '''
        revised = self.db.execute(explicit_ancestors + '''SELECT a.id,n.id FROM ancestors a JOIN events n ON n.supersedes=a.id
            WHERE a.id != ? LIMIT 1''', (event_id, event_id)).fetchone()
        if revised:
            reasons.append({'reason':'An explicitly linked decision was revised.','record_id':revised[0],'replacement_id':revised[1]})
        direction=self.direction()
        version=direction['version']
        if direction.get('status')=='needs_review':
            reasons.append({'reason':'The evidence supporting the current project requirements needs review.','version':version})
        old=self.db.execute(ancestors + '''SELECT e.id,coalesce(json_extract(e.payload,'$.project_revision'),0) FROM ancestors a JOIN events e ON e.id=a.id
            WHERE e.kind='decision' AND coalesce(json_extract(e.payload,'$.project_revision'),0) != ? LIMIT 1''', (event_id,version)).fetchone() if version else None
        if old:
            reasons.append({'reason':'A decision used earlier project requirements.','record_id':old[0],'used_version':old[1],'current_version':version})
        from .planning import latest
        for choice in self.db.execute(ancestors + '''SELECT e.id,e.episode_id,json_extract(e.payload,'$.work_plan_id') AS plan_id
                FROM ancestors a JOIN events e ON e.id=a.id WHERE e.kind='decision'
                AND json_extract(e.payload,'$.work_plan_id') IS NOT NULL''', (event_id,)):
            prior = self._event(choice['plan_id'])['payload']
            current = latest(self, choice['episode_id'], 'work_plan')
            if current and any(prior.get(key, [] if key=='depends_on' else None) != current.get(key, [] if key=='depends_on' else None)
                               for key in ('scope','autonomy','depends_on')):
                reasons.append({'reason':'The work scope or prerequisites changed after this decision.','record_id':choice['id'],
                                'used_plan_id':choice['plan_id'],'current_plan_id':current['id']})
        direct = {row['id']: row['source_key'] for row in self.db.execute(
            'SELECT s.id,s.source_key FROM sources s JOIN dependencies d ON d.source_id=s.id WHERE d.event_id=?',
            (event_id,))}
        # A corrected outcome explicitly reassesses the current source. Its
        # decision retains the older evidence that prompted the correction.
        reassessed = {key for source_id, key in direct.items() if self.source_status(source_id)=='current_copy'} \
            if self._event(event_id)['kind']=='outcome' else set()
        rows = self.db.execute(ancestors + '''SELECT DISTINCT s.id,s.source_key FROM dependencies d
            JOIN ancestors a ON d.event_id=a.id JOIN sources s ON s.id=d.source_id''', (event_id,))
        for row in rows:
            state=self.source_status(row[0])
            if state=='superseded' and row[0] not in direct and row['source_key'] in reassessed:
                continue
            if state!='current_copy':reasons.append({'reason':'Referenced evidence changed or requires a check.','source_id':row[0],'status':state})
        return reasons

    def _lesson_review(self, lesson_id):
        import json
        row = self.db.execute("""SELECT id,payload FROM events WHERE kind='lesson_review'
            AND json_extract(payload,'$.lesson_id')=? ORDER BY rowid DESC LIMIT 1""", (lesson_id,)).fetchone()
        if row:
            value = json.loads(row['payload'])
            value['event_id'] = row['id']
            if value['status'] == 'accepted' and self._needs_review(row['id']):
                value['status'] = 'needs_review'
            return value

    def pending(self, episode_id=None, *, decision_id=None, limit=100, offset=0, due_only=False, unscheduled_only=False):
        """Unresolved execution and consequences, including work without a date."""
        import json
        from .core import InvalidRecord
        if type(limit) is not int or not 1 <= limit <= 10000 or type(offset) is not int or offset < 0:
            raise InvalidRecord('Use limit 1–10000 and a nonnegative offset.')
        if episode_id:
            self.episode(episode_id)
        sql = """SELECT d.id,d.episode_id,d.created_at,d.payload,
          (SELECT id FROM events a WHERE a.kind='action' AND a.decision_id=d.id) AS action_id,
          (SELECT payload FROM events r WHERE r.kind='action_result' AND r.decision_id=d.id) AS execution,
          (SELECT payload FROM events o WHERE o.kind='outcome' AND o.decision_id=d.id ORDER BY seq DESC LIMIT 1) AS outcome,
          (SELECT payload FROM events f WHERE f.kind='follow_up' AND f.decision_id=d.id ORDER BY seq DESC LIMIT 1) AS follow_up,
          EXISTS(SELECT 1 FROM events n WHERE n.supersedes=d.id) AS replaced
          FROM events d WHERE d.kind='decision'"""
        sql += ' AND d.episode_id=?' if episode_id else ''
        args = [episode_id] if episode_id else []
        if decision_id:
            sql += ' AND d.id=?'; args.append(decision_id)
        filters = "coalesce(json_extract(outcome,'$.assessment'),'pending') NOT IN ('good','bad') AND (action_id IS NOT NULL OR replaced=0)"
        deadline = "coalesce(json_extract(follow_up,'$.review_after'),json_extract(payload,'$.review_after'))"
        if due_only:
            filters += ' AND ' + deadline + '<=?'
            args.append(self.now())
        if unscheduled_only:
            filters += ' AND ' + deadline + ' IS NULL'
        selected = 'SELECT * FROM (' + sql + ') WHERE ' + filters
        total = self.db.execute('SELECT count(*) FROM (' + selected + ')', args).fetchone()[0]
        rows = self.db.execute(selected + ' ORDER BY created_at,id LIMIT ? OFFSET ?', (*args, limit, offset))
        decisions = []
        for row in rows:
            outcome = json.loads(row['outcome']) if row['outcome'] else None
            if outcome and outcome['assessment'] in {'good', 'bad'}:
                continue
            if row['replaced'] and not row['action_id']:
                continue
            payload = json.loads(row['payload'])
            follow = json.loads(row['follow_up']) if row['follow_up'] else {}
            execution = json.loads(row['execution']) if row['execution'] else None
            state = ('not_started' if not row['action_id'] else
                     'execution_unconfirmed' if not execution and not outcome else
                     'consequence_' + (outcome['assessment'] if outcome else 'pending'))
            decisions.append({'id': row['id'], 'episode_id': row['episode_id'], 'state': state,
                'decision': payload['decision'], 'created_at': row['created_at'],
                'review_after': follow.get('review_after', payload.get('review_after')),
                'owner': follow.get('owner', payload.get('follow_up_owner')),
                'execution_status': execution['execution_status'] if execution else 'unconfirmed'})
        return {'decisions': decisions, 'total': total, 'offset': offset, 'more': offset + len(decisions) < total, 'note': 'Unconfirmed actions must be reconciled with the host; do not repeat them automatically.'}

    def due(self, *, limit=20):
        from .core import InvalidRecord
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise InvalidRecord('limit must be between 1 and 1000.')
        sources = self.db.execute("""SELECT id FROM sources s WHERE review_after IS NOT NULL AND review_after<=?
            AND version=(SELECT max(version) FROM sources x WHERE x.source_key=s.source_key)
            ORDER BY review_after,id LIMIT ?""", (self.now(), limit+1)).fetchall()
        due = self.pending(limit=limit, due_only=True)
        unscheduled = self.pending(limit=limit, unscheduled_only=True)
        return {'sources': [r[0] for r in sources[:limit]], 'decisions': [r['id'] for r in due['decisions']],
                'checks': due['decisions'], 'unscheduled': [r['id'] for r in unscheduled['decisions']],
                'more': {'sources': len(sources)>limit, 'decisions': due['more'], 'unscheduled': unscheduled['more']},
                'note': 'The host must run these checks. No source refresh or outcome verification has occurred.'}

    def inspect(self, episode_id, *, limit=20):
        """Bounded state to reread after a conflict; never automatically rebase."""
        from .core import InvalidRecord
        if type(limit) is not int or not 1 <= limit <= 100:
            raise InvalidRecord('limit must be between 1 and 100.')
        episode = self.episode(episode_id)
        rows = self.db.execute('SELECT id FROM events WHERE episode_id=? ORDER BY seq DESC LIMIT ?', (episode_id, limit))
        return {'episode': episode, 'recent': [self.read(row[0]) for row in rows],
                'pending': self.pending(episode_id, limit=limit)['decisions'],
                'pending_more': self.pending(episode_id, limit=limit)['more'],
                'next_step': 'Reconsider using the current evidence; submit a new request key with this version.'}

    def maintain(self):
        """Explicit local maintenance; preserve original records and search history."""
        with self._write():
            self.db.execute("INSERT INTO search_index(search_index) VALUES ('optimize')")
        return {'integrity': self.db.execute('PRAGMA integrity_check').fetchone()[0],
                'foreign_key_errors': [tuple(r) for r in self.db.execute('PRAGMA foreign_key_check')],
                'search_rows': self.db.execute('SELECT count(*) FROM search_index').fetchone()[0]}

    def export_html(self, destination, **options):
        from .viewer import export_html
        return export_html(self, destination, **options)

    def lineage(self, decision_id, *, limit=10, offset=0):
        """Page the original choices, assessments and revisions without losing history."""
        from .core import InvalidRecord
        decision = self._event(decision_id)
        if decision['kind'] != 'decision':
            raise InvalidRecord('Lineage requires a decision ID.')
        if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
            raise InvalidRecord('Use limit 1–100 and a nonnegative offset.')
        # The existing contract makes every decision in an episode a revision of
        # the latest choice. Include all outcome revisions, including old failures.
        where = "episode_id=? AND kind IN ('decision','action','action_result','outcome','follow_up')"
        total = self.db.execute('SELECT count(*) FROM events WHERE '+where,(decision['episode_id'],)).fetchone()[0]
        ids = self.db.execute('SELECT id FROM events WHERE '+where+' ORDER BY seq LIMIT ? OFFSET ?',
                              (decision['episode_id'],limit,offset)).fetchall()
        return {'episode':self.episode(decision['episode_id']), 'records':[self.read(r[0]) for r in ids],
                'total':total,'offset':offset,'more':offset+len(ids)<total,
                'note':'Earlier failures remain evidence. A later success does not erase an earlier attempt.'}

    def signals(self, *, limit=10, scan_limit=500, offset=0):
        """Mechanical review candidates, not automatically accepted lessons."""
        from .core import InvalidRecord
        import json
        if type(limit) is not int or not 1 <= limit <= 100 or type(scan_limit) is not int or not 1 <= scan_limit <= 5000 or type(offset) is not int or offset < 0:
            raise InvalidRecord('Use limit 1–100 and scan_limit 1–5000.')
        ids=self.db.execute("SELECT id FROM events WHERE kind IN ('decision','outcome','lesson','research','review','correction') ORDER BY rowid DESC LIMIT ?",(scan_limit+1,)).fetchall()
        signals=[]
        for row in ids[:scan_limit]:
            item=self.read(row[0])
            if item['status']=='needs_review':
                signals.append({'type':'evidence_drift','record_ids':[item['id']], 'reason':'Supporting evidence changed, expired or depends on a revised record. Recheck before reuse.'})
        # Group only comparable recorded assessments. Do not label similar prose
        # as the same failure, infer causation, or hide successful attempts.
        groups={}
        for row in self.db.execute('''SELECT o.id,o.payload,d.payload AS choice,ep.subject,ep.task_type,ep.criterion
          FROM events o JOIN events d ON d.id=o.decision_id JOIN episodes ep ON ep.id=o.episode_id
          WHERE o.kind='outcome' AND NOT EXISTS (SELECT 1 FROM events n WHERE n.supersedes=o.id)
          ORDER BY o.rowid DESC LIMIT ?''',(scan_limit,)):
            p=json.loads(row['payload']);d=json.loads(row['choice'])
            key=(row['subject'],row['task_type'],row['criterion'],d.get('model'),d.get('condition'))
            group=groups.setdefault(key,{'assessed':0,'good':0,'failures':{}})
            if p['assessment'] in {'good','bad'}: group['assessed']+=1
            if p['assessment']=='good': group['good']+=1
            if p['assessment']=='bad' and p.get('failure_type'):
                group['failures'].setdefault(p['failure_type'],[]).append(row['id'])
        for key,group in groups.items():
            for failure,records in group['failures'].items():
                if len(records)>=2:
                    signals.append({'type':'repeated_failure','failure_type':failure,'record_ids':records[:20],
                        'failure_count':len(records),'assessed':group['assessed'],'successful':group['good'],
                        'subject':key[0],'task_type':key[1],'criterion':key[2],
                        'reason':'This named failure recurred in comparable recorded work. Inspect causes, successes and recoveries before proposing a scoped anti-pattern.'})
        return {'signals':signals[offset:offset+limit],'more':len(signals)>offset+limit,'offset':offset,'scanned':min(len(ids),scan_limit),
                'scan_limited':len(ids)>scan_limit,'note':'Signals nominate evidence for review. They do not change instructions, accept lessons or establish behavioural drift statistically.'}
