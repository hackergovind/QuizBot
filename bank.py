"""Local question bank — serve quizzes without hitting OpenRouter every time."""
import json
import os
import random

BANK_PATH = os.path.join(os.path.dirname(__file__), "questions.json")
STATE_PATH = os.path.join(os.path.dirname(__file__), "state.json")
SCORES_PATH = os.path.join(os.path.dirname(__file__), "scores.json")


def load_bank() -> list[dict]:
    if not os.path.exists(BANK_PATH):
        return []
    try:
        with open(BANK_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_bank(bank: list[dict]) -> None:
    tmp = BANK_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(bank, f, ensure_ascii=False, indent=1)
    os.replace(tmp, BANK_PATH)


def add_to_bank(new_items: list[dict]) -> int:
    """Append validated quizzes, skipping duplicates (by question text). Returns added count."""
    bank = load_bank()
    seen = {q.get("question", "").strip().lower() for q in bank}
    added = 0
    for q in new_items:
        key = q.get("question", "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        bank.append(q)
        added += 1
    if added:
        save_bank(bank)
    return added


def load_state() -> dict:
    default = {"pos": 0, "set_no": 1, "posted_in_set": 0, "api_used_today": 0, "api_day": ""}
    if not os.path.exists(STATE_PATH):
        return default
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            d = json.load(f)
        for k, v in default.items():
            d.setdefault(k, v)
        return d
    except Exception:
        return default


def save_state(state: dict) -> None:
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=1)
    os.replace(tmp, STATE_PATH)


def pop_next(shuffle_each_set: bool = True) -> tuple[dict | None, dict]:
    """Take next quiz from bank in order; reshuffle at each 120-set boundary.

    Returns (quiz_or_None, state). Caller must save_state after updating counters.
    """
    from quiz_gen import SET_SIZE  # avoid circulars at import time

    bank = load_bank()
    if not bank:
        return None, load_state()
    state = load_state()
    pos = state.get("pos", 0) % len(bank)
    # new set boundary (except very first start): reshuffle to vary order
    if shuffle_each_set and state.get("posted_in_set", 0) == 0 and state.get("pos", 0) != 0:
        random.shuffle(bank)
        save_bank(bank)
    quiz = bank[pos]
    state["pos"] = (pos + 1) % len(bank)
    state["posted_in_set"] = state.get("posted_in_set", 0) + 1
    if state["posted_in_set"] >= SET_SIZE:
        state["posted_in_set"] = 0
        state["set_no"] = state.get("set_no", 1) + 1
        state["new_set"] = True
    else:
        state["new_set"] = False
    return quiz, state


# ---- leaderboard store (works in groups; channel polls are anonymous) ----
def load_scores() -> dict:
    if not os.path.exists(SCORES_PATH):
        return {}
    try:
        with open(SCORES_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_scores(scores: dict) -> None:
    tmp = SCORES_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(scores, f, indent=1)
    os.replace(tmp, SCORES_PATH)


def add_score(user_id: int, name: str, correct: bool, set_no: int) -> None:
    scores = load_scores()
    key = str(set_no)
    board = scores.setdefault(key, {})
    entry = board.setdefault(str(user_id), {"name": name, "correct": 0, "attended": 0})
    entry["name"] = name
    entry["attended"] += 1
    if correct:
        entry["correct"] += 1
    save_scores(scores)


def top_board(set_no: int, limit: int = 10) -> list[tuple[str, int, int]]:
    scores = load_scores()
    board = scores.get(str(set_no), {})
    rows = [(v["name"], v["correct"], v["attended"]) for v in board.values()]
    rows.sort(key=lambda r: (-r[1], r[2]))
    return rows[:limit]
