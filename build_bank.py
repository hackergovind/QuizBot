"""Fill questions.json in batches. Respects daily API budget so free quota never dies.

Usage:
  python build_bank.py          # add up to DAILY_REFILL (default 40) quizzes
  python build_bank.py --n 30   # add up to 30 this run
  python build_bank.py --target 1200
"""
import argparse
import asyncio
import datetime as dt
import os
import sys

import httpx
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(__file__))

from bank import add_to_bank, load_bank, load_state, save_state  # noqa: E402
from quiz_gen import generate_batch  # noqa: E402

API_KEY = os.getenv("OPENROUTER_API_KEY", "")
MODEL = os.getenv("OPENROUTER_MODEL", "nvidia/nemotron-3-super-120b-a12b:free")
TARGET = int(os.getenv("BANK_TARGET", "1200") or 1200)
DAILY_REFILL = int(os.getenv("DAILY_REFILL", "40") or 40)
BATCH = 5


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=None)
    ap.add_argument("--target", type=int, default=None)
    args = ap.parse_args()
    target = args.target or TARGET
    have = len(load_bank())
    if have >= target:
        print(f"bank full: {have}/{target}, nothing to do")
        return

    # daily budget guard
    state = load_state()
    today = dt.date.today().isoformat()
    if state.get("api_day") != today:
        state["api_day"] = today
        state["api_used_today"] = 0
    used = state.get("api_used_today", 0)
    budget = args.n or min(DAILY_REFILL - used, target - have)
    if budget <= 0:
        print(f"daily budget exhausted ({used}/{DAILY_REFILL}). Try tomorrow or raise DAILY_REFILL.")
        return
    # each API call yields ~BATCH quizzes
    calls_needed = max(1, (budget + BATCH - 1) // BATCH)
    print(f"bank {have}/{target} | budget {budget} quizzes ~ {calls_needed} API calls (used {used} today)")

    seen = [q.get("question", "") for q in load_bank()[-30:]]
    total_added = 0
    async with httpx.AsyncClient() as http:
        for i in range(calls_needed):
            try:
                items = await generate_batch(http, API_KEY, MODEL, seen)
                added = add_to_bank(items)
                total_added += added
                seen += [q["question"] for q in items]
                state["api_used_today"] = state.get("api_used_today", 0) + 1
                save_state(state)
                print(f"batch {i+1}/{calls_needed}: +{added} (bank {len(load_bank())})")
                await asyncio.sleep(5)  # gentle pace, avoid 429
            except Exception as e:  # noqa: BLE001
                print(f"batch {i+1} failed: {e}")
                await asyncio.sleep(15)
    print(f"done: +{total_added}, bank now {len(load_bank())}/{target}")


if __name__ == "__main__":
    asyncio.run(main())
