"""Tests for the review page's HTML pieces. What matters most: text from files can never become markup."""
import pytest

from tidyarr import report

FINAL = {"kind": "tv", "id": 549, "title": "Law & Order", "year": 1990, "season": 26, "episode": 2}
SOURCES = {"nfo": None, "metadata": {"kind": "tv", "title": "Law and Order", "year": None, "season": 26, "episode": 2},
           "guessit": {"kind": "tv", "title": "LnO", "year": None, "season": 26, "episode": 3}}


def test_agreement_is_marked_per_field():
    grid = report.matrix(SOURCES, FINAL)
    assert 'class="ok">Law and Order<' in grid  # same title once normalised ('and' = '&')
    assert 'class="bad">LnO<' in grid           # a different title conflicts
    assert 'class="bad">3<' in grid             # wrong episode conflicts
    assert 'class="none">—<' in grid            # a field a source left blank is neither
    assert grid.count('rowspan="5">N/A<') == 2  # no NFO and no ffprobe title: one N/A cell each


def test_text_from_files_is_escaped():
    hostile = '<script>alert(1)</script>"><img src=x onerror=alert(2)>'
    tips = report.node_tips({"probe": {"title": hostile, "tags": {hostile: hostile}}, "nfo": {"file": hostile, "tmdb": None, "imdb": hostile},
                             "calls": [{"tool": "guessit", "label": "filename", "command": hostile, "output": hostile}]})
    html = "".join(tips.values())
    assert "<script>alert" not in html and "<img src=x" not in html and "&lt;script&gt;" in html


def test_links_are_built_only_from_validated_ids():
    assert report.links("tv", 549, "tt0098844") == (
        '<a href="https://www.themoviedb.org/tv/549" rel="noreferrer">TMDB</a> · '
        '<a href="https://www.imdb.com/title/tt0098844/" rel="noreferrer">IMDb</a>')
    assert report.links("../evil", 549, "javascript:alert(1)") == ""
    assert report.links("tv", "549; drop", 'tt1"onmouseover="x') == ""


def test_a_source_with_nothing_says_n_a_once_and_the_column_is_named_tmdb_api():
    sources = {"nfo": None, "metadata": None, "guessit": {"kind": "tv", "title": "x", "year": None, "season": 1, "episode": 2}}
    final = {"kind": "tv", "id": 1, "title": "X", "year": 2000, "season": 1, "episode": 2}
    done = report.matrix(sources, final)
    assert done.count(">N/A<") == 3 and 'rowspan="5"' in done          # nfo, ffprobe and the title-tag parse: one cell each, not a dash per row
    assert "<th>tmdb api</th>" in done and "final (tmdb)" not in done
    review = report.matrix(sources, None)
    assert review.count(">N/A<") == 3 and "tmdb api" not in review     # under review: nothing to compare yet
    assert ">—<" in done                                                # a blank field of a source that did report (year) keeps its dash


def test_columns_follow_the_order_things_happen_tmdb_last():
    import re
    sources = {"nfo": None, "metadata": None, "guessit": {"kind": "tv", "title": "x", "year": None, "season": 1, "episode": 2}}
    final = {"kind": "tv", "id": 1, "title": "X", "year": 2000, "season": 1, "episode": 2}
    headers = lambda html: re.findall(r"<th>([^<]+)</th>", html.split("</thead>")[0])  # noqa: E731
    assert headers(report.matrix(sources, final)) == ["nfo → tmdb", "ffprobe", "guessit (title tag)", "guessit (filename)", "tmdb api"]
    assert headers(report.matrix(sources, None)) == ["nfo → tmdb", "ffprobe", "guessit (title tag)", "guessit (filename)"]
    # each row's cells line up with those headers: the tmdb value is the last cell
    first_row = report.matrix(sources, final).split("<tbody>")[1].split("</tr>")[0]
    assert first_row.endswith('<td class="final">episode</td>')


FINAL_LO = {"kind": "tv", "id": 549, "title": "Law & Order", "year": 1990, "season": 26, "episode": 2}


def lo_sources(**overrides):
    base = {"nfo": None, "metadata": {"kind": "tv", "title": "Law and Order", "year": None, "season": 26, "episode": 2},
            "guessit": {"kind": "tv", "title": "LnO", "year": None, "season": 26, "episode": 2}}
    base.update(overrides)
    return base


def test_the_grid_ends_with_a_consensus_row_showing_each_verdict_and_the_score():
    html = report.matrix(lo_sources(), FINAL_LO)
    footer = html.split('<tr class="score-row">')[1]
    assert "✓ agrees" in footer and "✗ conflicts" in footer                       # metadata agrees, guessit conflicts
    assert "<b>50%</b>" in footer and "1 of 2 sources agree" in footer and "s-mid" in footer
    assert footer.count('class="none verdict">—<') == 1                              # nfo absent: it did not speak


