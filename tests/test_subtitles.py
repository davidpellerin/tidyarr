from pathlib import Path

from tidyarr import subtitles


def touch(*paths):
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")


def tails(video):
    return sorted((p.name, tail) for p, tail in subtitles.find(video))


def test_subtitles_beside_the_video_keep_what_follows_its_name(tmp_path):
    video = tmp_path / "Show.S01E01.mkv"
    touch(video, tmp_path / "Show.S01E01.srt", tmp_path / "Show.S01E01.en.forced.srt", tmp_path / "Show.S01E01.idx",
          tmp_path / "Show.S01E01.sub", tmp_path / "Show.S01E01.nfo", tmp_path / "Show.S01E010.srt", tmp_path / "Other.en.srt")
    assert tails(video) == [("Show.S01E01.en.forced.srt", ".en.forced.srt"), ("Show.S01E01.idx", ".idx"),
                            ("Show.S01E01.srt", ".srt"), ("Show.S01E01.sub", ".sub")]


def test_a_subtitle_goes_with_the_video_whose_name_it_matches_most_closely(tmp_path):
    short, extended = tmp_path / "Film.mkv", tmp_path / "Film.Extended.mkv"
    touch(short, extended, tmp_path / "Film.en.srt", tmp_path / "Film.Extended.en.srt")
    assert tails(short) == [("Film.en.srt", ".en.srt")]
    assert tails(extended) == [("Film.Extended.en.srt", ".en.srt")]


def test_a_subs_folder_named_after_the_episode_belongs_to_it(tmp_path):
    e1, e2 = tmp_path / "Show.S01E01.mkv", tmp_path / "Show.S01E02.mkv"
    touch(e1, e2, tmp_path / "Subs/Show.S01E01/2_English.srt", tmp_path / "Subs/Show.S01E01/3_French.srt",
          tmp_path / "Subs/Show.S01E02/2_English.srt", tmp_path / "Subs/stray.srt")
    assert tails(e1) == [("2_English.srt", ".English.srt"), ("3_French.srt", ".French.srt")]


def test_a_loose_subs_folder_belongs_only_to_a_video_alone_in_its_folder(tmp_path):
    film = tmp_path / "Film.2010/Film.2010.1080p.mkv"
    touch(film, tmp_path / "Film.2010/Subs/2_English.srt", tmp_path / "Film.2010/Subs/3_English.srt", tmp_path / "Film.2010/Subs/4_Spanish.srt")
    # The two English tracks keep their whole names, so one doesn't overwrite the other.
    assert tails(film) == [("2_English.srt", ".2_English.srt"), ("3_English.srt", ".3_English.srt"), ("4_Spanish.srt", ".Spanish.srt")]
    pack = tmp_path / "Pack"
    touch(pack / "a.mkv", pack / "b.mkv", pack / "Subtitles/English.srt")
    assert tails(pack / "a.mkv") == []


def test_a_renamed_subtitle_takes_the_new_name_of_its_video():
    assert subtitles.renamed(Path("TV/Show/S01E01 - Pilot.mkv"), ".en.srt") == Path("TV/Show/S01E01 - Pilot.en.srt")
