#!/usr/bin/env python3
"""Provision, disable, or reset AutoBackup accounts without self-signup.

Run only on the administrator's machine with SUPABASE_URL and
SUPABASE_SERVICE_ROLE_KEY environment variables. Never commit these secrets.
"""
import argparse
import getpass
import json
import os
import re
from urllib.request import Request, urlopen
from urllib.error import HTTPError

LOGIN_DOMAIN = "users.autobackup.example.com"
USERNAME = re.compile(r"^[a-z][a-z0-9_.-]{2,31}$")
UUID = re.compile(r"^[0-9a-f-]{36}$", re.I)


def api(method, path, body):
    base = os.environ["SUPABASE_URL"].rstrip("/")
    secret = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    if not re.fullmatch(r"https://[a-z0-9-]+\.supabase\.co", base):
        raise RuntimeError("SUPABASE_URL must be your Supabase HTTPS project URL")
    req = Request(base + "/auth/v1/admin/" + path,
                  data=json.dumps(body).encode() if body is not None else None,
                  headers={"apikey": secret, "Authorization": "Bearer " + secret,
                           "Content-Type": "application/json"},
                  method=method)
    try:
        with urlopen(req, timeout=30) as response:
            return json.loads(response.read())
    except HTTPError as exc:
        raise RuntimeError(f"Supabase administration request returned HTTP {exc.code}") from exc


def main():
    parser = argparse.ArgumentParser(description="AutoBackup administrator user provisioning")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create")
    create.add_argument("username")
    for cmd in ("disable", "enable", "reset-password"):
        p = sub.add_parser(cmd)
        p.add_argument("user_id", help="UUID printed by create")
    args = parser.parse_args()
    if args.command == "create":
        username = args.username.lower()
        if not USERNAME.fullmatch(username):
            parser.error("Username: 3-32 lowercase letters/digits/._-; must start with letter")
        password = getpass.getpass("New password (at least 14 characters): ")
        if len(password) < 14:
            parser.error("Password too short")
        user = api("POST", "users", {
            "email": username + "@" + LOGIN_DOMAIN, "password": password,
            "email_confirm": True,
            "app_metadata": {"autobackup_enabled": True},
        })
        print("Created account:", username, "| user ID:", user.get("id"))
        print("Give the user their username and password by a secure channel.")
    else:
        if not UUID.fullmatch(args.user_id):
            parser.error("Invalid user ID")
        if args.command == "reset-password":
            password = getpass.getpass("Replacement password (at least 14 characters): ")
            if len(password) < 14:
                parser.error("Password too short")
            body = {"password": password}
        else:
            body = {"app_metadata": {"autobackup_enabled": args.command == "enable"}}
        api("PUT", "users/" + args.user_id, body)
        print("Updated user", args.user_id,
              "(previously minted R2 credentials remain usable until expiry).")


if __name__ == "__main__":
    main()
