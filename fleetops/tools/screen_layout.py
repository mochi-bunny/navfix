#!/usr/bin/env python3
"""Tile the demo on the GB10's screen: Isaac Sim on the left, the dashboard on the right.

  python3 screen_layout.py              open the dashboard window if needed, then tile
  python3 screen_layout.py --split 0.55 Isaac's share of the screen width

Uses the window manager's standard EWMH requests through libX11 (no xdotool on this box), so GNOME
honours them like a user action. Needs DISPLAY / XAUTHORITY of the logged-in desktop.
"""
import argparse, ctypes, ctypes.util, os, subprocess, time

ap = argparse.ArgumentParser()
ap.add_argument("--split", type=float, default=0.55)
ap.add_argument("--url", default="http://localhost:8095/")
ap.add_argument("--no-open", action="store_true")
args = ap.parse_args()
os.environ.setdefault("DISPLAY", ":1")
os.environ.setdefault("XAUTHORITY", f"/run/user/{os.getuid()}/gdm/Xauthority")

X = ctypes.cdll.LoadLibrary(ctypes.util.find_library("X11"))
X.XOpenDisplay.restype = ctypes.c_void_p
X.XDefaultRootWindow.argtypes = [ctypes.c_void_p]; X.XDefaultRootWindow.restype = ctypes.c_ulong
X.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]; X.XInternAtom.restype = ctypes.c_ulong
X.XDisplayWidth.argtypes = X.XDisplayHeight.argtypes = [ctypes.c_void_p, ctypes.c_int]
X.XFlush.argtypes = [ctypes.c_void_p]


class CM(ctypes.Structure):
    _fields_ = [("type", ctypes.c_int), ("serial", ctypes.c_ulong), ("send_event", ctypes.c_int),
                ("display", ctypes.c_void_p), ("window", ctypes.c_ulong), ("message_type", ctypes.c_ulong),
                ("format", ctypes.c_int), ("data", ctypes.c_long * 5)]


class EV(ctypes.Union):
    _fields_ = [("xclient", CM), ("pad", ctypes.c_long * 24)]


X.XSendEvent.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_long, ctypes.POINTER(EV)]
d = X.XOpenDisplay(None)
if not d:
    raise SystemExit("cannot open the display (DISPLAY / XAUTHORITY?)")
root = X.XDefaultRootWindow(d)
A = lambda n: X.XInternAtom(d, n, 0)
SW, SH = X.XDisplayWidth(d, 0), X.XDisplayHeight(d, 0)


def send(win, msg, data):
    ev = EV(); c = ev.xclient
    c.type, c.send_event, c.display, c.window, c.format = 33, 1, d, win, 32
    c.message_type = A(msg)
    for i, v in enumerate(data):
        c.data[i] = v
    X.XSendEvent(d, root, 0, (1 << 20) | (1 << 19), ctypes.byref(ev))


def find(substr):
    """Largest top-level window whose title contains substr (case-insensitive)."""
    import re
    out = subprocess.run(["xwininfo", "-root", "-tree"], capture_output=True, text=True).stdout
    best = None
    for m in re.finditer(r'^\s*(0x[0-9a-f]+) "([^"]*)": \(([^)]*)\)\s+(\d+)x(\d+)', out, re.M):
        wid, title, cls, w, h = int(m.group(1), 16), m.group(2), m.group(3), int(m.group(4)), int(m.group(5))
        if "mutter-x11-frames" in cls or not cls.strip():        # GNOME's frame around a window, not the window
            continue
        if substr.lower() in title.lower() and w > 200 and h > 200 and (best is None or w * h > best[1]):
            best = (wid, w * h)
    return best[0] if best else None


def place(win, x, y, w, h):
    # un-maximise / un-fullscreen first, or GNOME ignores the move
    send(win, b"_NET_WM_STATE", [0, A(b"_NET_WM_STATE_MAXIMIZED_VERT"), A(b"_NET_WM_STATE_MAXIMIZED_HORZ"), 2, 0])
    send(win, b"_NET_WM_STATE", [0, A(b"_NET_WM_STATE_FULLSCREEN"), 0, 2, 0])
    flags = (0xF << 8) | (2 << 12) | 10            # x, y, w, h given; source = pager; gravity static
    send(win, b"_NET_MOVERESIZE_WINDOW", [flags, x, y, w, h])
    send(win, b"_NET_ACTIVE_WINDOW", [2, 0, 0, 0, 0])
    X.XFlush(d)


# match the dashboard by ITS OWN page title only: never "any Firefox window" (that grabbed someone's browser once)
DASH_TITLE = "Fleet & Field"
dash = find(DASH_TITLE)
if dash is None and not args.no_open:
    prof = os.path.expanduser("~/fleetops/run/ff-dash")
    os.makedirs(prof, exist_ok=True)
    subprocess.Popen(["firefox", "--new-instance", "--profile", prof, args.url],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    for _ in range(60):
        time.sleep(0.5)
        dash = find(DASH_TITLE)
        if dash:
            time.sleep(1.5)
            break
isaac = find("Isaac Sim")
split = int(SW * args.split) if isaac else 0
top = 32                                             # GNOME top bar
if isaac:
    place(isaac, 0, top, split, SH - top)
if dash:
    place(dash, split, top, SW - split, SH - top)
print(f"screen {SW}x{SH} | isaac: {'%#x' % isaac if isaac else 'not open'} | dashboard: {'%#x' % dash if dash else 'not open'}")
