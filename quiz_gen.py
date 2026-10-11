"""Generate AIAPGET Homeopathy MCQs via OpenRouter. Strict: 4 options, homeopathy only."""
import json
import random
import re
import httpx

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

SYSTEM_PROMPT = """You are a strict AIAPGET (All India AYUSH Post Graduate Entrance Test) Homeopathy question setter. You reproduce REAL Previous Year Questions (PYQs) — you do NOT invent your own questions except as a last resort.

RULES - NEVER BREAK:
1. Output ONLY Homeopathy AIAPGET entrance-test MCQs. No general knowledge, no ayurveda-only, no modern-medicine-only, no unrelated topics.
2. Allowed subjects ONLY: Homeopathic Materia Medica, Organon of Medicine & Homeopathic Philosophy, Homeopathic Repertory, Homeopathic Pharmacy, Practice of Medicine (from homeopathic perspective).
3. PRIORITY: use a REAL AIAPGET / AYUSH PGET / UPSC Homeopathy PYQ whenever you know one. At least 8 out of 10 questions MUST be real PYQs, worded as in the original paper. Do NOT paraphrase away the exam wording; keep options in original order but set correct_index accordingly.
4. If you genuinely do not know a real PYQ for the given topic, you may give ONE exam-pattern question, but mark subject as "Homeopathy (Model)".
5. Every question MUST be a single MCQ with EXACTLY 4 options, exactly 1 correct.
6. Question: 20-280 characters, exam-style, unambiguous. Prefix with year when known, e.g. "[AIAPGET 2022] ...".
7. Each option: 1-90 characters, plausible, similar length. No "All of the above" / "None of the above".
8. Explanation: 20-180 characters, why the correct option is right + source (e.g. "AIAPGET 2021; Ref: Boericke").
9. Output VALID JSON ONLY, no markdown, no extra text:
{"question": "[AIAPGET 2021] ...", "options": ["...", "...", "...", "..."], "correct_index": 0, "explanation": "...", "subject": "..."}
correct_index is 0-3.
NEVER output fake years. If year is unknown, omit the year tag but keep the PYQ wording.
"""

TOPICS = [
    "Homeopathic Materia Medica - polychrests",
    "Homeopathic Materia Medica - nosodes and sarcodes",
    "Organon of Medicine - aphorisms",
    "Homeopathic Philosophy - Kent, Hahnemann, Roberts",
    "Homeopathic Repertory - Kent, Boger-Boenninghausen, Murphy",
    "Homeopathic Pharmacy - potentisation, scales, vehicles",
    "Practice of Medicine - respiratory disorders homeopathic management",
    "Practice of Medicine - skin disorders homeopathic management",
    "Practice of Medicine - paediatrics homeopathic management",
    "Materia Medica - keynotes and modalities",
    "Surgery (homeopathy perspective)",
    "Gynaecology & Obstetrics (homeopathy perspective)",
    "Anatomy", "Physiology", "Pathology",
    "Forensic Medicine & Toxicology", "Community Medicine",
]

# One exam set = 120 questions (real AIAPGET pattern)
SET_SIZE = 120


def _safe_str(v, default="") -> str:
    if v is None:
        return default
    s = str(v).strip()
    # Telegram poll options can break on smart quotes / odd unicode — normalize
    repl = {"’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-", "…": "..."}
    for k, rep in repl.items():
        s = s.replace(k, rep)
    # drop remaining non-printable / replacement chars
    s = s.replace("�", "'")
    return s


