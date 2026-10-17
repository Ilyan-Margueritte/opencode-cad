#!/usr/bin/env python3
"""Run a CadQuery script, validate the part, export STEP/STL and render PNG views.

Contract for the user script:
  - single part: define a variable `result` (cq.Workplane, cq.Assembly or cq.Shape)
  - several printed parts: define `PARTS = {"name": shape, ...}` instead of `result`
    (each part in its PRINTING orientation, on z=0), optional per-part checks
    `PARTS_EXPECT = {"name": {... same keys as EXPECT ...}}`, and optional
    `ASSEMBLY` (cq.Assembly, the same parts in assembled position: exported as
    model_assembly.step, rendered as a fit view, target of --section)
  - may define `PARAMS` (dict, echoed in the report) and `EXPECT` (dict):
      EXPECT = {
          "bbox": [x, y, z],        # expected overall dimensions in mm
          "tol": 0.5,               # tolerance on bbox in mm
          "volume": 12345.0,        # expected volume in mm3
          "volume_tol_pct": 5,      # tolerance on volume in %
          "solids": 1,              # expected number of solids
      }
  - every printed part is checked against the printer bed (Creality K1 SE:
    220 x 220 x 250 mm, default margin 5 mm). Override with
    `PRINTER = {"x": ..., "y": ..., "z": ..., "margin": ...}` (partial merge)
    or disable with `PRINT_CHECK = False`.

Output: one JSON document on stdout (last line), also written to <out>/report.json
"""
import argparse
import contextlib
import io
import json
import runpy
import sys
import traceback
from pathlib import Path

VIEWS = {
    "iso": (25, -60),
    "front": (0, -90),
    "right": (0, 0),
    "back": (0, 90),
    "left": (0, 180),
    "top": (90, -90),
    "bottom": (-90, -90),
}

DEFAULT_PRINTER = {"x": 220.0, "y": 220.0, "z": 250.0, "margin": 5.0}  # Creality K1 SE


def sanitize(name):
    """Part names become file names: keep letters, digits, dash and underscore."""
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(name))


def usable_volume(printer):
    margin = float(printer.get("margin", DEFAULT_PRINTER["margin"]))
    return [float(printer.get(axis, DEFAULT_PRINTER[axis])) - margin for axis in ("x", "y", "z")]


def check_print_volume(m, report, usable, prefix=""):
    label = f"{usable[0]:g} x {usable[1]:g} x {usable[2]:g}"
    for axis, value, limit in zip("XYZ", m["bbox_dims_mm"], usable):
        if value > limit + 1e-6:
            report["errors"].append(
                f"{prefix}{axis} = {value:g} mm exceeds the usable print volume ({label} mm). "
                "Split the part into printable pieces (PARTS) with alignment joints, or reduce/reorient it."
            )


def user_traceback(exc, script_path):
    """Keep only the frames from the user's script plus the error message."""
    frames = [f for f in traceback.extract_tb(exc.__traceback__) if f.filename == script_path]
    lines = [f"  line {f.lineno}, in {f.name}: {f.line}" for f in frames]
    msg = "".join(traceback.format_exception_only(type(exc), exc)).strip()
    head = "Traceback (your script only):\n" + "\n".join(lines) + "\n" if lines else ""
    return head + msg


def to_shape(obj, cq, name="result"):
    if isinstance(obj, cq.Workplane):
        shapes = [v for v in obj.vals() if isinstance(v, cq.Shape)]
        if not shapes:
            raise ValueError(f"`{name}` is an empty Workplane (nothing on the stack).")
        return shapes[0] if len(shapes) == 1 else cq.Compound.makeCompound(shapes)
    if isinstance(obj, cq.Assembly):
        return obj.toCompound()
    if isinstance(obj, cq.Shape):
        return obj
    raise TypeError(f"`{name}` must be a Workplane, Assembly or Shape (got {type(obj).__name__}).")


def metrics(shape):
    bb = shape.BoundingBox()
    c = shape.Center()
    return {
        "valid": bool(shape.isValid()),
        "solids": len(shape.Solids()),
        "faces": len(shape.Faces()),
        "edges": len(shape.Edges()),
        "volume_mm3": round(shape.Volume(), 3),
        "area_mm2": round(shape.Area(), 3),
        "bbox_dims_mm": [round(bb.xlen, 3), round(bb.ylen, 3), round(bb.zlen, 3)],
        "bbox_min": [round(bb.xmin, 3), round(bb.ymin, 3), round(bb.zmin, 3)],
        "bbox_max": [round(bb.xmax, 3), round(bb.ymax, 3), round(bb.zmax, 3)],
        "center": [round(c.x, 3), round(c.y, 3), round(c.z, 3)],
    }


