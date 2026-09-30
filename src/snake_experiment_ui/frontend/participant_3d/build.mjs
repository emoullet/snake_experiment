import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { build } from 'esbuild';

const here = path.dirname(fileURLToPath(import.meta.url));
const inputs = ['package.json', 'pnpm-lock.yaml', 'pnpm-workspace.yaml', 'src/main.js'];
const hash = createHash('sha256');
for (const name of inputs) {
  hash.update(name);
  hash.update(await readFile(path.join(here, name)));
}
const sourceHash = hash.digest('hex');
await build({
  entryPoints: [path.join(here, 'src/main.js')],
  outfile: path.join(here, '../../snake_experiment_ui/static/participant_3d.bundle.js'),
  bundle: true,
  minify: true,
  target: ['es2020'],
  format: 'iife',
  legalComments: 'inline',
  banner: { js: `// source-sha256: ${sourceHash}` },
});
