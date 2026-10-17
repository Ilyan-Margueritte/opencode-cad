"""L-bracket with counterbored holes, wall holes, gusset and fillets (patterns: unions with overlap, copyWorkplane, selectors, fillet last)."""
import cadquery as cq

PARAMS = dict(
    base_l=80.0,      # plate length (X)
    base_w=40.0,      # plate width (Y)
    thick=5.0,        # plate and wall thickness
    wall_h=50.0,      # total height of the vertical wall
    hole_d=5.5,       # M5 clearance
    cb_d=10.0,        # counterbore diameter
    cb_depth=3.0,
    gusset=25.0,      # gusset leg length
    gusset_t=5.0,
    fillet_r=3.0,
)
P = PARAMS

EXPECT = {"bbox": [80, 40, 50], "tol": 0.2, "solids": 1, "volume": 34500, "volume_tol_pct": 3}

# 1. Base plate: sits on z=0, centred on X/Y
base = cq.Workplane("XY").box(P["base_l"], P["base_w"], P["thick"], centered=(True, True, False))

# 2. Vertical wall along the back edge (y = +base_w/2)
y_back = P["base_w"] / 2
y_wall_front = y_back - P["thick"]
wall = (
    cq.Workplane("XY")
    .box(P["base_l"], P["thick"], P["wall_h"], centered=(True, False, False))
    .translate((0, y_wall_front, 0))
)
body = base.union(wall)

# 3. Gusset: triangle drawn on "YZ" (local x = Y, local y = Z), extruded symmetrically along X.
#    The polygon penetrates 1 mm into the plate and the wall so the union is robust.
g, t = P["gusset"], P["thick"]
gusset = (
    cq.Workplane("YZ")
    .polyline(
        [
            (y_wall_front - g, t - 1),
            (y_wall_front + 1, t - 1),
            (y_wall_front + 1, t + g + 1),
            (y_wall_front - g, t),
        ]
    )
    .close()
    .extrude(P["gusset_t"] / 2, both=True)
)
body = body.union(gusset)

# 4. Two horizontal holes through the wall. "XZ" normal is -Y, so extruding goes toward -Y.
wall_holes = (
    cq.Workplane("XZ", origin=(0, y_back + 1, 0))
    .pushPoints([(-25, 35), (25, 35)])
    .circle(P["hole_d"] / 2)
    .extrude(P["thick"] + 2)
)
body = body.cut(wall_holes)

# 5. Counterbored holes in the plate, drilled from the plate's top face (z = thick)
body = (
    body.copyWorkplane(cq.Workplane("XY", origin=(0, 0, P["thick"])))
    .pushPoints([(-30, -10), (30, -10)])
    .cboreHole(P["hole_d"], P["cb_d"], P["cb_depth"])
)

# 6. Fillets LAST: vertical corner edges (front corners of the plate, back corners of plate+wall)
body = body.edges("|Z and <Y").fillet(P["fillet_r"])
body = body.edges("|Z and >Y").fillet(P["fillet_r"])

result = body
