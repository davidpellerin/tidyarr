"""Still frames from inside a video, so a person can see what it is (a title card, the credits) when its name tells them nothing.

ffmpeg cannot say which frame is a title card, so this samples the video and ranks the samples by what title cards and
credits have in common: sharp, high-contrast edges (the sides of letters), staying put for a second or more, and, when
`tesseract` is installed, big words that can actually be read. Black frames, fades and flat colour are thrown away, no two
alike are kept, and the picks are spread over the opening (title cards), the end (credits) and the middle. The pictures are
untrusted data from the file: they are displayed, never interpreted.
"""
import functools
import math
import os
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from operator import mul, sub

WIDTH, HEIGHT = 256, 144   # the size a candidate is judged at: small, so judging is quick
OPENING_STEP, ENDING, MIDDLE = 1, 6, 4   # the opening is judged every this many seconds; this many moments are sampled from the end and the middle
SHARE = {"opening": 0.6, "middle": 0.25, "ending": 0.25}   # the most of the picks (as a share) that may come from one part
TOO_FLAT = 10             # a frame whose brightness varies less than this (standard deviation, 0-255) is black, white, flat colour or mid-fade
EDGE = 40                 # a step this big (of 255) between neighbouring pixels is an edge like a letter's side; grain and gradients stay below
CALM = 6                  # a frame whose picture changes this much (mean per pixel) one second later counts half as much as a still one
MOVING = 0.25             # but never less than this share: title sequences animate, and a frame in motion can still be the title
BLOCK = 16                # pictures are compared as grids of BLOCK x BLOCK pixel averages: the layout, not every moving detail
TOO_ALIKE = 16            # pictures closer than this (mean brightness difference per block) count as the same one
OCR_READS = {"opening": 16, "middle": 3, "ending": 4}   # how many of the best of each part are read for text
OCR_WIDTH, OCR_CONFIDENCE, OCR_WORDS, OCR_TIMEOUT = 960, 70, 10, 20
TITLE_SIZE = 0.08         # words this tall (share of the picture's height) are as big as a title; credits and captions are a fraction of that
WORD = re.compile(r"[A-Za-z][A-Za-z'-]{2,}")   # what counts as a read word: letters only, three or more (no stray marks from texture)
TIMEOUT = 20
SCAN_TIMEOUT = 90         # the one pass over the opening decodes a few minutes of video


def opening(duration):
    """(start, length) in seconds of the opening: a quarter of the video, at most 4 minutes. It is read in one pass, a view every second."""
    end = min(duration * 0.25, 240)
    start = min(3.0, duration * 0.02)
    return start, end - start


def moments(duration):
    """Seconds into the video, in order, to look at separately in the middle and at the end, where credits are."""
    times = [duration * (0.35 + 0.3 * i / max(MIDDLE - 1, 1)) for i in range(MIDDLE)]
    times += [duration - 5 - 100 * (ENDING - 1 - i) / max(ENDING - 1, 1) for i in range(ENDING)]
    return sorted({round(max(0.0, min(t, duration - 1)), 1) for t in times})


def part(seconds, duration):
    """Which part of the video a moment is in: 'opening', 'ending' or 'middle'."""
    if seconds <= min(duration * 0.25, 240) + 1:
        return "opening"
    return "ending" if seconds >= duration - 120 else "middle"


def _run(file, seconds, filters, extra, timeout, before, loglevel):
    command = ["ffmpeg", "-v", loglevel, *before, "-ss", str(seconds), "-i", str(file), "-an", "-sn", "-dn", "-vf", filters, *extra, "-"]
    try:
        proc = subprocess.run(command, capture_output=True, stdin=subprocess.DEVNULL, timeout=timeout)
    except FileNotFoundError:
        raise RuntimeError("ffmpeg is not installed") from None
    except subprocess.TimeoutExpired:
        return None
    return proc if proc.returncode == 0 and proc.stdout else None


def _ffmpeg(file, seconds, filters, extra, timeout=TIMEOUT, before=()):
    """The output of ffmpeg for the frame(s) at `seconds` (seeking before the input is fast: it jumps rather than decodes), or None.
    `before` are options for the decoder, which must come ahead of the input."""
    proc = _run(file, seconds, filters, extra, timeout, before, "error")
    return proc.stdout if proc else None


