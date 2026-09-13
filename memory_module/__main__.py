"""CLI: python -m memory_module --db project.sqlite COMMAND."""

import argparse
import json
from pathlib import Path
import sqlite3
import sys
from . import Memory, MemoryError, Hooks, migrate, dumps


def main(argv=None):
    parser = argparse.ArgumentParser(description='Record, search and review project memory.')
    parser.add_argument('--db', required=True, help='Local project database')
    sub = parser.add_subparsers(dest='command', required=True)
    inputs = {'init', 'start', 'source', 'record', 'hook'}
    for name in sorted(inputs):
        command = sub.add_parser(name)
        command.add_argument('--input', default='-', help='JSON file; default reads stdin')
    command = sub.add_parser('document', help='Capture selected local Markdown files without interpreting them')
    command.add_argument('paths', nargs='+')
    command.add_argument('--subject', required=True, choices=['general', 'code', 'writing', 'research'])
    command.add_argument('--review-after')
    for name in ['search', 'context']:
        command = sub.add_parser(name)
        command.add_argument('query')
        command.add_argument('--subject', choices=['general', 'code', 'writing', 'research'])
        command.add_argument('--include-general', action='store_true')
        if name == 'context':
            command.add_argument('--episode')
            command.add_argument('--max-chars', type=int, default=4000)
        else:
            command.add_argument('--limit', type=int, default=10)
            command.add_argument('--include-history', action='store_true')
            command.add_argument('--compact', action='store_true')
            command.add_argument('--offset', type=int, default=0)
    command = sub.add_parser('read')
    command.add_argument('id')
    command.add_argument('--detail', action='store_true')
    for name in ['episode', 'inspect']:
        command = sub.add_parser(name)
        command.add_argument('id')
    command = sub.add_parser('history')
    command.add_argument('--episode')
    command.add_argument('--day')
    for name in ['metrics', 'maintain']:
        sub.add_parser(name)
    command = sub.add_parser('lineage')
    command.add_argument('id')
    command.add_argument('--limit',type=int,default=10)
    command.add_argument('--offset',type=int,default=0)
    command = sub.add_parser('signals')
    command.add_argument('--limit',type=int,default=10)
    command.add_argument('--offset',type=int,default=0)
    command.add_argument('--scan-limit',type=int,default=500)
    command=sub.add_parser('pending')
    command.add_argument('--episode')
    command.add_argument('--limit',type=int,default=100)
    command.add_argument('--offset',type=int,default=0)
    command.add_argument('--due-only',action='store_true')
    command=sub.add_parser('due')
    command.add_argument('--limit',type=int,default=20)
    for name in ['backup', 'migrate']:
        command = sub.add_parser(name)
        command.add_argument('destination')
    command = sub.add_parser('html')
    command.add_argument('destination')
    command.add_argument('--episode')
    command.add_argument('--subject', choices=['general', 'code', 'writing', 'research'])
    command.add_argument('--since')
    command.add_argument('--until')
    command.add_argument('--max-records', type=int, default=1000)
    command.add_argument('--max-bytes', type=int, default=10_000_000)
    command.add_argument('--include-bodies', action='store_true')
    args = parser.parse_args(argv)
    try:
        data = None
        if args.command in inputs:
            data = json.loads(sys.stdin.read() if args.input == '-' else Path(args.input).read_text(encoding='utf-8'))
            if not isinstance(data, dict):
                raise ValueError('Input must be a JSON object.')
        if args.command == 'migrate':
            result = migrate(args.db, args.destination)
        elif args.command == 'init':
            with Memory.create(args.db, **data) as memory:
                result = {'project': memory.project, 'status': 'created', 'path': str(memory.path)}
        else:
            with Memory(args.db) as memory:
                if args.command in {'start', 'source', 'record'}:
                    result = getattr(memory, args.command)(**data)
                elif args.command == 'document':
                    with memory._write():
                        result = {'documents': [memory.document(path, subject=args.subject, review_after=args.review_after) for path in args.paths]}
                elif args.command == 'hook':
                    actor, event = data.pop('actor'), data.pop('event')
                    hooks = Hooks(memory, actor)
                    if event not in {'before_action', 'after_action', 'capture', 'resume'}:
                        raise ValueError('Unknown hook event.')
                    result = getattr(hooks, event)(**data)
                elif args.command == 'search':
                    result = memory.search(args.query, limit=args.limit, include_history=args.include_history,
                                           subject=args.subject, include_general=args.include_general, compact=args.compact, offset=args.offset)
                elif args.command == 'context':
                    result = memory.context(args.query, episode_id=args.episode, budget=args.max_chars,
                                            subject=args.subject, include_general=args.include_general)
                elif args.command == 'read':
                    result = memory.read(args.id, detail=args.detail)
                elif args.command in {'episode', 'inspect'}:
                    result = getattr(memory, args.command)(args.id)
                elif args.command == 'history':
                    print(memory.history(args.episode, day=args.day), end='')
                    return 0
                elif args.command == 'pending':
                    result=memory.pending(args.episode,limit=args.limit,offset=args.offset,due_only=args.due_only)
                elif args.command == 'due':
                    result=memory.due(limit=args.limit)
                elif args.command == 'lineage':
                    result=memory.lineage(args.id,limit=args.limit,offset=args.offset)
                elif args.command == 'signals':
                    result=memory.signals(limit=args.limit,offset=args.offset,scan_limit=args.scan_limit)
                elif args.command == 'backup':
                    result = memory.backup(args.destination)
                elif args.command == 'html':
                    result = memory.export_html(args.destination, episode_id=args.episode, subject=args.subject,
                        since=args.since, until=args.until, max_records=args.max_records,
                        max_bytes=args.max_bytes, include_bodies=args.include_bodies)
                else:
                    result = getattr(memory, args.command)()
        print(dumps(result))
        return 0
    except (MemoryError, ValueError, TypeError, KeyError, OSError, sqlite3.Error) as exc:
        print(dumps({'error': type(exc).__name__, 'message': str(exc)}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
