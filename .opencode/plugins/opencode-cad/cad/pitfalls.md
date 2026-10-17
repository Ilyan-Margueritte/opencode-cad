# CadQuery pitfalls and robust patterns (CadQuery 2.x, units = millimetres)

## Script contract
- The script must define `result` (a `cq.Workplane`, `cq.Assembly` or `cq.Shape`). `cad_run` exports STEP/STL and renders views for you.
- Put every dimension in a `PARAMS` dict at the top. Put sanity checks in `EXPECT = {"bbox": [x, y, z], "tol": 0.5, "solids": 1, "volume": V, "volume_tol_pct": 5}`.
- Do NOT call `show_object`, `cq.exporters.export`, `input()`, or open any GUI. Use `print()` freely: output appears in the report.
- Convention unless told otherwise: Z is up, the part sits on z=0, centred on X/Y.

## Planes and axes
- "XY": normal +Z, local x = X, local y = Y.
- "XZ": normal is **-Y** (!), local x = X, local y = Z. A positive `extrude` on "XZ" goes toward -Y.
- "YZ": normal +X, local x = Y, local y = Z.
- `Workplane("XY", origin=(x, y, z))` sets the origin. `.workplane(offset=h)` moves along the normal.
- `faces(">Z").workplane()` creates a plane on that face. The origin is the projection of the *global origin* onto the face. Use `.workplane(centerOption="CenterOfBoundBox")` to centre it on the face. It needs ONE planar face (or several coplanar ones).
- Coordinates passed to `rect`, `circle`, `pushPoints`, `moveTo`, `polyline` are LOCAL to the current workplane.

## Primitives and argument order
- `box(length, width, height)` is centred on all three axes by default. To sit on z=0: `centered=(True, True, False)`.
- `cylinder(height, radius)` (height first!). `circle(radius)` takes a RADIUS. `hole(diameter)` takes a DIAMETER. `sphere(radius)`.
- `polygon(nSides, diameter)` uses the circumscribed diameter. `slot2D(length, diameter, angle=0)`: length is end-to-end including the round ends.
- `rect(xLen, yLen)` is centred on the current point. `ellipse(x_radius, y_radius)`.
- 2D wires must be closed before `extrude`/`revolve`: end a `polyline`/`lineTo` chain with `.close()`.
- For complex 2D shapes with rounded corners use the Sketch API: `s = cq.Sketch().rect(40, 20).vertices().fillet(3)`, then `cq.Workplane("XY").placeSketch(s).extrude(5)`.

## Methods that do NOT exist (do not invent them)
- `roundedBox`, `extrudeBoth` (use `extrude(d, both=True)`), `cutExtrude`, `hollow`, `bevel` (use `chamfer`), `Workplane.fuse` / `Workplane.subtract` (use `union` / `cut`).
- `Workplane.add(obj)` pushes objects on the stack; it does NOT make a union.
- If unsure an API exists, write a 3-line test script and run `cad_run`, instead of guessing in a large script.

## Selectors
- Faces/edges by direction: `">Z"` max Z, `"<Z"` min Z, `"|Z"` edges parallel to Z, `"#Z"` perpendicular to Z, `"+Z"` / `"-Z"` normal direction, `"%CIRCLE"` by geometry type, `">Z[-2]"` second from the top.
- Combine with `and`, `or`, `not`: `edges("|Z and <Y")` = vertical edges at minimum Y. Each term selects among the same input, then the results are intersected.
- `">Z"` keeps everything whose centre is at the maximum, so it can return several faces/edges. Check with `.size()`.
- Selecting from a Workplane whose stack is no longer the solid (after `.workplane()`, `.pushPoints()`, `.rect()`...) gives surprising results. Use `.end()` or keep named variables for intermediate solids.
- Fallback when selectors get complicated: `cq.selectors.BoxSelector((x0, y0, z0), (x1, y1, z1))`.

## Booleans
- `a.union(b)`, `a.cut(b)`, `a.intersect(b)`. The tool `b` is in GLOBAL coordinates: build it at the right place (`origin=`, `.translate(...)`) or it cuts at the origin.
- Cutters/unions that only TOUCH a surface (coplanar faces, shared edges) are the #1 cause of invalid or skin-thin results. Overlap by 0.1-1 mm: make cutters longer than the part, make joined bodies penetrate each other.
- Cut many features with ONE boolean: build all cutters in one Workplane (`pushPoints(pts).circle(r).extrude(h)`) then `body.cut(cutters)`. Never cut hole by hole in a loop.
- Hole helpers need a solid in the chain: `body.faces(">Z").workplane().pushPoints(pts).hole(d)`. For a plane that is not a face, use `body.copyWorkplane(cq.Workplane("XY", origin=(0, 0, z0))).pushPoints(pts).cboreHole(d, cb_d, cb_depth)`.
- `hole`, `cboreHole`, `cskHole` drill from the workplane in the direction OPPOSITE to its normal (into the material under the face). `cutBlind(-depth)` does likewise; a positive value cuts away from the face.
- Error "Cannot find a solid on the stack or in the parent chain": the Workplane you called hole/cut/union on has no solid. Chain it from the body or use `copyWorkplane`.

