import {readFile, writeFile} from 'node:fs/promises';
import {dirname, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const source = await readFile(resolve(root, 'mirra_agents/model_limits.json'), 'utf8');
const registry = JSON.parse(source);
if (registry.schema_version !== 1 || registry.compaction_basis !== 'usable_input_budget' || registry.compaction_ratio !== 0.9) {
    throw new Error('Unsupported Agent model registry schema');
}
await writeFile(resolve(root, 'gateway/agent_model_limits.json'), source, 'utf8');