def _strong(a, b):
    """How many of the paired pixels differ by more than EDGE."""
    return sum(map(EDGE.__lt__, map(abs, map(sub, a, b))))


def _difference(a, b):
    """The mean brightness difference per pixel between two pictures."""
    return sum(map(abs, map(sub, a, b))) / len(a)


def judge(pixels, later=None):
    """A score for a WIDTH x HEIGHT greyscale picture, or None when it is not worth showing. `later` is the same view a second on.

    The score is how many pixels sit on a strong edge (percent), weighed down, but only so far, the more the picture
    changes by `later`: a title card holds still, and a cut, fade or action scene does not. Black, white and flat frames and fades are not scored."""
    n = len(pixels)
    mean = sum(pixels) / n
    if sum(map(mul, pixels, pixels)) / n - mean * mean < TOO_FLAT ** 2:  # not "dark": white credits on black have a low mean but plenty of spread
        return None
    across = sum(_strong(pixels[row:row + WIDTH - 1], pixels[row + 1:row + WIDTH]) for row in range(0, n, WIDTH))
    down = _strong(pixels, pixels[WIDTH:])
    change = _difference(pixels, later) if later else 0
    return 100 * (across + down) / n * (MOVING + (1 - MOVING) / (1 + change / CALM))


def read_text(file, seconds):
    """(words, size) for the frame at `seconds`: how many words tesseract read with confidence, and the height of the tallest
    as a share of the picture's. (0, 0) if tesseract is missing, slow or sees nothing."""
    if not shutil.which("tesseract"):
        return 0, 0.0
    image = _ffmpeg(file, seconds, f"scale={OCR_WIDTH}:-2,format=gray", ["-frames:v", "1", "-c:v", "pgm", "-f", "image2pipe"])
    if not image:
        return 0, 0.0
    try:
        proc = subprocess.run(["tesseract", "stdin", "stdout", "--psm", "11", "tsv"], input=image, capture_output=True, timeout=OCR_TIMEOUT,
                              env={**os.environ, "OMP_THREAD_LIMIT": "1"})  # one thread each: several frames are read at once
    except (OSError, subprocess.TimeoutExpired):
        return 0, 0.0
    rows = [line.split("\t") for line in proc.stdout.decode("utf-8", "replace").splitlines()[1:]]
    rows = [r for r in rows if len(r) == 12]
    page = next((float(r[9]) for r in rows if r[0] == "1"), 0)
    read = [float(r[9]) for r in rows if r[0] == "5" and float(r[10] or -1) >= OCR_CONFIDENCE and WORD.fullmatch(r[11].strip())]
    return len(read), (max(read) / page if read and page else 0.0)


def text_boost(words, size):
    """What a frame's score is multiplied by for its text. Big words matter most: a title card has a few huge ones, while
    credits, captions and promos have many small ones. The number of words adds a little."""
    return 1 + 3 * min(size / TITLE_SIZE, 1) + min(words, OCR_WORDS) / OCR_WORDS


@functools.lru_cache(maxsize=None)
def _layout(pixels):
    """The picture as the average brightness of each BLOCK x BLOCK square. Two shots of the same scene, or the same logo a few
    seconds apart, differ in a hundred small details but have nearly the same layout; different scenes do not."""
    n = len(pixels)
    return tuple(sum(sum(pixels[at:at + BLOCK]) for at in range(top + x, top + x + BLOCK * WIDTH, WIDTH)) / BLOCK ** 2
                 for top in range(0, n, BLOCK * WIDTH) for x in range(0, WIDTH, BLOCK))


def _alike(a, b):
    return _difference(_layout(a), _layout(b)) < TOO_ALIKE


def distinct(ranked, count):
    """The first `count` of `ranked` ([(score, seconds, pixels)]) that are not alike."""
    kept = []
    for r in ranked:
        if len(kept) < count and not any(_alike(r[2], k[2]) for k in kept):
            kept.append(r)
    return kept


