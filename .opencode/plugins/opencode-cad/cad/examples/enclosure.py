"""Two-part enclosure (base + lid) laid out for printing (patterns: hollow via outer.cut(inner), screw bosses, assembly)."""
import cadquery as cq

PARAMS = dict(
    length=100.0, width=60.0, height=35.0,   # outer size of the base
    wall=2.4, floor=2.0,
    corner_r=6.0,
    boss_d=8.0, screw_d=2.6, screw_clear=3.4,
    lid_t=2.4, lip_h=3.0, lip_wall=1.2, lip_clear=0.3,
    gap=10.0,                                # spacing between the two printed parts
)
P = PARAMS

# lid is placed beside the base: x spans [-50, 50] for the base and [60, 160] for the lid
EXPECT = {"bbox": [210, 60, 35], "tol": 0.3, "solids": 2, "volume": 56000, "volume_tol_pct": 5}


def rounded_box(l, w, h, r):
    """Box sitting on z=0 with its vertical edges rounded."""
    return cq.Workplane("XY").box(l, w, h, centered=(True, True, False)).edges("|Z").fillet(r)


# Screw positions (inner corners), shared by base and lid
bx = P["length"] / 2 - P["wall"] - P["boss_d"] / 2 + 0.5   # +0.5: boss overlaps the wall
by = P["width"] / 2 - P["wall"] - P["boss_d"] / 2 + 0.5
SCREW_PTS = [(sx * bx, sy * by) for sx in (-1, 1) for sy in (-1, 1)]


def make_base():
    outer = rounded_box(P["length"], P["width"], P["height"], P["corner_r"])
    # Inner cavity: same recipe, smaller; it sticks out above the top so the base is open
    inner = rounded_box(
        P["length"] - 2 * P["wall"],
        P["width"] - 2 * P["wall"],
        P["height"],
        P["corner_r"] - P["wall"],
    ).translate((0, 0, P["floor"]))
    base = outer.cut(inner)

    # Screw bosses, rising from the floor up to just below the lid lip
    boss_top = P["height"] - P["lip_h"] - 1.0
    bosses = (
        cq.Workplane("XY", origin=(0, 0, P["floor"] - 0.5))
        .pushPoints(SCREW_PTS)
        .circle(P["boss_d"] / 2)
        .extrude(boss_top - P["floor"] + 0.5)
    )
    pilots = (
        cq.Workplane("XY", origin=(0, 0, boss_top - 10))
        .pushPoints(SCREW_PTS)
        .circle(P["screw_d"] / 2)
        .extrude(11)
    )
    return base.union(bosses).cut(pilots)


def make_lid():
    plate = rounded_box(P["length"], P["width"], P["lid_t"], P["corner_r"])

    # Lip hanging below the plate, inset by lip_clear from the base's inner wall
    inset = 2 * (P["wall"] + P["lip_clear"])
    lip_outer = rounded_box(
        P["length"] - inset,
        P["width"] - inset,
        P["lip_h"] + 0.5,
        P["corner_r"] - P["wall"] - P["lip_clear"],
    ).translate((0, 0, -P["lip_h"]))
    lip_inner = rounded_box(
        P["length"] - inset - 2 * P["lip_wall"],
        P["width"] - inset - 2 * P["lip_wall"],
        P["lip_h"] + 2,
        max(0.5, P["corner_r"] - P["wall"] - P["lip_clear"] - P["lip_wall"]),
    ).translate((0, 0, -P["lip_h"] - 1))
    lid = plate.union(lip_outer.cut(lip_inner))

    holes = (
        cq.Workplane("XY", origin=(0, 0, -1))
        .pushPoints(SCREW_PTS)
        .circle(P["screw_clear"] / 2)
        .extrude(P["lid_t"] + 2)
    )
    return lid.cut(holes)


base = make_base()
# Flip the lid (plate on the print bed, lip up) and move it beside the base
lid = (
    make_lid()
    .rotate((0, 0, 0), (1, 0, 0), 180)
    .translate((P["length"] + P["gap"], 0, P["lid_t"]))
)

assy = cq.Assembly()
assy.add(base, name="base", color=cq.Color("gray"))
assy.add(lid, name="lid", color=cq.Color("lightblue"))
result = assy
