#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent / '.env', override=False)

from app.core.migrations import migrate_down, migrate_up, migration_status  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description='AiceMind Admin DB migrations')
    sub = parser.add_subparsers(dest='cmd', required=True)

    sub.add_parser('up', help='Apply pending migrations')

    p_down = sub.add_parser('down', help='Rollback applied migrations')
    p_down.add_argument('--steps', type=int, default=1, help='Number of migrations to rollback')

    sub.add_parser('status', help='Show migration status')

    args = parser.parse_args()

    if args.cmd == 'up':
        applied = migrate_up()
        print(json.dumps({'ok': True, 'applied': applied}, ensure_ascii=False))
        return 0

    if args.cmd == 'down':
        rolled = migrate_down(args.steps)
        print(json.dumps({'ok': True, 'rolledBack': rolled}, ensure_ascii=False))
        return 0

    if args.cmd == 'status':
        rows = migration_status()
        print(json.dumps({'ok': True, 'rows': rows}, ensure_ascii=False, indent=2))
        return 0

    return 1


if __name__ == '__main__':
    sys.exit(main())
