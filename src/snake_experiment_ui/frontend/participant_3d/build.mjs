import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { build } from 'esbuild';

const here = path.dirname(fileURLToPath(import.meta.url));
const inputs = [
  'package.json', 'pnpm-lock.yaml', 'pnpm-workspace.yaml',
  'src/main.js', 'src/participant.js', 'src/animation.js',
  'src/mapping.js', 'src/kinematics.js',
];
const hash = createHash('sha256');
for (const name of inputs) {
  hash.update(name);
  hash.update(await readFile(path.join(here, name)));
}
const sourceHash = hash.digest('hex');
for (const [entry, output] of [
  ['main.js', 'participant_3d.bundle.js'],
  ['participant.js', 'participant_mapping.bundle.js'],
]) {
  await build({
    entryPoints: [path.join(here, 'src', entry)],
    outfile: path.join(here, '../../snake_experiment_ui/static', output),
    bundle: true,
    minify: true,
    target: ['es2020'],
    format: 'iife',
    legalComments: 'inline',
    banner: { js: `// source-sha256: ${sourceHash}` },
  });
}
