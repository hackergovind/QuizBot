"""Autonomous AIAPGET Homeopathy Quiz Bot — bank-first (no rate-limit death)."""
import asyncio
import datetime as dt
import logging
import os

import httpx
from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ParseMode
from telegram.error import RetryAfter, TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    PollAnswerHandler,
)

from bank import add_score, consume_next, load_bank, load_state, pop_next, save_state, top_board
from quiz_gen import SET_SIZE, generate_batch

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "nvidia/nemotron-3-super-120b-a12b:free")
CHANNEL_ID = os.getenv("CHANNEL_ID", "@homeopathyezy")
GROUP_ID = os.getenv("GROUP_ID", "")  # linked discussion group for leaderboard (optional)
ADMIN_ID = int(os.getenv("ADMIN_ID", "8490922727") or 8490922727)
QUIZ_INTERVAL = int(os.getenv("QUIZ_INTERVAL", "60") or 60)  # 20s vote + gap
DAILY_REFILL = int(os.getenv("DAILY_REFILL", "40") or 40)
BANK_TARGET = int(os.getenv("BANK_TARGET", "2000") or 2000)
# True: each posted quiz is DELETED from memory forever (2000-cycle consume mode)
CONSUME_MODE = os.getenv("CONSUME_MODE", "true").strip().lower() in ("1", "true", "yes")

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO
)
log = logging.getLogger("quizbot")

running = True
http_client: httpx.AsyncClient | None = None
# poll_id -> (correct_ids, set_no)
open_polls: dict[str, tuple[list[int], int]] = {}


def is_admin(update: Update) -> bool:
    return update.effective_user is not None and update.effective_user.id == ADMIN_ID


def _refill_allowed() -> bool:
    """Max DAILY_REFILL API calls/day so free quota never dies."""
    st = load_state()
    today = dt.date.today().isoformat()
    if st.get("api_day") != today:
        st["api_day"] = today
        st["api_used_today"] = 0
        save_state(st)
    return st.get("api_used_today", 0) < DAILY_REFILL


async def _refill_bank(reason: str = "") -> int:
    """One batched API call -> ~10 quizzes into bank. Returns added count."""
    global http_client
    assert http_client is not None
    if not _refill_allowed():
        log.warning("refill skipped (daily budget %s used) %s", DAILY_REFILL, reason)
        return 0
    from bank import add_to_bank

    bank = load_bank()
    seen = [q.get("question", "") for q in bank[-15:]]
    try:
        items = await generate_batch(http_client, OPENROUTER_API_KEY, OPENROUTER_MODEL, seen, n=10)
        added = add_to_bank(items)
        st = load_state()
        st["api_used_today"] = st.get("api_used_today", 0) + 1
        save_state(st)
        log.info("refill %s: +%s (bank %s)", reason, added, len(load_bank()))
        return added
    except Exception as e:  # noqa: BLE001
        log.warning("refill failed %s: %s", reason, e)
        return 0


