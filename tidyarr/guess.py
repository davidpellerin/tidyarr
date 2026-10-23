"""First pass: parse a release filename with guessit, called as a library (so it works in a frozen build)."""
import json
import time

from guessit import guessit
from guessit.jsonutils import GuessitEncoder

from tidyarr import calls

# guessit reports countries by name (e.g. 'UNITED STATES'); TMDB uses ISO codes. Only the common ones.
COUNTRY_CODES = {
    "UNITED STATES": "US", "UNITED KINGDOM": "GB", "CANADA": "CA", "AUSTRALIA": "AU", "INDIA": "IN",
    "POLAND": "PL", "FRANCE": "FR", "GERMANY": "DE", "JAPAN": "JP", "SOUTH KOREA": "KR",
}


def guess(path, label="filename"):
    """Return guessit's fields for `path`, plus `country_code` when the country is one we know."""
    started = time.monotonic()
    # Serialized the way `guessit -j` prints it, so the transcript shows the equivalent CLI command and its output.
    out = json.dumps(guessit(path), cls=GuessitEncoder, ensure_ascii=False)
    calls.record("guessit", label, ["guessit", "-j", "--", path], out, time.monotonic() - started, 0)
    parsed = json.loads(out)
    country = parsed.get("country")
    if country:
        parsed["country_code"] = COUNTRY_CODES.get(str(country).upper())
    return parsed
