#!/usr/bin/env python3
"""
create_session.py - Create a fake xrdp-like systemd-logind session for testing.

Steps:
  1. Creates a session via org.freedesktop.login1.Manager.CreateSession, mimicking
     an xrdp session, and prints the D-Bus object path.
  2. Waits for a keypress, then closes the FIFO fd to transition the session to
     "closing" state (this is what logind detects when the session owner disconnects).
  3. Waits for a second keypress, then calls ReleaseSession and exits.

Must be run as root (CreateSession is a privileged D-Bus method).
"""

import os
import sys
import termios
import tty

import dbus

# Environment variable used to detect that we already re-exec'd under a scope.
_REEXECED_VAR = "_CREATE_SESSION_REEXECED"


def _reexec_in_transient_scope() -> None:
    """Re-exec this script inside a transient systemd scope unit.

    logind's CreateSession rejects callers whose PID is already associated
    with a session or user slice (error: "Already running in a session or user
    slice").  Running inside a transient *system* scope (not a session scope)
    satisfies logind's check.  systemd-run --scope inherits stdio so the TTY
    and interactive prompts still work.
    """
    if os.environ.get(_REEXECED_VAR) == "1":
        return  # already inside a scope, nothing to do

    env = os.environ.copy()
    env[_REEXECED_VAR] = "1"
    cmd = [
        "systemd-run",
        "--scope",        # transient scope, not a service — stdio is inherited
        "--",
        sys.executable,
        *sys.argv,
    ]
    # Replace the current process; the exec'd systemd-run creates a new scope
    # cgroup under system.slice (not session-N.scope) before exec'ing Python.
    os.execvpe("systemd-run", cmd, env)


def getch(prompt: str) -> str:
    """Print prompt and read a single character without echo.
    Falls back to input() if stdin is not a TTY (e.g. piped input).
    """
    print(prompt, flush=True)
    if not sys.stdin.isatty():
        return sys.stdin.readline()
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
    return ch


def main() -> None:
    _reexec_in_transient_scope()  # no-op if already inside a scope

    if os.getuid() != 0:
        print(
            "ERROR: CreateSession is a privileged D-Bus method and requires root.",
            file=sys.stderr,
        )
        sys.exit(1)

    bus = dbus.SystemBus()
    manager_obj = bus.get_object(
        "org.freedesktop.login1", "/org/freedesktop/login1"
    )
    manager = dbus.Interface(manager_obj, "org.freedesktop.login1.Manager")

    # Use the original (pre-sudo) user's UID so the session is created for
    # the real user rather than root.  Fall back to the current UID if
    # SUDO_UID is not set (e.g. when run as root without sudo).
    real_uid = int(os.environ.get("SUDO_UID", os.getuid()))
    uid = dbus.UInt32(real_uid)
    pid = dbus.UInt32(os.getpid())

    print("Creating session (mimicking xrdp, x11, remote)...")

    (
        session_id,
        object_path,
        runtime_path,
        fifo_fd_obj,
        _out_uid,
        _seat_id,
        _vtnr,
        existing,
    ) = manager.CreateSession(
        uid,                                   # uid
        pid,                                   # pid  (0 = use sender's PID)
        "xrdp",                                # service
        "x11",                                 # type
        "user",                                # class
        "",                                    # desktop
        "",                                    # seat_id  (no seat for remote)
        dbus.UInt32(0),                        # vtnr
        "",                                    # tty
        ":10",                                 # display
        dbus.Boolean(True),                    # remote
        "",                                    # remote_user
        "192.168.1.1",                         # remote_host
        dbus.Array([], signature="(sv)"),      # properties
    )

    # dbus-python returns Unix FDs as dbus.types.UnixFd; .take() transfers
    # ownership of the underlying integer fd to us.
    fifo_fd: int = fifo_fd_obj.take()

    print()
    print(f"  Session ID:   {session_id}")
    print(f"  Object path:  {object_path}")
    print(f"  Runtime path: {runtime_path}")
    print(f"  FIFO fd:      {fifo_fd}")
    print(f"  Existing:     {bool(existing)}")
    print()

    # ── Phase 1 ─────────────────────────────────────────────────────────────
    getch("Press any key to transition session to 'closing' (will close the FIFO)...")
    print()

    # Closing our end of the FIFO causes logind to detect EOF on its read end
    # and call session_stop(), which transitions the session state to "closing".
    os.close(fifo_fd)
    print(f"FIFO closed.  Session '{session_id}' should now be in 'closing' state.")
    print()

    # ── Phase 2 ─────────────────────────────────────────────────────────────
    getch("Press any key to release the session and exit...")
    print()

    manager.ReleaseSession(str(session_id))
    print(f"ReleaseSession('{session_id}') called.  Exiting.")


if __name__ == "__main__":
    main()
