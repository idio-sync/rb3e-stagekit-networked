"""Container health check: ask the server's own health endpoint.

A separate script rather than a long shell line in the Dockerfile, so it can be
read, tested, and fixed like any other code.

It uses only the standard library, so the image needs no curl or wget, and it
probes the loopback address even when the server is bound to every interface:
the check runs inside the container and has no business leaving it.

Exit status 0 means healthy. Anything else tells Docker to retry, and
eventually to mark the container unhealthy.
"""

import json
import os
import sys
import urllib.request


def target(environ=None):
    environ = os.environ if environ is None else environ
    host = (environ.get("RB3E_WEB_HOST") or "").strip()
    if host in ("", "0.0.0.0", "::", "*"):
        # A wildcard bind is not an address to connect to.
        host = "127.0.0.1"
    port = (environ.get("RB3E_WEB_PORT") or "").strip() or "8321"
    return "http://%s:%s/api/health" % (host, port)


def check(url, timeout=4.0, opener=None):
    """Return (ok, detail). Health needs no token, by design."""
    opener = urllib.request.urlopen if opener is None else opener
    try:
        response = opener(url, timeout=timeout)
        body = response.read()
    except Exception as error:
        return False, "%s: %s" % (url, error)
    status = getattr(response, "status", None)
    if status is None:
        status = response.getcode()
    if status != 200:
        return False, "%s returned HTTP %s" % (url, status)
    try:
        document = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeError) as error:
        return False, "%s returned an unreadable body: %s" % (url, error)
    if not document.get("running"):
        # The port answers before every service is up, so a reachable server
        # that is not actually running is not yet healthy.
        return False, "%s reports the server is not running" % url
    return True, "ok"


def main(environ=None):
    url = target(environ)
    ok, detail = check(url)
    if not ok:
        sys.stderr.write(detail + "\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
