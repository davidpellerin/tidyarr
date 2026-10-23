"""Offline tests for tidyarr.tmdb: the HTTP layer (`_get`) is replaced, so no network or key is needed."""
import io
import urllib.error

import pytest

from tidyarr import config, tmdb

TV_RESULTS = {"results": [
    {"id": 2996, "name": "The Office", "first_air_date": "2001-07-09", "origin_country": ["GB"], "overview": "UK"},
    {"id": 2316, "name": "The Office", "first_air_date": "2005-03-24", "origin_country": ["US"], "overview": "US"},
]}


def test_search_puts_matching_country_first(monkeypatch):
    monkeypatch.setattr(tmdb, "_get", lambda path, **params: TV_RESULTS)
    assert [c["id"] for c in tmdb.search("the office", "tv", country="US")] == [2316, 2996]


def test_search_keeps_tmdb_order_without_country(monkeypatch):
    monkeypatch.setattr(tmdb, "_get", lambda path, **params: TV_RESULTS)
    assert [c["id"] for c in tmdb.search("the office", "tv")] == [2996, 2316]


def test_search_respects_limit_and_handles_no_results(monkeypatch):
    monkeypatch.setattr(tmdb, "_get", lambda path, **params: TV_RESULTS)
    assert len(tmdb.search("the office", "tv", limit=1)) == 1
    monkeypatch.setattr(tmdb, "_get", lambda path, **params: None)
    assert tmdb.search("nothing", "movie") == []


def test_movie_candidate_parses_year(monkeypatch):
    movie = {"results": [{"id": 278, "title": "The Shawshank Redemption", "release_date": "1994-09-23"}]}
    monkeypatch.setattr(tmdb, "_get", lambda path, **params: movie)
    (candidate,) = tmdb.search("shawshank redemption", "movie")
    assert (candidate["title"], candidate["year"]) == ("The Shawshank Redemption", 1994)


@pytest.mark.parametrize("episode_json, expected", [({"name": "Initiation"}, "Initiation"), (None, None)])
def test_episode_title(monkeypatch, episode_json, expected):
    monkeypatch.setattr(tmdb, "_get", lambda path, **params: episode_json)
    assert tmdb.episode_title(2316, 3, 4) == expected


def test_candidates_keep_the_rating_language_and_original_title(monkeypatch):
    movie = {"results": [{"id": 1, "title": "Spirited Away", "original_title": "千と千尋の神隠し", "original_language": "ja",
                          "release_date": "2001-07-20", "vote_average": 8.5, "vote_count": 17000},
                         {"id": 2, "title": "Unseen", "original_title": "Unseen", "release_date": "2020-01-01", "vote_average": 0, "vote_count": 0}]}
    monkeypatch.setattr(tmdb, "_get", lambda path, **params: movie)
    seen, unseen = tmdb.search("x", "movie")
    assert (seen["original_title"], seen["language"], seen["rating"], seen["votes"], seen["date"]) == ("千と千尋の神隠し", "ja", 8.5, 17000, "2001-07-20")
    assert (unseen["original_title"], unseen["rating"]) == (None, None)  # same title is not repeated; no votes is no rating


def test_episode_info_has_summary_air_date_and_runtime(monkeypatch):
    monkeypatch.setattr(tmdb, "_get", lambda path, **params: {"name": "Initiation", "overview": "Pam.", "air_date": "2005-10-04", "runtime": 22})
    assert tmdb.episode_info(2316, 3, 4) == {"title": "Initiation", "overview": "Pam.", "air_date": "2005-10-04", "runtime": 22}
    monkeypatch.setattr(tmdb, "_get", lambda path, **params: None)
    assert tmdb.episode_info(2316, 3, 4) is None


def test_clean_name():
    show = {"kind": "tv", "title": "The Office", "year": 2005}
    assert tmdb.clean_name(show) == "The Office (2005)"
    assert tmdb.clean_name(show, season=3, episode=4) == "The Office (2005) - S03E04"
    assert tmdb.clean_name({"kind": "movie", "title": "Untitled", "year": None}) == "Untitled"


def test_clean_name_ignores_episode_numbers_for_movies():
    movie = {"kind": "movie", "title": "Heat", "year": 1995}
    assert tmdb.clean_name(movie, season=1, episode=2) == "Heat (1995)"


