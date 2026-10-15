import { cp, mkdir, rm, chmod } from 'node:fs/promises';
const dist = new URL('../dist/', import.meta.url);
await rm(dist, { recursive: true, force: true });
await mkdir(dist, { recursive: true });
await cp(new URL('../src/', import.meta.url), dist, { recursive: true });
await chmod(new URL('cli.js', dist), 0o755);
