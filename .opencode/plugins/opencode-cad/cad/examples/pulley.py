"""V-groove pulley with hub, lightening holes, keyway and set screw (patterns: revolve a profile, polarArray, radial cuts)."""
import cadquery as cq

PARAMS = dict(
    bore_r=6.0,          # 12 mm bore
    hub_r=14.0,
    hub_ext=8.0,         # hub sticks out below the disc by this much
    rim_r=40.0,
    width=20.0,          # disc / groove width along Z
    flange_t=3.0,
    groove_bottom_r=32.0,
    n_holes=6, hole_circle_r=23.0, hole_d=12.0,
    key_w=4.0, key_depth=2.5,
    setscrew_d=4.0,
)
P = PARAMS

EXPECT = {"bbox": [80, 80, 28], "tol": 0.2, "solids": 1, "volume": 75200, "volume_tol_pct": 2}

# Half profile in (radius, z), counter-clockwise, never crossing the axis
w, f = P["width"], P["flange_t"]
profile = [
    (P["bore_r"], -P["hub_ext"]),
    (P["hub_r"], -P["hub_ext"]),
    (P["hub_r"], 0),
    (P["rim_r"], 0),
    (P["rim_r"], f),
    (P["groove_bottom_r"], w / 2),   # V groove bottom
    (P["rim_r"], w - f),
    (P["rim_r"], w),
    (P["bore_r"], w),
]

# "XZ": local x = radius (global X), local y = global Z. Axis (0,0,0)->(0,1,0) is the global Z axis.
body = cq.Workplane("XZ").polyline(profile).close().revolve(360, (0, 0, 0), (0, 1, 0))

# Lightening holes: drilled from the top face (z = width), through the disc
body = (
    body.faces(">Z")
    .workplane()
    .polarArray(P["hole_circle_r"], 0, 360, P["n_holes"])
    .hole(P["hole_d"])
)

# Keyway along the bore toward +Y (starts inside the bore void, ends key_depth beyond the bore wall)
total_h = w + P["hub_ext"]
keyway = (
    cq.Workplane("XY")
    .box(P["key_w"], P["key_depth"] + 1.0, total_h + 2, centered=(True, False, False))
    .translate((0, P["bore_r"] - 1.0, -P["hub_ext"] - 1))
)

# Radial set-screw hole through the hub, toward +X, at mid-height of the hub extension
setscrew = (
    cq.Workplane("YZ", origin=(P["bore_r"] - 1.0, 0, -P["hub_ext"] / 2))
    .circle(P["setscrew_d"] / 2)
    .extrude(P["hub_r"] - P["bore_r"] + 3.0)
)

result = body.cut(keyway).cut(setscrew)