def test_the_key_comes_from_the_environment_then_the_settings_file(monkeypatch):
    monkeypatch.delenv("TMDB_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="Settings"):
        tmdb.api_key()
    assert tmdb.key_status() == {"source": None, "key": None}
    config.save({config.KEY: "from-settings-2222"})  # saved from the page: applies at once
    assert tmdb.find_key() == ("from-settings-2222", "settings")
    monkeypatch.setenv("TMDB_API_KEY", "from-env-3333")
    assert tmdb.api_key() == "from-env-3333"
    assert tmdb.key_status() == {"source": "TMDB_API_KEY", "key": "from-env-3333"}


def test_a_key_is_checked_with_tmdb_before_it_is_accepted(monkeypatch):
    sent = []
    def urlopen(url, timeout):
        sent.append(url)
        if "0" * 32 in url:
            raise urllib.error.HTTPError(url, 401, "Unauthorized", {}, None)
        return io.BytesIO(b"{}")
    monkeypatch.setattr(tmdb.urllib.request, "urlopen", urlopen)
    with pytest.raises(ValueError, match="32 letters") as refused:
        tmdb.check_key("not-a-key")
    assert "not-a-key" not in str(refused.value) and not sent  # malformed: never sent, never repeated
    with pytest.raises(ValueError, match="doesn't accept"):
        tmdb.check_key("0" * 32)
    tmdb.check_key("a" * 32)
    assert sent[-1].startswith("https://api.themoviedb.org/3/configuration?api_key=")


def image(path, vote, width=1920, height=1080):
    return {"file_path": path, "vote_average": vote, "width": width, "height": height}


ARTWORK = {"backdrops": [image(f"/back{i}abc.jpg", i) for i in range(8)],
           "logos": [image("/logo1abc.png", 5, 500, 200), image("/logo2abc.png", 7, 500, 200), image("/logo3abc.png", 1, 500, 200)],
           "posters": [image("/poster1abc.jpg", 3, 500, 750), {"vote_average": 9, "width": 1, "height": 1}]}   # the last has no file


def test_images_take_the_best_rated_of_each_kind_and_skip_entries_with_no_file(monkeypatch):
    monkeypatch.setattr(tmdb, "_get", lambda path, **params: ARTWORK)
    found = tmdb.images("movie", 603)
    kinds = [i["kind"] for i in found]
    assert kinds == ["backdrop"] * 6 + ["logo"] * 2 + ["poster"]
    assert [i["path"] for i in found if i["kind"] == "backdrop"][:2] == ["/back7abc.jpg", "/back6abc.jpg"]   # best first
    assert [i["path"] for i in found if i["kind"] == "logo"] == ["/logo2abc.png", "/logo1abc.png"]
    assert found[0]["width"] == 1920 and found[0]["height"] == 1080


def test_images_ask_for_english_and_language_free_artwork_so_logos_and_posters_are_readable(monkeypatch):
    seen = []
    monkeypatch.setattr(tmdb, "_get", lambda path, **params: seen.append((path, params)) or ARTWORK)
    tmdb.images("tv", 549)
    assert seen == [("/tv/549/images", {"include_image_language": "en,null"})]


def test_a_tv_episode_adds_its_own_stills_first(monkeypatch):
    stills = {"stills": [image(f"/still{i}abc.jpg", i) for i in range(6)]}
    monkeypatch.setattr(tmdb, "_get", lambda path, **params: stills if "/episode/" in path else ARTWORK)
    found = tmdb.images("tv", 549, season=26, episode=2)
    assert [i["kind"] for i in found][:4] == ["still"] * 4 and found[0]["path"] == "/still5abc.jpg"
    assert found[4]["kind"] == "backdrop"


def test_a_movie_or_a_show_without_episode_numbers_has_no_stills(monkeypatch):
    paths = []
    monkeypatch.setattr(tmdb, "_get", lambda path, **params: paths.append(path) or ARTWORK)
    tmdb.images("movie", 603, season=1, episode=1)
    tmdb.images("tv", 549)
    assert paths == ["/movie/603/images", "/tv/549/images"]


def test_images_of_an_unknown_title_are_none_but_a_missing_episode_just_has_no_stills(monkeypatch):
    monkeypatch.setattr(tmdb, "_get", lambda path, **params: None)
    assert tmdb.images("tv", 1) is None
    monkeypatch.setattr(tmdb, "_get", lambda path, **params: None if "/episode/" in path else ARTWORK)
    assert "still" not in [i["kind"] for i in tmdb.images("tv", 549, season=1, episode=1)]


def test_details_carry_genres_people_certification_and_season_counts(monkeypatch):
    movie = {"id": 603, "title": "The Matrix", "release_date": "1999-03-30", "genres": [{"name": "Action"}, {"name": "Science Fiction"}],
             "credits": {"cast": [{"name": f"Actor {i}"} for i in range(8)], "crew": [{"name": "Lana", "job": "Director"}, {"name": "Joel", "job": "Producer"}]},
             "release_dates": {"results": [{"iso_3166_1": "GB", "release_dates": [{"certification": "15"}]},
                                           {"iso_3166_1": "US", "release_dates": [{"certification": ""}, {"certification": "R"}]}]}}
    seen = {}
    monkeypatch.setattr(tmdb, "_get", lambda path, **params: seen.update(params) or movie)
    found = tmdb.details("movie", 603)
    assert seen["append_to_response"] == "credits,release_dates"
    assert (found["genres"], found["director"], found["certification"]) == (["Action", "Science Fiction"], ["Lana"], "R")
    assert found["cast"] == ["Actor 0", "Actor 1", "Actor 2", "Actor 3", "Actor 4"] and found["seasons"] is None


def test_a_show_has_creators_content_rating_and_counts(monkeypatch):
    show = {"id": 2316, "name": "The Office", "first_air_date": "2005-03-24", "created_by": [{"name": "Greg Daniels"}],
            "number_of_seasons": 9, "number_of_episodes": 201,
            "content_ratings": {"results": [{"iso_3166_1": "GB", "rating": "12"}, {"iso_3166_1": "US", "rating": "TV-14"}]}}
    monkeypatch.setattr(tmdb, "_get", lambda path, **params: show)
    found = tmdb.details("tv", 2316)
    assert (found["director"], found["certification"], found["seasons"], found["episodes"]) == (["Greg Daniels"], "TV-14", 9, 201)


def test_search_results_without_those_fields_just_have_them_empty(monkeypatch):
    monkeypatch.setattr(tmdb, "_get", lambda path, **params: TV_RESULTS)
    first = tmdb.search("the office", "tv")[0]
    assert (first["genres"], first["director"], first["cast"], first["certification"]) == ([], [], [], None)
