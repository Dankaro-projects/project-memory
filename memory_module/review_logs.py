"""Read bounded host events without exposing command output in review status."""
from datetime import datetime, timezone
import json


class ReviewLog:
    def __init__(self, folder):
        self.folder = folder
        self.offset = 0
        self.pending = b''
        self.discarding = False
        self.last_size = (0, 0)
        self.result = None
        self.metrics = {'phase':'starting', 'last_event':None, 'last_activity_at':None,
                        'completed_inspections':0, 'failed_inspections':0, 'active_inspections':0,
                        'host_error_events':0, 'reconnect_events':0, 'unparsed_events':0,
                        'output_bytes':0, 'stderr_bytes':0, 'provider_usage':None, 'model':None}

    def event(self, value):
        if not isinstance(value,dict):
            self.metrics['unparsed_events'] += 1
            return
        kind = value.get('type','unknown')
        if not isinstance(kind,str): kind = 'unknown'
        self.metrics['last_event'] = kind[:100]
        if kind in {'error','turn.failed'} or value.get('is_error'):
            self.metrics['host_error_events'] += 1
            self.metrics['phase'] = 'host_error'
        if kind in {'reconnect','reconnecting','connection.reconnecting'}:
            self.metrics['reconnect_events'] += 1
        if isinstance(value.get('model'),str): self.metrics['model'] = value['model'][:180]
        # Codex emits aggregate usage at turn completion; Claude emits it in result.
        if isinstance(value.get('usage'),dict) and kind in {'turn.completed','result'}:
            if kind=='result':
                self.metrics['provider_usage'] = value['usage']
            else:
                usage = self.metrics['provider_usage'] or []
                self.metrics['provider_usage'] = (usage+[value['usage']])[-100:]
        item = value.get('item')
        if isinstance(item,dict) and item.get('type')=='command_execution':
            if kind=='item.started':
                self.metrics['active_inspections'] += 1
            elif kind=='item.completed':
                self.metrics['active_inspections'] = max(0,self.metrics['active_inspections']-1)
                self.metrics['completed_inspections'] += 1
                if item.get('exit_code') not in (None,0): self.metrics['failed_inspections'] += 1
            self.metrics['phase'] = 'inspecting' if self.metrics['active_inspections'] else 'awaiting_host'
        if kind=='assistant':
            message = value.get('message',{})
            if isinstance(message.get('model'),str): self.metrics['model'] = message['model'][:180]
            for block in message.get('content',[]):
                if isinstance(block,dict) and block.get('type')=='tool_use':
                    self.metrics['active_inspections'] += 1
                    self.metrics['phase'] = 'inspecting'
        if kind=='user':
            for block in value.get('message',{}).get('content',[]):
                if isinstance(block,dict) and block.get('type')=='tool_result':
                    self.metrics['active_inspections'] = max(0,self.metrics['active_inspections']-1)
                    self.metrics['completed_inspections'] += 1
                    if block.get('is_error'): self.metrics['failed_inspections'] += 1
                    self.metrics['phase'] = 'inspecting' if self.metrics['active_inspections'] else 'awaiting_host'
        if kind in {'turn.started','thread.started','system'} and not self.metrics['active_inspections']:
            self.metrics['phase'] = 'awaiting_host'
        if kind=='result': self.result = value
        if kind in {'turn.completed','result'} and not value.get('is_error'):
            self.metrics['phase'] = 'report_received'

    def read(self, final=False):
        output, stderr = self.folder/'output.jsonl', self.folder/'stderr.log'
        stats = [p.stat() if p.exists() else None for p in (output,stderr)]
        sizes = tuple(s.st_size if s else 0 for s in stats)
        changed = [s.st_mtime for old,new,s in zip(self.last_size,sizes,stats) if s and new!=old]
        if changed:
            self.metrics['last_activity_at'] = datetime.fromtimestamp(max(changed),timezone.utc).isoformat()
        self.last_size = sizes
        self.metrics.update(output_bytes=sizes[0], stderr_bytes=sizes[1])
        if output.exists():
            with output.open('rb') as stream:
                stream.seek(self.offset)
                while True:
                    chunk = stream.read(1024*1024)
                    self.offset += len(chunk)
                    self.pending += chunk
                    while b'\n' in self.pending:
                        line, self.pending = self.pending.split(b'\n',1)
                        if self.discarding:
                            self.discarding = False
                        else:
                            self.parse(line)
                    if len(self.pending)>4*1024*1024:
                        self.pending = b''
                        if not self.discarding: self.metrics['unparsed_events'] += 1
                        self.discarding = True
                    if not final or not chunk: break
        if final and self.pending and not self.discarding:
            self.parse(self.pending)
            self.pending = b''
        last = self.metrics['last_activity_at']
        self.metrics['seconds_since_activity'] = round(max(0,(datetime.now(timezone.utc)-datetime.fromisoformat(last)).total_seconds()),1) if last else None
        return dict(self.metrics)

    def parse(self, line):
        if not line.strip(): return
        if len(line)>4*1024*1024:
            self.metrics['unparsed_events'] += 1
            return
        try:
            value = json.loads(line)
            self.event(value)
        except (ValueError,TypeError,AttributeError):
            self.metrics['unparsed_events'] += 1
