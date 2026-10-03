#!/usr/bin/env python3
"""FleetOps LIVE warehouse: 20 Nova Carter AMRs on aisle routes, Isaac Sim 6.0.1.
Robots cycle pick -> carry -> dropoff -> charge; battery and motor temp evolve;
telemetry is appended to telemetry.jsonl for the supervisor agent.

  DISPLAY=:1 python warehouse_live.py            # window on screen, runs until closed
  python warehouse_live.py --headless --steps 300
"""
import argparse, time
ap = argparse.ArgumentParser()
ap.add_argument("--headless", action="store_true")
ap.add_argument("--robots", type=int, default=20)
ap.add_argument("--steps", type=int, default=0, help="0 = run until the window is closed")
ap.add_argument("--record", type=float, default=0, help="seconds of cinematic video to render (implies headless)")
ap.add_argument("--res", default="1920x1080")
ap.add_argument("--only-frames", default="", help="comma list of frame numbers to render (preview)")
ap.add_argument("--bridge", default="", help="push the fleet to this sim bridge, e.g. http://127.0.0.1:3001")
ap.add_argument("--token-file", default="", help="file holding the bridge token (default ../secrets/bridge_token)")
ap.add_argument("--seed", type=int, default=7)
ap.add_argument("--cams", action="store_true", help="serve live camera streams (MJPEG) for the dashboard")
ap.add_argument("--cam-port", type=int, default=8212)
ap.add_argument("--cam-res", default="640x360")
args = ap.parse_args()

T0 = time.time()
def mark(msg): print(f"[fleetops {time.time()-T0:6.1f}s] {msg}", flush=True)

from isaacsim.simulation_app import SimulationApp
if args.record:
    args.headless = True
sim_app = SimulationApp({"headless": args.headless, "width": 1920, "height": 1080})
mark("app up")

import numpy as np, math, random, json
from isaacsim.core.api import World
from isaacsim.core.utils.stage import add_reference_to_stage
from isaacsim.core.prims import SingleXFormPrim
from isaacsim.storage.native import get_assets_root_path
import omni.usd
from pxr import Usd, UsdGeom, Gf, UsdLux, UsdShade, Sdf

random.seed(7)
CARTER = f"{get_assets_root_path()}/Isaac/Robots/NVIDIA/NovaCarter/nova_carter.usd"
world = World(stage_units_in_meters=1.0)
st = omni.usd.get_context().get_stage()

# ---------------- layout (metres) ----------------
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
SIM_DIR = os.path.join(HERE, "..", "sim") if os.path.isdir(os.path.join(HERE, "..", "sim")) else HERE
sys.path.insert(0, SIM_DIR)
from fleet import (Fleet, AIS, LEN, centers, XMIN, XMAX, YBOT, YTOP, BAY, EXIT_GAP, REJOIN,
                   PICK_X, DOCK_X, PICK_DX, DOCK_DX, NORTHBOUND)

STATIC_USD = os.path.join(HERE, "warehouse_static.usda")

