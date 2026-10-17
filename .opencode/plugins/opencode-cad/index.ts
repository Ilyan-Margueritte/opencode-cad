import type { Plugin } from "@opencode/plugin"
import { spawnSync } from "node:child_process"
import fs from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"

// Self-contained plugin folder (project `.opencode/plugins/opencode-cad/`
// or global `~/.config/opencode/plugins/opencode-cad/`):
//   index.ts          <- this file
//   cad/cad_runner.py <- executes + validates + renders
//   cad/pitfalls.md   <- CadQuery cheat-sheet for the model
//   cad/examples/*.py <- working reference scripts
const HERE = path.dirname(fileURLToPath(import.meta.url))
const CAD_DIR = path.join(HERE, "cad")
const RUNNER = path.join(CAD_DIR, "cad_runner.py")
const PITFALLS = path.join(CAD_DIR, "pitfalls.md")
const EXAMPLES = path.join(CAD_DIR, "examples")
const REQUIREMENTS = path.join(CAD_DIR, "requirements.txt")

const RUN_TIMEOUT_MS = 180_000
const INSTALL_TIMEOUT_MS = 900_000

function exists(p: string): boolean {
  try {
    return fs.existsSync(p)
  } catch {
    return false
  }
}

function venvPython(root: string): string | null {
  const unix = path.join(root, ".venv", "bin", "python")
  const win = path.join(root, ".venv", "Scripts", "python.exe")
  if (exists(unix)) return unix
  if (exists(win)) return win
  return null
}

// CAD_PYTHON overrides everything, then <project>/.venv, then system python.
function pythonFor(root: string): string {
  return process.env.CAD_PYTHON || venvPython(root) || (process.platform === "win32" ? "python" : "python3")
}

type RunResult = { code: number; stdout: string; stderr: string; timedOut: boolean; spawnError?: string }

function run(cmd: string, args: string[], cwd: string, timeoutMs: number): RunResult {
  const r = spawnSync(cmd, args, { cwd, encoding: "utf8", timeout: timeoutMs, maxBuffer: 64 * 1024 * 1024 })
  const err = r.error as NodeJS.ErrnoException | undefined
  return {
    code: r.status ?? -1,
    stdout: r.stdout ?? "",
    stderr: r.stderr ?? "",
    timedOut: err?.code === "ETIMEDOUT",
    spawnError: err && err.code !== "ETIMEDOUT" ? err.message : undefined,
  }
}

function tail(s: string, n = 3000): string {
  return s.length > n ? "..." + s.slice(-n) : s
}

function formatReport(rep: any, outDir: string, root: string): string {
  const rel = (p: string) => path.relative(root, p) || p
  const L: string[] = []
  L.push(rep.ok ? "STATUS: OK" : "STATUS: FAILED")
  if (rep.printer?.check === false) L.push("(note: PRINT_CHECK is disabled in this script: the printer bed is not enforced)")

  if (rep.errors?.length) {
    L.push("", "ERRORS (fix these first):")
    for (const e of rep.errors) L.push(`- ${e}`)
  }
  if (rep.warnings?.length) {
    L.push("", "WARNINGS:")
    for (const w of rep.warnings) L.push(`- ${w}`)
  }

  const m = rep.metrics
  if (m) {
    L.push(
      "",
      "METRICS:",
      `- valid: ${m.valid}   solids: ${m.solids}   faces: ${m.faces}   edges: ${m.edges}`,
      `- volume: ${m.volume_mm3} mm3   surface area: ${m.area_mm2} mm2`,
      `- bbox size (X x Y x Z): ${m.bbox_dims_mm.join(" x ")} mm`,
      `- bbox min: ${m.bbox_min.join(", ")}   bbox max: ${m.bbox_max.join(", ")}`,
      `- center of mass: ${m.center.join(", ")}`,
    )
  }
  if (rep.parts?.length) {
    L.push("", "PARTS (each exported separately):")
    for (const p of rep.parts) {
      const pm = p.metrics ?? {}
      L.push(
        `- ${p.name}: ${pm.solids} solid(s), volume ${pm.volume_mm3} mm3, bbox size (X x Y x Z): ${(pm.bbox_dims_mm ?? []).join(" x ")} mm`,
      )
    }
  }
  if (rep.solids_detail?.length) {
    L.push("", "SOLIDS:")
    for (const s of rep.solids_detail) {
      L.push(`- #${s.index}: volume ${s.volume_mm3} mm3, min (${s.bbox_min.join(", ")}), max (${s.bbox_max.join(", ")})`)
    }
  }
  if (rep.params) L.push("", "PARAMS: " + JSON.stringify(rep.params))
  if (rep.script_stdout && rep.script_stdout.trim()) {
    L.push("", "SCRIPT OUTPUT:", tail(rep.script_stdout.trim(), 1500))
  }
  if (rep.files?.length) {
    L.push("", `FILES (in ${rel(outDir)}):`)
    for (const f of rep.files) L.push(`- ${rel(f)}`)
    L.push(
      "",
      "Next: open the PNG views with the Read tool if you can see images, or run the cad-critic subagent for a review.",
    )
  }
  return L.join("\n")
}

