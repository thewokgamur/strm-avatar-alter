"""
Stream Avatar - pure Python (pygame) version. No HTML, no browser, no web server.

Every YouTube live-chat chatter gets their own avatar with a name tag and
speech bubble. Chat commands: !jump  !run  !die  !skin <name>  !bomb <youtube name>
(any other message = small hop).

Usage:   python avatar.py VIDEO_ID_OR_URL [options]
Test:    python avatar.py --test          (fake chatters, no YouTube needed)

Options: --width 640 --height 400 --scale 2 --max 40 --expire 600 --fps 60
         --keep 10 --bg 00ff00 --nowander --left --char sunny --transparent

Sprites: sprites/<character>/<Anim>_<n>.png   (Idle Run Jump Fall Land Die)
         each chatter picks a random character folder; a missing animation
         reuses the closest one that exists in that folder.
Skins:   !skin <name> is saved per chatter in userskin.json (next to this file);
         the random skin a new chatter gets is saved too, on their first message.
OBS:     Sources -> Window Capture -> "Stream Avatar", then add a Chroma Key
         filter (green, default --bg 00ff00).  Or use Game Capture.
         Run with --transparent for a real transparent window instead: then in
         OBS use Capture Method "Windows 10 (1903 and up)" + "Allow Transparency".
Keys:    M = fake message, J / R / D = jump / run / die, B = bomb, Esc = quit
"""
import argparse, json, os, random, re, sys, threading, time, colorsys
from collections import defaultdict

import pygame

ROOT = os.path.dirname(os.path.abspath(__file__))
SKIN_FILE = os.path.join(ROOT, "userskin.json")
TAG_FONT = os.path.join(ROOT, "fonts", "RussoOne-Regular.ttf")   # name-tag font (bundled)
TAG_SIZE = 20                                       # name-tag pixel size
GRAVITY, JUMP_V, RUN_SPEED = 1800, -620, 130
RUN_TIME, DIE_TIME, BUBBLE_TIME = 4, 4, 5
IDLE_FT, RUN_FT = 0.16, 0.12
WALK_SPEED = 240                                   # !bomb approach speed
BOMB_FUSE, BLAST_TIME = 1.6, .35                   # fuse, explosion flash duration
BOMB_RANGE, BOMB_UP_RANGE = 170, 46                # blast radius, "too close" radius
BOMB_PUSH, BOMB_UP = 420, 780                      # sideways shove, upward launch
MAX_BOMBS = 10

# ------------------------------------------------------------------ chat feed
events, ev_lock = [], threading.Lock()


def push(typ, name, message, cid):
    with ev_lock:
        events.append({"type": typ, "name": name, "message": message, "cid": cid})


def clean_message(c):
    """YouTube emoji shortcodes -> real emoji; custom channel emoji are dropped."""
    parts = []
    for p in getattr(c, "messageEx", None) or [c.message or ""]:
        if isinstance(p, str):
            parts.append(p)
        elif p.get("id") and not p["id"].isascii():
            parts.append(p["id"])
    return " ".join("".join(parts).split())


def chat_worker(video_id, keep):
    import pytchat
    while True:
        try:
            chat = pytchat.create(video_id=video_id, interruptable=False)
            print("Connected to chat:", video_id)
            first = True
            while chat.is_alive():
                items = chat.get().sync_items()
                if first:
                    first = False
                    if keep > 0:
                        items = sorted(items, key=lambda c: c.timestamp)[-keep:]
                for c in items:
                    push(c.type, c.author.name, clean_message(c),
                         getattr(c.author, "channelId", "") or c.author.name)
                time.sleep(0.5)
            print("Chat ended or stream offline. Retrying in 10s...")
        except Exception as e:
            print("Chat error:", repr(e), "- retrying in 10s...")
        time.sleep(10)


# ------------------------------------------------------------------ sprites
ANIMS = ("Idle", "Run", "Jump", "Fall", "Land", "Die")
# If a character folder has no <Anim>_N.png, borrow the first of these instead.
FALLBACK = {
    "Idle": ("Run", "Land", "Jump", "Fall", "Die"),
    "Run": ("Idle", "Land", "Jump", "Fall"),
    "Jump": ("Idle", "Run", "Land"),
    "Land": ("Idle", "Jump", "Run"),
    "Die": ("Idle", "Land", "Run"),
}


