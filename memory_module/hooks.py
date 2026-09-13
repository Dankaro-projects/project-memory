"""Host adapters. Call these at tool boundaries; prompts alone do not enforce capture."""

import time
from .core import Conflict, InvalidRecord, MemoryError, _text


class CaptureFailure(MemoryError):
    """The operation may have run. Reconcile its result before any retry."""


class Hooks:
    OPERATIONS = {'edit', 'execute', 'test', 'publish', 'delete'}
    CAPTURES = {'review_completed': 'review', 'research_completed': 'research',
                'user_correction': 'correction', 'lesson_proposed': 'lesson'}

    def __init__(self, memory, actor):
        _text(actor, 'actor', 200)
        self.memory, self.actor = memory, actor

    def before_action(self, *, episode_id, operation, decision, expected_version,
                      request_key, evidence=None, links=None, supersedes=None):
        """Persist intent before invoking the tool; reject repeated execution attempts."""
        with self.memory._write():
            return self._prepare(episode_id=episode_id, operation=operation, decision=decision,
                expected_version=expected_version, request_key=request_key, evidence=evidence,
                links=links, supersedes=supersedes)

    def _prepare(self, *, episode_id, operation, decision, expected_version,
                 request_key, evidence=None, links=None, supersedes=None):
        if operation not in self.OPERATIONS:
            raise InvalidRecord(f'operation must be one of {sorted(self.OPERATIONS)}.')
        if not isinstance(decision, dict) or not {'review_after', 'follow_up_owner'} <= decision.keys():
            raise InvalidRecord('Hook decisions need review_after and follow_up_owner.')
        _text(request_key, 'request_key', 180)
        memory = self.memory
        # Fail closed even if this is an identical delivery: a persisted action
        # might have executed before a crash or a lost acknowledgement.
        if memory.db.execute('SELECT 1 FROM events WHERE request_key=?', (request_key + ':action',)).fetchone():
            raise Conflict('Action was already prepared and may have run. Inspect it; do not execute it again.')
        recorded = memory.record(episode_id, 'decision', decision, expected_version=expected_version,
            request_key=request_key + ':decision', actor=self.actor, evidence=evidence, links=links, supersedes=supersedes)
        action = memory.record(episode_id, 'action', {'action': operation, 'host_reference': request_key},
            expected_version=recorded['version'], request_key=request_key + ':action', actor=self.actor,
            decision_id=recorded['id'])
        if action['duplicate']:
            raise Conflict('Another host prepared this action. Reconcile before executing.')
        return {'episode_id': episode_id, 'decision_id': recorded['id'],
                'action_id': action['id'], 'version': action['version'], 'request_key': request_key}

    def after_action(self, *, ticket, execution_status, summary, expected_version,
                     duration_ms=None, artifact=None, evidence=None):
        memory = self.memory
        action = memory._event(ticket['action_id'])
        if (action['kind'] != 'action' or action['episode_id'] != ticket['episode_id']
                or action['decision_id'] != ticket['decision_id']
                or action['request_key'] != ticket['request_key'] + ':action'
                or action['actor'] != self.actor):
            raise InvalidRecord('Ticket does not match this host action.')
        payload = {'execution_status': execution_status, 'summary': summary}
        if duration_ms is not None:
            payload['duration_ms'] = duration_ms
        if artifact is not None:
            payload['artifact'] = artifact
        return memory.record(ticket['episode_id'], 'action_result', payload,
            expected_version=expected_version, request_key=ticket['request_key'] + ':result',
            actor=self.actor, decision_id=ticket['decision_id'], evidence=evidence,
            links=[{'event_id': ticket['action_id'], 'reason': 'Execution report for this recorded action.'}])

    def run(self, operation_fn, *, summarize=None, **before):
        """Wrap one host operation. Never retry the operation or rebase a conflict.

        A returned callback is 'completed', not a successful consequence. The
        host must still assess the result against the episode's criterion.
        """
        ticket = self.before_action(**before)
        started = time.perf_counter()
        try:
            result = operation_fn()
        except BaseException as operation_error:
            try:
                self.after_action(ticket=ticket, execution_status='failed',
                    summary=f'Host callback raised {type(operation_error).__name__}; inspect the host output.',
                    expected_version=ticket['version'], duration_ms=round((time.perf_counter()-started)*1000))
            except BaseException as capture_error:
                raise CaptureFailure(f'Operation raised {type(operation_error).__name__} and result capture failed. '
                                     f'Reconcile action {ticket["action_id"]}; do not rerun automatically.') from capture_error
            raise
        try:
            summary = summarize(result) if summarize else 'Host callback returned. The consequence has not been assessed.'
            recorded = self.after_action(ticket=ticket, execution_status='completed', summary=summary,
                expected_version=ticket['version'], duration_ms=round((time.perf_counter()-started)*1000))
        except BaseException as capture_error:
            raise CaptureFailure(f'Host callback returned but result capture failed. Reconcile action '
                                 f'{ticket["action_id"]}; do not rerun automatically.') from capture_error
        return {'ticket': ticket, 'capture': recorded, 'result': result}

    def capture(self, *, trigger, episode_id, payload, expected_version, request_key, evidence, links=None):
        if trigger not in self.CAPTURES:
            raise InvalidRecord(f'Unknown trigger. Use one of {sorted(self.CAPTURES)}.')
        if not evidence:
            raise InvalidRecord('A captured interpretation needs evidence references.')
        if trigger == 'user_correction' and self.memory.episode(episode_id)['subject'] != 'writing':
            raise InvalidRecord('Writing corrections belong in a writing episode; link them from other work.')
        return self.memory.record(episode_id, self.CAPTURES[trigger], payload,
            expected_version=expected_version, request_key=request_key, actor=self.actor,
            evidence=evidence, links=links)

    def resume(self, episode_id=None, limit=10):
        """Call on host startup, task resume and scheduled check runs."""
        return {'pending': self.memory.pending(episode_id, limit=limit), 'due': self.memory.due(limit=limit)}
