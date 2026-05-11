#!/usr/bin/env python3
"""
monitor_session.py - Monitor mutable properties of a systemd-logind session.

Usage:
  monitor_session.py monitor <session_dbus_path>
  monitor_session.py poll   <session_dbus_path>

  <session_dbus_path> example: /org/freedesktop/login1/session/_31

Modes:
  monitor  Subscribe to the org.freedesktop.DBus.Properties.PropertiesChanged
           D-Bus signal for the given session object and print each event as it
           arrives.  Exits on Ctrl-C.

  poll     Read the mutable properties once per second.  Prints the initial
           snapshot, then on every subsequent poll either prints the diff or
           reports that nothing has changed.  Exits on Ctrl-C or when the
           session object disappears.

Only the seven non-const (emits-change) properties are tracked:
  Active, IdleHint, IdleSinceHint, IdleSinceHintMonotonic,
  LockedHint, State, Type
"""

import sys
import time
import datetime

import dbus
import dbus.mainloop.glib
from gi.repository import GLib


BUS_NAME       = "org.freedesktop.login1"
SESSION_IFACE  = "org.freedesktop.login1.Session"
PROPS_IFACE    = "org.freedesktop.DBus.Properties"

# Properties that are NOT marked "const" in the introspection / documentation
# and therefore can change at runtime.
MUTABLE_PROPS = [
    "Active",
    "IdleHint",
    "IdleSinceHint",
    "IdleSinceHintMonotonic",
    "LockedHint",
    "State",
    "Type",
]


# ── Helpers ──────────────────────────────────────────────────────────────────

def now_str() -> str:
    return datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]


def get_mutable_props(props_iface: dbus.Interface) -> dict:
    """Return a plain-Python dict of the mutable session properties."""
    all_props = props_iface.GetAll(SESSION_IFACE)
    return {k: all_props[k] for k in MUTABLE_PROPS if k in all_props}


def normalise(value) -> str:
    """Stable string representation for comparison across dbus types."""
    return str(value)


def format_snapshot(props: dict) -> str:
    lines = [f"  {k}: {props[k]}" for k in MUTABLE_PROPS if k in props]
    return "\n".join(lines)


def get_props_iface(session_path: str) -> tuple[dbus.SystemBus, dbus.Interface]:
    bus = dbus.SystemBus()
    try:
        obj = bus.get_object(BUS_NAME, session_path)
        return bus, dbus.Interface(obj, PROPS_IFACE)
    except dbus.DBusException as exc:
        print(f"Error accessing session {session_path}: {exc}", file=sys.stderr)
        sys.exit(1)


# ── Monitor mode ─────────────────────────────────────────────────────────────

def run_monitor(session_path: str) -> None:
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus, props_iface = get_props_iface(session_path)

    # Print initial state so the user has a baseline.
    try:
        initial = get_mutable_props(props_iface)
    except dbus.DBusException as exc:
        print(f"Error reading initial properties: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"[{now_str()}] Monitoring PropertiesChanged on: {session_path}")
    print(f"[{now_str()}] Initial mutable properties:")
    print(format_snapshot(initial))
    print()

    def on_props_changed(
        interface_name, changed_props, invalidated_props, **_kwargs
    ) -> None:
        if interface_name != SESSION_IFACE:
            return
        ts = now_str()
        print(f"[{ts}] PropertiesChanged signal received!")
        if changed_props:
            print("  Changed:")
            for k, v in changed_props.items():
                print(f"    {k}: {v}")
        if invalidated_props:
            print(f"  Invalidated: {list(invalidated_props)}")
        print()

    bus.add_signal_receiver(
        on_props_changed,
        signal_name="PropertiesChanged",
        dbus_interface=PROPS_IFACE,
        bus_name=BUS_NAME,
        path=session_path,
    )

    loop = GLib.MainLoop()
    print("Waiting for signals... (Ctrl-C to stop)\n")
    try:
        loop.run()
    except KeyboardInterrupt:
        print("\nInterrupted.")


# ── Poll mode ─────────────────────────────────────────────────────────────────

def run_poll(session_path: str) -> None:
    _bus, props_iface = get_props_iface(session_path)

    print(f"[{now_str()}] Polling properties of: {session_path}  (1 s interval)")
    print(f"[{now_str()}] Mutable properties (initial):")

    try:
        prev = get_mutable_props(props_iface)
    except dbus.DBusException as exc:
        print(f"Error reading initial properties: {exc}", file=sys.stderr)
        sys.exit(1)

    print(format_snapshot(prev))
    print()

    try:
        while True:
            time.sleep(1)

            try:
                current = get_mutable_props(props_iface)
            except dbus.DBusException as exc:
                print(f"[{now_str()}] Session gone or error: {exc}")
                break

            diff = {
                k: (normalise(prev.get(k)), normalise(current[k]))
                for k in current
                if normalise(current[k]) != normalise(prev.get(k))
            }

            if diff:
                print(f"[{now_str()}] Properties changed:")
                for k, (old_val, new_val) in diff.items():
                    print(f"  {k}: {old_val} → {new_val}")
                print()
                prev = dict(current)
            else:
                print(f"[{now_str()}] No properties changed.")

    except KeyboardInterrupt:
        print("\nInterrupted.")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    if len(sys.argv) != 3:
        print(
            f"Usage: {sys.argv[0]} monitor|poll <session_dbus_path>",
            file=sys.stderr,
        )
        sys.exit(1)

    mode, session_path = sys.argv[1], sys.argv[2]

    if mode == "monitor":
        run_monitor(session_path)
    elif mode == "poll":
        run_poll(session_path)
    else:
        print(
            f"Unknown mode '{mode}'. Use 'monitor' or 'poll'.", file=sys.stderr
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
