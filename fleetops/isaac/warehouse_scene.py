#!/usr/bin/env python3
"""FleetOps warehouse — sleek build. Shelving uprights+beams, coloured totes,
pick stations, charge docks, polished floor, Nova Carter AMRs. Renders a hero shot."""
from isaacsim.simulation_app import SimulationApp
sim_app = SimulationApp({"headless": True})
import numpy as np, math, random, sys
from isaacsim.core.api import World
from isaacsim.core.utils.stage import add_reference_to_stage
from isaacsim.core.prims import SingleXFormPrim
from isaacsim.storage.native import get_assets_root_path
import omni.usd, omni.replicator.core as rep
from pxr import UsdGeom, Gf, UsdLux, UsdShade, Sdf

random.seed(3)
A = get_assets_root_path()
CARTER = f"{A}/Isaac/Robots/NVIDIA/NovaCarter/nova_carter.usd"
w = World(stage_units_in_meters=1.0)
st = omni.usd.get_context().get_stage()

# ---------- materials ----------
def pbr(path, color, rough=0.5, metal=0.0, emiss=None):
    mtl = UsdShade.Material.Define(st, path)
    sh = UsdShade.Shader.Define(st, path+"/S")
    sh.CreateIdAttr("UsdPreviewSurface")
    sh.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*color))
    sh.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(rough)
    sh.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(metal)
    if emiss:
        sh.CreateInput("emissiveColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*emiss))
    mtl.CreateSurfaceOutput().ConnectToSource(sh.ConnectableAPI(), "surface")
    return mtl
M = {
 "floor":  pbr("/World/M/floor",(0.10,0.11,0.13),0.35,0.0),
 "steel":  pbr("/World/M/steel",(0.30,0.33,0.38),0.4,0.8),
 "beam":   pbr("/World/M/beam",(0.95,0.55,0.10),0.5,0.3),   # orange racking beams
 "tote_b": pbr("/World/M/tb",(0.12,0.45,0.85),0.6),
 "tote_y": pbr("/World/M/ty",(0.95,0.75,0.12),0.6),
 "tote_g": pbr("/World/M/tg",(0.15,0.70,0.45),0.6),
 "tote_r": pbr("/World/M/tr",(0.85,0.25,0.25),0.6),
 "pick":   pbr("/World/M/pick",(0.12,0.55,0.35),0.5,0.0,emiss=(0.0,0.25,0.12)),
 "dock":   pbr("/World/M/dock",(0.10,0.40,0.60),0.5,0.0,emiss=(0.0,0.15,0.3)),
 "line":   pbr("/World/M/line",(0.85,0.85,0.2),0.6,0.0,emiss=(0.3,0.3,0.0)),
}
def bind(prim_path, mtl):
    UsdShade.MaterialBindingAPI(st.GetPrimAtPath(prim_path)).Bind(mtl)

def box(path, pos, size, mtl):
    c = UsdGeom.Cube.Define(st, path); c.CreateSizeAttr(1.0)
    SingleXFormPrim(path, position=np.array(pos,dtype=float), scale=np.array(size,dtype=float))
    bind(path, M[mtl]); return path

# ---------- floor ----------
box("/World/Floor",(10,0,-0.02),(60,42,0.04),"floor")
# guide lines down the drive lanes
AISLES=6; PITCH=5.0; LEN=26.0
centers=[a*PITCH for a in range(AISLES)]
AIDX=list(range(AISLES))
for ai,cx in enumerate(centers):
    box(f"/World/Line_{ai}",(cx,0,0.001),(0.12,LEN,0.005),"line")

# ---------- racking: uprights + 3 beam levels + totes ----------
ri=0
for cx in centers:
    for side in (-1,1):
        bx = cx + side*1.9
        # frame uprights (4 posts per bay, several bays down the aisle)
        for by in np.linspace(-LEN/2+1, LEN/2-1, 5):
            for dx in (-0.8,0.8):
                box(f"/World/U_{ri}",(bx+dx,by,1.4),(0.09,0.09,2.8),"steel"); ri+=1
        # horizontal beams at 3 levels
        for lvl in (0.55,1.35,2.15):
            box(f"/World/B_{ri}",(bx,0,lvl),(1.7,LEN-1,0.06),"beam"); ri+=1
        # totes sitting on the beams
        tcols=["tote_b","tote_y","tote_g","tote_r"]
        for by in np.linspace(-LEN/2+1.5, LEN/2-1.5, 11):
            for lvl in (0.78,1.68,2.58):
                if random.random()<0.8:
                    box(f"/World/T_{ri}",(bx,by,lvl),(1.3,1.1,0.5),random.choice(tcols)); ri+=1

# ---------- pick stations (left) + charge docks (right) ----------
for k,yy in enumerate(np.linspace(-9,9,4)):
    box(f"/World/Pick_{k}",(-4.5,yy,0.05),(2.2,2.2,0.1),"pick")
rightx=centers[-1]+4.5
for k,yy in enumerate(np.linspace(-9,9,4)):
    box(f"/World/Dock_{k}",(rightx,yy,0.05),(2.0,2.0,0.1),"dock")

# ---------- lighting ----------
dome=UsdLux.DomeLight.Define(st,"/World/Dome"); dome.CreateIntensityAttr(700.0)
dome.CreateColorAttr(Gf.Vec3f(0.8,0.85,1.0))
key=UsdLux.DistantLight.Define(st,"/World/Key"); key.CreateIntensityAttr(2200.0)
key.CreateColorAttr(Gf.Vec3f(1.0,0.96,0.9))
SingleXFormPrim("/World/Key", orientation=np.array([0.88,-0.33,0.33,0.0]))
# warm overhead strips
for ai,cx in enumerate(centers):
    r=UsdLux.RectLight.Define(st,f"/World/Lx_{ai}")
    r.CreateIntensityAttr(9000.0); r.CreateWidthAttr(1.2); r.CreateHeightAttr(LEN)
    SingleXFormPrim(f"/World/Lx_{ai}", position=np.array([cx,0,4.2]),
                    orientation=np.array([0.707,0.707,0,0]))

# ---------- robots ----------
w.scene.add_default_ground_plane(z_position=-0.05)
bots=[]
for i in range(12):
    p=f"/World/Carter_{i}"; add_reference_to_stage(usd_path=CARTER,prim_path=p)
    cx=centers[i%AISLES]; yaw=random.choice([0,math.pi])
    SingleXFormPrim(p, position=np.array([cx, random.uniform(-9,9),0.0]),
                    orientation=np.array([math.cos(yaw/2),0,0,math.sin(yaw/2)]))
    bots.append(p)

w.reset()
for _ in range(25): w.step(render=True)

# ---------- hero camera ----------
cam=rep.create.camera(position=(13,-30,22), look_at=(13,2,0.5))
rp=rep.create.render_product(cam,(1920,1080))
wr=rep.WriterRegistry.get("BasicWriter")
wr.initialize(output_dir="/home/ferbin/warehouse-isaac/hero", rgb=True)
wr.attach([rp])
for _ in range(10): rep.orchestrator.step()
rep.orchestrator.wait_until_complete()
sim_app.close(); print("SLEEK_DONE")