## Fillets and chamfers
- Apply them LAST, after all booleans, and select only the edges you need (`edges("|Z")`, `edges(">Z")`...).
- The radius must be smaller than half the thinnest adjacent wall and smaller than the shortest selected edge. Opposite fillets on a thin wall of thickness t need r < t/2.
- "BRep_API: command not done" / "StdFail_NotDone" = fillet, chamfer, shell or offset failed. Try: smaller radius, fewer edges, other order, or build the rounded shape directly (e.g. fillet a box before cutting features into it).
- Robust helper:

```python
def safe_fillet(wp, selector, r):
    for k in (1.0, 0.75, 0.5, 0.35, 0.2):
        try:
            return wp.edges(selector).fillet(r * k)
        except Exception:
            continue
    print(f"WARNING: fillet {selector} r={r} skipped")
    return wp
```

## Shell / hollow parts
- `solid.faces(">Z").shell(-t)` removes the selected face and hollows INWARD (negative thickness). It often fails on complex solids.
- Most robust method: build the outer solid, build the inner solid with the same recipe at (size - 2t), position it, and `outer.cut(inner)`. Make the inner one stick out past the open face by >=1 mm.
- Inner corner radius = outer radius - wall (keep it > 0).
- Check hollow parts with `cad_run section="z:<mid height>"` to see the walls.

## Revolve
- Draw the half-profile on "XZ" (local x = radius, local y = Z), as a closed polyline, then `.revolve(360, (0, 0, 0), (0, 1, 0))`. The axis points are LOCAL 2D coordinates, so (0,1,0) is the global Z axis on "XZ".
- The profile must stay on one side of the axis (x >= 0) and must not cross it. Points exactly on the axis are fine.
- Domed or rounded ends: use `threePointArc` or `radiusArc` in the profile instead of fillet-ing afterwards.

## Loft and sweep
- Loft: `cq.Workplane("XY").rect(40, 40).workplane(offset=30).circle(10).loft(combine=True)`. Use `ruled=True` for straight (non-smooth) transitions.
- Sweep: build the path first (`cq.Workplane("XZ").spline([...])` or lines/arcs), then draw the profile on a plane perpendicular to the path start: `cq.Workplane("XY").circle(r).sweep(path, transition="round")`. Use `isFrenet=True` for helices.
- Pipes: sweep an outer circle, sweep an inner circle, `outer.cut(inner)`.

## Patterns and arrays
- `rarray(xSpacing, ySpacing, xCount, yCount)`: centred grid. `polarArray(radius, startAngle, angle, count)`: points on a circle. Both leave points on the stack, then call `.circle(r)`, `.rect(...)`, `.hole(d)`...
- `pushPoints([(x, y), ...])` for arbitrary positions.
- `mirror("YZ")` mirrors about the YZ plane (union with the original by default). `rotate((0,0,0), (0,0,1), angle_deg)` rotates about an axis given by two points.

## Assemblies
- Simplest: position parts with `.translate((x, y, z))` / `.rotate(...)` and combine with `cq.Assembly().add(part, name="a", color=cq.Color("gray"))`. Assign `result = assy`.
- Declare `EXPECT["solids"] = N` for assemblies, otherwise `cad_run` warns about several solids.
- Parts that must be ONE printed body need to overlap, then `union`.

## Things not to attempt from scratch
- Real screw threads (use a plain cylinder at the nominal/minor diameter), involute gears (use the `cq_gears` package if installed; otherwise a clearly approximated tooth profile), text with exotic fonts, organic/freeform surfaces.
- If a request needs one of these, say so and use the simplest faithful stand-in.

## Debugging playbook
1. Read the LAST line of the traceback first, then the line number in your script.
2. "BRep_API: command not done": see Fillets / Shell. "Null TopoDS_Shape object": a selection matched nothing or a boolean produced an empty shape (print `.size()`). "No pending wires present": a 2D sketch was not closed or was created in a different chain.
3. Isolate: temporarily set `result = intermediate_solid`, run, inspect the metrics and the views.
4. Print dimensions: `print(wp.val().BoundingBox().zlen)`.
5. Change ONE thing per iteration. If the same error appears three times, change the strategy (e.g. replace shell by outer.cut(inner), replace fillet by a built-in rounded profile).
6. `solids > 1` when you expected 1: some body does not overlap the rest. Look at SOLIDS in the report for the stray one's position.
7. Wrong size by a factor 2: radius vs diameter mix-up. Feature on the wrong side: "XZ" normal is -Y, or the cutter was not translated.

## Performance
- Fewer, larger booleans beat many small ones. Build pattern cutters together.
- Fillet only the edges you need. Avoid fillets on edges produced by boolean-heavy arrays.
- Very dense arrays (hundreds of features): build one unit, then `rarray` its cutter profile once instead of copying solids.

## Maker / 3D-printing defaults (if the user does not give values)
- Walls 1.2-2.4 mm (multiples of the 0.4 mm nozzle), floor 1.6-2 mm.
- Clearance for sliding/press fits 0.2-0.4 mm per side. Printed holes: add 0.2 mm to the diameter.
- M3: clearance hole 3.4 mm, pilot hole in plastic 2.6 mm, heat-set insert hole about 4.2 mm, counterbore 6.2 mm x 3 mm.
- Keep overhangs at 45 degrees or less, or orient the part so the large flat face is on z=0.
