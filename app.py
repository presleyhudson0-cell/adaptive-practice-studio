import io
import json
import os
import random
import re
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None

st.set_page_config(page_title="Adaptive Practice Studio", page_icon="📚", layout="wide")
DATA_DIR = Path.home() / ".adaptive_practice_studio"
DATA_FILE = DATA_DIR / "progress.json"


def read_progress():
    try:
        if DATA_FILE.exists():
            data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        pass
    return []


def save_progress(records):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(records, indent=2), encoding="utf-8")


def normalize_question(q):
    if not isinstance(q, dict):
        return None
    prompt = str(q.get("question", "")).strip()
    choices = q.get("options", [])
    if not isinstance(choices, list) or len(choices) != 4:
        return None
    choices = [str(x).strip() for x in choices]
    if not prompt or any(not c for c in choices) or len(set(choices)) != 4:
        return None
    try:
        answer_index = int(q.get("answer_index", -1))
    except (ValueError, TypeError):
        return None
    if answer_index not in range(4):
        return None
    return {"question": prompt, "options": choices, "answer_index": answer_index,
            "topic": str(q.get("topic", "General")).strip() or "General",
            "explanation": str(q.get("explanation", "")).strip()}


def arithmetic_question(topic, difficulty):
    high = {"Easy": 12, "Medium": 30, "Hard": 100}[difficulty]
    a, b = random.randint(2, high), random.randint(2, high)
    if topic == "Addition":
        correct, prompt = a + b, f"What is {a} + {b}?"
    elif topic == "Subtraction":
        a, b = max(a, b), min(a, b)
        correct, prompt = a - b, f"What is {a} − {b}?"
    elif topic == "Multiplication":
        correct, prompt = a * b, f"What is {a} × {b}?"
    else:
        quotient = random.randint(2, high)
        divisor = random.randint(2, 12)
        correct, prompt = quotient, f"What is {quotient * divisor} ÷ {divisor}?"
    choices = {correct}
    attempts = 0
    while len(choices) < 4 and attempts < 100:
        attempts += 1
        offset = random.choice([-1, 1]) * random.randint(1, max(4, abs(correct) // 4 + 3))
        candidate = correct + offset
        if candidate >= 0:
            choices.add(candidate)
    while len(choices) < 4:
        choices.add(max(choices) + 1)
    choices = [str(x) for x in choices]
    random.shuffle(choices)
    return {"question": prompt, "options": choices, "answer_index": choices.index(str(correct)),
            "topic": topic, "explanation": f"The correct answer is {correct}."}


def generate_offline(count, difficulty, focus=None):
    topics = ["Addition", "Subtraction", "Multiplication", "Division"]
    return [arithmetic_question(focus or random.choice(topics), difficulty) for _ in range(count)]


def extract_json(text):
    cleaned = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", text.strip(), flags=re.I)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start >= 0 and end > start:
            return json.loads(cleaned[start:end + 1])
        raise


def generate_ai(key, course, source, count, difficulty, focus=None):
    from openai import OpenAI
    client = OpenAI(api_key=key)
    focus_prompt = f"Focus questions on this skill or weak topic: {focus}." if focus else "Vary topics across the material."
    prompt = f"""Create {count} accurate multiple-choice practice questions for the student's course: {course}.
Difficulty: {difficulty}. {focus_prompt}
Use ONLY the source material below. If it lacks material needed for {count} valid questions, create fewer questions, not facts.
Return ONLY a JSON object with a key 'questions' holding a list of objects.
Each object must contain: question (string), options (array of exactly 4 distinct answer strings),
answer_index (integer 0-3), topic (short string), explanation (why correct).
Questions must have exactly one correct answer. Avoid repeating questions verbatim.
Source material:\n{source[:24000]}"""
    response = client.responses.create(model="gpt-4.1-mini", input=prompt)
    payload = extract_json(response.output_text)
    raw = payload.get("questions", [])
    return [q for v in raw if (q := normalize_question(v)) is not None][:count]


def upload_text(upload):
    if upload is None:
        return ""
    if upload.name.lower().endswith(".pdf"):
        if PdfReader is None:
            raise ValueError("Install pypdf to read PDFs.")
        reader = PdfReader(io.BytesIO(upload.getvalue()))
        return "\n".join(p.extract_text() or "" for p in reader.pages)
    return upload.getvalue().decode("utf-8", errors="replace")


def record_answer(index):
    questions = st.session_state.questions
    answers = st.session_state.answers
    selected = st.session_state.get(f"choice_{index}")
    if selected is None or index in answers:
        return
    q = questions[index]
    correct = selected == q["answer_index"]
    answers[index] = {"selected": selected, "correct": correct}
    records = read_progress()
    records.append({"timestamp": datetime.now(timezone.utc).isoformat(), "course": st.session_state.course,
                    "topic": q["topic"], "correct": correct, "mode": st.session_state.mode})
    save_progress(records)


def reset_quiz(questions, course, mode):
    st.session_state.questions = questions
    st.session_state.answers = {}
    st.session_state.course = course
    st.session_state.mode = mode
    st.session_state.quiz_id = st.session_state.get("quiz_id", 0) + 1


st.title("📚 Adaptive Practice Studio")
st.caption("Practice by topic, see explanations, and target your mistakes. Data is stored locally on this computer.")

with st.sidebar:
    st.header("Configure your practice")
    mode = st.radio("Question source", ["Offline math", "AI from class notes"], help="AI mode requires your own OpenAI API key and may incur charges.")
    difficulty = st.selectbox("Difficulty", ["Easy", "Medium", "Hard"], index=1)
    count = st.slider("Number of questions", 3, 12, 6)
    course = "Math practice"
    notes = ""
    key = ""
    if mode == "AI from class notes":
        course = st.text_input("Class / subject", "Calculus")
        notes = st.text_area("Paste course material", height=160, placeholder="Paste notes, definitions, examples, or study guide text")
        uploaded = st.file_uploader("Or upload a text or text-based PDF", type=["txt", "md", "pdf"])
        if uploaded is not None:
            try:
                notes += "\n" + upload_text(uploaded)
            except Exception as exc:
                st.error(f"Could not read the file: {exc}")
        key = st.text_input("OpenAI API key (not saved)", type="password", value=os.environ.get("OPENAI_API_KEY", ""))
        st.caption("Do not paste sensitive notes. Uploaded text is sent to the API when you generate questions. Scanned PDFs may not extract text.")
    create = st.button("Generate practice test", type="primary", use_container_width=True)

if create:
    with st.spinner("Preparing questions..."):
        try:
            if mode == "Offline math":
                qs = generate_offline(count, difficulty)
            elif not key.strip() or len(notes.strip()) < 30:
                st.error("For AI mode, add an API key and at least 30 characters of class material.")
                qs = []
            else:
                qs = generate_ai(key.strip(), course, notes, count, difficulty)
            if qs:
                reset_quiz(qs, course, mode)
                st.success(f"Created {len(qs)} questions.")
            elif mode == "AI from class notes" and key and len(notes.strip()) >= 30:
                st.warning("No valid questions were generated. Try adding more notes.")
        except Exception as exc:
            st.error(f"Couldn't generate questions. Check the API key and connection. Details: {exc}")

questions = st.session_state.get("questions", [])
if questions:
    st.subheader("Your practice test")
    st.caption("Submit each answer to see feedback. Your progress is saved after each submitted answer.")
    for i, q in enumerate(questions):
        with st.container(border=True):
            st.markdown(f"**Question {i + 1} · {q['topic']}**")
            st.write(q["question"])
            key_choice = f"choice_{st.session_state.quiz_id}_{i}"
            if i not in st.session_state.answers:
                selected = st.radio("Choose an answer", options=list(range(4)),
                                    format_func=lambda x, opts=q["options"]: opts[x],
                                    index=None, key=key_choice, label_visibility="collapsed")
                if st.button("Submit answer", key=f"submit_{st.session_state.quiz_id}_{i}"):
                    if selected is None:
                        st.warning("Choose an answer first.")
                    else:
                        st.session_state[f"choice_{i}"] = selected
                        record_answer(i)
                        st.rerun()
            else:
                answer = st.session_state.answers[i]
                correct = q["options"][q["answer_index"]]
                if answer["correct"]:
                    st.success(f"Correct! {correct}")
                else:
                    st.error(f"Correct answer: {correct}")
                if q["explanation"]:
                    st.info(q["explanation"])

    if len(st.session_state.answers) == len(questions):
        correct_count = sum(v["correct"] for v in st.session_state.answers.values())
        st.metric("Your score", f"{correct_count}/{len(questions)}")
        weak = sorted({q["topic"] for i, q in enumerate(questions) if not st.session_state.answers[i]["correct"]})
        if weak:
            st.write("**Topics to practice:** " + ", ".join(weak))
            focus = st.selectbox("Choose a topic for targeted practice", weak)
            if st.button("Generate targeted follow-up questions"):
                try:
                    if st.session_state.mode == "Offline math":
                        qs = generate_offline(5, difficulty, focus)
                    else:
                        if not key.strip() or len(notes.strip()) < 30:
                            st.error("Re-enter your API key and course notes in the sidebar first.")
                            qs = []
                        else:
                            qs = generate_ai(key.strip(), st.session_state.course, notes, 5, difficulty, focus)
                    if qs:
                        reset_quiz(qs, st.session_state.course, st.session_state.mode)
                        st.rerun()
                except Exception as exc:
                    st.error(f"Could not create targeted practice: {exc}")
        else:
            st.success("Great work — no missed topics on this test!")
else:
    st.info("Choose a mode in the sidebar and click **Generate practice test** to start.")

st.divider()
st.subheader("Performance history")
records = read_progress()
if records:
    import pandas as pd
    df = pd.DataFrame(records)
    overall = df["correct"].mean() * 100
    st.metric("Overall correctness across saved attempts", f"{overall:.1f}%")
    topic_data = df.groupby("topic", as_index=False).agg(attempts=("correct", "size"), accuracy=("correct", "mean"))
    topic_data["accuracy"] = (topic_data["accuracy"] * 100).round(1)
    topic_data = topic_data.rename(columns={"topic": "Topic", "attempts": "Attempts", "accuracy": "Accuracy (%)"})
    st.dataframe(topic_data, hide_index=True, use_container_width=True)
    st.download_button("Download progress as CSV", df.to_csv(index=False), file_name="practice-progress.csv", mime="text/csv")
    if st.button("Clear saved progress"):
        save_progress([])
        st.rerun()
else:
    st.caption("Your performance summary appears after you submit questions.")