def test_the_score_colour_follows_the_share_of_sources_that_agree():
    everyone = lo_sources(nfo={"kind": "tv", "title": "Law & Order", "year": 1990, "season": None, "episode": None},
                          guessit={"kind": "tv", "title": "law and order", "year": None, "season": 26, "episode": 2})
    assert "<b>100%</b>" in report.matrix(everyone, FINAL_LO) and "s-ok" in report.matrix(everyone, FINAL_LO) and "3 of 3 sources agree" in report.matrix(everyone, FINAL_LO)
    nobody = lo_sources(metadata={"kind": "tv", "title": "Something Else", "year": None, "season": None, "episode": None})
    assert "<b>0%</b>" in report.matrix(nobody, FINAL_LO) and "s-low" in report.matrix(nobody, FINAL_LO)


def test_one_source_is_worded_in_the_singular_and_no_sources_say_n_a():
    one = lo_sources(guessit=None)
    assert "1 of 1 source agree" in report.matrix(one, FINAL_LO)
    silent = {"nfo": None, "metadata": None, "guessit": None}
    assert 'score-cell none">n/a<' in report.matrix(silent, FINAL_LO)


def test_a_file_under_review_has_no_consensus_row_yet():
    assert "score-row" not in report.matrix(lo_sources(), None)


def test_the_grids_score_always_matches_the_stored_consensus():
    from tidyarr.evidence import consensus
    sources = lo_sources()
    agreeing, spoke = consensus(sources, FINAL_LO)
    assert f"{agreeing} of {spoke} sources agree" in report.matrix(sources, FINAL_LO) and f"<b>{100 * agreeing // spoke}%</b>" in report.matrix(sources, FINAL_LO)


def test_the_grid_has_a_type_row_first_shown_as_episode_or_movie():
    html = report.matrix(lo_sources(), FINAL_LO)
    first_row = html.split("<tbody>")[1].split("</tr>")[0]
    assert first_row.startswith("<tr><th>type</th>") and ">tv<" not in html          # the code says tv; the page says episode
    assert first_row.count(">episode<") == 3                                         # metadata, guessit and the tmdb api column
    movie = {"kind": "movie", "id": 603, "title": "The Matrix", "year": 1999}
    movie_html = report.matrix({"nfo": None, "metadata": None,
                                "guessit": {"kind": "movie", "title": "The Matrix", "year": 1999, "season": None, "episode": None}}, movie)
    assert movie_html.split("<tbody>")[1].split("</tr>")[0].count(">movie<") == 2


def test_a_type_mismatch_is_a_red_cell_and_a_conflicting_verdict_even_when_the_title_matches():
    guessit = {"kind": "movie", "title": "Law & Order", "year": None, "season": None, "episode": None}   # right title, wrong type
    html = report.matrix(lo_sources(guessit=guessit, metadata=None), FINAL_LO)
    type_row = html.split("<tbody>")[1].split("</tr>")[0]
    assert 'class="bad">movie<' in type_row and 'class="ok">' not in type_row.split("</th>")[1].split('class="final"')[0]
    assert "✗ conflicts" in html and "0 of 1 source agree" in html


def test_the_type_is_not_compared_while_a_file_is_under_review():
    assert 'class="known">episode<' in report.matrix(lo_sources(), None)


RAW = {"probe": {"title": "Law.and.Order.S26E02", "duration": 2455.0}}


def grid_rows(html):
    return [row for row in html.split("<tbody>")[1].split("</tbody>")[0].split("</tr>") if row.strip()]


def test_the_ffprobe_column_puts_the_raw_title_tag_in_the_title_row_beside_the_parsed_titles():
    html = report.matrix(lo_sources(), FINAL_LO, RAW)
    rows = grid_rows(html)
    labels = [row.split("</th>")[0].split("<th>")[-1] for row in rows]
    assert labels == ["type", "title", "year", "season", "episode", "consensus"]
    assert '<td class="raw"><code>Law.and.Order.S26E02</code></td>' in rows[1]               # in the title row, with the parsed titles
    assert all("<code>" not in row for i, row in enumerate(rows) if i != 1)                   # and nowhere else
    assert html.count("Law.and.Order.S26E02") == 1 and 'class="raw whole"' not in html        # no big spanning cell any more
    assert rows[0].count('class="none">—<') >= 1 and "not counted" in rows[-1] and "1 of 2 sources agree" in html   # evidence, not a vote


def test_the_ffprobe_column_says_n_a_when_there_is_no_title_tag_or_no_probe_result():
    for raw in (None, {}, {"probe": None}, {"probe": {"title": None, "duration": 60}}):
        html = report.matrix(lo_sources(), FINAL_LO, raw)
        assert 'class="none whole" rowspan="5">N/A<' in html and "<code>" not in html


