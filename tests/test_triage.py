"""Offline tests for automatic matching and the decision store: TMDB calls are replaced."""
import pytest

from tidyarr import decisions, tmdb, triage


def cand(id_, title, year, countries=(), kind="tv"):
    return {"id": id_, "kind": kind, "title": title, "year": year, "countries": list(countries), "overview": ""}


def fake_tmdb(monkeypatch, candidates, episodes=()):
    """`episodes` is the set of TMDB ids that have the episode being asked about."""
    monkeypatch.setattr(tmdb, "search", lambda *a, **kw: candidates)
    monkeypatch.setattr(tmdb, "episode_title", lambda tv_id, s, e: "Title" if tv_id in episodes else None)


def test_movie_with_matching_title_and_year_is_accepted(monkeypatch):
    fake_tmdb(monkeypatch, [cand(1, "The Dark Knight", 2008, kind="movie")])
    ident = triage.auto_resolve({"type": "movie", "title": "The Dark Knight", "year": 2008})
    assert (ident["kind"], ident["id"], tmdb.render(ident)) == ("movie", 1, "The Dark Knight (2008)")


def test_movie_title_matching_ignores_case_punctuation_and_leading_the(monkeypatch):
    fake_tmdb(monkeypatch, [cand(1, "The Matrix", 1999, kind="movie")])
    assert tmdb.render(triage.auto_resolve({"type": "movie", "title": "matrix", "year": 1999})) == "The Matrix (1999)"


def test_movie_without_year_goes_to_a_human(monkeypatch):
    fake_tmdb(monkeypatch, [cand(1, "Dune", 2021, kind="movie")])
    assert triage.auto_resolve({"type": "movie", "title": "Dune"}) is None


def test_movie_with_different_title_goes_to_a_human(monkeypatch):
    fake_tmdb(monkeypatch, [cand(1, "Parasite", 2019, kind="movie")])
    assert triage.auto_resolve({"type": "movie", "title": "Parasite Gisaengchung", "year": 2019}) is None


def test_episode_is_accepted_when_exactly_one_show_has_it(monkeypatch):
    shows = [cand(1995, "The Office", 1995, ["US"]), cand(2005, "The Office", 2005, ["US"]), cand(2001, "The Office", 2001, ["GB"])]
    fake_tmdb(monkeypatch, shows, episodes={2005})
    g = {"type": "episode", "title": "the office", "country_code": "US", "season": 3, "episode": 4}
    ident = triage.auto_resolve(g)
    assert (ident["id"], ident["season"], ident["episode"]) == (2005, 3, 4)
    assert tmdb.render(ident) == "The Office (2005) - S03E04"


def test_episode_is_ambiguous_when_several_shows_have_it(monkeypatch):
    shows = [cand(1, "Battlestar Galactica", 2004), cand(2, "Battlestar Galactica", 1978)]
    fake_tmdb(monkeypatch, shows, episodes={1, 2})
    assert triage.auto_resolve({"type": "episode", "title": "Battlestar Galactica", "season": 1, "episode": 1}) is None


def test_episode_without_season_or_episode_goes_to_a_human(monkeypatch):
    fake_tmdb(monkeypatch, [cand(1, "Breaking Bad", 2008)], episodes={1})
    assert triage.auto_resolve({"type": "episode", "title": "Breaking Bad", "season": 1}) is None


def test_decisions_round_trip_and_missing_file_is_empty(tmp_path):
    path = tmp_path / "nested" / "decisions.json"
    assert decisions.load(path) == {}
    record = decisions.make_record("a.mkv", tmdb.identity(cand(1, "A", 2000, kind="movie")), "human", decided_at="2026-10-16")
    decisions.save({"path:a.mkv": record}, path)
    assert decisions.load(path) == {"path:a.mkv": record}
    assert decisions.render(record) == "A (2000)"


def test_decisions_save_keeps_the_previous_version_and_leaves_no_temp_file(tmp_path):
    path = tmp_path / "decisions.json"
    decisions.save({"path:a.mkv": {"n": 1}}, path)
    decisions.save({"path:a.mkv": {"n": 2}}, path)
    assert decisions.load(path) == {"path:a.mkv": {"n": 2}}
    assert decisions.load(decisions.backup_path(path)) == {"path:a.mkv": {"n": 1}}
    assert sorted(p.name for p in tmp_path.iterdir()) == ["decisions.json", "decisions.json.bak"]


def test_decisions_failed_save_leaves_the_file_as_it_was(tmp_path):
    path = tmp_path / "decisions.json"
    decisions.save({"path:a.mkv": {"n": 1}}, path)
    with pytest.raises(TypeError):
        decisions.save({"path:a.mkv": object()}, path)  # not JSON: fails midway through writing
    assert decisions.load(path) == {"path:a.mkv": {"n": 1}}
    assert sorted(p.name for p in tmp_path.iterdir()) == ["decisions.json"]


def test_decisions_load_refuses_a_damaged_file(tmp_path):
    path = tmp_path / "decisions.json"
    path.write_text('{"path:a.mkv": {')
    with pytest.raises(RuntimeError, match="not valid JSON"):
        decisions.load(path)


def test_identity_stores_ids_not_a_formatted_name():
    ident = tmdb.identity(cand(62560, "Mr. Robot", 2015), season=2, episode=3)
    assert ident == {"kind": "tv", "id": 62560, "title": "Mr. Robot", "year": 2015, "season": 2, "episode": 3}
    assert "name" not in ident and tmdb.render(ident) == "Mr. Robot (2015) - S02E03"
    movie = tmdb.identity(cand(278, "Heat", 1995, kind="movie"), season=1, episode=1)
    assert "season" not in movie  # episode numbers never leak onto a movie


def test_fingerprint_survives_move_and_rename_but_not_content_change(tmp_path):
    original = tmp_path / "Messy.Name.1080p.mkv"
    original.write_bytes(b"x" * 3_000_000 + b"tail")
    key = decisions.key_for("Messy.Name.1080p.mkv", tmp_path)
    (tmp_path / "sub").mkdir()
    original.rename(tmp_path / "sub" / "Clean Name (2000).mkv")
    assert decisions.key_for("sub/Clean Name (2000).mkv", tmp_path) == key
    (tmp_path / "sub" / "Clean Name (2000).mkv").write_bytes(b"y" * 3_000_000 + b"tail")
    assert decisions.key_for("sub/Clean Name (2000).mkv", tmp_path) != key


def test_small_files_hash_without_error_and_missing_files_fall_back_to_the_path(tmp_path):
    (tmp_path / "tiny.mkv").write_bytes(b"abc")
    assert decisions.key_for("tiny.mkv", tmp_path).startswith("fp:")
    assert decisions.key_for("not/on/disk.mkv", tmp_path) == "path:not/on/disk.mkv"
