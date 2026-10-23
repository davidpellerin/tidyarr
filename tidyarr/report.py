"""HTML pieces for the review page: the field-by-field evidence grid, the plain-words clues, and the flow diagram's
hover cards.

Everything that came from a file (names, tags, NFO text) is HTML-escaped, and links are built only from validated ids, never from text found in files.
"""
import html
import re

from tidyarr.evidence import agrees, norm_title

FIELDS = (("type", "kind"), ("title", "title"), ("year", "year"), ("season", "season"), ("episode", "episode"))  # (row label, key)
TYPE_NAMES = {"tv": "episode", "movie": "movie"}  # the code says tv; people say episode
IMDB_ID = re.compile(r"tt\d{5,10}")
NOTHING_FOUND = {"nfo": "N/A", "metadata": "N/A", "guessit": "N/A"}  # a whole source that produced nothing

CSS = """
:root{--bg:#f6f7f9;--card:#fff;--ink:#1b1f24;--dim:#6b7480;--line:#e3e6ea;--ok:#1a7f4b;--ok-bg:#e3f4ea;--bad:#b3261e;--bad-bg:#fbe7e5;
--warn:#8a5a00;--warn-bg:#fdf1d8;--accent:#2457c5}
@media (prefers-color-scheme:dark){:root{--bg:#14171b;--card:#1c2025;--ink:#e6e9ed;--dim:#98a2ae;--line:#2c323a;--ok:#5fd18b;--ok-bg:#16301f;
--bad:#ff8b82;--bad-bg:#3a1c1a;--warn:#f0c15a;--warn-bg:#352a10;--accent:#7fa6ff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,sans-serif}
main{max-width:1000px;margin:0 auto;padding:24px 16px 64px}h1{font-size:1.5rem;margin:0 0 4px}
.sub{color:var(--dim);margin:0 0 20px}.stats{display:flex;flex-wrap:wrap;gap:12px;margin:0 0 24px}
.stat{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 16px}.stat b{display:block;font-size:1.4rem}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;margin:0 0 16px}
.top{display:flex;flex-wrap:wrap;align-items:center;gap:8px 12px}.top h2{font-size:1.1rem;margin:0;flex:1 1 280px}
.path{color:var(--dim);font:13px ui-monospace,monospace;margin:4px 0 12px;word-break:break-all}
.badge{font-size:12px;border-radius:99px;padding:2px 10px;border:1px solid var(--line);color:var(--dim)}
.badge.auto{color:var(--ok);background:var(--ok-bg)}.badge.human{color:var(--warn);background:var(--warn-bg)}
.score{display:flex;align-items:center;gap:8px;min-width:150px}.bar{flex:1;height:8px;border-radius:99px;background:var(--line);overflow:hidden}
.bar i{display:block;height:100%;background:var(--ok)}.bar.mid i{background:var(--warn)}.bar.low i{background:var(--bad)}
.wrap{overflow-x:auto}table.matrix{border-collapse:collapse;width:100%;min-width:560px}
.matrix th,.matrix td{border:1px solid var(--line);padding:6px 10px;text-align:left;vertical-align:top}
.matrix thead th{color:var(--dim);font-weight:600;font-size:13px}.matrix tbody th{color:var(--dim);font-weight:500;width:84px}
td.whole{vertical-align:middle;text-align:center}td.raw:not(.none){text-align:left;font-size:13px}td.raw .k{color:var(--dim)}td.raw code{font:13px ui-monospace,monospace;word-break:break-all}
.score-row th,.score-row td{border-top:2px solid var(--dim)}td.verdict{text-align:center;font-size:13px}
td.score-cell b{font-size:1.15rem}td.score-cell small{color:var(--dim);font-weight:400}td.s-ok b{color:var(--ok)}td.s-mid b{color:var(--warn)}td.s-low b{color:var(--bad)}td.ok{background:var(--ok-bg);color:var(--ok)}td.bad{background:var(--bad-bg);color:var(--bad)}td.none{color:var(--dim)}td.final{font-weight:600}
details{margin-top:10px}summary{cursor:pointer;color:var(--accent)}pre{background:var(--bg);border:1px solid var(--line);border-radius:8px;
padding:10px;overflow-x:auto;font-size:13px}dl{display:grid;grid-template-columns:110px 1fr;gap:4px 12px;margin:8px 0}dt{color:var(--dim)}dd{margin:0;word-break:break-word}
.term{background:#0d1117;color:#c9d1d9;border:1px solid #30363d;border-radius:8px;padding:12px;font:13px/1.5 ui-monospace,monospace;
overflow:auto;max-height:28em;white-space:pre-wrap;word-break:break-word}.term .prompt{color:#7ee787}.term .cmd{color:#e6edf3;font-weight:600}
.term .foot{color:#6e7681}.term a{color:#58a6ff}
a{color:var(--accent)}
"""


