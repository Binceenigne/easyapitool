import {build} from 'esbuild';
import {dirname,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
const root=resolve(dirname(fileURLToPath(import.meta.url)),'..');
await build({entryPoints:[resolve(root,'frontend/sdk/agents-entry.js')],outfile:resolve(root,'frontend/scripts/agents-sdk.js'),bundle:true,platform:'browser',format:'iife',target:['chrome105'],minify:true,legalComments:'eof',conditions:['browser'],define:{'process.env.NODE_ENV':'"production"'}});
