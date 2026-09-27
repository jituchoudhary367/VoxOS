"""Manage the logins Vox may use. Secrets are typed here, never told to Vox.

    python -m voxos.vault add amazon --domain amazon.in
    python -m voxos.vault list
    python -m voxos.vault remove amazon

The username is asked for, the password is read without echo, and both go
straight into Windows Credential Manager.
"""

from __future__ import annotations

import argparse
import getpass

from voxos.vault.store import get_vault


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m voxos.vault")
    sub = parser.add_subparsers(dest="command", required=True)
    add = sub.add_parser("add", help="save a login (you will be prompted for it)")
    add.add_argument("name", help="short name you and Vox use, e.g. amazon")
    add.add_argument("--domain", action="append", required=True,
                     help="site it belongs to, e.g. amazon.in (repeat for more)")
    sub.add_parser("list", help="show saved logins (names and sites only)")
    remove = sub.add_parser("remove", help="delete a saved login")
    remove.add_argument("name")
    args = parser.parse_args(argv)

    vault = get_vault()
    if args.command == "add":
        username = input("Username / email: ").strip()
        password = getpass.getpass("Password (not shown): ")
        try:
            vault.add(args.name, username=username, password=password, domains=args.domain)
        except ValueError as e:
            print(f"Not saved: {e}")
            return 1
        print(f"Saved '{args.name.lower()}' for {', '.join(args.domain)} in Windows Credential Manager.")
    elif args.command == "list":
        items = vault.list()
        for item in items:
            print(f"{item['name']}: {', '.join(item['domains'])}")
        if not items:
            print("No saved logins.")
    elif args.command == "remove":
        print("Removed." if vault.remove(args.name) else "No such login.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
