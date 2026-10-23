"""Offline tests for tidyarr.frames: ffmpeg and tesseract are replaced, so neither is ever run."""
import subprocess

import pytest

from tidyarr import frames

W, H = frames.WIDTH, frames.HEIGHT
SIZE = W * H
BLACK = bytes(SIZE)
FLAT = bytes([128]) * SIZE
def stripes(shift=0, rows=H):
    """Edges across the top `rows` rows, black below: a busy picture. Each shift is a picture that is not alike another's."""
    return bytes((255 if (i // W + i + shift) % 8 < 4 else 20) if i // W < rows else 0 for i in range(SIZE))


def tiles(seed, rows=H):
    """Large blocks, bright or dark by a pattern the seed decides, in the top `rows` rows and black below: pictures that differ in layout."""
    return bytes((255 if (x // 16 * 7 + y // 16 * 3 + seed * 5) % 11 < 5 else 20) if y < rows else 0 for y in range(H) for x in range(W))


STRIPES = stripes()
SOFT = bytes(60 + (i % W) // 2 for i in range(SIZE))                           # a gentle gradient: contrast but no edges
CREDITS = bytes(255 if 20 <= i // W < 120 and (i // W) % 10 < 4 and (i % W) % 6 < 3 and 40 < i % W < 210 else 0 for i in range(SIZE))  # lines of white text on black
OTHER = stripes(4)                                                              # STRIPES with the stripes the other way round


@pytest.fixture(autouse=True)
def no_tesseract(monkeypatch):
    monkeypatch.setattr(frames.shutil, "which", lambda name: None)


def fake_ffmpeg(monkeypatch, views, jpeg=b"\xff\xd8jpeg", offset=0):
    """`views` maps a second to the greyscale picture ffmpeg would give there, or to (picture, the picture a second on) or, near the
    end of the video, to (picture,). In the scan of the opening a picture holds still (it is also the next second's) unless it says
    otherwise, and a second with no view is black. The scan reports each view `offset` seconds later than its second (real
    frames are not on whole seconds). A look at any other second with no view fails to read. Returns the commands run."""
    commands = []

    def run(command, **kw):
        commands.append(command)
        if command[0] != "ffmpeg":
            raise AssertionError(command)
        seconds = float(command[command.index("-ss") + 1])
        if "rawvideo" in command and "-t" in command:   # the one pass over the opening
            at = {}
            for second, view in views.items():
                pair = view if isinstance(view, tuple) else (view, view)
                at[second], at[round(second + 1, 1)] = pair[0], pair[-1]
            length = int(float(command[command.index("-t") + 1]))
            data = b"".join(at.get(round(seconds + i, 1), BLACK) for i in range(length))
            info = "".join(f"[Parsed_showinfo_1 @ 0x5] n:{i:4d} pts:{i * 1000} pts_time:{i + offset}  duration: 1\n" for i in range(length))
            return subprocess.CompletedProcess(command, 0, data, info.encode())
        if "rawvideo" in command:
            view = views.get(seconds)
            data = b"".join(view) if isinstance(view, tuple) else (view * 2 if view else b"")
            return subprocess.CompletedProcess(command, 0 if data else 1, data, b"")
        return subprocess.CompletedProcess(command, 0, jpeg, b"")
    monkeypatch.setattr(frames.subprocess, "run", run)
    return commands


def test_the_opening_is_a_quarter_of_the_video_at_most_four_minutes_after_the_first_moments():
    assert frames.opening(2640) == (3.0, 237.0)
    assert frames.opening(600) == (3.0, 147.0)
    assert frames.opening(100) == (2.0, 23.0)   # the start never passes a fiftieth of a short video


def test_moments_are_the_middle_and_the_end_in_order_and_inside_the_video():
    times = frames.moments(2640)
    assert times == sorted(times) and len(set(times)) == len(times) == frames.MIDDLE + frames.ENDING
    assert all(2640 * 0.35 <= t <= 2639 for t in times) and times[-1] > 2500   # the credits are sampled too


def test_moments_of_a_short_clip_stay_inside_it():
    assert all(0 <= t <= 9 for t in frames.moments(10))


def test_moments_are_told_apart_by_the_part_of_the_video_they_are_in():
    assert [frames.part(t, 2640) for t in (3, 240, 245, 1300, 2519, 2520, 2600)] == ["opening", "opening", "middle", "middle", "middle", "ending", "ending"]
    assert (frames.part(20, 100), frames.part(50, 100)) == ("opening", "ending")   # in a short video the parts overlap: the opening wins


def test_black_white_and_flat_frames_and_fades_are_not_scored():
    assert frames.judge(BLACK) is None and frames.judge(FLAT) is None and frames.judge(bytes([250]) * SIZE) is None


def test_white_text_on_black_is_scored_so_credits_are_not_thrown_out_as_dark():
    assert frames.judge(CREDITS) > 0


def test_sharp_edges_score_higher_than_a_soft_picture():
    assert frames.judge(STRIPES) > frames.judge(SOFT)


def test_a_picture_that_holds_still_scores_higher_than_the_same_one_that_changes():
    still, moving = frames.judge(STRIPES, STRIPES), frames.judge(STRIPES, OTHER)
    assert still == frames.judge(STRIPES) and moving < still / 2   # a cut or a scene in motion: not a title card


def test_a_picture_in_motion_keeps_a_share_of_its_score_because_title_sequences_animate():
    still, moving = frames.judge(STRIPES, STRIPES), frames.judge(STRIPES, OTHER)
    assert moving >= still * frames.MOVING


def test_pictures_are_alike_when_their_layout_is_even_if_the_details_are_not():
    nearly = tiles(0)[:5000] + bytes(100) + tiles(0)[5100:]            # a few pixels different
    assert frames._alike(tiles(0), nearly) and frames._alike(STRIPES, OTHER)   # stripes finer than a block average to the same grey
    assert not frames._alike(tiles(0), tiles(1)) and not frames._alike(tiles(0), tiles(2, H // 2))


def test_distinct_takes_the_best_pictures_that_are_not_alike():
    ranked = [(9, 1.0, tiles(0)), (8, 2.0, tiles(0)), (7, 3.0, tiles(1)), (6, 4.0, tiles(2))]
    assert [r[1] for r in frames.distinct(ranked, 2)] == [1.0, 3.0] and [r[1] for r in frames.distinct(ranked, 9)] == [1.0, 3.0, 4.0]


def test_choose_keeps_the_best_and_drops_pictures_alike_in_time_order():
    ranked = [(9, 50.0, tiles(0)), (8, 20.0, tiles(0)), (7, 30.0, tiles(1))]
    assert [s for s, _ in frames.choose(ranked, 600, 8)] == [30.0, 50.0]   # the second tiles(0) is the same picture


def test_choose_does_not_let_the_opening_fill_every_pick_while_the_end_has_something():
    opening = [(10 - i, float(10 + i), tiles(i)) for i in range(8)]
    credits = (1, 590.0, tiles(8, 100))
    seconds = [s for s, _ in frames.choose(opening + [credits], 600, 8)]
    assert 590.0 in seconds and sum(s < 100 for s in seconds) == 7   # opening is capped at 5 first, then fills the rest


def test_choose_fills_up_from_any_part_when_the_others_have_nothing():
    ranked = [(10 - i, float(10 + i), tiles(i)) for i in range(8)]
    assert len(frames.choose(ranked, 600, 8)) == 8


def test_pick_skips_black_frames_and_prefers_a_still_title_card_to_a_busy_moving_scene(monkeypatch):
    fake_ffmpeg(monkeypatch, {3.0: BLACK, 5.0: (tiles(0, H // 2), tiles(1, H // 2)), 7.0: CREDITS})
    picked = frames.pick("x.mkv", 600, count=1)
    assert [f["time"] for f in picked] == [7.0]
    assert picked[0]["jpeg"] == b"\xff\xd8jpeg"


def test_pick_returns_frames_in_the_order_they_occur(monkeypatch):
    fake_ffmpeg(monkeypatch, {9.0: STRIPES, 5.0: CREDITS})
    assert [f["time"] for f in frames.pick("x.mkv", 600)] == [5.0, 9.0]


def test_pick_shows_one_of_several_frames_that_are_the_same_picture(monkeypatch):
    fake_ffmpeg(monkeypatch, {t: STRIPES for t in (3.0, 5.0, 7.0, 9.0, 11.0)})
    assert len(frames.pick("x.mkv", 600)) == 1


def test_the_opening_is_read_in_one_pass_and_the_rest_one_look_at_a_time(monkeypatch):
    commands = fake_ffmpeg(monkeypatch, {5.0: STRIPES})
    frames.pick("x.mkv", 600)
    raw = [c for c in commands if "rawvideo" in c]
    scan = [c for c in raw if "-t" in c]
    assert len(scan) == 1 and len(raw) == 1 + len(frames.moments(600))
    assert scan[0].index("-skip_frame") < scan[0].index("-i")   # a decoder option has to come before the input


def test_pick_judges_every_other_second_of_the_opening_when_asked_to(monkeypatch):
    monkeypatch.setattr(frames, "OPENING_STEP", 2)
    fake_ffmpeg(monkeypatch, {4.0: STRIPES})   # seen at 4.0 and, holding still, at 5.0; the scan starts at 3.0, so 4.0 is the odd second
    assert [f["time"] for f in frames.pick("x.mkv", 600)] == [5.0]   # the odd second is only the one "a second on" for 3.0


def test_a_frame_is_shown_at_the_time_it_was_judged_not_at_the_whole_second_nearest(monkeypatch):
    commands = fake_ffmpeg(monkeypatch, {7.0: STRIPES}, offset=0.4)   # the real frame is 0.4s after the second
    picked = frames.pick("x.mkv", 600)
    assert [f["time"] for f in picked] == [7.4]
    shown = [c for c in commands if "mjpeg" in c]
    assert [c[c.index("-ss") + 1] for c in shown] == ["7.4"]   # the picture is taken from that frame, not from 7.0


def test_the_last_view_of_the_opening_is_not_a_candidate_for_it_has_no_later_view_to_show_it_holds_still(monkeypatch):
    start, length = frames.opening(600)
    last = start + int(length) - 1   # the final second the scan returns
    assert (last - start) % frames.OPENING_STEP == 0   # it would be a candidate if it had a later view
    fake_ffmpeg(monkeypatch, {last: (STRIPES,)})
    assert frames.pick("x.mkv", 600) == []


def test_pick_copes_with_a_moment_too_near_the_end_to_have_a_view_a_second_later(monkeypatch):
    moment = frames.moments(600)[-1]
    fake_ffmpeg(monkeypatch, {moment: (STRIPES,)})   # only one picture came back
    assert [f["time"] for f in frames.pick("x.mkv", 600)] == [moment]


def test_pick_still_finds_frames_in_the_middle_and_end_when_the_scan_of_the_opening_fails(monkeypatch):
    moment = frames.moments(600)[0]
    commands = fake_ffmpeg(monkeypatch, {moment: STRIPES})
    original = frames.subprocess.run
    monkeypatch.setattr(frames.subprocess, "run", lambda c, **kw: subprocess.CompletedProcess(c, 1, b"", b"") if "-t" in c else original(c, **kw))
    assert [f["time"] for f in frames.pick("x.mkv", 600)] == [moment]
    assert commands


def test_pick_gives_no_frames_for_a_video_that_is_all_black(monkeypatch):
    fake_ffmpeg(monkeypatch, {t: BLACK for t in frames.moments(600)})
    assert frames.pick("x.mkv", 600) == []


def test_pick_needs_a_known_length_and_an_installed_ffmpeg(monkeypatch):
    with pytest.raises(RuntimeError, match="length"):
        frames.pick("x.mkv", None)

    def missing(*a, **kw):
        raise FileNotFoundError
    monkeypatch.setattr(frames.subprocess, "run", missing)
    with pytest.raises(RuntimeError, match="not installed"):
        frames.pick("x.mkv", 600)


TSV = "\n".join(["level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext",
                 "1\t1\t0\t0\t0\t0\t0\t0\t960\t540\t-1\t",
                 "5\t1\t1\t1\t1\t1\t10\t10\t50\t54\t96.5\tTHE",
                 "5\t1\t1\t1\t1\t2\t70\t10\t90\t20\t91\tLONG",
                 "5\t1\t1\t1\t1\t3\t170\t10\t30\t20\t40\tROAD",     # not read clearly
                 "5\t1\t1\t1\t2\t1\t10\t50\t20\t20\t95\t|",         # a mark, not a word
                 "5\t1\t1\t1\t2\t2\t40\t50\t20\t20\t95\tA",         # too short to count
                 "5\t1\t1\t1\t2\t3\t70\t50\t80\t20\t88\tHome's"])


def fake_tesseract(monkeypatch, tsv):
    monkeypatch.setattr(frames.shutil, "which", lambda name: "/usr/bin/" + name)

    def run(command, **kw):
        if command[0] == "tesseract":
            return subprocess.CompletedProcess(command, 0, tsv.encode(), b"")
        return subprocess.CompletedProcess(command, 0, b"P5 pgm", b"")
    monkeypatch.setattr(frames.subprocess, "run", run)


def test_read_text_counts_only_words_tesseract_is_confident_about_and_sizes_the_tallest(monkeypatch):
    fake_tesseract(monkeypatch, TSV)
    assert frames.read_text("x.mkv", 10.0) == (3, 0.1)   # THE, LONG, Home's; the tallest is 54px of a 540px picture


def test_read_text_is_nothing_without_tesseract_or_when_it_fails(monkeypatch):
    assert frames.read_text("x.mkv", 10.0) == (0, 0.0)   # the autouse fixture hides tesseract
    fake_tesseract(monkeypatch, "")
    assert frames.read_text("x.mkv", 10.0) == (0, 0.0)

    def slow(command, **kw):
        raise subprocess.TimeoutExpired(command, 1)
    monkeypatch.setattr(frames.subprocess, "run", slow)
    assert frames.read_text("x.mkv", 10.0) == (0, 0.0)


def test_big_words_boost_a_frame_more_than_many_small_ones():
    assert frames.text_boost(1, 0) == pytest.approx(1.1) and frames.text_boost(0, 0) == 1
    assert frames.text_boost(3, 0.1) > frames.text_boost(40, 0.02)   # a title beats a page of credits
    assert frames.text_boost(3, 0.5) == frames.text_boost(3, 0.08)   # past title size, bigger adds nothing


def test_pick_prefers_the_frame_with_big_words_to_one_that_merely_has_more_edges_or_more_words(monkeypatch):
    fake_ffmpeg(monkeypatch, {5.0: tiles(0, H // 2), 7.0: tiles(1, H // 4), 9.0: tiles(2, H // 4)})
    assert frames.pick("x.mkv", 600, count=1)[0]["time"] == 5.0    # more edges wins on its own...
    monkeypatch.setattr(frames, "read_text", lambda file, seconds: {7.0: (3, 0.1), 9.0: (40, 0.02)}.get(seconds, (0, 0.0)))
    assert frames.pick("x.mkv", 600, count=1)[0]["time"] == 7.0    # ...until another has big words (not just many small ones)