def e(value):
    return html.escape("" if value is None else str(value), quote=True)


def cell_class(field, value, final):
    """none = the source said nothing; ok = it backs the final answer; bad = it conflicts with it."""
    if value is None:
        return "none"
    if final is None:
        return "ok"
    same = norm_title(str(value)) == norm_title(str(final)) if field == "title" else value == final
    return "ok" if same else "bad"


def score_class(percent):
    return "s-ok" if percent == 100 else "s-mid" if percent >= 50 else "s-low"


# Grid columns, in the order things happen. Each header names what produced the text under it.
# `vote` marks the columns that are opinions about identity; ffprobe's raw title is evidence, not an opinion.
COLUMNS = (("nfo", "nfo → tmdb"), ("ffprobe", "ffprobe"), ("metadata", "guessit (title tag)"),
           ("guessit", "guessit (filename)"), ("tmdb", "tmdb api"))
VOTERS = ("nfo", "metadata", "guessit")


def ffprobe_cell(raw, index, label):
    """ffprobe reports one raw string, the title tag. It goes in the title row, right beside the parsed titles it is to be
    compared with; its other rows are dashes. With no tag, the column says N/A once, spanning the rows."""
    title = ((raw or {}).get("probe") or {}).get("title")
    if not title:
        return f'<td class="none whole" rowspan="{len(FIELDS)}">N/A</td>' if index == 0 else ""
    return f'<td class="raw"><code>{e(title)}</code></td>' if label == "title" else '<td class="none">—</td>'


def matrix(sources, final, raw=None):
    """The field-by-field grid. Columns follow the order things happen: nfo (looked up on TMDB), ffprobe's raw title,
    guessit's parse of that title, guessit's parse of the filename, then the TMDB lookup. Below the fields, a consensus row gives each opinion's verdict and the
    overall score. With `final=None` (a file still under review) there is no TMDB answer yet, so no TMDB column, no
    consensus row, and nothing to colour against. `raw` is the evidence's raw material (for ffprobe's title)."""
    pending = final is None
    final = final or {}
    columns = [(key, label) for key, label in COLUMNS if not (pending and key == "tmdb")]
    head = "".join(f"<th>{label}</th>" for _, label in columns)
    rows = []
    for index, (label, field) in enumerate(FIELDS):
        def shown(value):
            return "—" if value is None else e(TYPE_NAMES.get(value, value) if field == "kind" else value)

        def cell(name):
            if name == "ffprobe":
                return ffprobe_cell(raw, index, label)
            if name == "tmdb":
                return f'<td class="final">{shown(final.get(field))}</td>'
            opinion = sources.get(name)
            if isinstance(opinion, dict):
                value = opinion.get(field)
                css = "known" if pending and value is not None else cell_class(field, value, final.get(field))
                return f'<td class="{css}">{shown(value)}</td>'
            if index:  # a whole source that produced nothing is one N/A cell spanning the rows
                return ""
            return f'<td class="none whole" rowspan="{len(FIELDS)}">{NOTHING_FOUND[name]}</td>'
        rows.append(f'<tr><th>{label}</th>{"".join(cell(key) for key, _ in columns)}</tr>')
    if not pending:
        rows.append(consensus_row(sources, final, columns))
    return f'<div class="wrap"><table class="matrix"><thead><tr><th></th>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'


