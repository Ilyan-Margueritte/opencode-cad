#!/usr/bin/env python3
"""Smoke tests for cad_runner.py.

Runs the real runner on generated CadQuery scripts (and on the shipped
examples) and asserts on the JSON report and the exported files.
No test framework: only the runner's own requirements are needed.

Usage:  .venv/Scripts/python tests/test_cad_runner.py   (Windows)
        .venv/bin/python tests/test_cad_runner.py       (macOS/Linux)

Exit code 0 = all green.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / ".opencode" / "plugins" / "opencode-cad" / "cad" / "cad_runner.py"
EXAMPLES = RUNNER.parent / "examples"

# Creality K1 SE (220x220x250) with the default 5 mm margin
USABLE = "215 x 215 x 245"


def parse_report(proc):
    for line in reversed(proc.stdout.strip().splitlines()):
        try:
            return json.loads(line)
        except ValueError:
            continue
    return None


def run_script(source, args=()):
    """Run the runner on `source` inside a temp dir; return (report, file names)."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        script = td / "script.py"
        script.write_text(source, encoding="utf8")
        out = td / "out"
        proc = subprocess.run(
            [sys.executable, str(RUNNER), str(script), "--out", str(out), *args],
            capture_output=True, text=True, timeout=300,
        )
        report = parse_report(proc)
        if report is None:
            raise AssertionError(
                "runner produced no JSON report\n"
                f"exit={proc.returncode}\nstdout tail:\n{proc.stdout[-1500:]}\n"
                f"stderr tail:\n{proc.stderr[-1500:]}"
            )
        files = sorted(p.name for p in out.glob("*")) if out.exists() else []
        return report, files


def run_example(name, args=()):
    script = EXAMPLES / name
    assert script.exists(), f"missing example {script}"
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "out"
        proc = subprocess.run(
            [sys.executable, str(RUNNER), str(script), "--out", str(out), *args],
            capture_output=True, text=True, timeout=300,
        )
        report = parse_report(proc)
        if report is None:
            raise AssertionError(
                f"no JSON report for example {name}\nstdout tail:\n{proc.stdout[-1500:]}\n"
                f"stderr tail:\n{proc.stderr[-1500:]}"
            )
        files = sorted(p.name for p in out.glob("*")) if out.exists() else []
        return report, files


BOX_40 = """
import cadquery as cq
EXPECT = {"bbox": [40, 30, 20], "tol": 0.01, "solids": 1}
result = cq.Workplane("XY").box(40, 30, 20, centered=(True, True, False))
"""

BOX_260 = """
import cadquery as cq
result = cq.Workplane("XY").box(260, 50, 20, centered=(True, True, False))
"""

PARTS_OK = """
import cadquery as cq
def block(x, y, z):
    return cq.Workplane("XY").box(x, y, z, centered=(True, True, False))
PARTS = {"base": block(40, 30, 20), "lid": block(30, 30, 10)}
PARTS_EXPECT = {
    "base": {"bbox": [40, 30, 20], "tol": 0.01, "solids": 1},
    "lid": {"bbox": [30, 30, 10], "tol": 0.01, "solids": 1},
}
"""

PARTS_BAD_LID = PARTS_OK.replace('"lid": {"bbox": [30, 30, 10]', '"lid": {"bbox": [31, 30, 10]')

PARTS_ASSEMBLY = """
import cadquery as cq
base = cq.Workplane("XY").box(40, 30, 20, centered=(True, True, False))
lid = cq.Workplane("XY").box(40, 30, 4, centered=(True, True, False))
PARTS = {"base": base, "lid": lid}
ASSEMBLY = cq.Assembly().add(base, name="base").add(lid.translate((0, 0, 20)), name="lid")
"""


def t_single_box_fits():
    rep, files = run_script(BOX_40, ["--no-render"])
    f = []
    if not rep["ok"]:
        f.append(f"expected ok, errors={rep['errors']}")
    for want in ("model.step", "model.stl"):
        if want not in files:
            f.append(f"missing {want} (got {files})")
    return f


def t_single_too_big_fails():
    rep, _ = run_script(BOX_260, ["--no-render"])
    f = []
    if rep["ok"]:
        f.append("expected failure for a 260 mm part")
    joined = " | ".join(rep["errors"])
    if "exceeds the usable print volume" not in joined:
        f.append(f"missing print-volume error (errors={rep['errors']})")
    if USABLE not in joined:
        f.append(f"missing usable volume text '{USABLE}' (errors={rep['errors']})")
    return f


def t_print_check_can_be_disabled():
    rep, _ = run_script(BOX_260 + "\nPRINT_CHECK = False\n", ["--no-render"])
    f = []
    if not rep["ok"]:
        f.append(f"expected ok with PRINT_CHECK=False, errors={rep['errors']}")
    return f


