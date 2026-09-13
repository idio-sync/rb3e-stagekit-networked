"""Checks that must pass before any service starts.

Configuration loading is deliberately read-only: it creates no directories and
opens no sockets. That leaves a gap. An appdata mount the container cannot write
is the expected first failure on any host whose ownership does not match the
image, and today it surfaces as a database error from deep inside startup.

These checks run once, before the supervisor, so a permissions problem stops the
process instead of half-starting it. Failures name the effective uid and gid and
the directory's owner, because "permission denied" without those is a guessing
game for whoever is reading the container log.
"""

import os
from pathlib import Path
import posixpath
import uuid


PROBE_NAME = ".rb3e-write-probe"


class PreflightError(Exception):
    """A precondition for starting the server is not met."""


def describe_identity():
    """Describe who this process is, in terms both platforms can answer."""
    parts = []
    for name in ("geteuid", "getegid"):
        getter = getattr(os, name, None)
        if getter is not None:
            parts.append("%s=%d" % (name[3:], getter()))
    if not parts:
        # Windows has no uid. The account name is the useful equivalent.
        parts.append("user=%s" % (os.environ.get("USERNAME") or "unknown"))
    return ", ".join(parts)


def _owner(path):
    try:
        info = os.stat(path)
    except OSError:
        return "unknown"
    parts = []
    for attribute in ("st_uid", "st_gid"):
        value = getattr(info, attribute, None)
        if value is not None:
            parts.append("%s=%d" % (attribute[3:], value))
    parts.append("mode=%o" % (info.st_mode & 0o777))
    return ", ".join(parts)


MOUNTINFO = "/proc/self/mountinfo"


def containing_mount(path, mountinfo=MOUNTINFO, realpath=None):
    """The mount point that contains path, or None when it cannot be read.

    Reads the kernel's mount table rather than using os.path.ismount, which
    compares a directory's device against its parent's. That test answers a
    different question and gets this one wrong twice: a bind mount within the
    same filesystem shares its parent's device and reads as "not a mount", and
    any subdirectory of a real mount reads the same way. Both would make the
    caller announce that history is about to be lost when it is not.
    """
    resolve = os.path.realpath if realpath is None else realpath
    try:
        with open(mountinfo, encoding="utf-8") as handle:
            entries = handle.readlines()
    except OSError:
        return None
    try:
        # Mount points are always POSIX paths. Normalising to them keeps the
        # comparison meaningful, and letting a test supply realpath keeps this
        # unit-testable on Windows, where os.path.realpath("/data") is
        # "C:\data" and would match nothing.
        target = posixpath.normpath(resolve(str(path)).replace(os.sep, "/"))
    except OSError:
        return None
    best = None
    for entry in entries:
        fields = entry.split()
        # id, parent, major:minor, root, mount point, ...
        if len(fields) < 5:
            continue
        point = (fields[4].replace("\\040", " ")
                 .replace("\\011", "\t")
                 .replace("\\012", "\n")
                 .replace("\\134", "\\"))
        if target == point or target.startswith(point.rstrip("/") + "/"):
            if best is None or len(point) > len(best):
                best = point
    return best


def _default_is_mount(path, mountinfo=MOUNTINFO):
    """Whether the data directory sits on a mount, or None when we cannot tell.

    True means something is mounted at or above the directory, so its contents
    outlive the container. False means it sits on the container's own root
    filesystem. None means this is not a host that publishes a mount table --
    Windows and macOS -- and the caller says nothing rather than guessing.
    """
    # Production always resolves symlinks; only tests inject realpath.
    point = containing_mount(path, mountinfo)
    if point is None:
        return None
    return point != "/"


def check_data_directory(path, is_mount=_default_is_mount, _open=None):
    """Make sure the data directory exists and is writable by this process.

    Creates the directory if it is absent, writes and removes a probe file, and
    returns a list of non-fatal warnings. Raises PreflightError when the server
    could not use the directory at all.
    """
    path = Path(path)
    opener = open if _open is None else _open
    identity = describe_identity()

    if path.exists() and not path.is_dir():
        raise PreflightError(
            "The data directory %s exists but is not a directory. Point "
            "data_dir (RB3E_DATA_DIR) at a directory, or move what is there."
            % path)

    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise PreflightError(
            "Could not create the data directory %s (%s). This process is %s; "
            "the parent directory is %s. On Unraid, appdata is owned by "
            "nobody:users (99:100); on another host, build the image with "
            "--build-arg PUID=... --build-arg PGID=... to match."
            % (path, error, identity, _owner(path.parent)))

    # A unique name opened with "x" (O_CREAT|O_EXCL). A fixed name opened "w"
    # would truncate and then delete whatever happened to be there already,
    # which is an unacceptable thing for a read-only-ish check to do.
    probe = path / ("%s-%s" % (PROBE_NAME, uuid.uuid4().hex))
    # One template, interpolated once at each raise site. Building it with a
    # first % pass would leave a "%s" template carrying whatever literal % came
    # from the path -- an unexpanded %APPDATA% in a template field, say -- and
    # the second pass would read it as a format spec and raise ValueError,
    # which main() does not catch.
    unwritable = (
        "The data directory %s is not writable (%s). This process is %s; the "
        "directory is %s. On Unraid, appdata is owned by nobody:users "
        "(99:100); on another host, build the image with --build-arg "
        "PUID=... --build-arg PGID=... to match.")
    try:
        handle = opener(str(probe), "x")
    except OSError as error:
        raise PreflightError(unwritable % (path, error, identity, _owner(path)))
    closed = False
    try:
        # A full or over-quota share fails at one of these three, and every
        # one must be a PreflightError: main catches nothing else, so a raw
        # OSError would become the traceback this check exists to prevent.
        # close() is inside the try, not suppressed in finally, because a
        # buffered write is only really delivered when the handle is flushed,
        # and swallowing that error is how a full disk passes a write check.
        handle.write("ok")
        handle.close()
        closed = True
    except OSError as error:
        raise PreflightError(unwritable % (path, error, identity, _owner(path)))
    finally:
        if not closed:
            try:
                handle.close()
            except Exception:
                pass
        try:
            probe.unlink()
        except OSError:
            # Written but not removable is odd, and not a reason to refuse to
            # run. Cleaning up in finally means a failed write leaves nothing
            # behind either. Only this probe is ever removed.
            pass

    warnings = []
    mounted = is_mount(path)
    if mounted is False:
        warnings.append(
            "The data directory %s is not a mount point, so session and play "
            "history live inside the container and are lost when it is "
            "replaced. Bind a host directory to it, for example "
            "-v /mnt/user/appdata/rb3e-dashboard:/data." % path)
    return warnings
