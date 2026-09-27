"""See or edit what Vox has learned about you.

    python -m voxos.core.profile show
    python -m voxos.core.profile forget "HP laptops"
    python -m voxos.core.profile reset
"""

from __future__ import annotations

import argparse

from voxos.core.profile.store import get_profile


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m voxos.core.profile")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("show", help="print the profile")
    forget = sub.add_parser("forget", help="remove facts containing a phrase")
    forget.add_argument("phrase")
    sub.add_parser("reset", help="forget all facts and habits (keeps the user id)")
    args = parser.parse_args()

    profile = get_profile()
    if args.command == "show":
        print(f"File: {profile.path}")
        print(f"User id: {profile.user_id}")
        print(profile.summary() or "(nothing learned yet)")
    elif args.command == "forget":
        removed = profile.forget(args.phrase)
        profile.save()
        print(f"Removed {removed} fact(s).")
    elif args.command == "reset":
        profile.facts.clear()
        profile.name = None
        for counter in profile.habits.values():
            counter.clear()
        profile.save()
        print("Profile reset.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
