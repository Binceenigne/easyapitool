const assert = require('node:assert/strict');
const {test, before} = require('node:test');
const {build} = require('esbuild');
const path = require('node:path');
const registry = require('../mirra_agents/model_limits.json');
let runNativeAgent;

before(async () => {
    const bundle = await build({entryPoints: [path.resolve(__dirname, '../frontend/sdk/agents-entry.js')],
        bundle: true, platform: 'node', format: 'cjs', write: false, packages: 'external'});
    const module = {exports: {}};
    new Function('require', 'module', 'exports', 'window', bundle.outputFiles[0].text)(require, module, module.exports, {});
    runNativeAgent = module.exports.runNativeAgent;
});

const message = text => ({type: 'message', id: `msg-${text}`, role: 'assistant', status: 'completed',
    content: [{type: 'output_text', text, annotations: []}]});
const response = (model, output) => ({status: 200, body: {id: 'resp-fixture', object: 'response', created_at: 1,
    model, status: 'completed', output, usage: {input_tokens: 10, output_tokens: 3, total_tokens: 13,
        input_tokens_details: {cached_tokens: 2}, output_tokens_details: {reasoning_tokens: 1}}}});
const setup = {model: 'gpt-6.1-terra', effort: 'high', instructions: 'Plan an image', input: 'Original owner prompt'};

test('native SDK uses the official maximum output and 90% of usable input', async () => {
    let request;
    const result = await runNativeAgent({...setup, transport: async body => {
        request = body;
        return response(body.model, [message('final')]);
    }});
    assert.equal(result.prompt, 'final');
    assert.equal(request.max_output_tokens, 128000);
    assert.deepEqual(request.context_management, [{type: 'compaction', compact_threshold: 829800}]);
    assert.equal(request.truncation, 'disabled');
    assert.equal(request.store, false);
    assert(request.include.includes('reasoning.encrypted_content'));
});

test('undocumented model aliases keep their provider limits', async () => {
    let request;
    await runNativeAgent({...setup, model: 'gpt-6.1-terra-private', transport: async body => {
        request = body;
        return response(body.model, [message('custom-final')]);
    }});
    assert.equal(Object.hasOwn(request, 'max_output_tokens'), false);
    assert.equal(Object.hasOwn(request, 'context_management'), false);
});

test('every canonical model and exact alias stays consistent with the shared registry', async () => {
    const ids = [...Object.keys(registry.models), ...Object.keys(registry.aliases)];
    for (const model of ids) {
        let request;
        await runNativeAgent({...setup, model, transport: async body => {
            request = body;
            return response(body.model, [message('registry-final')]);
        }});
        const expected = registry.models[registry.aliases[model] || model];
        assert.equal(request.max_output_tokens, expected.max_output_tokens, model);
        if (expected.server_compaction) {
            const usable = Math.min(expected.max_input_tokens ?? expected.context_window,
                expected.context_window - expected.max_output_tokens);
            assert.deepEqual(request.context_management, [{type: 'compaction', compact_threshold: Math.floor(usable * 9 / 10)}], model);
        } else assert.equal(Object.hasOwn(request, 'context_management'), false, model);
        assert.equal(request.include.includes('reasoning.encrypted_content'), expected.reasoning, model);
    }
});

test('compaction survives native SDK tool turns and semantic continuation without losing vision or settings', async () => {
    const requests = [], events = [], selections = [];
    const compaction = id => ({type: 'compaction', id, encrypted_content: `opaque+${id}/payload==`});
    const configuration = {type: 'configuration_update', reasoning: {effort: 'high'}};
    const imageUrl = 'https://cdn.example/real-product.png';
    const call = (name, args, index) => ({type: 'function_call', name, id: `fc-${index}`, call_id: `call-${index}`,
        arguments: JSON.stringify(args), status: 'completed'});
    const search = {type: 'web_search_call', id: 'web-one', status: 'completed', action: {type: 'search', query: 'product', sources: []},
        results: [{type: 'image_result', image_url: imageUrl, source_website_url: 'https://maker.example/product', caption: 'Product'}]};
    const result = await runNativeAgent({...setup, webSearch: true, maxTurns: 8, maxInspectReferences: 2, maxAdoptReferences: 1,
        onEvent: event => events.push(event),
        inspectReferences: async urls => {
            assert.deepEqual(urls, [imageUrl]);
            return {candidates: [{id: 'webref-product', imageUrl,
                previewDataUrl: 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6D0YAAAAASUVORK5CYII='}], failures: []};
        },
        selectReferences: async (refs, rationale) => selections.push({refs, rationale}),
        transport: async body => {
            requests.push(body);
            const step = requests.length;
            const output = ({
                1: [search, compaction('cmp-one'), configuration, message('premature')],
                2: [call('inspect_visual_references', {image_urls: []}, 2)],
                3: [compaction('cmp-two'), configuration, message('inspect-complete')],
                4: [call('select_visual_references', {candidate_ids: ['webref-product'], rationale: 'Preserve product shape'}, 4)],
                5: [message('faithful-final')],
            })[step];
            assert(output, 'completed tools or semantic feedback repeated unexpectedly');
            return response(body.model, output);
        }});
    assert.equal(result.prompt, 'faithful-final');
    assert.equal(requests.length, 5);
    for (const [index, id] of [[1, 'cmp-one'], [3, 'cmp-two']]) {
        assert.equal(requests[index].input[0].type, 'compaction');
        assert.equal(requests[index].input[0].encrypted_content, `opaque+${id}/payload==`);
        assert.equal(requests[index].input[1].type, 'configuration_update');
        assert.equal(requests[index].input[1].reasoning.effort, 'high');
        assert(!JSON.stringify(requests[index].input).includes('Original owner prompt'));
    }
    assert(JSON.stringify(requests[2].input).includes('data:image/png;base64,'), 'inspected vision did not reach the next model turn');
    for (const request of requests) {
        assert.equal(request.store, false);
        assert.equal(request.max_output_tokens, 128000);
        assert.equal(request.context_management[0].compact_threshold, 829800);
        assert.equal(request.tools.filter(tool => tool.type === 'function').length, 2);
        assert(request.tools.some(tool => tool.type === 'web_search'));
    }
    assert.equal(selections.length, 1);
    assert.equal(result.webReferences[0].id, 'webref-product');
    assert.equal(result.reasoningStats.turnCount, 5);
    assert.equal(result.reasoningStats.toolCallCount, 3);
    assert.equal(result.usage.totalTokens, 65);
    assert.equal(events.filter(event => event.type === 'tool_completed').length, 2);
});