def solids_detail(shape, limit=20):
    out = []
    for i, s in enumerate(shape.Solids()[:limit]):
        b = s.BoundingBox()
        out.append(
            {
                "index": i,
                "volume_mm3": round(s.Volume(), 3),
                "bbox_min": [round(b.xmin, 2), round(b.ymin, 2), round(b.zmin, 2)],
                "bbox_max": [round(b.xmax, 2), round(b.ymax, 2), round(b.zmax, 2)],
            }
        )
    return out


def check_expect(m, expect, report, prefix=""):
    if not expect:
        return
    tol = float(expect.get("tol", 0.5))
    if "bbox" in expect:
        for axis, e, g in zip("XYZ", expect["bbox"], m["bbox_dims_mm"]):
            if abs(e - g) > tol:
                report["errors"].append(
                    prefix + f"Size along {axis}: expected {e} mm, got {g} mm (tolerance {tol} mm)."
                )
    if "volume" in expect:
        pct = float(expect.get("volume_tol_pct", 5))
        e, g = float(expect["volume"]), m["volume_mm3"]
        if e > 0 and abs(g - e) / e * 100 > pct:
            report["errors"].append(prefix + f"Volume: expected ~{e} mm3, got {g} mm3 (tolerance {pct}%).")
    if "solids" in expect and m["solids"] != int(expect["solids"]):
        report["errors"].append(prefix + f"Solid count: expected {expect['solids']}, got {m['solids']}.")


def heuristics(m, expect, report, prefix=""):
    if not m["valid"]:
        report["errors"].append(
            prefix + "Invalid geometry (isValid() is False). Simplify booleans/fillets, avoid coplanar faces."
        )
    if m["volume_mm3"] <= 0:
        report["errors"].append(prefix + "Volume is zero or negative: result is not a closed solid.")
    if m["solids"] > 1 and not (expect and "solids" in expect):
        report["warnings"].append(
            prefix + f"{m['solids']} separate solids: disconnected parts? Make them overlap/touch, "
            "or declare EXPECT['solids'] if intentional (assembly)."
        )
    if max(m["bbox_dims_mm"]) > 5000:
        report["warnings"].append(prefix + "Largest dimension > 5 m: possible unit mistake (CadQuery uses mm).")
    if min(m["bbox_dims_mm"]) < 0.1:
        report["warnings"].append(prefix + "A dimension is < 0.1 mm: flat part or possible unit mistake.")


def make_section(shape, spec, cq):
    """spec like 'z:10'. Removes the material on one side so the cut face points at the camera."""
    axis, off = spec.split(":")
    axis, off = axis.strip().lower(), float(off)
    bb = shape.BoundingBox()
    big = max(bb.xlen, bb.ylen, bb.zlen) * 4 + 10
    cx, cy, cz = (bb.xmin + bb.xmax) / 2, (bb.ymin + bb.ymax) / 2, (bb.zmin + bb.zmax) / 2
    if axis == "z":  # keep z <= off, look from the top
        box = cq.Workplane("XY").box(big, big, big, centered=(True, True, False)).translate((cx, cy, off))
        view = "top"
    elif axis == "y":  # keep y >= off, look from the front (-Y)
        box = cq.Workplane("XY").box(big, big, big, centered=(True, False, True)).translate((cx, off - big, cz))
        view = "front"
    elif axis == "x":  # keep x <= off, look from the right (+X)
        box = cq.Workplane("XY").box(big, big, big, centered=(False, True, True)).translate((off, cy, cz))
        view = "right"
    else:
        raise ValueError("section axis must be x, y or z (e.g. 'z:10').")
    return shape.cut(box.val()), view, ("xyz".index(axis), off)