def consensus_row(sources, final, columns):
    """Each opinion's verdict (it counts only if it spoke) and the overall score, worked out from the grid above it."""
    verdicts = {name: agrees(sources[name], final) if isinstance(sources.get(name), dict) else None for name in VOTERS}
    spoke = sum(v is not None for v in verdicts.values())
    agreeing = sum(v is True for v in verdicts.values())

    def verdict_cell(name):
        if name == "ffprobe":  # raw evidence: it is shown, not counted
            return '<td class="none verdict">not counted</td>'
        return {True: '<td class="ok verdict">✓ agrees</td>', False: '<td class="bad verdict">✗ conflicts</td>',
                None: '<td class="none verdict">—</td>'}[verdicts[name]]
    if spoke:
        percent = 100 * agreeing // spoke
        score = (f'<td class="score-cell {score_class(percent)}"><b>{percent}%</b><small> {agreeing} of {spoke} '
                 f'source{"s" if spoke != 1 else ""} agree</small></td>')
    else:
        score = '<td class="score-cell none">n/a</td>'
    cells = "".join(score if name == "tmdb" else verdict_cell(name) for name, _ in columns)
    return f'<tr class="score-row"><th>consensus</th>{cells}</tr>'


CLUE_LABELS = (("guessit", "File name"), ("metadata", "Title inside the video"), ("nfo", "Info file (.nfo)"))


def said(opinion):
    """An opinion as a person would say it: 'Law and Order · S26E02' or 'Heat (1995)'."""
    text = str(opinion.get("title") or "?") + (f" ({opinion['year']})" if opinion.get("year") else "")
    if opinion.get("season") is not None and opinion.get("episode") is not None:
        text += f" · S{opinion['season']:02d}E{opinion['episode']:02d}"
    return text


def clues(sources, final):
    """The grid's opinions in plain words, for the review page: what each source said and whether it backs the answer
    (`agrees` is None when there is no answer yet or the source said nothing). Plain data; the page inserts it as text."""
    out = []
    for name, label in CLUE_LABELS:
        opinion = sources.get(name)
        if isinstance(opinion, dict):
            out.append({"source": label, "said": said(opinion), "agrees": agrees(opinion, final) if final else None})
        else:
            out.append({"source": label, "said": "Nothing found", "agrees": None})
    return out


def links(kind, tmdb_id, imdb):
    out = []
    if kind in ("tv", "movie") and isinstance(tmdb_id, int):
        out.append(f'<a href="https://www.themoviedb.org/{kind}/{tmdb_id}" rel="noreferrer">TMDB</a>')
    if isinstance(imdb, str) and IMDB_ID.fullmatch(imdb):
        out.append(f'<a href="https://www.imdb.com/title/{imdb}/" rel="noreferrer">IMDb</a>')
    return " · ".join(out)


