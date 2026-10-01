// Builds the deployment in Vercel's Build Output API format (.vercel/output):
// the React app as static files, and the whole API bundled by esbuild into one
// Node function. Bundling here means production never depends on how Vercel
// would compile individual TypeScript files. Run with: npm run build:vercel

import { build } from "esbuild";
import { cpSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { execSync } from "node:child_process";

const OUT = ".vercel/output";
const FUNC = `${OUT}/functions/api.func`;

rmSync(OUT, { recursive: true, force: true });

// 1. The React app.
execSync("npx vite build", { stdio: "inherit" });
mkdirSync(`${OUT}/static`, { recursive: true });
cpSync("dist", `${OUT}/static`, { recursive: true });

// 2. The API as one CommonJS file with every dependency inlined.
mkdirSync(FUNC, { recursive: true });
await build({
  entryPoints: ["server/vercel.ts"],
  outfile: `${FUNC}/index.js`,
  bundle: true,
  platform: "node",
  target: "node22",
  format: "cjs",
  sourcemap: true,
  logLevel: "info",
});
writeFileSync(
  `${FUNC}/.vc-config.json`,
  JSON.stringify(
    {
      runtime: "nodejs22.x",
      handler: "index.js",
      launcherType: "Nodejs",
      shouldAddSourcemapSupport: true,
      // Planning calls Claude and several data APIs; 15 to 25 seconds is normal.
      maxDuration: 120,
      // Next to the Neon database in us-east-1.
      regions: ["iad1"],
    },
    null,
    2,
  ),
);

// 3. Routing: every /api/* request goes to the one function (with the original
// path both set on the request and passed as __path), real files are served as
// they are, and every other path gets the React app, which routes on the client.
writeFileSync(
  `${OUT}/config.json`,
  JSON.stringify(
    {
      version: 3,
      routes: [
        {
          src: "^/api/(.*)$",
          dest: "/api?__path=$1",
          transforms: [{ type: "request.path", op: "set", args: "/api/$1" }],
        },
        { handle: "filesystem" },
        { src: "^/(.*)$", dest: "/index.html" },
      ],
    },
    null,
    2,
  ),
);

console.log(`Wrote ${OUT}`);
