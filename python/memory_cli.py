"""Small local query tool for Spidey's event memory."""

import argparse
import json

from memory_store import MemoryStore


def main():
    parser = argparse.ArgumentParser(description="Query Spidey event memory")
    subparsers = parser.add_subparsers(dest="command", required=True)

    last_seen = subparsers.add_parser("last-seen")
    last_seen.add_argument("person")
    last_seen.add_argument("--room")

    intruders = subparsers.add_parser("intruders")
    intruders.add_argument("--limit", type=int, default=20)

    recent = subparsers.add_parser("recent")
    recent.add_argument("--limit", type=int, default=20)

    args = parser.parse_args()
    memory = MemoryStore()
    if args.command == "last-seen":
        result = memory.last_seen(args.person, args.room)
    elif args.command == "intruders":
        result = memory.recent_intruders(args.limit)
    else:
        result = memory.recent_events(args.limit)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
