"""FatSecret Platform API - Proof-of-Concept Probe
==================================================
THROWAWAY PoC — do NOT wire this into the bot.

PURPOSE
-------
Validates connectivity and access to the FatSecret API at three escalating
levels to evaluate replacing manual food logging in GarminBot.

HOW TO REGISTER A FATSECRET APP
---------------------------------
1. Go to https://platform.fatsecret.com/api/
2. Sign in / create a developer account.
3. Create a new application. Note down:
   - Consumer Key  → FATSECRET_CONSUMER_KEY in .env
   - Consumer Secret → FATSECRET_CONSUMER_SECRET in .env
4. IP WHITELIST: FatSecret blocks requests from unknown IPs.
   In the developer portal, add your machine's public IP to the allow-list
   before running L0. Without this step L0 will return a 401/403.

USER FOOD DATA (L1 / L2)
--------------------------
L1 performs a 3-legged OAuth1 PIN flow linked to a real fatsecret.com
user account. L2 reads that user's food diary.  For L2 to return any data
you must log at least one day of food in the FatSecret iOS/Android app
(or fatsecret.com) before running the probe.

LIBRARY NOTE
-------------
pip name:    fatsecret  (NOT pyfatsecret — that is a different, OAuth2-only package)
import name: fatsecret
class:       Fatsecret

Run with:
    .venv-fatsecret\\Scripts\\python.exe scripts\\fatsecret_probe.py --help
    .venv-fatsecret\\Scripts\\python.exe scripts\\fatsecret_probe.py --level 0
"""

import argparse
import datetime
import json
import os
import sys
from pathlib import Path
from typing import Any

# dotenv is included in the fatsecret package deps — load it gracefully.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # fall through to plain os.getenv


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
TOKEN_FILE = DATA_DIR / "fatsecret_token.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_jsonable(obj: Any) -> Any:
    """Recursively convert pydantic models and lists to plain dicts for json.dumps."""
    if isinstance(obj, list):
        return [_to_jsonable(x) for x in obj]
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    return obj


def _get_credentials() -> tuple[str, str]:
    """Read consumer key/secret from environment; abort with clear message if missing."""
    key = os.getenv("FATSECRET_CONSUMER_KEY", "")
    secret = os.getenv("FATSECRET_CONSUMER_SECRET", "")
    if not key or not secret:
        print(
            "ERROR: FATSECRET_CONSUMER_KEY and/or FATSECRET_CONSUMER_SECRET not set.\n"
            "Add them to .env or export them before running."
        )
        sys.exit(1)
    return key, secret


def _load_saved_token() -> tuple[str, str] | None:
    """Return (access_token, access_token_secret) from TOKEN_FILE, or None."""
    if not TOKEN_FILE.exists():
        return None
    try:
        data = json.loads(TOKEN_FILE.read_text())
        return (data["access_token"], data["access_token_secret"])
    except (KeyError, json.JSONDecodeError) as exc:
        print(f"  [warn] token file exists but could not be parsed: {exc}")
        return None


