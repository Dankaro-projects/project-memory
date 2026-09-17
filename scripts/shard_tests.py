"""Split the test modules into shards of similar cost.

The suite runs in about one minute here and in fifteen to twenty minutes on a
Windows runner, where starting a process and running git are far slower. Every
test still runs; they are shared between jobs so that no single job approaches
its limit. Modules are ordered by their measured cost and dealt to the shard
with the least work so far, which keeps the halves close together.
"""
import argparse
from pathlib import Path

# Seconds measured on Windows runners (the slowest of four jobs of run 35211086683, 17 September 2026), from the first test line of a module to the first test line of the next. A module that is not listed is assumed
# to be light, which is true of every module that measured fifteen seconds or less there.
COST = {
    'test_machine': 260, 'test_focus': 238, 'test_hive_workers': 201, 'test_api': 192,
    'test_delegation': 119, 'test_workspace': 83, 'test_focus_review': 79, 'test_hive': 72,
    'test_memory': 62, 'test_routing': 59, 'test_usage': 58, 'test_reassess': 55,
    'test_workflow': 52, 'test_guards': 48, 'test_review_diagnostics': 41, 'test_coverage': 35,
    'test_templates': 33, 'test_feedback': 32, 'test_codex': 28, 'test_instructions': 28,
    'test_failure_recovery': 27, 'test_planning': 26, 'test_install_clients': 25, 'test_n8n_architecture': 25,
    'test_claude': 23, 'test_mcp_tables': 23, 'test_phase': 19, 'test_architecture': 17,
    'test_product': 16,
}
DEFAULT_COST = 15


def shards(names, count):
    """Deal the most expensive module to the emptiest shard."""
    groups = [[] for _ in range(count)]
    totals = [0] * count
    for name in sorted(names, key=lambda item: (-COST.get(item, DEFAULT_COST), item)):
        index = totals.index(min(totals))
        groups[index].append(name)
        totals[index] += COST.get(name, DEFAULT_COST)
    return groups, totals


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--shard', type=int, default=1, help='One based shard number.')
    parser.add_argument('--of', type=int, default=3, help='How many shards.')
    parser.add_argument('--plan', action='store_true', help='Print every shard with its estimated cost.')
    args = parser.parse_args()
    if args.of < 1 or not 1 <= args.shard <= args.of:
        raise SystemExit('Select a shard between 1 and the number of shards.')
    names = sorted(path.stem for path in Path(__file__).resolve().parents[1].joinpath('tests').glob('test_*.py'))
    groups, totals = shards(names, args.of)
    if args.plan:
        for number, (group, total) in enumerate(zip(groups, totals), start=1):
            print(f'shard {number}: about {total} seconds on Windows, {len(group)} modules')
            print('  ' + ' '.join(group))
        return
    print(' '.join('tests.' + name for name in groups[args.shard - 1]))


if __name__ == '__main__':
    main()
