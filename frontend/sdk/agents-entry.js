import {Agent, Runner, OpenAIResponsesModel, webSearchTool, setTracingDisabled, tool, MaxTurnsExceededError, ModelRefusalError, UserError, ModelBehaviorError} from '@openai/agents';
import OpenAI from 'openai';
import modelLimits from '../../mirra_agents/model_limits.json';
setTracingDisabled(true);
function documentedLimits(model) {
 const identifier=String(model||'').trim().toLowerCase();
 return modelLimits.models[modelLimits.aliases?.[identifier]||identifier];
}
function modelPolicy(model) {
 const limits=documentedLimits(model);
 if(!limits)return{};
 const usable=Math.min(limits.max_input_tokens??limits.context_window,limits.context_window-limits.max_output_tokens);
 return{maxTokens:limits.max_output_tokens,truncation:'disabled',...(limits.server_compaction&&usable>0?{contextManagement:[{type:'compaction',compactThreshold:Math.floor(usable*9/10)}]}:{})};
}
const documentedReasoning=model=>Boolean(documentedLimits(model)?.reasoning);
function compactedInput(input,updates) {
 if(!Array.isArray(input))return input;
 for(let index=input.length-1;index>=0;index--){
  const marker=input[index];if(marker?.type!=='compaction'||typeof marker.encrypted_content!=='string'||!marker.encrypted_content)continue;
  const suffix=input.slice(index),recorded=updates.get(marker.id);if(!recorded?.length)return suffix;
  let end=1;while(suffix[end]?.type==='configuration_update')end++;
  const seen=new Set(recorded.map(item=>JSON.stringify(item)));
  return[suffix[0],...recorded,...suffix.slice(1,end).filter(item=>!seen.has(JSON.stringify(item))),...suffix.slice(end)];
 }
 return input;
}
function linkedSignals(...signals) {
 const controller=new AbortController();
 const listeners=signals.filter(Boolean).map(signal=>{const abort=()=>controller.abort(signal.reason);if(signal.aborted)abort();else signal.addEventListener('abort',abort,{once:true});return{signal,abort};});
 return{signal:controller.signal,cleanup:()=>listeners.forEach(({signal,abort})=>signal.removeEventListener('abort',abort))};
}
const inspectSchema={type:'object',properties:{image_urls:{type:'array',items:{type:'string'},maxItems:12}},required:['image_urls'],additionalProperties:false};
const selectSchema={type:'object',properties:{candidate_ids:{type:'array',items:{type:'string'},maxItems:8},rationale:{type:'string'}},required:['candidate_ids','rationale'],additionalProperties:false};
const publicCandidate=value=>Object.fromEntries(['id','title','caption','imageUrl','thumbnailUrl','sourceUrl','width','height'].filter(key=>value[key]!=null).map(key=>[key,value[key]]));
export async function runNativeAgent({model,effort,instructions,input,webSearch=false,signal,transport,onEvent=()=>{},inspectReferences,selectReferences,maxInspectReferences=0,maxAdoptReferences=0,maxTurns=8,getRequestCount}) {
 if(typeof transport!=='function')throw new Error('Native Agents transport is unavailable');
 let lastResponseMeta={},turn=0,localToolCount=0,selected=[],selectionMade=false;
 const images=new Map(),sources=new Map(),inspected=new Map(),attempted=new Set(),webCalls=new Set(),toolCalls=new Set(),compactionUpdates=new Map();
 const stats=()=>{const requests=typeof getRequestCount==='function'?Number(getRequestCount()):NaN;return{turnCount:turn,requestCount:Number.isFinite(requests)&&requests>=0?requests:turn,toolCallCount:toolCalls.size,webSearchCallCount:webCalls.size,localToolCallCount:localToolCount};};
 const emit=event=>onEvent({...event,...stats()});
 const canInspect=webSearch&&maxInspectReferences>0&&typeof inspectReferences==='function';
 const localTools=[];
 function execute(name,callback){return async(args,_context,details)=>{
  const callId=details?.toolCall?.callId||details?.toolCall?.id||`native-${name}-${toolCalls.size+1}`;
  if(!toolCalls.has(callId)){toolCalls.add(callId);localToolCount++;}
  emit({type:'tool_started',turn,callId,tool:name,arguments:args});
  try{const result=await callback(args);const first=Array.isArray(result)?result[0]?.text:result;let metadata;try{metadata=typeof first==='string'?JSON.parse(first):first;}catch{metadata={};}
   emit({type:'tool_completed',turn,callId,tool:name,resultCount:metadata?.candidates?.length??metadata?.selectedIds?.length??0,result:{candidateCount:metadata?.candidates?.length,failureCount:metadata?.failures?.length,selectedIds:metadata?.selectedIds,rationale:metadata?.rationale,...(metadata?.error?{ok:false,error:metadata.error.message,code:metadata.error.code}:{})}});return result;
  }catch(error){emit({type:'tool_failed',turn,callId,tool:name,error:error.message});throw error;}
 };}
 if(canInspect){
  localTools.push(tool({name:'inspect_visual_references',description:'下载并检视本轮OpenAI官方图片搜索的真实候选。image_urls为空时读取当前候选，只允许实际搜索结果的图片URL。',parameters:inspectSchema,errorFunction:null,
   execute:execute('inspect_visual_references',async args=>{
    const urls=args.image_urls.length?args.image_urls:[...images.keys()].slice(0,12);
    if(urls.some(url=>!images.has(url)))return JSON.stringify({error:{code:'unknown_reference',message:'只能检视本轮OpenAI图片搜索返回的图片URL'}});
    urls.forEach(url=>attempted.add(url));const fresh=urls.filter(url=>![...inspected.values()].some(item=>item.imageUrl===url)).slice(0,Math.max(0,maxInspectReferences-inspected.size));
    const loaded=await inspectReferences(fresh,signal),output=[],metadata={candidates:[],failures:loaded.failures||[]};
    for(const candidate of loaded.candidates||[]){if(inspected.has(candidate.id)||inspected.size>=maxInspectReferences||!candidate.previewDataUrl?.startsWith('data:image/'))continue;inspected.set(candidate.id,candidate);selectionMade=false;metadata.candidates.push(publicCandidate(candidate));output.push({type:'image',image:candidate.previewDataUrl});}
    return[{type:'text',text:JSON.stringify(metadata)},...output];
   })}));
  localTools.push(tool({name:'select_visual_references',description:`从已检视的候选选择实际用于生图的ID，最多${maxAdoptReferences}张。不采用时传空数组并说明原因。`,parameters:selectSchema,errorFunction:null,
   execute:execute('select_visual_references',async args=>{const ids=[...new Set(args.candidate_ids)];if(typeof args.rationale!=='string'||!args.rationale.trim()||args.rationale.length>1600)return JSON.stringify({error:{code:'invalid_reference_selection',message:'请简短说明采用或不采用的原因'}});if(ids.length>maxAdoptReferences||ids.some(id=>!inspected.has(id)))return JSON.stringify({error:{code:'invalid_reference_selection',message:'只能采用已检视且在当前参考槽位上限内的候选'}});selected=ids.map(id=>inspected.get(id));selectionMade=true;if(selectReferences)await selectReferences(selected,args.rationale);return JSON.stringify({selectedIds:ids,rationale:args.rationale});})}));
  instructions+=`\n得到相关图片候选后先调用inspect_visual_references检视真实画面，再调用select_visual_references明确采用哪些图（最多${maxAdoptReferences}张），或传空数组说明不采用原因。不能把网页引用当成真实图片。`;
 }
 const client=new OpenAI({apiKey:'mirra-native-secret-placeholder',baseURL:'https://mirra-native.invalid/v1',dangerouslyAllowBrowser:true,maxRetries:0,timeout:16*60*1000,
  fetch:async(url,init={})=>{
   if(!String(url).endsWith('/responses')||init.method!=='POST')throw new Error('Unsupported SDK transport route');if(signal?.aborted)throw new DOMException('已取消','AbortError');
   const body=JSON.parse(init.body||'{}');if(body.stream===true)throw new Error('Streaming SDK transport is not enabled');
   // SDK 0.22 trims to the latest opaque compaction, but does not create run
   // items for its configuration_update suffix. Replay those accepted provider
   // items unchanged so settings survive both tool loops and semantic resumes.
   body.input=compactedInput(body.input,compactionUpdates);
   const linked=linkedSignals(signal,init.signal);turn++;emit({type:'turn_started',turn,attempt:1,maxAttempts:3});
   try{const result=await transport(body,linked.signal),data=typeof result.body==='string'?JSON.parse(result.body):result.body;lastResponseMeta={status:result.status||200,request_id:result.headers?.['x-request-id']||data?.id};
    if((result.status||200)<300&&data?.status==='completed')for(const[index,item]of(data.output||[]).entries()){
     if(item.type!=='compaction'||!item.id||!item.encrypted_content)continue;
     const updates=[];for(const following of data.output.slice(index+1)){if(following.type!=='configuration_update')break;updates.push(following);}compactionUpdates.set(item.id,updates);
    }
    for(const item of data?.output||[]){
     if(item.type==='web_search_call'){const callId=item.id||`web-${turn}-${webCalls.size+1}`;if(!webCalls.has(callId)){webCalls.add(callId);toolCalls.add(callId);emit({type:'web_search_started',turn,callId,tool:'web_search',action:item.action?.type||'search',query:item.action?.query});}
      for(const image of item.results||[])if(image.type==='image_result'&&image.image_url?.startsWith('https://')&&images.size<48)images.set(image.image_url,image);
      for(const source of item.action?.sources||[])if(/^https?:\/\//.test(source.url||''))sources.set(source.url,{url:source.url,title:source.title||''});
      emit({type:'web_search_completed',turn,callId,tool:'web_search',action:item.action?.type||'search',query:item.action?.query,queries:item.action?.queries,url:item.action?.url,sources:[...sources.values()],images:item.results?.filter(item=>item.type==='image_result')||[],resultCount:item.results?.length||item.action?.sources?.length||0});
     }
     for(const content of item.content||[]){for(const citation of content.annotations||[])if(citation.type==='url_citation'&&/^https?:\/\//.test(citation.url||''))sources.set(citation.url,{url:citation.url,title:citation.title||''});if(content.type==='output_text'&&content.text)emit({type:'text_delta',turn,delta:content.text});}
    }
    emit({type:'turn_completed',turn,usage:data?.usage||{}});return new Response(typeof result.body==='string'?result.body:JSON.stringify(result.body),{status:result.status||200,headers:{'content-type':'application/json',...result.headers}});
   }finally{linked.cleanup();}
  }});
 const agent=new Agent({name:'Mirra image prompt planner',instructions,model:new OpenAIResponsesModel(client,model),modelSettings:{store:false,...modelPolicy(model),preserveRawUsage:true,parallelToolCalls:false,reasoning:{effort},retry:{maxRetries:0},providerData:{...(webSearch?{max_tool_calls:3}:{}),include:[...(documentedReasoning(model)?['reasoning.encrypted_content']:[]),...(webSearch?['web_search_call.action.sources','web_search_call.results']:[])]}},tools:[...(webSearch?[webSearchTool({searchContentTypes:['image','text'],imageSettings:{maxResults:8,caption:true}})]:[]),...localTools]});
 const runner=new Runner({tracingDisabled:true,traceIncludeSensitiveData:false});let result,history=input;const usage={inputTokens:0,outputTokens:0,totalTokens:0,cachedTokens:0};
 try{for(;;){if(turn>=maxTurns)throw Object.assign(new Error('Agent已达到处理轮数上限'),{code:'max_turns'});result=await runner.run(agent,history,{signal,maxTurns:maxTurns-turn});const part=result.state?.usage;if(part)for(const key of Object.keys(usage))usage[key]+=Number(part[key])||0;
   let feedback='';if(canInspect&&images.size&&!attempted.size)feedback='官方图片搜索已返回候选，请先调用inspect_visual_references检视真实图片，再完成方案。';else if(inspected.size&&!selectionMade)feedback='请调用select_visual_references明确采用哪些已检视的图片，或传空数组说明不采用原因，然后完成最终提示词。';if(!feedback)break;history=[...result.history,{role:'user',content:feedback}];
  }}catch(error){if(error instanceof MaxTurnsExceededError)error.code='max_turns';else if(error instanceof ModelRefusalError)error.code='model_refusal';else if(error instanceof UserError)error.code='invalid_request';else if(error instanceof ModelBehaviorError)error.code='invalid_agent_output';if(!error.status)error.status=lastResponseMeta.status;if(!error.request_id)error.request_id=lastResponseMeta.request_id;error.reasoningStats=stats();throw error;}
 const prompt=String(result.finalOutput||'').trim();if(!prompt){const error=new Error('Agent 未返回可用的提示词');error.code='empty_agent_output';Object.assign(error,lastResponseMeta);throw error;}
 return{prompt,usage:{...usage,callCount:turn,hasTokenUsage:true},reasoningStats:stats(),webSearchUsed:webCalls.size>0,webSearchSources:[...sources.values()],webReferences:selected.map(({previewDataUrl,...item})=>item),lastResponseId:result.lastResponseId};
}
window.MirraAgentsSDK={version:'0.22.0',runNativeAgent};