def _save_token(access_token: str, access_token_secret: str) -> None:
    """Persist OAuth1 access token to TOKEN_FILE as JSON (owner-only perms)."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(
        json.dumps(
            {"access_token": access_token, "access_token_secret": access_token_secret},
            indent=2,
        )
    )
    try:
        os.chmod(TOKEN_FILE, 0o600)  # no-op on Windows; matters on the Hetzner VPS
    except OSError:
        pass
    print(f"  Token saved to {TOKEN_FILE}")


# ---------------------------------------------------------------------------
# L0: 2-legged food search (OAuth2 client credentials)
# ---------------------------------------------------------------------------

def run_level_0() -> None:
    """L0: public food search via OAuth2 client credentials — no user needed."""
    print("\n=== L0: 2-legged food search (OAuth2) ===")
    from fatsecret import Fatsecret  # import inside function — never at module level

    key, secret = _get_credentials()
    try:
        fs = Fatsecret(key, secret, auth="oauth2")
        results = fs.foods.search_v1(search_expression="apple", max_results=3)
        print(f"  Raw result: {json.dumps(_to_jsonable(results), indent=2, default=str)}")
        print("  L0 PASSED")
    except Exception as exc:
        print(f"  L0 FAILED: {type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# L1: 3-legged OAuth1 — authenticate + profile.get
# ---------------------------------------------------------------------------

def run_level_1() -> None:
    """L1: 3-legged OAuth1 PIN flow (or reuse saved token) + profile.get."""
    print("\n=== L1: 3-legged OAuth1 — profile.get ===")
    from fatsecret import Fatsecret

    key, secret = _get_credentials()
    saved = _load_saved_token()

    try:
        if saved:
            print(f"  Reusing saved token from {TOKEN_FILE}")
            fs = Fatsecret(key, secret, session_token=saved)
        else:
            fs = Fatsecret(key, secret)  # OAuth1 is the default
            authorize_url = fs.get_authorize_url(callback_url="oob")
            print(f"\n  Visit this URL and approve the app:\n  {authorize_url}\n")
            verifier = input("  Paste the PIN from FatSecret: ").strip()
            token_tuple = fs.authenticate(verifier)
            _save_token(token_tuple[0], token_tuple[1])
            masked_token = token_tuple[0][:6] + "..." if len(token_tuple[0]) > 6 else "***"
            masked_secret = token_tuple[1][:6] + "..." if len(token_tuple[1]) > 6 else "***"
            print(f"  access_token        = {masked_token} (masked)")
            print(f"  access_token_secret = {masked_secret} (masked)")

        profile = fs.profile.get_v1()
        print(f"  profile.get() raw: {json.dumps(_to_jsonable(profile), indent=2, default=str)}")
        print("  L1 PASSED")
    except Exception as exc:
        print(f"  L1 FAILED: {type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# L2: food diary entries for a given date
# ---------------------------------------------------------------------------

def run_level_2(target_date: datetime.date) -> None:
    """L2: read food diary entries for target_date via OAuth1 user session."""
    print(f"\n=== L2: food diary entries for {target_date} ===")
    from fatsecret import Fatsecret

    key, secret = _get_credentials()
    saved = _load_saved_token()
    if not saved:
        print(
            "  L2 FAILED: no saved token found. Run L1 first to authenticate and save a token."
        )
        return

    try:
        fs = Fatsecret(key, secret, session_token=saved)

        # The library's diary.entries_get_v1 accepts date as datetime.date,
        # datetime.datetime, int (unix timestamp), or float — it calls
        # Fatsecret.unix_time_v2() internally which converts to days-since-epoch.
        # We pass a datetime.date directly; no manual conversion needed.
        entries = fs.diary.entries_get_v1(date=target_date)
        print(f"  Raw entries ({len(entries)} item(s)):")
        for entry in entries:
            print(f"    {json.dumps(_to_jsonable(entry), indent=4, default=str)}")

        # Inspect macro fields — FoodEntry model uses: protein, fat, carbohydrate
        # (confirmed from fatsecret/models/_generated/food_diary.py FoodEntry class)
        macro_fields = {"protein", "fat", "carbohydrate"}
        present: set[str] = set()
        missing: set[str] = set()
        for entry in entries:
            entry_dict = _to_jsonable(entry)
            for field in macro_fields:
                if entry_dict.get(field) is not None:
                    present.add(field)
                else:
                    missing.add(field)

        print(f"\n  Macro fields present : {sorted(present) or '(none — no entries?)'}")
        print(f"  Macro fields missing : {sorted(missing) or '(none)'}")
        print("  L2 PASSED")
    except Exception as exc:
        print(f"  L2 FAILED: {type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "FatSecret API probe — tests connectivity at three escalating levels.\n"
            "Requires FATSECRET_CONSUMER_KEY and FATSECRET_CONSUMER_SECRET in .env."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--level",
        choices=["0", "1", "2", "all"],
        default="all",
        help=(
            "Which level to run: "
            "0=2-legged food search (OAuth2), "
            "1=3-legged PIN auth + profile.get (OAuth1), "
            "2=food diary entries (OAuth1, requires L1 first), "
            "all=run 0 then 1 then 2 (default)."
        ),
    )
    parser.add_argument(
        "--date",
        default=None,
        metavar="YYYY-MM-DD",
        help="Target date for L2 diary lookup (default: today).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    # Parse --date
    if args.date:
        try:
            target_date = datetime.date.fromisoformat(args.date)
        except ValueError:
            print(f"ERROR: --date must be YYYY-MM-DD, got: {args.date!r}")
            sys.exit(1)
    else:
        target_date = datetime.date.today()

    level = args.level
    if level in ("0", "all"):
        run_level_0()
    if level in ("1", "all"):
        run_level_1()
    if level in ("2", "all"):
        run_level_2(target_date)


if __name__ == "__main__":
    main()