def duration_text(seconds):
    if not isinstance(seconds, (int, float)):
        return "?"
    minutes, secs = divmod(int(round(seconds)), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m {secs:02d}s" if hours else f"{minutes}m {secs:02d}s"


API_COMMAND = re.compile(r"GET https://api\.themoviedb\.org/3(?P<path>/[^?\s]*)\?api_key=<redacted>(?:&(?P<query>\S*))?")
SAFE_PAGE = re.compile(r"https://www\.themoviedb\.org/(?:search/(?:tv|movie)\?query=[A-Za-z0-9%+._~-]{1,300}"
                       r"|(?:tv|movie)/\d{1,9}(?:/season/\d{1,3}/episode/\d{1,4})?)")


def tmdb_page_url(command):
    """The public themoviedb.org page that matches a recorded API request, or None. The API URL itself can't be linked
    (it needs the API key); the website shows the same show, episode or search with no key. The URL is rebuilt from
    pieces that are checked against a strict pattern, because the command text is stored data."""
    match = API_COMMAND.fullmatch(command or "")
    if not match:
        return None
    path, query = match["path"], match["query"] or ""
    search = re.fullmatch(r"/search/(tv|movie)", path)
    if search:
        term = dict(part.split("=", 1) for part in query.split("&") if "=" in part).get("query")
        url = f"https://www.themoviedb.org/search/{search[1]}?query={term}" if term else None
    elif re.fullmatch(r"/(?:tv|movie)/\d+(?:/season/\d+/episode/\d+)?", path):
        url = f"https://www.themoviedb.org{path}"
    else:
        url = None
    return url if url and SAFE_PAGE.fullmatch(url) else None


def identity_url(identity):
    """The public themoviedb.org page for a decided identity (the episode page if it has a season and episode), or None."""
    kind, tmdb_id = identity.get("kind"), identity.get("id")
    if kind not in ("tv", "movie") or not isinstance(tmdb_id, int):
        return None
    url = f"https://www.themoviedb.org/{kind}/{tmdb_id}"
    season, episode = identity.get("season"), identity.get("episode")
    if kind == "tv" and isinstance(season, int) and isinstance(episode, int):
        url += f"/season/{season}/episode/{episode}"
    return url if SAFE_PAGE.fullmatch(url) else None


def terminal(call):
    """One recorded command, drawn like a terminal: the command, exactly what it printed, and how it ended. TMDB requests
    also get a link, under the command, to the matching page on themoviedb.org (opens in a new tab)."""
    result = call.get("status") or f'exit {call.get("exit_code")}'
    foot = f'# {result} · {call.get("seconds")}s' + (f' · {call["note"]}' if call.get("note") else "")
    page = tmdb_page_url(call.get("command")) if call.get("tool") == "tmdb" else None
    link = (f'<span class="foot"># </span><a href="{e(page)}" target="_blank" rel="noopener noreferrer">view on themoviedb.org ↗</a>\n'
            if page else "")  # right under the command, where it stays visible when a long reply scrolls
    return (f'<pre class="term"><span class="prompt">$</span> <span class="cmd">{e(call.get("command"))}</span>\n{link}'
            f'{e(call.get("output"))}\n<span class="foot">{e(foot)}</span></pre>')


def probe_summary(probe):
    tags = ", ".join(f"{k}={v}" for k, v in (probe.get("tags") or {}).items()) or "—"
    return (f'<dl><dt>title tag</dt><dd>{e(probe.get("title") or "—")}</dd><dt>length</dt><dd>{e(duration_text(probe.get("duration")))}</dd>'
            f'<dt>video</dt><dd>{e(probe.get("video") or "—")}</dd><dt>audio</dt><dd>{e(", ".join(probe.get("audio") or []) or "—")}</dd>'
            f'<dt>subtitles</dt><dd>{e(", ".join(probe.get("subtitles") or []) or "—")}</dd><dt>other tags</dt><dd>{e(tags)}</dd></dl>')


def nfo_summary(nfo):
    ref = nfo.get("tmdb") or {}
    found = links(ref.get("kind"), ref.get("id"), nfo.get("imdb")) or "no ids found"
    return f'<dl><dt>file</dt><dd>{e(nfo.get("file"))}</dd><dt>points at</dt><dd>{found}</dd></dl>'


def node_tips(raw):
    """What each step of the flow diagram returned, as escaped HTML for the hover card: the commands drawn like a
    terminal, in the order they ran, with ffprobe's short summary and the NFO's ids where there are any. Keyed by the
    diagram's node names; a step with nothing to show has no entry."""
    raw = raw or {}
    runs = raw.get("calls") or []

    def of(tool, label=None):
        return "".join(terminal(c) for c in runs if c.get("tool") == tool and (label is None or c.get("label") == label))
    tips = {"guessit": of("guessit", "filename"),
            "metadata": of("guessit", "container title"),
            "probe": (probe_summary(raw["probe"]) if raw.get("probe") else "") + of("ffprobe"),
            "nfo": nfo_summary(raw["nfo"]) if raw.get("nfo") else "",
            "tmdb": of("tmdb")}
    for number, call in enumerate([c for c in runs if c.get("tool") == "tmdb"], 1):  # the diagram has one box per request
        tips[f"tmdb{number}"] = terminal(call)
    return {name: html for name, html in tips.items() if html}