export default {
  id: "opencode-cad",

  async setup(ctx) {
    const projectRoot = () => String(ctx.location.project.canonical)

    const registration = await ctx.tool.transform((editor) => {
      editor.add({
        name: "cad_run",
        description:
          "Execute a CadQuery script (define `result` for a single part, or `PARTS` = {name: shape} for several printed parts plus optional `ASSEMBLY`), " +
          "validate the geometry, check optional expectations (EXPECT, or PARTS_EXPECT per part), enforce the printer bed (Creality K1 SE: 220 x 220 x 250 mm, 5 mm margin), " +
          "export STEP/STL (one set per part) and render PNG views. ALWAYS call this after every change to the script. " +
          "Returns a precise report: errors (with a trimmed traceback), warnings, volume, bounding box, solid count and output files.",
        input: {
          type: "object",
          properties: {
            script: { type: "string", description: "Path to the .py CadQuery script, relative to the project root" },
            expect: {
              type: "string",
              description: 'Optional JSON overriding EXPECT in the script, e.g. {"bbox":[80,40,50],"tol":0.5,"solids":1}. Ignored in multi-part scripts: use PARTS_EXPECT.',
            },
            section: {
              type: "string",
              description: "Optional cross-section as 'axis:offset' (e.g. 'z:10', 'y:0'). Cuts the part, or the ASSEMBLY in multi-part scripts. Useful to inspect hollow parts and fits.",
            },
            views: {
              type: "string",
              description: "Comma-separated views: iso,front,back,left,right,top,bottom (default: iso,front,top,right)",
            },
            render: { type: "boolean", description: "Set false to skip PNG rendering (faster)" },
          },
          required: ["script"],
          additionalProperties: false,
        },
        async execute(raw) {
          const args = raw as { script: string; expect?: string; section?: string; views?: string; render?: boolean }
          const root = projectRoot()
          try {
            const script = path.resolve(root, args.script)
            if (!exists(script)) return { content: `STATUS: FAILED\nERRORS:\n- Script not found: ${args.script}` }

            if (args.expect) {
              try {
                JSON.parse(args.expect)
              } catch {
                return { content: `STATUS: FAILED\nERRORS:\n- \`expect\` is not valid JSON: ${args.expect}` }
              }
            }

            const outDir = path.join(root, "cad_out", path.basename(script, ".py"))
            const cmd = [RUNNER, script, "--out", outDir]
            if (args.expect) cmd.push("--expect", args.expect)
            if (args.section) cmd.push("--section", args.section)
            if (args.views) cmd.push("--views", args.views)
            if (args.render === false) cmd.push("--no-render")

            const r = run(pythonFor(root), cmd, root, RUN_TIMEOUT_MS)

            if (r.spawnError) {
              return { content: `STATUS: FAILED\nERRORS:\n- Could not start Python (${r.spawnError}). Call cad_env with install=true.` }
            }
            if (r.timedOut) {
              return {
                content:
                  `STATUS: FAILED\nERRORS:\n- Timed out after ${RUN_TIMEOUT_MS / 1000}s. ` +
                  "Simplify: fewer fillets, one combined boolean instead of cutting in a loop, smaller arrays (see cad_pitfalls topic 'performance').",
              }
            }

            const lines = r.stdout.trim().split("\n")
            let rep: any = null
            for (let i = lines.length - 1; i >= 0 && !rep; i--) {
              try {
                rep = JSON.parse(lines[i])
              } catch {
                /* not the report line */
              }
            }
            if (!rep) {
              return { content: `STATUS: FAILED\nERRORS:\n- The runner crashed before producing a report.\n${tail(r.stderr || r.stdout)}` }
            }
            return { content: formatReport(rep, outDir, root) }
          } catch (e) {
            return { content: `STATUS: FAILED\nERRORS:\n- cad_run crashed: ${String(e)}` }
          }
        },
      })

      editor.add({
        name: "cad_env",
        description:
          "Check that Python + CadQuery + matplotlib are available, and optionally install them into a project-local .venv. " +
          "Set the CAD_PYTHON environment variable to use a specific interpreter.",
        input: {
          type: "object",
          properties: {
            install: {
              type: "boolean",
              description: "If true and something is missing: create .venv (unless CAD_PYTHON is set) and pip install requirements",
            },
          },
          additionalProperties: false,
        },
        async execute(raw) {
          const args = raw as { install?: boolean }
          const root = projectRoot()
          try {
            const check = () =>
              run(
                pythonFor(root),
                ["-c", "import cadquery, matplotlib, numpy; print('cadquery', cadquery.__version__)"],
                root,
                60_000,
              )

            let c = check()
            if (c.code === 0) return { content: `OK: ${c.stdout.trim()} (python: ${pythonFor(root)})` }
            if (!args.install) {
              return { content: `NOT READY.\n${tail(c.stderr || c.spawnError || "")}\nCall cad_env with install=true to set it up.` }
            }

            let py = process.env.CAD_PYTHON || venvPython(root)
            if (!py) {
              const sys = process.platform === "win32" ? "python" : "python3"
              const v = run(sys, ["-m", "venv", path.join(root, ".venv")], root, 120_000)
              if (v.code !== 0) return { content: `Could not create .venv:\n${tail(v.stderr || v.spawnError || "")}` }
              py = venvPython(root)
              if (!py) return { content: "A .venv was created but its python interpreter was not found." }
            }

            const p = run(py, ["-m", "pip", "install", "-r", REQUIREMENTS], root, INSTALL_TIMEOUT_MS)
            if (p.code !== 0) return { content: `pip install failed:\n${tail(p.stderr || p.spawnError || "")}` }

            c = check()
            return {
              content:
                c.code === 0
                  ? `INSTALLED: ${c.stdout.trim()} (python: ${pythonFor(root)})`
                  : `Installed, but the import still fails:\n${tail(c.stderr)}`,
            }
          } catch (e) {
            return { content: `cad_env crashed: ${String(e)}` }
          }
        },
      })

      editor.add({
        name: "cad_pitfalls",
        description:
          "Read the CadQuery cheat-sheet: API pitfalls and robust patterns (planes/axes, selectors, fillets, shell, revolve, booleans, arrays, debugging errors). " +
          "Call once before writing a script, and again with a topic whenever an error occurs.",
        input: {
          type: "object",
          properties: {
            topic: {
              type: "string",
              description: "Optional keyword to return only matching sections (e.g. 'fillet', 'revolve', 'shell', 'BRep_API')",
            },
          },
          additionalProperties: false,
        },
        async execute(raw) {
          const args = raw as { topic?: string }
          try {
            if (!exists(PITFALLS)) return { content: "pitfalls.md is missing from the plugin install." }
            const text = fs.readFileSync(PITFALLS, "utf8")
            if (!args.topic) return { content: text }
            const parts = text.split(/\n(?=## )/)
            const t = args.topic.toLowerCase()
            // Prefer sections whose heading matches; otherwise fall back to any section mentioning the topic
            const byHeading = parts.filter((p) => p.split("\n")[0].toLowerCase().includes(t))
            const hits = byHeading.length ? byHeading : parts.filter((p) => p.toLowerCase().includes(t))
            if (hits.length) return { content: hits.join("\n\n") }
            return { content: `No section mentions "${args.topic}". Sections:\n` + parts.map((p) => p.split("\n")[0]).join("\n") }
          } catch (e) {
            return { content: `cad_pitfalls crashed: ${String(e)}` }
          }
        },
      })

      editor.add({
        name: "cad_example",
        description:
          "List or read working CadQuery example scripts (two-part enclosure with assembly view, bracket with hole patterns, revolved pulley). " +
          "Call with no arguments to list them, or with a name to read the source and copy its patterns.",
        input: {
          type: "object",
          properties: {
            name: { type: "string", description: "Example name without .py; omit to list all" },
          },
          additionalProperties: false,
        },
        async execute(raw) {
          const args = raw as { name?: string }
          try {
            if (!exists(EXAMPLES)) return { content: "The examples folder is missing from the plugin install." }
            const files = fs
              .readdirSync(EXAMPLES)
              .filter((f) => f.endsWith(".py"))
              .sort()
            if (!args.name) {
              const rows = files.map((f) => {
                const src = fs.readFileSync(path.join(EXAMPLES, f), "utf8")
                const m = src.match(/^"""([\s\S]*?)"""/)
                const first = (m?.[1] ?? "").trim().split("\n")[0]
                return `- ${f.replace(/\.py$/, "")}: ${first}`
              })
              return { content: rows.join("\n") + "\n\nCall again with name=<example> to read the source." }
            }
            const wanted = args.name.replace(/\.py$/, "")
            const f = files.find((x) => x.replace(/\.py$/, "") === wanted)
            if (!f) {
              return { content: `Unknown example "${args.name}". Available: ${files.map((x) => x.replace(/\.py$/, "")).join(", ")}` }
            }
            return { content: fs.readFileSync(path.join(EXAMPLES, f), "utf8") }
          } catch (e) {
            return { content: `cad_example crashed: ${String(e)}` }
          }
        },
      })
    })

    return async () => {
      await registration.dispose()
    }
  },
} satisfies Plugin