def render(shape, out, names, suffix="", highlight=None):
    import numpy as np
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection

    bb = shape.BoundingBox()
    size = max(bb.xlen, bb.ylen, bb.zlen, 1e-6)
    verts, tris = shape.tessellate(size / 400.0, 0.2)
    V = np.array([[v.x, v.y, v.z] for v in verts])
    T = np.array(tris)
    tri = V[T]
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    n = n / (np.linalg.norm(n, axis=1, keepdims=True) + 1e-12)
    light = np.array([0.4, -0.5, 0.8])
    light = light / np.linalg.norm(light)
    shade = 0.3 + 0.7 * np.clip(np.abs(n @ light), 0, 1)
    colors = np.stack([shade * 0.50, shade * 0.62, shade * 0.85, np.ones_like(shade)], axis=1)
    if highlight is not None:
        # Cut faces lying exactly on the section plane are painted orange so the walls stand out
        axis_idx, off = highlight
        mask = np.all(np.abs(tri[:, :, axis_idx] - off) < 1e-3, axis=1)
        colors[mask] = (0.95, 0.55, 0.15, 1.0)

    center = np.array([(bb.xmin + bb.xmax) / 2, (bb.ymin + bb.ymax) / 2, (bb.zmin + bb.zmax) / 2])
    half = size / 2 * 1.05
    files = []
    for name in names:
        if name not in VIEWS:
            continue
        elev, azim = VIEWS[name]
        fig = plt.figure(figsize=(6, 6), dpi=130)
        ax = fig.add_subplot(111, projection="3d")
        ax.add_collection3d(Poly3DCollection(tri, facecolors=colors, edgecolors="none"))
        ax.set_xlim(center[0] - half, center[0] + half)
        ax.set_ylim(center[1] - half, center[1] + half)
        ax.set_zlim(center[2] - half, center[2] + half)
        ax.set_box_aspect((1, 1, 1))
        ax.view_init(elev=elev, azim=azim)
        ax.set_xlabel("X (mm)")
        ax.set_ylabel("Y (mm)")
        if abs(elev) == 90:  # top/bottom: the Z axis points at the camera, drop it
            ax.set_zticks([])
        else:
            ax.set_zlabel("Z (mm)")
        ax.set_title(f"{name}{suffix}")
        tag = suffix.strip().replace(" ", "_").replace(":", "")
        path = out / (f"view_{name}_{tag}.png" if tag else f"view_{name}.png")
        fig.savefig(path, bbox_inches="tight")
        plt.close(fig)
        files.append(str(path))
    return files


def make_exports(shape, cq, out, name=None, stl=True):
    """Export one shape; returns the written file paths."""
    w = cq.Workplane("XY").newObject([shape])
    base = out / (f"model_{sanitize(name)}" if name else "model")
    files = []
    step = base.with_suffix(".step")
    cq.exporters.export(w, str(step))
    files.append(str(step))
    if stl:
        stl_path = base.with_suffix(".stl")
        cq.exporters.export(w, str(stl_path), tolerance=0.05, angularTolerance=0.2)
        files.append(str(stl_path))
    return files


def safe_render(shape, cq, out, view_names, suffix, section, report):
    try:
        files = render(shape, out, view_names, suffix=suffix)
        if section:
            cut, view, hl = make_section(shape, section, cq)
            files += render(cut, out, [view, "iso"], suffix=f"{suffix} section {section}", highlight=hl)
        return files
    except ImportError:
        report["warnings"].append("matplotlib/numpy missing: renders skipped (pip install matplotlib numpy).")
        return []
    except Exception as e:  # noqa: BLE001
        report["warnings"].append(f"Render failed: {e}")
        return []


def run_single(ns, cq, report, out, a, expect_override, usable, print_check):
    try:
        shape = to_shape(ns["result"], cq)
    except Exception as e:  # noqa: BLE001
        report["errors"].append(str(e))
        return

    m = metrics(shape)
    report["metrics"] = m
    if m["solids"] > 1:
        report["solids_detail"] = solids_detail(shape)

    expect = dict(ns.get("EXPECT") or {})
    expect.update(expect_override)
    heuristics(m, expect, report)
    check_expect(m, expect, report)
    if print_check:
        check_print_volume(m, report, usable)

    try:
        report["files"] += make_exports(shape, cq, out)
    except Exception as e:  # noqa: BLE001
        report["warnings"].append(f"Export failed: {e}")

    if not a.no_render:
        view_names = [v.strip() for v in a.views.split(",") if v.strip()]
        report["files"] += safe_render(shape, cq, out, view_names, "", a.section, report)