def _extract_json(text: str) -> dict:
    """Pull JSON out even if model wraps it in code fences or adds reasoning."""
    if not text:
        raise ValueError("empty model response")
    text = text.strip()
    # remove markdown fences
    text = re.sub(r"^```(json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    # find all {...} blocks, try largest-first
    candidates = re.findall(r"\{.*?\}", text, re.DOTALL)
    # also try greedy match as fallback
    greedy = re.search(r"\{.*\}", text, re.DOTALL)
    if greedy:
        candidates.append(greedy.group(0))
    for cand in reversed(candidates):
        try:
            obj = json.loads(cand)
            if isinstance(obj, dict) and "question" in obj and "options" in obj:
                return obj
        except Exception:
            continue
    # last resort: direct parse
    return json.loads(text)


def _validate(data: dict) -> dict:
    q = _safe_str(data.get("question"))
    opts = data.get("options", [])
    ci = data.get("correct_index", data.get("answer", data.get("correct_option", -1)))
    exp = _safe_str(data.get("explanation", data.get("reason", "")), "See standard text.")
    subj = _safe_str(data.get("subject", "Homeopathy"), "Homeopathy")[:60]

    if len(q) < 10 or len(q) > 300:
        raise ValueError("bad question length")
    if not isinstance(opts, list) or len(opts) != 4:
        raise ValueError("must have exactly 4 options")
    opts = [_safe_str(o)[:100] for o in opts]
    if any(len(o) < 1 for o in opts):
        raise ValueError("empty option")
    if len(set(o.lower() for o in opts)) != 4:
        raise ValueError("duplicate options")
    try:
        ci = int(ci)
    except Exception:
        raise ValueError("correct_index must be 0-3")
    if ci not in (0, 1, 2, 3):
        raise ValueError("correct_index must be 0-3")
    if len(exp) < 5:
        exp = "Correct as per standard Homeopathy text."
    # Telegram limits: question 1-300, option 1-100, explanation 0-200
    return {
        "question": q[:300],
        "options": opts,
        "correct_index": ci,
        "explanation": exp[:200],
        "subject": subj or "Homeopathy",
    }


async def generate_batch(
    http: httpx.AsyncClient, api_key: str, model: str, seen: list[str], n: int = 5
) -> list[dict]:
    """One API call -> N quizzes. 5x fewer requests, avoids 429s."""
    topic = random.choice(TOPICS)
    avoid = ""
    if seen:
        avoid = "Do NOT repeat:\n- " + "\n- ".join(seen[-15:]) + "\n"
    user_msg = (
        f"Give {n} REAL AIAPGET Homeopathy PYQs on/around: {topic}.\n{avoid}"
        "Mostly real previous-year questions with year tags. Do not invent. "
        f"Return a JSON ARRAY of {n} objects only, each "
        '{"question": "[AIAPGET 2021] ...", "options": [4 strings], '
        '"correct_index": 0, "explanation": "...", "subject": "..."}. No markdown.'
    )
    r = await http.post(
        OPENROUTER_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://t.me/homeopathyezy",
            "X-Title": "AIAPGET Homeopathy Quiz Bot",
        },
        json={
            "model": model,
            "models": [model, "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free"],
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            "temperature": 0.7,
            "max_tokens": 6000,
        },
        timeout=180,
    )
    r.raise_for_status()
    payload = r.json()
    if "choices" not in payload:
        raise ValueError(f"no choices: {str(payload)[:300]}")
    text = payload["choices"][0]["message"].get("content") or ""
    if not text.strip():
        raise ValueError("empty content (reasoning-only response)")
    # salvage: pull every complete {...} object, even if array got truncated
    items: list = []
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if m:
        try:
            parsed = json.loads(m.group(0))
            items = parsed if isinstance(parsed, list) else [parsed]
        except Exception:
            items = []
    if not items:
        for cand in re.findall(r"\{[^{}]+\}", text, re.DOTALL):
            try:
                o = json.loads(cand)
                if isinstance(o, dict) and "question" in o:
                    items.append(o)
            except Exception:
                continue
    out = []
    for it in items:
        try:
            out.append(_validate(it))
        except Exception:
            continue
    if not out:
        raise ValueError(f"batch had 0 valid quizzes (raw {len(text)} chars)")
    return out


async def generate_quiz(
    http: httpx.AsyncClient, api_key: str, model: str, seen: list[str], retries: int = 5
) -> dict:
    """Call OpenRouter and return a validated quiz dict. Raises on failure."""
    # fallback models if primary is rate-limited
    models = [model, "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free", "google/gemma-4-26b-a4b-it:free"]
    last_err = None
    for attempt in range(retries):
        use_model = models[attempt % len(models)]
        topic = random.choice(TOPICS)
        avoid = ""
        if seen:
            avoid = "Do NOT repeat these recent questions:\n- " + "\n- ".join(seen[-8:]) + "\n"
        user_msg = (
            f"Give ONE REAL AIAPGET Homeopathy PYQ on: {topic}.\n{avoid}"
            "Prefer an actual previous-year question with year tag. Do not invent. Return JSON only."
        )
        try:
            r = await http.post(
                OPENROUTER_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://t.me/homeopathyezy",
                    "X-Title": "AIAPGET Homeopathy Quiz Bot",
                },
                json={
                    "model": use_model,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_msg},
                    ],
                    "temperature": 0.9,
                    "max_tokens": 800,
                },
                timeout=60,
            )
            r.raise_for_status()
            payload = r.json()
            if "choices" not in payload:
                raise ValueError(f"no choices: {str(payload)[:300]}")
            text = payload["choices"][0]["message"]["content"]
            data = _extract_json(text)
            return _validate(data)
        except Exception as e:  # noqa: BLE001 - retry any failure
            last_err = e
            import asyncio as _aio

            await _aio.sleep(2 + attempt * 2)
            continue
    raise RuntimeError(f"Quiz generation failed after {retries} tries: {last_err}")
