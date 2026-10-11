# AIAPGET Homeopathy Quiz Bot (Telegram)

Autonomous bot: posts unlimited 4-option Homeopathy AIAPGET PYQ quizzes as Telegram
**Quiz polls** with 20-second voting, then auto-reveals correct answer + explanation.

## How rate-limit death was fixed
- Bot serves from local `questions.json` bank — **zero API calls per quiz**.
- `build_bank.py` refills in batches (1 call = ~5 Qs), max `DAILY_REFILL=40` calls/day.
- Free OpenRouter quota is 50 req/day → old live-API bot died in ~30 min. Bank bot runs forever.

## 1. Setup channel
1. Bot `@homeopathytestquiz_bot` is already admin in `https://t.me/homeopathyezy`.
2. Optional ranked group: create/link a discussion group, add bot as admin,
   set `GROUP_ID=-100xxxx` in `.env` (channel polls are anonymous — no scores possible there).

## 2. Run
```bat
cd homeopathy-quiz-bot
pip install -r requirements.txt
python build_bank.py --n 30   # fill bank (uses daily budget)
python bot.py                 # posts Set N Qx/120 every QUIZ_INTERVAL
```

## 3. Commands
Anyone: `/start` `/set` `/leaderboard` `/score` `/id`
Admin only (`ADMIN_ID`): `/quiznow` `/stop` `/startauto` `/refill`

## 4. Config (.env)
| Key | Meaning |
|---|---|
| `BOT_TOKEN` / `OPENROUTER_API_KEY` | secrets |
| `OPENROUTER_MODEL` | `nvidia/nemotron-3-super-120b-a12b:free` |
| `CHANNEL_ID` | `@homeopathyezy` |
| `GROUP_ID` | ranked group or empty |
| `QUIZ_INTERVAL` | `60` (20s vote + gap) |
| `BANK_TARGET` / `DAILY_REFILL` | `1200` / `40` |
| `ADMIN_ID` | `8490922727` |

## 5. Files
`bot.py` (bank-first loop, 120-Q sets, leaderboard) · `quiz_gen.py` (PYQ prompt,
single + batch) · `bank.py` (bank/state/scores store) · `build_bank.py`
`questions.json` (bank) · `state.json` (pos/set/api budget) · `scores.json`