async def send_one_quiz(app: Application, chat_id: str, anonymous: bool) -> bool:
    """Serve next quiz from bank (consume mode: posted quiz is deleted from memory)."""
    quiz, state = consume_next() if CONSUME_MODE else pop_next()
    if quiz is None:
        log.warning("bank empty — emergency refill")
        added = await _refill_bank("empty")
        if not added:
            return False
        quiz, state = consume_next() if CONSUME_MODE else pop_next()
        if quiz is None:
            return False
    # background top-up when bank runs low (don't block posting)
    if len(load_bank()) < BANK_TARGET and len(load_bank()) - state.get("pos", 0) < 200:
        if _refill_allowed():
            app.create_task(_refill_bank("low"))

    save_state(state)
    set_no = state.get("set_no", 1)
    q_no = state.get("posted_in_set", 1)
    header = f"[Set {set_no} Q{q_no}/{SET_SIZE}]"
    try:
        msg = await app.bot.send_poll(
            chat_id=chat_id,
            question=f"{header} {quiz['question']}"[:300],
            options=quiz["options"],
            type="quiz",
            correct_option_ids=[quiz["correct_index"]],
            explanation=f"[{quiz.get('subject','Homeopathy')}] {quiz['explanation']}"[:200],
            open_period=20,
            is_anonymous=anonymous,
        )
        if msg.poll:
            open_polls[msg.poll.id] = ([quiz["correct_index"]], set_no)
        left = len(load_bank())
        log.info("posted set %s Q%s to %s (bank left %s): %s", set_no, q_no, chat_id, left, quiz["question"][:60])
        if state.get("cycle_done"):
            await app.bot.send_message(
                chat_id=chat_id,
                text=f"🎯 <b>2000 questions completed ({state.get('posted_total', 0)} total).</b>\n"
                "Memory cleared — old questions deleted. Fresh bank building up!",
                parse_mode=ParseMode.HTML,
            )
        if state.get("new_set"):
            await app.bot.send_message(
                chat_id=chat_id,
                text=f"🏁 <b>Set {set_no - 1} complete (120 Qs).</b>\n/new leaderboard below. Starting <b>Set {set_no}</b> — scores refreshed!",
                parse_mode=ParseMode.HTML,
            )
            await post_board(app, chat_id, set_no - 1)
        return True
    except RetryAfter as e:
        log.warning("flood control, sleeping %ss", e.retry_after + 2)
        await asyncio.sleep(e.retry_after + 2)
        return False
    except TelegramError as e:
        log.error("send failed to %s: %s", chat_id, e)
        return False


async def post_board(app: Application, chat_id: str, set_no: int) -> None:
    rows = top_board(set_no)
    if not rows:
        await app.bot.send_message(
            chat_id=chat_id,
            text=f"📊 <b>Set {set_no} leaderboard:</b> no ranked votes yet — "
            "add me to a group for ranked polls (channel polls are anonymous).",
            parse_mode=ParseMode.HTML,
        )
        return
    lines = [f"📊 <b>Set {set_no} Top {len(rows)}</b>"]
    for i, (name, correct, attended) in enumerate(rows, 1):
        lines.append(f"{i}. {name} — {correct}✅ / {attended}")
    await app.bot.send_message(chat_id=chat_id, text="\n".join(lines), parse_mode=ParseMode.HTML)


async def auto_loop(app: Application):
    await asyncio.sleep(5)
    log.info("auto-loop -> %s every %ss (bank %s, consume=%s)", CHANNEL_ID, QUIZ_INTERVAL, len(load_bank()), CONSUME_MODE)
    while True:
        try:
            if running:
                if CONSUME_MODE and not load_bank():
                    # memory empty + daily refill budget decides pace — wait, don't hot-loop
                    log.info("bank empty, waiting 30 min for refill budget")
                    await asyncio.sleep(1800)
                    continue
                # channel must stay anonymous; group copy ranked when GROUP_ID set
                ok = await send_one_quiz(app, CHANNEL_ID, anonymous=True)
                if GROUP_ID:
                    try:
                        await send_one_quiz(app, GROUP_ID, anonymous=False)
                    except Exception as e:  # noqa: BLE001
                        log.warning("group post failed: %s", e)
                await asyncio.sleep(QUIZ_INTERVAL if ok else 15)
            else:
                await asyncio.sleep(5)
        except asyncio.CancelledError:
            break
        except Exception as e:  # noqa: BLE001
            log.exception("loop error: %s", e)
            await asyncio.sleep(10)