def choose(ranked, duration, count):
    """The best `count` of [(score, seconds, pixels)] (best first) that are not alike, in time order. No part of the video
    may fill more than its SHARE of the picks while others have something to offer."""
    chosen = []
    for capped in (True, False):
        for _, seconds, pixels in ranked:
            if len(chosen) >= count:
                break
            where = part(seconds, duration)
            if any(s == seconds or _alike(pixels, p) for s, p in chosen):
                continue
            if capped and sum(part(s, duration) == where for s, _ in chosen) >= math.ceil(count * SHARE[where]):
                continue
            chosen.append((seconds, pixels))
    return sorted(chosen)


def pick(file, duration, count=8):
    """[{'time': seconds, 'jpeg': bytes}] for up to `count` good frames of `file`, in the order they occur.

    Raises RuntimeError if ffmpeg is missing or the duration is unknown. A file with no usable frame gives []."""
    if not duration or duration < 2:
        raise RuntimeError("can't pick frames: the length of the video is unknown")
    size = WIDTH * HEIGHT
    small = f"fps=1,scale={WIDTH}:{HEIGHT},format=gray"
    start, length = opening(duration)

    def look(seconds):
        """The view at `seconds` and the one a second on (fps=1 makes the two), or None when it can't be read."""
        data = _ffmpeg(file, seconds, small, ["-frames:v", "2", "-f", "rawvideo"])
        if not data or len(data) < size:
            return None
        return seconds, data[:size], data[size:2 * size] if len(data) >= 2 * size else None

    def scan():
        """A view about a second apart through the opening, from one pass: seeking to each moment would decode from the keyframe
        before it every time, several times the work. Reference frames are enough to judge, so the rest are skipped.

        Each view is a real frame, the first at least a second after the one before, and showinfo reports when it is. fps=1 would
        give views at whole seconds, but each could be a frame from up to half a second away, and a title card that shows for
        three seconds can be missed by that. Judged at its real time, the frame that is judged is the one that is shown."""
        proc = _run(file, start, f"select='isnan(prev_selected_t)+gte(t-prev_selected_t,1)',showinfo,scale={WIDTH}:{HEIGHT},format=gray",
                    ["-t", str(length), "-fps_mode", "passthrough", "-f", "rawvideo"], SCAN_TIMEOUT, ["-skip_frame", "noref"], "info")
        if not proc:
            return []
        times = [float(t) for t in re.findall(r"pts_time:(-?\d+(?:\.\d+)?)", proc.stderr.decode("utf-8", "replace"))]
        views = [proc.stdout[i:i + size] for i in range(0, len(proc.stdout) - size + 1, size)]
        # The last view has no later one to show whether it holds still, so it is only ever the "later" of the one before.
        return [(round(start + times[i], 2), views[i], views[i + 1]) for i in range(0, min(len(views), len(times)) - 1, OPENING_STEP)]

    with ThreadPoolExecutor(max_workers=4) as pool:
        scanning = pool.submit(scan)   # alongside the looks at the middle and the end
        looked = [r for r in pool.map(look, moments(duration)) if r]
        scored = [(judge(pixels, later), seconds, pixels) for seconds, pixels, later in scanning.result() + looked]
        ranked = sorted((r for r in scored if r[0] is not None), key=lambda r: r[0], reverse=True)
        # Text that can be read is the surest sign of a title card, so the best few of each part are read, and big text counts most.
        read = [r for where, n in OCR_READS.items() for r in distinct([r for r in ranked if part(r[1], duration) == where], n)]
        text = dict(zip((r[1] for r in read), pool.map(lambda r: read_text(file, r[1]), read)))
        ranked = sorted(((score * text_boost(*text.get(seconds, (0, 0.0))), seconds, pixels) for score, seconds, pixels in ranked),
                        key=lambda r: r[0], reverse=True)
        # The pictures are kept at the video's own size. Non-square pixels (old DVD rips) are widened so the shape is the one a player shows.
        whole = "scale=trunc(iw*sar/2)*2:ih,setsar=1,format=yuvj420p"
        chosen = choose(ranked, duration, count)
        jpegs = list(pool.map(lambda c: _ffmpeg(file, c[0], whole, ["-frames:v", "1", "-c:v", "mjpeg", "-q:v", "4", "-f", "image2pipe"]), chosen))
    return [{"time": seconds, "jpeg": jpeg} for (seconds, _), jpeg in zip(chosen, jpegs) if jpeg]