def _anim(name):
    """'idle' / 'IDLE' / 'Idle' -> 'Idle'."""
    return name[:1].upper() + name[1:].lower()


def _frames(paths, scale):
    src = [pygame.image.load(path).convert_alpha() for _, path in sorted(paths)]
    gaps = [s.get_height() - s.get_bounding_rect().bottom for s in src]   # source pixels
    surf = [pygame.transform.scale(s, (s.get_width() * scale, s.get_height() * scale)) for s in src]
    if max(gaps) - min(gaps) <= 4:
        # frames only differ by a few pixels of pose -> keep every foot on the ground
        return [(s, g * scale) for s, g in zip(surf, gaps)]
    # a strip that moves the whole character inside a fixed canvas (hover / float):
    # keep the drawn positions, only the lowest frame touches the ground
    pad = min(gaps) * scale
    return [(s, pad) for s in surf]


def resolve_anims(anim):
    """Fill in the states a character folder is missing (e.g. star/ has only Run)."""
    if not anim:
        return anim
    out = dict(anim)
    out.setdefault("Idle", next(iter(out.values())))
    for k in ANIMS:
        if k in out or k == "Fall":               # no Fall -> frame() holds the jump pose
            continue
        out[k] = next((out[a] for a in FALLBACK[k] if a in out), out["Idle"])
    return out


def load_sprites(scale):
    """sprites/<character>/<Anim>_<n>.png -> {character: {anim: [(surface, pad)]}}
    Loose .png files directly in sprites/ are treated as one character called 'default'."""
    d = os.path.join(ROOT, "sprites")
    found = defaultdict(lambda: defaultdict(list))
    for entry in sorted(os.listdir(d)):
        p = os.path.join(d, entry)
        if os.path.isdir(p):
            if entry.lower() == "bomb":          # sprites/bomb is the bomb prop
                continue
            for fn in sorted(os.listdir(p)):
                m = re.fullmatch(r"([A-Za-z]+)_(\d+)\.png", fn, re.IGNORECASE)
                if m:
                    found[entry][_anim(m.group(1))].append((int(m.group(2)), os.path.join(p, fn)))
        elif entry.lower().endswith(".png"):
            m = re.fullmatch(r"([A-Za-z]+)_(\d+)\.png", entry, re.IGNORECASE)
            if m:
                found["default"][_anim(m.group(1))].append((int(m.group(2)), p))
    out = {}
    for name, anim in found.items():
        if anim:
            out[name] = resolve_anims({k: _frames(v, scale) for k, v in anim.items()})
    if not out:
        sys.exit(f"No sprites found in {d} (expected <character>/<Anim>_N.png)")
    return out


def load_bomb(scale):
    """sprites/bomb/*.png -> blob frames; empty list if the folder is missing."""
    d = os.path.join(ROOT, "sprites", "bomb")
    num = lambda fn: int(re.search(r"(\d+)", fn).group(1)) if re.search(r"(\d+)", fn) else 0
    try:
        fn = [f for f in os.listdir(d) if f.lower().endswith(".png")]
    except OSError:
        return []
    return _frames([(num(f), os.path.join(d, f)) for f in sorted(fn, key=num)], scale) if fn else []


