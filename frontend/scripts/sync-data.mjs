// Copy the committed snapshots into public/ so the app reads them as static files.
// The Today screen never calls a backend: brief.json ships with the bundle.
import { cpSync, existsSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const src = join(here, "..", "..", "data", "snapshots");
const dst = join(here, "..", "public", "data");
mkdirSync(dst, { recursive: true });
if (existsSync(join(src, "brief.json"))) {
  cpSync(src, dst, { recursive: true, filter: (p) => !p.endsWith(".gitkeep") });
  console.log("snapshots -> public/data");
} else {
  console.warn("no data/snapshots/brief.json; run backend/scripts/build_brief.py");
}
