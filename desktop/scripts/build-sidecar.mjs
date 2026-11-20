import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, copyFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const desktop = fileURLToPath(new URL("..", import.meta.url));
const root = path.dirname(desktop);
const windows = process.platform === "win32";
const localPython = path.join(
  root,
  ".venv",
  windows ? "Scripts/python.exe" : "bin/python",
);
const python =
  process.env.LEGENDARY_DESKTOP_PYTHON ||
  (existsSync(localPython) ? localPython : windows ? "python" : "python3");
const targetArg = process.argv.indexOf("--target");
let target =
  targetArg >= 0
    ? process.argv[targetArg + 1]
    : process.env.TAURI_ENV_TARGET_TRIPLE;
if (!target) {
  const rustc = spawnSync("rustc", ["-vV"], { encoding: "utf8" });
  target = rustc.stdout?.match(/^host: (.+)$/m)?.[1];
}
if (!target)
  throw new Error(
    "Install Rust or pass --target <host-triple>. Sidecars must be built on their target OS/architecture.",
  );

const work = path.join(desktop, ".sidecar-build");
const probe = spawnSync(
  python,
  ["-c", "import legendary.core; import PyInstaller"],
  { cwd: root, encoding: "utf8" },
);
if (probe.error) throw probe.error;
if (probe.status !== 0) {
  throw new Error(
    `The sidecar requires Python 3.10–3.13 and the project/build dependencies. Run uv sync --python 3.12 --extra pyinstaller_build.\n${probe.stderr}`,
  );
}
const result = spawnSync(
  python,
  [
    "-m",
    "PyInstaller",
    "--noconfirm",
    "--clean",
    "--onefile",
    "--name",
    "legendary-sidecar",
    "--paths",
    root,
    "--additional-hooks-dir",
    path.join(desktop, "pyinstaller-hooks"),
    "--distpath",
    path.join(work, "dist"),
    "--workpath",
    path.join(work, "work"),
    "--specpath",
    work,
    path.join(desktop, "sidecar_entry.py"),
  ],
  { cwd: root, stdio: "inherit" },
);
if (result.error) throw result.error;
if (result.status !== 0) process.exit(result.status ?? 1);

const binaries = path.join(desktop, "src-tauri", "binaries");
mkdirSync(binaries, { recursive: true });
const suffix = windows ? ".exe" : "";
copyFileSync(
  path.join(work, "dist", `legendary-sidecar${suffix}`),
  path.join(binaries, `legendary-sidecar-${target}${suffix}`),
);
console.log(`Built legendary-sidecar for ${target}`);