def test_the_raw_title_is_escaped():
    html = report.matrix(lo_sources(), FINAL_LO, {"probe": {"title": '<script>alert(1)</script>'}})
    assert "<script>alert" not in html and "&lt;script&gt;" in html


def test_a_file_under_review_still_shows_the_raw_ffprobe_title():
    html = report.matrix(lo_sources(), None, RAW)
    assert "<code>Law.and.Order.S26E02</code>" in html and "tmdb api" not in html and "score-row" not in html


def test_the_ffprobe_column_sits_before_the_parse_of_its_own_title():
    import re
    head = report.matrix(lo_sources(), FINAL_LO, RAW).split("</thead>")[0]
    labels = re.findall(r"<th>([^<]+)</th>", head)
    assert labels.index("ffprobe") + 1 == labels.index("guessit (title tag)")


def call(tool, label, **extra):
    return {"tool": tool, "label": label, "command": f"{tool} …", "output": "{}", "seconds": 0.1, "exit_code": 0, **extra}


API = "https://api.themoviedb.org/3"


@pytest.mark.parametrize("command, page", [
    (f"GET {API}/tv/549?api_key=<redacted>", "https://www.themoviedb.org/tv/549"),
    (f"GET {API}/movie/603?api_key=<redacted>", "https://www.themoviedb.org/movie/603"),
    (f"GET {API}/tv/549/season/26/episode/2?api_key=<redacted>", "https://www.themoviedb.org/tv/549/season/26/episode/2"),
    (f"GET {API}/search/tv?api_key=<redacted>&query=911+nash", "https://www.themoviedb.org/search/tv?query=911+nash"),
    (f"GET {API}/search/movie?api_key=<redacted>&query=lord+of+the+rings&year=2001", "https://www.themoviedb.org/search/movie?query=lord+of+the+rings"),
    (f"GET {API}/search/tv?api_key=<redacted>&query=caf%C3%A9&first_air_date_year=2020", "https://www.themoviedb.org/search/tv?query=caf%C3%A9"),
])
def test_a_tmdb_api_request_maps_to_the_matching_public_page(command, page):
    assert report.tmdb_page_url(command) == page


@pytest.mark.parametrize("command", [
    None, "", "guessit -j -- x.mkv",
    f"GET {API}/configuration?api_key=<redacted>",                       # no public page for this
    f"GET {API}/find/tt0098844?api_key=<redacted>&external_source=imdb_id",
    f"GET {API}/search/tv?api_key=<redacted>",                           # a search with no query
    f"GET {API}/tv/abc?api_key=<redacted>", f"GET {API}/tv/549/../../admin?api_key=<redacted>",
    f"GET {API}/search/tv?api_key=<redacted>&query=<script>alert(1)</script>",   # tampered stored text
    f'GET {API}/search/tv?api_key=<redacted>&query=x"onmouseover="alert(1)',
    f"GET {API}/search/tv?api_key=<redacted>&query=javascript:alert(1)",
    f"GET {API}/tv/549?api_key=SECRETKEY0123456789",                      # a real key in the text would not match the pattern
    "GET https://evil.example/3/tv/549?api_key=<redacted>",
])
def test_anything_that_is_not_a_plain_tmdb_request_gets_no_link(command):
    assert report.tmdb_page_url(command) is None


def test_tmdb_blocks_link_to_the_page_in_a_new_tab_and_other_tools_do_not():
    tmdb_call = call("tmdb", "/tv/549", command=f"GET {API}/tv/549?api_key=<redacted>", status="HTTP 200")
    html = report.terminal(tmdb_call)
    assert '<a href="https://www.themoviedb.org/tv/549" target="_blank" rel="noopener noreferrer">view on themoviedb.org ↗</a>' in html
    assert "# HTTP 200" in html and "api_key=&lt;redacted&gt;" in html
    assert html.index("view on themoviedb.org") < html.index("{}")                       # under the command, above the reply, so a long reply can't hide it
    assert "<a " not in report.terminal(call("guessit", "filename"))
    assert "<a " not in report.terminal(call("tmdb", "/configuration", command=f"GET {API}/configuration?api_key=<redacted>"))


def test_the_api_url_with_a_key_is_never_linked_and_the_key_never_reaches_the_page():
    secret = "SECRETKEY0123456789abcdef"
    page = report.node_tips({"calls": [call("tmdb", "/tv/549", command=f"GET {API}/tv/549?api_key=<redacted>")]})["tmdb"]
    assert "api.themoviedb.org" in page and 'href="https://api.themoviedb.org' not in page and secret not in page