def t_printer_can_be_overridden():
    rep, _ = run_script(BOX_260 + '\nPRINTER = {"x": 300.0}\n', ["--no-render"])
    f = []
    if not rep["ok"]:
        f.append(f"expected ok with a 300 mm X bed, errors={rep['errors']}")
    return f


def t_parts_export_each_piece():
    rep, files = run_script(PARTS_OK, ["--no-render"])
    f = []
    if not rep["ok"]:
        f.append(f"expected ok, errors={rep['errors']}")
    for want in (
        "model_base.step",
        "model_base.stl",
        "model_lid.step",
        "model_lid.stl",
    ):
        if want not in files:
            f.append(f"missing {want} (got {files})")
    parts = rep.get("parts")
    if not parts or [p.get("name") for p in parts] != ["base", "lid"]:
        f.append(f"expected parts=[base, lid] with metrics, got {parts}")
    elif not all("metrics" in p for p in parts):
        f.append("a part entry has no metrics")
    return f


def t_parts_expect_failure_is_prefixed():
    rep, _ = run_script(PARTS_BAD_LID, ["--no-render"])
    f = []
    if rep["ok"]:
        f.append("expected failure on the lid bbox")
    joined = " | ".join(rep["errors"])
    if "[lid]" not in joined:
        f.append(f"missing [lid] prefix (errors={rep['errors']})")
    if "Size along" not in joined:
        f.append(f"missing bbox mismatch message (errors={rep['errors']})")
    return f


def t_parts_assembly_section_render():
    rep, files = run_script(PARTS_ASSEMBLY, ["--section", "z:10", "--views", "iso"])
    f = []
    if not rep["ok"]:
        f.append(f"expected ok, errors={rep['errors']}")
    if "model_assembly.step" not in files:
        f.append(f"missing model_assembly.step (got {files})")
    if "view_iso_base.png" not in files:
        f.append(f"missing per-part render view_iso_base.png (got {files})")
    if not any("assembly" in n and "section" in n for n in files):
        f.append(f"missing assembly section render (got {files})")
    return f


def t_result_wins_over_parts_with_warning():
    rep, _ = run_script(BOX_40 + '\nPARTS = {"extra": result}\n', ["--no-render"])
    f = []
    if not rep["ok"]:
        f.append(f"expected ok (result wins), errors={rep['errors']}")
    joined = " | ".join(rep["warnings"])
    if "PARTS is ignored" not in joined:
        f.append(f"missing PARTS-ignored warning (warnings={rep['warnings']})")
    return f


def t_expect_ignored_in_parts_mode():
    rep, _ = run_script(
        'import cadquery as cq\nEXPECT = {"solids": 1}\n'
        'PARTS = {"base": cq.Workplane("XY").box(40, 30, 20, centered=(True, True, False))}\n',
        ["--no-render"],
    )
    f = []
    if not rep["ok"]:
        f.append(f"expected ok, errors={rep['errors']}")
    joined = " | ".join(rep["warnings"])
    if "PARTS_EXPECT" not in joined:
        f.append(f"missing PARTS_EXPECT warning (warnings={rep['warnings']})")
    return f


def t_example_enclosure_is_multi_part():
    rep, files = run_example("enclosure.py", ["--no-render"])
    f = []
    if not rep["ok"]:
        f.append(f"enclosure example failed: {rep['errors']}")
    for want in ("model_base.step", "model_base.stl", "model_lid.step", "model_lid.stl", "model_assembly.step"):
        if want not in files:
            f.append(f"missing {want} (got {files})")
    return f


def t_example_bracket_still_single_part():
    rep, files = run_example("bracket.py", ["--no-render"])
    f = []
    if not rep["ok"]:
        f.append(f"bracket example failed: {rep['errors']}")
    if "model.step" not in files:
        f.append(f"missing model.step (got {files})")
    return f


def t_example_pulley_still_single_part():
    rep, files = run_example("pulley.py", ["--no-render"])
    f = []
    if not rep["ok"]:
        f.append(f"pulley example failed: {rep['errors']}")
    if "model.step" not in files:
        f.append(f"missing model.step (got {files})")
    return f


TESTS = [
    t_single_box_fits,
    t_single_too_big_fails,
    t_print_check_can_be_disabled,
    t_printer_can_be_overridden,
    t_parts_export_each_piece,
    t_parts_expect_failure_is_prefixed,
    t_parts_assembly_section_render,
    t_result_wins_over_parts_with_warning,
    t_expect_ignored_in_parts_mode,
    t_example_enclosure_is_multi_part,
    t_example_bracket_still_single_part,
    t_example_pulley_still_single_part,
]


def main():
    failed = 0
    for test in TESTS:
        try:
            problems = test()
        except Exception as exc:  # noqa: BLE001
            problems = [f"exception: {exc!r}"]
        if problems:
            failed += 1
            print(f"FAIL {test.__name__}")
            for p in problems:
                print(f"     - {p}")
        else:
            print(f"PASS {test.__name__}")
    print(f"\n{len(TESTS) - failed}/{len(TESTS)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
