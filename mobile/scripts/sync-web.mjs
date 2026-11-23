import { cp, mkdir, rm } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';

const mobileRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const projectRoot = resolve(mobileRoot, '..');
const sourceRoot = resolve(projectRoot, 'frontend');
const targetRoot = resolve(mobileRoot, 'web');

execFileSync(process.execPath, [resolve(projectRoot, 'scripts/build-agents.mjs')], { cwd: projectRoot, stdio: 'inherit' });
await rm(targetRoot, { recursive: true, force: true });
await mkdir(targetRoot, { recursive: true });
await cp(sourceRoot, targetRoot, { recursive: true });
