"""Verify the uploaded files and wait until TestPyPI's install index sees them."""

import hashlib
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

ATTEMPTS = 12
INTERVAL_SECONDS = 5
REQUEST_TIMEOUT_SECONDS = 10


def wait_for(url, ready, *, open_url=urllib.request.urlopen, sleep=time.sleep):
    """Poll a registry endpoint for bounded time; report the last HTTP status."""
    last_status = None
    for attempt in range(ATTEMPTS):
        try:
            with open_url(url, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                last_status = response.status
                content = response.read()
            if ready(content):
                return content
        except urllib.error.HTTPError as error:
            last_status = error.code
            if error.code != 404:
                raise
        if attempt + 1 < ATTEMPTS:
            sleep(INTERVAL_SECONDS)
    raise RuntimeError(f"{url} not ready after {ATTEMPTS} attempts; last HTTP status {last_status}")


class _IndexLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.filenames = set()

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            for name, value in attrs:
                if name == "href" and value:
                    path = urllib.parse.urlsplit(value).path
                    self.filenames.add(Path(urllib.parse.unquote(path)).name)


def verify(dist_dir, version, *, open_url=urllib.request.urlopen, sleep=time.sleep):
    files = list(Path(dist_dir).iterdir())
    assert len(files) == 2
    expected = {file.name: hashlib.sha256(file.read_bytes()).hexdigest() for file in files}
    url = f"https://test.pypi.org/pypi/atm-bd/{urllib.parse.quote(version, safe='')}/json"
    data = wait_for(url, lambda _: True, open_url=open_url, sleep=sleep)
    uploaded = {item["filename"]: item["digests"]["sha256"] for item in json.loads(data)["urls"]}
    assert expected == uploaded, "TestPyPI files or SHA-256 hashes differ from the build artifact"

    def index_has_files(content):
        parser = _IndexLinks()
        parser.feed(content.decode("utf-8"))
        return expected.keys() <= parser.filenames

    wait_for("https://test.pypi.org/simple/atm-bd/", index_has_files, open_url=open_url, sleep=sleep)
    print(f"TestPyPI {version} JSON and install index contain the verified distributions")


if __name__ == "__main__":
    verify(sys.argv[1], sys.argv[2])