def run_multi(ns, cq, report, out, a, usable, print_check, parts):
    if ns.get("EXPECT"):
        report["warnings"].append(
            "EXPECT is ignored in multi-part mode: use PARTS_EXPECT for per-part expectations."
        )
    if a.expect:
        report["warnings"].append("--expect is ignored in multi-part mode: use PARTS_EXPECT.")

    parts_expect = ns.get("PARTS_EXPECT") if isinstance(ns.get("PARTS_EXPECT"), dict) else {}
    view_names = [v.strip() for v in a.views.split(",") if v.strip()]
    report["parts"] = []

    for name, obj in parts.items():
        prefix = f"[{name}] "
        try:
            shape = to_shape(obj, cq, name=name)
        except Exception as e:  # noqa: BLE001
            report["errors"].append(prefix + str(e))
            continue

        m = metrics(shape)
        entry = {"name": name, "metrics": m}
        if m["solids"] > 1:
            entry["solids_detail"] = solids_detail(shape)

        exp = dict(parts_expect.get(name) or {})
        heuristics(m, exp, report, prefix)
        check_expect(m, exp, report, prefix)
        if print_check:
            check_print_volume(m, report, usable, prefix)

        try:
            files = make_exports(shape, cq, out, name=name)
            entry["files"] = files
            report["files"] += files
        except Exception as e:  # noqa: BLE001
            report["warnings"].append(f"{prefix}Export failed: {e}")

        if not a.no_render:
            files = safe_render(shape, cq, out, view_names, f" {name}", None, report)
            entry["files"] = entry.get("files", []) + files
            report["files"] += files

        report["parts"].append(entry)

    assy = ns.get("ASSEMBLY")
    assy_shape = None
    if assy is not None:
        try:
            assy_shape = to_shape(assy, cq, name="ASSEMBLY")
            report["files"] += make_exports(assy_shape, cq, out, name="assembly", stl=False)
        except Exception as e:  # noqa: BLE001
            report["errors"].append("[ASSEMBLY] " + str(e))
            assy_shape = None

    if not a.no_render:
        if assy_shape is not None:
            report["files"] += safe_render(assy_shape, cq, out, view_names, " assembly", a.section, report)
        elif a.section and assy is None:
            report["warnings"].append(
                "section is ignored in multi-part mode: define ASSEMBLY (cq.Assembly) to cut through."
            )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("script")
    ap.add_argument("--out", default="cad_out")
    ap.add_argument("--expect", default=None, help="JSON merged over the script's EXPECT")
    ap.add_argument("--section", default=None, help="e.g. z:10")
    ap.add_argument("--views", default="iso,front,top,right")
    ap.add_argument("--no-render", action="store_true")
    a = ap.parse_args()

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    script = str(Path(a.script).resolve())
    report = {"ok": False, "script": script, "errors": [], "warnings": [], "files": []}

    def finish():
        report["ok"] = not report["errors"]
        (out / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
        print(json.dumps(report, ensure_ascii=False))
        sys.exit(0 if report["ok"] else 1)

    try:
        import cadquery as cq
    except ImportError:
        report["errors"].append("cadquery is not installed: pip install cadquery matplotlib numpy")
        finish()

    if not Path(script).exists():
        report["errors"].append(f"File not found: {script}")
        finish()

    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            ns = runpy.run_path(script, run_name="__cad__")
    except Exception as e:  # noqa: BLE001
        report["errors"].append(user_traceback(e, script))
        report["script_stdout"] = buf.getvalue()[-2000:]
        finish()
    report["script_stdout"] = buf.getvalue()[-2000:]

    expect_override = {}
    if a.expect:
        try:
            expect_override = json.loads(a.expect)
        except ValueError:
            report["errors"].append(f"--expect is not valid JSON: {a.expect}")
            finish()

    printer = dict(DEFAULT_PRINTER)
    if isinstance(ns.get("PRINTER"), dict):
        printer.update(ns["PRINTER"])
    print_check = ns.get("PRINT_CHECK", True) is not False
    usable = usable_volume(printer)
    report["printer"] = {**printer, "usable": usable, "check": print_check}
    if ns.get("PARAMS"):
        report["params"] = ns["PARAMS"]

    parts = ns.get("PARTS")
    if "result" in ns:
        if parts:
            report["warnings"].append(
                "PARTS is ignored because `result` is also defined: remove `result` to use the multi-part mode."
            )
        run_single(ns, cq, report, out, a, expect_override, usable, print_check)
    elif isinstance(parts, dict) and parts:
        run_multi(ns, cq, report, out, a, usable, print_check, parts)
    elif parts is not None:
        report["errors"].append("PARTS must be a dict {name: Workplane|Assembly|Shape}.")
    else:
        report["errors"].append(
            "The script must define `result` (single part) or `PARTS` (dict of printed parts)."
        )

    finish()


if __name__ == "__main__":
    main()
