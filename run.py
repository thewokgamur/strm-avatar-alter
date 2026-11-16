"""
Stream Avatar launcher - paste a YouTube video id (or full URL) and it starts.

    double-click run.bat            (Windows)
    python run.py                   (asks for the id)
    python run.py dQw4w9WgXcQ        (goes straight to that stream)
    python run.py dQw4w9WgXcQ --test (extra options are passed to avatar.py)

Change FLAGS below to add or remove default options (see: python avatar.py --help).
"""
import os, re, subprocess, sys

ROOT = os.path.dirname(os.path.abspath(__file__))
SCRIPT = "avatar.py"                        # or "avatar1.py"
FLAGS = ["--transparent"]                   # [] = solid green background for chroma key
LAST = os.path.join(ROOT, "last_video.txt")
ID = re.compile(r"(?:v=|youtu\.be/|live/|shorts/|embed/)([\w-]{11})")


def parse(text):
    """'https://youtu.be/dQw4w9WgXcQ?t=3' / 'dQw4w9WgXcQ' -> 'dQw4w9WgXcQ'."""
    m = ID.search(text) or re.fullmatch(r"\s*([\w-]{11})\s*", text)
    return m.group(1) if m else None


def ask():
    args = sys.argv[1:]
    extra = []
    if args and not args[0].startswith("-"):
        return args[0], args[1:]          # given on the command line, no prompt
    last = ""
    try:
        with open(LAST, encoding="utf-8") as f:
            last = f.read().strip()
    except OSError:
        pass
    prompt = f"YouTube video id or URL{f' [{last}]' if last else ''}: "
    try:
        answer = input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        return "", extra
    return answer or last, extra


def main():
    raw, extra = ask()
    if not raw:
        print("No video id given.")
        return 2
    vid = parse(raw)
    if not vid:
        print(f"{raw!r} is not a YouTube video id (11 characters) or URL.")
        print("Example: https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        return 2
    try:
        with open(LAST, "w", encoding="utf-8") as f:
            f.write(vid)
    except OSError:
        pass
    cmd = [sys.executable, os.path.join(ROOT, SCRIPT), vid] + FLAGS + extra
    print("Starting:", " ".join(cmd))
    print("Keys: M = test message, J/R/D = jump/run/die, B = bomb, Esc = quit\n")
    try:
        return subprocess.call(cmd, cwd=ROOT)
    except FileNotFoundError:
        print("Could not start Python:", sys.executable)
        return 1


if __name__ == "__main__":
    sys.exit(main())