# ---- handlers ----
async def on_poll_answer(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Ranked scoring — fires only for non-anonymous (group) polls."""
    ans = update.poll_answer
    if not ans:
        return
    info = open_polls.get(ans.poll_id)
    if not info:
        return
    correct_ids, set_no = info
    user = ans.user
    name = user.first_name if user else "Unknown"
    picked = list(ans.option_ids)
    add_score(ans.user.id if ans.user else 0, name, picked == correct_ids, set_no)


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    st = load_state()
    await update.message.reply_text(
        "🏥 <b>AIAPGET Homeopathy Quiz Bot</b>\n\n"
        f"Set {st.get('set_no',1)} • Q{st.get('posted_in_set',0)}/{SET_SIZE} • bank {len(load_bank())}\n"
        f"Auto-posts to {CHANNEL_ID} every ~{QUIZ_INTERVAL}s (20s vote + answer).\n\n"
        "/score — your set score\n/leaderboard — set top 10\n/set — current set status\n"
        "Admin: /stop /startauto /quiznow /refill",
        parse_mode=ParseMode.HTML,
    )


async def cmd_stop(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global running
    if not is_admin(update):
        return
    running = False
    await update.message.reply_text("⏸ Auto-quiz paused.")


async def cmd_startauto(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global running
    if not is_admin(update):
        return
    running = True
    await update.message.reply_text("▶️ Auto-quiz resumed.")


async def cmd_quiznow(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    await update.message.reply_text("⏳ Posting from bank…")
    ok = await send_one_quiz(ctx.application, CHANNEL_ID, anonymous=True)
    if update.effective_chat and str(update.effective_chat.id) != CHANNEL_ID:
        await send_one_quiz(ctx.application, update.effective_chat.id, anonymous=False)
    if not ok:
        await update.message.reply_text("⚠️ Bank empty + refill blocked (daily budget?). Try /refill later.")


async def cmd_refill(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    await update.message.reply_text("⏳ Refilling bank (1 API call = ~10 Qs)…")
    added = await _refill_bank("manual")
    await update.message.reply_text(f"Bank now {len(load_bank())}/{BANK_TARGET} (+{added}).")


async def cmd_set(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    st = load_state()
    await update.message.reply_text(
        f"📦 Set {st.get('set_no',1)} — Q{st.get('posted_in_set',0)}/{SET_SIZE}\n"
        f"Bank: {len(load_bank())}/{BANK_TARGET}"
    )


async def cmd_board(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    st = load_state()
    await post_board(ctx.application, update.effective_chat.id, st.get("set_no", 1))


async def cmd_score(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    from bank import load_scores

    st = load_state()
    board = load_scores().get(str(st.get("set_no", 1)), {})
    e = board.get(str(update.effective_user.id))
    if not e:
        await update.message.reply_text("No ranked votes yet in this set (vote in the group, not channel).")
    else:
        await update.message.reply_text(f"✅ {e['correct']} correct / {e['attended']} attended (Set {st.get('set_no',1)}).")


async def cmd_id(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"chat_id: <code>{update.effective_chat.id}</code>", parse_mode=ParseMode.HTML)


async def post_init(app: Application):
    global http_client
    http_client = httpx.AsyncClient()
    # seed bank on first ever run so loop never posts nothing
    if not load_bank():
        log.info("empty bank — seeding with one batch")
        try:
            from bank import add_to_bank

            items = await generate_batch(http_client, OPENROUTER_API_KEY, OPENROUTER_MODEL, [], n=10)
            add_to_bank(items)
            st = load_state()
            st["api_used_today"] = st.get("api_used_today", 0) + 1
            save_state(st)
        except Exception as e:  # noqa: BLE001
            log.warning("seed failed: %s", e)
    app.create_task(auto_loop(app))


async def post_shutdown(app: Application):
    global http_client
    if http_client:
        await http_client.aclose()


def start_health_server() -> None:
    """Tiny stdlib HTTP server so Render free web services pass health checks.

    Render sends $PORT and sleeps the service after 15 min without traffic —
    ping / with UptimeRobot to keep the quiz loop alive.
    """
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    port = int(os.getenv("PORT", "0") or 0)
    if not port:
        return  # local run: no health server needed

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b"ok"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log.info("health server on :%s", port)


def main():
    if not BOT_TOKEN or not OPENROUTER_API_KEY:
        raise SystemExit("Missing BOT_TOKEN or OPENROUTER_API_KEY in .env")
    start_health_server()
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).post_shutdown(post_shutdown).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("stop", cmd_stop))
    app.add_handler(CommandHandler("startauto", cmd_startauto))
    app.add_handler(CommandHandler("quiznow", cmd_quiznow))
    app.add_handler(CommandHandler("refill", cmd_refill))
    app.add_handler(CommandHandler("set", cmd_set))
    app.add_handler(CommandHandler(["leaderboard", "board"], cmd_board))
    app.add_handler(CommandHandler("score", cmd_score))
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(PollAnswerHandler(on_poll_answer))
    log.info("bot starting… channel=%s group=%s admin=%s", CHANNEL_ID, GROUP_ID or "-", ADMIN_ID)
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