def load_skins():
    """userskin.json: {"<chatter id>": "<character>"} - survives restarts."""
    try:
        with open(SKIN_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return {str(k): v for k, v in data.items() if isinstance(v, str)} if isinstance(data, dict) else {}


def save_skins(skins):
    try:
        tmp = SKIN_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(skins, f, ensure_ascii=False, indent=1, sort_keys=True)
        os.replace(tmp, SKIN_FILE)                # atomic: never a half-written file
    except OSError as e:
        print("Could not save", SKIN_FILE + ":", e)


class SpriteCache:
    """(state-frame object, flip) -> surface, built lazily."""
    def __init__(self): self.c = {}
    def get(self, surf, flip):
        key = (id(surf), flip)
        s = self.c.get(key)
        if s is None:
            s = pygame.transform.flip(surf, True, False) if flip else surf
            self.c[key] = s
        return s


class Bomb:
    """Dropped on the floor: pulses for BOMB_FUSE seconds, then goes off."""
    def __init__(self, x, y, frames=()):
        self.x, self.y, self.t, self.done = x, y, 0.0, False
        self.frames = frames

    def update(self, dt, app):
        self.t += dt
        if not self.done and self.t >= BOMB_FUSE:
            self.done = True
            app.blast(self.x)

    def alive(self): return self.t < BOMB_FUSE + BLAST_TIME

    def draw(self, scr):
        if self.t >= BOMB_FUSE: return
        if self.frames:
            surf, pad = self.frames[int(self.t * 10) % len(self.frames)]
            scr.blit(surf, (round(self.x - surf.get_width() / 2),
                            round(self.y - surf.get_height() + pad)))
            return
        cx, cy = round(self.x), round(self.y - 10)
        pygame.draw.circle(scr, (18, 18, 22), (cx, cy), 10)
        pygame.draw.circle(scr, (78, 78, 90), (cx - 3, cy - 3), 3)
        pygame.draw.line(scr, (120, 90, 40), (cx, cy - 9), (cx + 5, cy - 16), 2)
        spark = (255, 220, 80) if int(self.t * 12) % 2 else (255, 120, 50)
        pygame.draw.circle(scr, spark, (cx + 6, cy - 17), 3)

    def draw_blast(self, scr):
        if not self.done: return
        k = min(1.0, (self.t - BOMB_FUSE) / BLAST_TIME)
        r = int(20 + (BOMB_RANGE + 30) * k)
        s = pygame.Surface((r * 2, r * 2), pygame.SRCALPHA)
        a = int(200 * (1 - k))
        pygame.draw.circle(s, (255, 196, 60, a), (r, r), r)
        pygame.draw.circle(s, (255, 255, 235, min(255, a + 55)), (r, r), max(3, int(r * .45)))
        scr.blit(s, (round(self.x) - r, round(self.y) - 18 - r))


# ------------------------------------------------------------------ text helpers
TEXT_FONT = "segoeuiemoji,applecoloremoji,arial,liberationsans,dejavusans,freesans"
TAG_FONT_NAMES = "russoone,russooner,russo_one," + TEXT_FONT


def _find(names):
    for n in names.split(","):
        p = pygame.font.match_font(n)
        if p: return p
    return None


class Fonts:
    """Normal text font + (optional) colour-emoji font, mixed per character.
    Bitmap emoji fonts (NotoColorEmoji) ignore the requested size, so their glyphs
    are rendered at native size and scaled down to the text height."""
    def __init__(self, size, path=None, names=TEXT_FONT):
        if path and not os.path.exists(path): path = None
        path = path or _find(names)
        self.text = pygame.font.Font(path, size) if path else pygame.font.Font(None, size + 4)
        self.h = self.text.get_linesize()
        self.emoji, self.cache = None, {}
        ep = _find("segoeuiemoji,applecoloremoji,notocoloremoji,symbola")
        if ep:
            try: self.emoji = pygame.font.Font(ep, 109 if "oto" in ep else size)
            except Exception: pass

    @staticmethod
    def is_emoji(ch):
        o = ord(ch)
        return o >= 0x1F000 or 0x2190 <= o <= 0x2BFF or o in (0x200D, 0xFE0F)

    def glyph(self, ch, color):
        key = (ch, color)
        g = self.cache.get(key)
        if g is None:
            if ord(ch) in (0x200D, 0xFE0F):                       # joiners: skip
                g = pygame.Surface((0, self.h), pygame.SRCALPHA)
            elif self.is_emoji(ch) and self.emoji:
                try:
                    raw = self.emoji.render(ch, True, color)
                    k = self.h / max(1, raw.get_height())
                    g = pygame.transform.smoothscale(raw, (max(1, int(raw.get_width() * k)), self.h)) if k < 1 else raw
                except Exception:
                    g = pygame.Surface((self.h // 2, self.h), pygame.SRCALPHA)
            else:
                try: g = self.text.render(ch, True, color)
                except Exception: g = pygame.Surface((self.h // 2, self.h), pygame.SRCALPHA)
            self.cache[key] = g
        return g

    def width(self, t): return sum(self.glyph(c, (0, 0, 0)).get_width() for c in t)

    def render(self, t, color):
        gl = [self.glyph(c, color) for c in t]
        s = pygame.Surface((max(1, sum(g.get_width() for g in gl)), self.h), pygame.SRCALPHA)
        x = 0
        for g in gl: s.blit(g, (x, 0)); x += g.get_width()
        return s


def render_tag(fonts, name, hue):
    col = tuple(int(c * 255) for c in colorsys.hls_to_rgb(hue / 360, .85, .9))
    fg, sh = fonts.render(name, col), fonts.render(name, (0, 0, 0))
    s = pygame.Surface((fg.get_width() + 4, fg.get_height() + 4), pygame.SRCALPHA)
    for dx, dy in ((0,2),(4,2),(2,0),(2,4),(0,0),(4,4),(0,4),(4,0)):
        s.blit(sh, (dx, dy))
    s.blit(fg, (2, 2))
    return s


def render_bubble(fonts, text, max_w=190):
    lines, cur = [], ""
    fits = lambda t: fonts.width(t) <= max_w
    for word in text.split(" "):
        t = f"{cur} {word}" if cur else word
        if fits(t): cur = t; continue
        if cur: lines.append(cur); cur = ""
        if fits(word): cur = word; continue
        for ch in word:
            if fits(cur + ch): cur += ch
            else: lines.append(cur); cur = ch
    if cur: lines.append(cur)
    if len(lines) > 4: lines = lines[:4]; lines[3] = lines[3][:-1] + "…"
    lh = fonts.h
    w = max(fonts.width(l) for l in lines) + 16
    h = len(lines) * lh + 10
    s = pygame.Surface((w + 4, h + 12), pygame.SRCALPHA)
    pygame.draw.rect(s, (255, 255, 255), (2, 2, w, h), border_radius=8)
    pygame.draw.rect(s, (40, 40, 40), (2, 2, w, h), 2, border_radius=8)
    pygame.draw.polygon(s, (255, 255, 255), [(w/2-6, h+1), (w/2+6, h+1), (w/2+2, h+9)])
    for i, l in enumerate(lines):
        s.blit(fonts.render(l, (30, 30, 30)), (10, 7 + i * lh))
    return s


# ------------------------------------------------------------------ avatar
class Avatar:
    def __init__(self, app, aid, name):
        self.app, self.id, self.name = app, aid, name[:16]
        self.full = name.strip()                  # full YouTube display name (tag is truncated)
        h = 0
        for c in aid: h = (h * 31 + ord(c)) & 0xFFFFFFFF
        self.hue = ((h * 2654435761) >> 7) % 360
        self.x = random.uniform(50, max(60, app.W - 50))
        self.y, self.vy = 0.0, 0.0
        self.vx, self.seek, self.seek_x = 0.0, None, 0.0
        self.right = random.random() < .5
        self.state, self.t, self.run_time = "air", 0.0, RUN_TIME
        self.bubble, self.bubble_t, self.top = None, 0.0, 0
        self.active = time.time()
        self.next_wander = random.uniform(6, 16)
        self.char = app.skins.get(aid) or random.choice(app.characters)
        self.tag = render_tag(app.tag_fonts, self.name, self.hue)

    def set(self, s): self.state, self.t = s, 0.0
    def jump(self, big=False):
        if self.state in ("idle", "run", "land", "seek"):
            self.seek = None
            self.vy = JUMP_V * (1.35 if big else 1); self.set("air")
    def run(self, secs=RUN_TIME):
        if self.state in ("idle", "land", "seek"):
            self.seek = None
            self.right = random.random() < .5; self.run_time = secs; self.set("run")
    def die(self):
        if self.state != "die": self.seek = None; self.set("die")
    def bomb(self, arg):
        """!bomb <youtube name> - walk over to that chatter and drop a bomb at their feet."""
        if self.state == "die": return False
        tgt = None
        if arg:
            q = arg.strip().lower().lstrip("@")
            others = [v for v in self.app.avatars.values() if v is not self]
            same = lambda v, n: n.lstrip("@") == q                # "@Alice" or "Alice"
            tgt = (next((v for v in others if same(v, v.full.lower())), None)
                   or next((v for v in others if same(v, v.name.lower())), None)
                   or next((v for v in others if v.id.lower() == q), None))
            if tgt is None:                                       # partial name, e.g. "ali"
                tgt = next((v for v in others if v.full.lower().lstrip("@").startswith(q)
                            or v.name.lower().startswith(q)), None)
            if tgt is None:
                self.say(f"who is {arg}?"); return False
        self.seek, self.seek_x, self.vx = tgt, tgt.x if tgt else self.x, 0.0
        self.set("seek")
        return True
    def skin(self, arg):
        """!skin <character> - swap this avatar's sprite folder (case-insensitive)."""
        c = self.app.skin_names.get(arg.lower()) if arg else None
        if c:
            self.char = c
            self.app.set_skin(self.id, c)
            return True
        opts = ", ".join(sorted(self.app.sprites))
        self.say((f"no skin {arg}. " if arg else "") + "skins: " + opts)
        return False
    def say(self, text):
        self.bubble = render_bubble(self.app.fonts, text); self.bubble_t = BUBBLE_TIME

    def update(self, dt):
        a, g = self.app, self.app.H
        self.t += dt; self.bubble_t = max(0, self.bubble_t - dt)
        if self.bubble_t <= 0: self.bubble = None
        if self.vx:                                   # bomb knockback
            self.x += self.vx * dt
            self.vx *= max(0.0, 1 - 3 * dt)
            if abs(self.vx) < 10: self.vx = 0.0
            if self.x < 24: self.x, self.vx = 24, -self.vx * .4
            elif self.x > a.W - 24: self.x, self.vx = a.W - 24, -self.vx * .4
        if self.state == "air":
            self.vy += GRAVITY * dt; self.y += self.vy * dt
            if self.y >= g: self.y, self.vy = g, 0; self.set("land")
        elif self.state == "seek":
            self.y = g
            if self.seek is not None and self.seek in a.avatars.values():
                self.seek_x = self.seek.x            # keep following a moving target
            d = self.seek_x - self.x
            if abs(d) <= 60:
                self.seek = None; self.set("idle")
                a.drop_bomb(self, (self.x + self.seek_x) / 2)   # between the two of them
            else:
                self.x += min(WALK_SPEED * dt, abs(d) - 60) * (1 if d > 0 else -1)
                self.right = d > 0
        elif self.state == "land":
            self.y = g
            if self.t > .18: self.set("idle")
        elif self.state == "run":
            self.y = g; self.x += RUN_SPEED * dt * (1 if self.right else -1)
            if self.x > a.W - 30: self.right = False
            if self.x < 30: self.right = True
            if self.t > self.run_time: self.set("idle")
        elif self.state == "die":
            self.y = g
            if self.t > DIE_TIME: self.set("idle")
        else:
            self.y = g
            self.next_wander -= dt
            if a.wander and self.next_wander <= 0:
                self.next_wander = random.uniform(8, 20); self.run(random.uniform(1, 2.5))

    def frame(self):
        A, pick = self.app.sprites[self.char], lambda L, i: L[i % len(L)]
        s = self.state
        if s == "idle": return pick(A["Idle"], int(self.t / IDLE_FT))
        if s == "air":
            J, F = A["Jump"], A.get("Fall") or []
            if self.vy < -150 or not F:
                return J[0] if self.vy < -400 else J[min(1, len(J) - 1)]
            if self.vy < 150: return J[-1]
            return pick(F, int(self.t / .1))
        if s == "land": return A["Land"][0]
        if s in ("run", "seek"): return pick(A["Run"], int(self.t / RUN_FT))
        return A["Die"][0]

    def draw_sprite(self, scr):
        surf, pad = self.frame()
        flip = self.right == self.app.face_left
        img = self.app.cache.get(surf, flip)
        self.top = self.y - img.get_height() + pad
        scr.blit(img, (round(self.x - img.get_width() / 2), round(self.top)))

    def draw_tag(self, scr):
        scr.blit(self.tag, (round(self.x - self.tag.get_width() / 2), round(self.top - 8 - self.tag.get_height())))

    def draw_bubble(self, scr):
        if not self.bubble or self.state == "die": return
        b, W = self.bubble, self.app.W
        bx = max(4, min(W - b.get_width() - 4, self.x - b.get_width() / 2))
        by = max(4, self.top - 18 - b.get_height())
        scr.blit(b, (round(bx), round(by)))


def make_transparent(key):
    """Windows: colour-key the window so OBS captures a real transparent background.
    OBS: Window Capture -> Capture Method "Windows 10 (1903 and up)" -> Allow Transparency."""
    if sys.platform != "win32":
        print("--transparent: only supported on Windows, keeping", key, "background")
        return False
    try:
        import ctypes
        hwnd = pygame.display.get_wm_info().get("window")
        if not hwnd:
            raise RuntimeError("no window handle")
        u = ctypes.windll.user32
        GWL_EXSTYLE, WS_EX_LAYERED, LWA_COLORKEY = -20, 0x00080000, 0x1
        u.SetWindowLongW(hwnd, GWL_EXSTYLE, u.GetWindowLongW(hwnd, GWL_EXSTYLE) | WS_EX_LAYERED)
        ref = key[0] | key[1] << 8 | key[2] << 16          # COLORREF is 0x00BBGGRR
        u.SetLayeredWindowAttributes(hwnd, ref, 0, LWA_COLORKEY)
        print("--transparent: background", key, "is see-through (OBS: Window Capture, Allow Transparency)")
        return True
    except Exception as e:
        print("--transparent failed:", e, "- keeping the solid background")
        return False


# ------------------------------------------------------------------ app
class App:
    def __init__(self, a):
        pygame.init()
        pygame.display.set_caption("Stream Avatar")
        self.W, self.H = a.width, a.height
        self.screen = pygame.display.set_mode((self.W, self.H), pygame.RESIZABLE)
        self.bg = tuple(int(a.bg[i:i+2], 16) for i in (0, 2, 4))
        if getattr(a, "transparent", False):
            make_transparent(self.bg)
        self.wander, self.face_left = not a.nowander, a.left
        self.max, self.expire, self.fps = a.max, a.expire, a.fps
        self.sprites = load_sprites(a.scale)
        self.bomb_frames = load_bomb(a.scale)
        forced = getattr(a, "char", None)
        self.forced = forced if forced in self.sprites else None
        self.characters = [self.forced] if self.forced else sorted(self.sprites)
        if forced and not self.forced:
            print(f"Unknown character {forced!r} - using: {', '.join(self.characters)}")
        self.skin_names = {c.lower(): c for c in self.sprites}
        # saved skins win over the random pick; a missing folder is ignored
        self.skins = {} if self.forced else {k: v for k, v in load_skins().items() if v in self.sprites}
        if self.skins:
            print(f"Loaded {len(self.skins)} saved skin(s) from userskin.json")
        print(f"Sprites: {', '.join(self.characters)}")
        self.cache = SpriteCache()
        self.fonts, self.tag_fonts = Fonts(14), Fonts(TAG_SIZE, TAG_FONT, TAG_FONT_NAMES)
        self.avatars, self.fake = {}, 0
        self.bombs = []

    def set_skin(self, aid, char):
        """Remember a chatter's skin in userskin.json."""
        self.skins[aid] = char
        save_skins(self.skins)

    def drop_bomb(self, av, x=None):
        if sum(1 for b in self.bombs if not b.done) < MAX_BOMBS:
            self.bombs.append(Bomb(av.x if x is None else x, av.y, self.bomb_frames))

    def blast(self, x):
        """Shove everyone in range away from x; anyone really close goes flying up."""
        for av in list(self.avatars.values()):
            if av.state == "die": continue
            d = abs(av.x - x)
            if d > BOMB_RANGE: continue
            s = 1 if av.x >= x else -1
            av.vx += s * (BOMB_PUSH * (1 - d / BOMB_RANGE) + 60)
            av.vy = -BOMB_UP if d <= BOMB_UP_RANGE else min(av.vy, -BOMB_UP * .4)
            av.seek = None                       # being blown up cancels the bombing run
            av.set("air")

    def get_avatar(self, e):
        aid = e["cid"] or e["name"]
        av = self.avatars.get(aid)
        if not av:
            if len(self.avatars) >= self.max:
                del self.avatars[min(self.avatars.values(), key=lambda v: v.active).id]
            av = self.avatars[aid] = Avatar(self, aid, e["name"] or "???")
            if not self.forced and aid not in self.skins:
                self.set_skin(aid, av.char)       # first chat: remember the random skin
        av.active = time.time()
        return av

    def handle(self, e):
        av, low = self.get_avatar(e), (e["message"] or "").strip().lower()
        if low == "!jump": av.jump(True)
        elif low == "!run": av.run()
        elif low == "!die": av.die()
        elif low == "!skin" or low.startswith("!skin "): av.skin(low[5:].strip())
        elif low == "!bomb" or low.startswith("!bomb "): av.bomb(low[5:].strip())
        elif e["type"] in ("superChat", "superSticker", "newSponsor"): av.jump(True)
        else: av.jump(False)
        if e["message"] and not low.startswith("!"): av.say(e["message"])

    def test_msg(self):
        self.fake += 1
        self.handle({"type": "textMessage", "cid": f"t{self.fake % 8}", "name": f"Tester{self.fake % 8}",
                     "message": f"Hello from chat #{self.fake}"})

    def run(self):
        clock, quit_ = pygame.time.Clock(), False
        while not quit_:
            dt = min(.05, clock.tick(self.fps) / 1000)
            for ev in pygame.event.get():
                if ev.type == pygame.QUIT: quit_ = True
                elif ev.type == pygame.VIDEORESIZE: self.W, self.H = ev.w, ev.h
                elif ev.type == pygame.KEYDOWN:
                    lst = list(self.avatars.values()); any_ = random.choice(lst) if lst else None
                    if ev.key == pygame.K_ESCAPE: quit_ = True
                    elif ev.key == pygame.K_m: self.test_msg()
                    elif any_ and ev.key == pygame.K_j: any_.jump()
                    elif any_ and ev.key == pygame.K_r: any_.run()
                    elif any_ and ev.key == pygame.K_d: any_.die()
                    elif ev.key == pygame.K_b:
                        who = list(self.avatars.values())
                        if len(who) > 1:
                            me = random.choice(who)
                            me.bomb(random.choice([v.full for v in who if v is not me]))
            with ev_lock:
                batch, events[:] = list(events), []
            for e in batch: self.handle(e)
            now = time.time()
            for av in [v for v in self.avatars.values() if now - v.active > self.expire]:
                del self.avatars[av.id]
            lst = sorted(self.avatars.values(), key=lambda v: v.active)   # newest on top
            for av in lst: av.update(dt)
            for b in self.bombs: b.update(dt, self)
            self.bombs = [b for b in self.bombs if b.alive()]
            self.screen.fill(self.bg)
            for av in lst: av.draw_sprite(self.screen)
            for b in self.bombs: b.draw(self.screen)      # above the sprites, below the tags
            for av in lst: av.draw_tag(self.screen)
            for av in lst: av.draw_bubble(self.screen)
            for b in self.bombs: b.draw_blast(self.screen)
            pygame.display.flip()
        pygame.quit()


def main():
    p = argparse.ArgumentParser(description="Python-only YouTube chat stream avatars")
    p.add_argument("video", nargs="?", help="YouTube video ID or URL")
    p.add_argument("--test", action="store_true", help="fake chatters, no YouTube")
    p.add_argument("--width", type=int, default=640); p.add_argument("--height", type=int, default=400)
    p.add_argument("--scale", type=int, default=2); p.add_argument("--max", type=int, default=40)
    p.add_argument("--expire", type=int, default=600); p.add_argument("--fps", type=int, default=60)
    p.add_argument("--keep", type=int, default=10); p.add_argument("--bg", default="00ff00")
    p.add_argument("--nowander", action="store_true"); p.add_argument("--left", action="store_true")
    p.add_argument("--transparent", action="store_true", help="see-through --bg colour for OBS (Windows)")
    p.add_argument("--char", help="force one sprite character folder (default: random per avatar)")
    a = p.parse_args()
    if not a.video and not a.test: p.error("give a VIDEO_ID_OR_URL or use --test")
    if a.video:
        m = re.search(r"(?:v=|youtu\.be/|live/)([\w-]{11})", a.video)
        threading.Thread(target=chat_worker, args=(m.group(1) if m else a.video, a.keep), daemon=True).start()
    app = App(a)
    if a.test:
        for _ in range(5): app.test_msg()
    app.run()


if __name__ == "__main__":
    main()