def build_static(path):
    """Build the racks/totes/lines/lights on a private offline stage and save it.
    No viewport is attached to this stage, so ~500 prims take well under a second."""
    from pxr import Usd
    ws = Usd.Stage.CreateInMemory()
    root = UsdGeom.Xform.Define(ws, "/Warehouse")
    ws.SetDefaultPrim(root.GetPrim())
    UsdGeom.Scope.Define(ws, "/Warehouse/Looks")

    def pbr(name, color, rough=0.5, metal=0.0, emiss=None):
        path = f"/Warehouse/Looks/{name}"
        m = UsdShade.Material.Define(ws, path)
        sh = UsdShade.Shader.Define(ws, path + "/S")
        sh.CreateIdAttr("UsdPreviewSurface")
        sh.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*color))
        sh.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(rough)
        sh.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(metal)
        if emiss:
            sh.CreateInput("emissiveColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*emiss))
        m.CreateSurfaceOutput().ConnectToSource(sh.ConnectableAPI(), "surface")
        return m

    def place(prim, pos=None, scale=None, rot_xyz=None):
        xf = UsdGeom.Xformable(prim)
        if pos is not None:
            xf.AddTranslateOp().Set(Gf.Vec3d(*pos))
        if rot_xyz is not None:
            xf.AddRotateXYZOp().Set(Gf.Vec3f(*rot_xyz))
        if scale is not None:
            xf.AddScaleOp().Set(Gf.Vec3f(*scale))

    count = [0]
    def box(name, pos, size, mtl):
        c = UsdGeom.Cube.Define(ws, f"/Warehouse/{name}")
        c.CreateSizeAttr(1.0)
        place(c.GetPrim(), pos, size)
        UsdShade.MaterialBindingAPI.Apply(c.GetPrim()).Bind(mtl)
        count[0] += 1

    M = {
        "floor": pbr("floor", (0.10, 0.11, 0.13), 0.3),
        "steel": pbr("steel", (0.30, 0.33, 0.38), 0.4, 0.8),
        "beam":  pbr("beam", (0.95, 0.55, 0.10), 0.5, 0.3),
        "tb":    pbr("tote_blue", (0.12, 0.45, 0.85), 0.6),
        "ty":    pbr("tote_yellow", (0.95, 0.75, 0.12), 0.6),
        "tg":    pbr("tote_green", (0.15, 0.70, 0.45), 0.6),
        "tr":    pbr("tote_red", (0.85, 0.25, 0.25), 0.6),
        "line":  pbr("line", (0.85, 0.85, 0.20), 0.6, 0, (0.3, 0.3, 0.0)),
        "pick":  pbr("pick", (0.12, 0.55, 0.35), 0.5, 0, (0.0, 0.25, 0.12)),
        "dock":  pbr("dock", (0.10, 0.40, 0.60), 0.5, 0, (0.0, 0.15, 0.30)),
    }
    box("Floor", (13, 0, -0.02), (70, 46, 0.04), M["floor"])
    # the floor must be a physics surface, otherwise the Carters fall straight through it
    from pxr import UsdPhysics
    UsdPhysics.CollisionAPI.Apply(ws.GetPrimAtPath("/Warehouse/Floor"))
    for ai, cx in enumerate(centers):
        box(f"Line_{ai}", (cx, 0, 0.001), (0.12, LEN, 0.005), M["line"])
    box("CrossB", ((XMIN + XMAX) / 2, YBOT, 0.001), (XMAX - XMIN + 1, 0.12, 0.005), M["line"])
    box("CrossT", ((XMIN + XMAX) / 2, YTOP, 0.001), (XMAX - XMIN + 1, 0.12, 0.005), M["line"])
    rng = random.Random(3)
    k = 0
    for cx in centers:
        for side in (-1, 1):
            bx = cx + side * 1.9
            for by in np.linspace(-LEN / 2 + 1, LEN / 2 - 1, 5):
                for dx in (-0.8, 0.8):
                    box(f"U_{k}", (bx + dx, by, 1.4), (0.09, 0.09, 2.8), M["steel"]); k += 1
            for lvl in (0.55, 1.35, 2.15):
                box(f"B_{k}", (bx, 0, lvl), (1.7, LEN - 1, 0.06), M["beam"]); k += 1
            for by in np.linspace(-LEN / 2 + 1.5, LEN / 2 - 1.5, 13):
                for lvl in (0.72, 1.52, 2.32):
                    if rng.random() < 0.7:
                        box(f"T_{k}", (bx, by, lvl), (0.7, 0.7, 0.3),
                            M[rng.choice(["tb", "ty", "tg", "tr"])]); k += 1
    for i, x in enumerate(PICK_X):
        box(f"Pick_{i}", (x + 1.1, YBOT - BAY, 0.02), (3.4, 1.5, 0.05), M["pick"])
        for j, dx in enumerate(PICK_DX):
            box(f"PickSpur_{i}_{j}", (x + dx, YBOT - BAY / 2, 0.001), (0.08, BAY, 0.005), M["line"])
    for i, x in enumerate(DOCK_X):
        box(f"Dock_{i}", (x - 1.1, YTOP + BAY, 0.02), (3.4, 1.5, 0.05), M["dock"])
        for j, dx in enumerate(DOCK_DX):
            box(f"DockSpur_{i}_{j}", (x + dx, YTOP + BAY / 2, 0.001), (0.08, BAY, 0.005), M["line"])
    # drive-through bays (fleet.py): out of the slot onto an exit lane behind it, back to the main lane downstream
    for tag, xs_, dxs, y, sgn in (("P", PICK_X, PICK_DX, YBOT, 1), ("D", DOCK_X, DOCK_DX, YTOP, -1)):
        for i, x in enumerate(xs_):
            ys, ye = y - sgn * BAY, y - sgn * (BAY + EXIT_GAP)
            x_end = x + sgn * REJOIN
            box(f"Exit{tag}_{i}", ((x + x_end) / 2, ye, 0.001), (abs(x_end - x) + 0.08, 0.08, 0.005), M["line"])
            box(f"Rejoin{tag}_{i}", (x_end, (y + ye) / 2, 0.001), (0.08, abs(y - ye), 0.005), M["line"])
            for j, dx in enumerate(dxs):
                box(f"SlotOut{tag}_{i}_{j}", (x + dx, (ys + ye) / 2, 0.001), (0.08, EXIT_GAP, 0.005), M["line"])

    dome = UsdLux.DomeLight.Define(ws, "/Warehouse/Dome")
    dome.CreateIntensityAttr(650.0); dome.CreateColorAttr(Gf.Vec3f(0.8, 0.85, 1.0))
    key = UsdLux.DistantLight.Define(ws, "/Warehouse/Key")
    key.CreateIntensityAttr(2000.0)
    place(key.GetPrim(), rot_xyz=(-40, 25, 0))
    for ai, cx in enumerate(centers):
        r = UsdLux.RectLight.Define(ws, f"/Warehouse/Lx_{ai}")
        r.CreateIntensityAttr(8000.0); r.CreateWidthAttr(1.2); r.CreateHeightAttr(LEN)
        place(r.GetPrim(), pos=(cx, 0, 4.2))
    # only touch the file when the scene actually changed: rewriting it after Isaac opens it
    # triggers a "Base USD files have been changed" popup in the GUI
    text = ws.GetRootLayer().ExportToString()
    try:
        old = open(path).read()
    except FileNotFoundError:
        old = None
    if text != old:
        with open(path, "w") as fh:
            fh.write(text)
    return count[0]

n_static = build_static(STATIC_USD)
mark(f"static warehouse written: {n_static} prims")
add_reference_to_stage(usd_path=STATIC_USD, prim_path="/World/Warehouse")
mark("warehouse referenced into the live stage")

# ---------------- robots ----------------
FLEET = Fleet(args.robots, seed=args.seed)   # traffic logic lives in fleet.py (tested headless by test_fleet.py)
bots = FLEET.bots
for b in bots:
    p = f"/World/Carter_{b.i}"
    add_reference_to_stage(usd_path=CARTER, prim_path=p)
    b.xf = SingleXFormPrim(p, position=np.array(b.pos))

# a red beacon floats above any robot with an error code, so faults are visible on screen
_beacon_mat = UsdShade.Material.Define(st, "/World/BeaconLook")
_bs = UsdShade.Shader.Define(st, "/World/BeaconLook/S"); _bs.CreateIdAttr("UsdPreviewSurface")
_bs.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(1.0, 0.1, 0.08))
_bs.CreateInput("emissiveColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(4.0, 0.2, 0.1))
_beacon_mat.CreateSurfaceOutput().ConnectToSource(_bs.ConnectableAPI(), "surface")
beacons = []
for b in bots:
    sph = UsdGeom.Sphere.Define(st, f"/World/Beacon_{b.i}"); sph.CreateRadiusAttr(0.22)
    UsdShade.MaterialBindingAPI.Apply(sph.GetPrim()).Bind(_beacon_mat)
    beacons.append((sph, UsdGeom.Xformable(sph).AddTranslateOp(), UsdGeom.Imageable(sph)))

def apply_poses():
    for b, (sph, op, img) in zip(bots, beacons):
        b.xf.set_world_pose(position=np.array(b.pos),
                            orientation=np.array([math.cos(b.yaw / 2), 0, 0, math.sin(b.yaw / 2)]))
        if b.error:
            op.Set(Gf.Vec3d(b.pos[0], b.pos[1], 1.25)); img.MakeVisible()
        else:
            img.MakeInvisible()

def min_spacing():
    return FLEET.min_spacing()
mark(f"{len(bots)} carters referenced")

# Each Carter carries ~6 stereo fisheye cameras; unused for a fleet view and very heavy to render.
cams = [prim for prim in st.Traverse()
        if prim.GetTypeName() == "Camera" and prim.GetPath().pathString.startswith("/World/Carter_")]
for prim in cams:
    prim.SetActive(False)
ncam = len(cams)
mark(f"deactivated {ncam} onboard cameras")

# The fleet is driven kinematically (pose set every frame), so PhysX must not also simulate the
# Carters: their chassis/wheel rigid bodies would fall under gravity while the root stays put.
from pxr import UsdPhysics, PhysxSchema
n_rb = n_j = n_art = 0
for prim in st.Traverse():
    if not prim.GetPath().pathString.startswith("/World/Carter_"):
        continue
    if prim.HasAPI(UsdPhysics.RigidBodyAPI):
        UsdPhysics.RigidBodyAPI(prim).CreateRigidBodyEnabledAttr(False); n_rb += 1
    if prim.IsA(UsdPhysics.Joint):
        UsdPhysics.Joint(prim).CreateJointEnabledAttr(False); n_j += 1
    if prim.HasAPI(UsdPhysics.ArticulationRootAPI):
        PhysxSchema.PhysxArticulationAPI.Apply(prim).CreateArticulationEnabledAttr(False); n_art += 1
mark(f"carter physics off: {n_rb} rigid bodies, {n_j} joints, {n_art} articulations")

world.reset()
mark("physics reset")

def chassis_z():
    """World height of every Carter's chassis link, the part that visibly sank."""
    out = []
    for b in bots:
        root = st.GetPrimAtPath(b.xf.prim_path)
        for prim in Usd.PrimRange(root):
            if prim.GetName() == "chassis_link":
                m = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
                out.append(m.ExtractTranslation()[2]); break
    return out

try:
    from isaacsim.core.utils.viewports import set_camera_view
    set_camera_view(eye=[12.5, -30.0, 13.0], target=[12.5, -6.0, 0.0])
except Exception as e:
    mark(f"viewport framing skipped: {e}")

# ---------------- cinematic recording ----------------
def smooth(u):
    u = max(0.0, min(1.0, u)); return u * u * (3 - 2 * u)

def lerp(a, b, u):
    return [a[i] + (b[i] - a[i]) * u for i in range(3)]

CX = (XMIN + XMAX) / 2
_cam = {"follow": None, "eye": None, "tgt": None, "shot": -1}

def cine_camera(t, total):
    """Four shots across the clip: crane-down establishing, a chase cam behind one robot,
    a tracking shot past the pick bays, and a pull-out to a high hero view of the fleet."""
    q = total / 4.0
    shot, u = min(3, int(t // q)), smooth((t % q) / q)
    ease = True
    if shot == 0:   # establishing: high and far, crane down and push in
        eye = lerp([CX - 30, -46, 30], [CX - 8, -30, 12], u)
        tgt = lerp([CX, 0, 0], [CX, -6, 0.8], u)
        ease = False
    elif shot == 1: # chase cam: behind a robot along its direction of travel, so always in the open lane
        if _cam["follow"] is None:   # pick a robot well inside an aisle when the shot starts
            mid = [b for b in bots if abs(b.pos[1]) < 6 and b.wp] or bots
            _cam["follow"] = mid[0]
        b = _cam["follow"]
        h = np.array([math.cos(b.yaw), math.sin(b.yaw)])
        eye = [b.pos[0] - 3.8 * h[0], b.pos[1] - 3.8 * h[1], 1.35]
        tgt = [b.pos[0] + 2.5 * h[0], b.pos[1] + 2.5 * h[1], 0.45]
    elif shot == 2: # tracking shot along the busy bottom lane, past the pick bays
        x = XMIN - 4 + (XMAX - XMIN + 8) * u
        eye = [x - 3.0, YBOT - 7.5, 3.2]
        tgt = [x + 2.0, YBOT + 0.5, 0.3]
        ease = False
    else:           # pull out from low over the lanes to a high three-quarter hero view
        eye = lerp([CX, YBOT - 5, 3.0], [CX - 6, -50, 30], u)
        tgt = lerp([CX, YBOT + 3, 0.3], [CX, -3, 0.0], u)
        ease = False
    if ease and _cam["eye"] is not None and _cam["shot"] == shot:   # low-pass the chase cam so turns glide, not snap
        k = 0.12
        eye = [_cam["eye"][i] + (eye[i] - _cam["eye"][i]) * k for i in range(3)]
        tgt = [_cam["tgt"][i] + (tgt[i] - _cam["tgt"][i]) * k for i in range(3)]
    _cam["eye"], _cam["tgt"], _cam["shot"] = eye, tgt, shot
    return eye, tgt

if args.record:
    import omni.replicator.core as rep
    from isaacsim.core.utils.viewports import set_camera_view
    W_, H_ = map(int, args.res.split("x"))
    cam = UsdGeom.Camera.Define(st, "/World/CineCam")
    cam.CreateFocalLengthAttr(22.0)
    cam.CreateClippingRangeAttr(Gf.Vec2f(0.05, 500.0))
    rp = rep.create.render_product("/World/CineCam", (W_, H_))
    out_dir = "/home/ferbin/warehouse-isaac/rec"
    writer = rep.WriterRegistry.get("BasicWriter")
    writer.initialize(output_dir=out_dir, rgb=True)
    writer.attach([rp])
    rep.orchestrator.set_capture_on_play(False)
    n = int(args.record * 30)
    only = {int(x) for x in args.only_frames.split(",") if x}
    dt = 1 / 30.0
    mark(f"recording {n} frames at {W_}x{H_} -> {out_dir}")
    t_rec = time.time()
    for f in range(n):
        FLEET.step(dt)
        apply_poses()
        world.step(render=False)
        eye, tgt = cine_camera(f * dt, args.record)   # every frame, so the chase smoothing is right
        if only and f not in only:
            continue
        set_camera_view(eye=eye, target=tgt, camera_prim_path="/World/CineCam")
        rep.orchestrator.step(rt_subframes=2, delta_time=0.0)
        if f % 150 == 0:
            el = time.time() - t_rec
            mark(f"frame {f}/{n}, {el:.0f}s elapsed, ~{el / (f + 1) * (n - f - 1):.0f}s left")
    rep.orchestrator.wait_until_complete()
    mark("RECORDING_DONE")
    sim_app.close()
    raise SystemExit(0)

# ---------------- live camera streams for the dashboard ----------------
CAMS = None
if args.cams:
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import cv2
    import omni.replicator.core as rep
    from isaacsim.core.utils.viewports import set_camera_view
    CX_ = (XMIN + XMAX) / 2
    CW, CH = map(int, args.cam_res.split("x"))
    FIXED = {  # name: (label, eye, target)
        "overview": ("Isaac · overview", (CX_ - 6, -50, 30), (CX_, -3, 0)),
        "aisle":    ("Isaac · aisle 2", (centers[2], -17, 2.2), (centers[2], 6, 0.4)),
        "bays":     ("Isaac · pack bays", (centers[1], YBOT - 10, 4.5), (centers[1], YBOT, 0.2)),
    }

    class Cams:
        def __init__(self):
            self.lock = threading.Lock()
            self.jpeg, self.rgb, self.seen = {}, {}, {}
            self.follow_id, self.follow_eye, self.follow_tgt = 0, None, None
            for name, (label, eye, tgt) in list(FIXED.items()) + [("follow", ("chase", (0, 0, 2), (1, 0, 0)))]:
                UsdGeom.Camera.Define(st, f"/World/Cam_{name}").CreateFocalLengthAttr(18.0 if name != "follow" else 16.0)
                set_camera_view(eye=list(eye), target=list(tgt), camera_prim_path=f"/World/Cam_{name}")
                rp = rep.create.render_product(f"/World/Cam_{name}", (CW, CH))
                ann = rep.AnnotatorRegistry.get_annotator("rgb"); ann.attach([rp])
                self.rgb[name] = ann

        def watching(self, name):
            return time.time() - self.seen.get(name, 0) < 3.0

        def update(self, step):
            b = bots[self.follow_id]
            h = (math.cos(b.yaw), math.sin(b.yaw))
            eye = [b.pos[0] - 3.6 * h[0], b.pos[1] - 3.6 * h[1], 1.5]
            tgt = [b.pos[0] + 2.5 * h[0], b.pos[1] + 2.5 * h[1], 0.4]
            if self.follow_eye is not None:   # glide through turns
                eye = [self.follow_eye[i] + (eye[i] - self.follow_eye[i]) * 0.15 for i in range(3)]
                tgt = [self.follow_tgt[i] + (tgt[i] - self.follow_tgt[i]) * 0.15 for i in range(3)]
            self.follow_eye, self.follow_tgt = eye, tgt
            set_camera_view(eye=eye, target=tgt, camera_prim_path="/World/Cam_follow")
            if step % 3:                      # encode about every 3rd frame, only for streams someone watches
                return
            for name, ann in self.rgb.items():
                if not self.watching(name):
                    continue
                try:
                    img = ann.get_data()
                except Exception:
                    continue
                if img is None or getattr(img, "size", 0) == 0:
                    continue
                ok, buf = cv2.imencode(".jpg", cv2.cvtColor(np.asarray(img)[..., :3], cv2.COLOR_RGB2BGR),
                                       [cv2.IMWRITE_JPEG_QUALITY, 78])
                if ok:
                    with self.lock:
                        self.jpeg[name] = buf.tobytes()

    CAMS = Cams()

    class CamHandler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            path = self.path.split("?")[0].rstrip("/")
            if path == "/cams":
                body = json.dumps({"fixed": [{"id": k, "label": v[0]} for k, v in FIXED.items()],
                                   "robots": len(bots), "follow": CAMS.follow_id, "res": [CW, CH]}).encode()
                self.send_response(200); self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body); return
            parts = path.split("/")[1:]       # stream/<cam>  or  stream/robot/<id>
            if len(parts) >= 2 and parts[0] in ("stream", "snap"):
                if parts[1] == "robot" and len(parts) == 3 and parts[2].isdigit():
                    CAMS.follow_id = max(0, min(len(bots) - 1, int(parts[2]))); name = "follow"
                else:
                    name = parts[1]
                if name not in CAMS.rgb:
                    self.send_response(404); self.end_headers(); return
                CAMS.seen[name] = time.time()
                if parts[0] == "snap":
                    for _ in range(40):
                        if name in CAMS.jpeg: break
                        time.sleep(0.05)
                    with CAMS.lock: frame = CAMS.jpeg.get(name, b"")
                    self.send_response(200 if frame else 503); self.send_header("Content-Type", "image/jpeg")
                    self.send_header("Content-Length", str(len(frame))); self.end_headers(); self.wfile.write(frame); return
                self.send_response(200)
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                self.send_header("Cache-Control", "no-store"); self.end_headers()
                last = None
                try:
                    while True:
                        CAMS.seen[name] = time.time()
                        with CAMS.lock: frame = CAMS.jpeg.get(name)
                        if frame is not None and frame is not last:
                            self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                             + str(len(frame)).encode() + b"\r\n\r\n" + frame + b"\r\n")
                            self.wfile.flush(); last = frame
                        time.sleep(0.08)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    return
            self.send_response(404); self.end_headers()

    _srv = ThreadingHTTPServer(("127.0.0.1", args.cam_port), CamHandler)
    threading.Thread(target=_srv.serve_forever, daemon=True).start()
    mark(f"camera streams on http://127.0.0.1:{args.cam_port}/stream/<overview|aisle|bays|robot/N>")

push = None
if args.bridge:
    from pusher import Pusher
    tok_file = args.token_file or os.path.join(HERE, "..", "secrets", "bridge_token")
    token = open(tok_file).read().strip() if os.path.exists(tok_file) else os.environ.get("BRIDGE_TOKEN", "")
    push = Pusher(FLEET, args.bridge, token, source="isaac")
    mark(f"pushing to {args.bridge} (token {'set' if token else 'MISSING'})")
tf = open(os.path.join(HERE, "telemetry.jsonl"), "w")
mark(f"FleetOps live: {len(bots)} Carters running")
step, t_run, t_prev = 0, time.time(), time.time()
closest_ever, overlap_steps = 1e9, 0
while sim_app.is_running():
    world.step(render=True)
    now = time.time()
    dt = min(0.1, max(1 / 120, now - t_prev)); t_prev = now   # real time, whatever the GUI frame rate
    if push:
        for c in push.apply():
            mark(f"demo command from the bridge: {c}")
    FLEET.step(dt)
    if push:
        push.tick(now)
    apply_poses()
    if CAMS:
        CAMS.update(step)
    sp = min_spacing()
    closest_ever = min(closest_ever, sp)
    overlap_steps += sp < 0.6   # Carter footprint is ~0.6 m: centres closer than that means overlap
    if step % 10 == 0:
        rec = {"t": step, "sec": round(time.time() - t_run, 1),
               "robots": [b.telem() for b in bots],
               "events": [{"type": "health", "robot": b.i, "detail": b.error} for b in bots if b.error]}
        tf.write(json.dumps(rec) + "\n"); tf.flush()
    if step % 300 == 0:
        zs = chassis_z() or [float("nan")]
        mark(f"step {step}, {(step + 1) / max(1e-6, time.time() - t_run):.1f} steps/s, sim t={FLEET.t:.0f}s, "
             + (f"pushes ok {push.sent} failed {push.failed}, " if push else "") +
             f"chassis z min {min(zs):.2f} max {max(zs):.2f} (n={len(zs)}), "
             f"closest ever {closest_ever:.2f} m, overlap steps {overlap_steps}")
    step += 1
    if args.steps and step >= args.steps:
        break
tf.close()
sim_app.close()
