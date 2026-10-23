"""Shared fixtures for the session and server tests."""

SHOW = {"kind": "tv", "id": 549, "title": "Law & Order", "year": 1990, "countries": ["US"], "overview": "Cops and DAs.", "poster": "/abc123.jpg",
        "date": "1990-09-13", "original_title": None, "language": "en", "rating": 7.4, "votes": 512}
OPINION = {"kind": "tv", "title": "Law and Order", "year": None, "season": 26, "episode": 2}
GUESS = {"kind": "tv", "title": "LnO", "year": None, "season": 26, "episode": 2}
IDENTITY = {"kind": "tv", "id": 549, "title": "Law & Order", "year": 1990, "season": 26, "episode": 2}


def evaluation(auto):
    ev = {"guessit_parse": {"type": "episode", "title": "LnO", "season": 26, "episode": 2}, "metadata_parse": None, "guessit": GUESS,
          "metadata": OPINION, "nfo_file": None, "nfo_ids": None, "probe": None, "errors": [],
          "calls": [{"tool": "guessit", "label": "filename", "command": "guessit -j -- LnO.26x02.mkv",
                     "output": '{"title": "LnO"}', "seconds": 0.2, "exit_code": 0}]}
    return {"ev": ev, "opinions": {"nfo": None, "metadata": OPINION, "guessit": GUESS},
            "verdict": {"identity": IDENTITY if auto else None, "conflict": False, "nfo": None},
            "score": (1, 2) if auto else None, "auto": auto}
