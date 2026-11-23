import {Agent, Runner, OpenAIResponsesModel, webSearchTool, setTracingDisabled,
  MaxTurnsExceededError, ModelRefusalError, UserError, ModelBehaviorError} from '@openai/agents';
import OpenAI from 'openai';
setTracingDisabled(true);

function linkedSignals(...signals) {
  const controller=new AbortController();
  const listeners=signals.filter(Boolean).map(signal=>{
    const abort=()=>controller.abort(signal.reason);
    if(signal.aborted)abort();else signal.addEventListener('abort',abort,{once:true});
    return {signal,abort};
  });
  return {signal:controller.signal,cleanup:()=>listeners.forEach(({signal,abort})=>signal.removeEventListener('abort',abort))};
}

export async function runNativeAgent({model, effort, instructions, input, webSearch=false, signal, transport}) {
  if (typeof transport !== 'function') throw new Error('Native Agents transport is unavailable');
  let lastResponseMeta={};
  const client = new OpenAI({apiKey:'mirra-native-secret-placeholder',baseURL:'https://mirra-native.invalid/v1',dangerouslyAllowBrowser:true,maxRetries:0,
    timeout:16*60*1000, // Native's 15-minute socket timeout owns retryable timeouts before this final failsafe.
    fetch:async(url,init={})=>{
      if(!String(url).endsWith('/responses') || init.method!=='POST')throw new Error('Unsupported SDK transport route');
      if(signal?.aborted)throw new DOMException('已取消','AbortError');
      const body=JSON.parse(init.body || '{}');
      if(body.stream===true)throw new Error('Streaming SDK transport is not enabled');
      const linked=linkedSignals(signal,init.signal);
      try {
        const result=await transport(body,linked.signal);
        const data=typeof result.body==='string'?JSON.parse(result.body):result.body;
        lastResponseMeta={status:result.status || 200,request_id:result.headers?.['x-request-id'] || data?.id};
        return new Response(typeof result.body==='string'?result.body:JSON.stringify(result.body),{status:result.status || 200,headers:{'content-type':'application/json',...result.headers}});
      } finally { linked.cleanup(); }
    }});
  const agent=new Agent({name:'Mirra image prompt planner',instructions,model:new OpenAIResponsesModel(client,model),
    modelSettings:{store:false,reasoning:{effort},retry:{maxRetries:0},
      ...(webSearch ? {providerData:{max_tool_calls:3}} : {})},tools:webSearch?[webSearchTool()]:[]});
  const runner=new Runner({tracingDisabled:true,traceIncludeSensitiveData:false});
  let result;
  try { result=await runner.run(agent,input,{signal,maxTurns:8}); }
  catch(error) {
    // instanceof survives minification, unlike the SDK's constructor-based error names.
    if(error instanceof MaxTurnsExceededError) error.code='max_turns';
    else if(error instanceof ModelRefusalError) error.code='model_refusal';
    else if(error instanceof UserError) error.code='invalid_request';
    else if(error instanceof ModelBehaviorError) error.code='invalid_agent_output';
    if(!error.status) error.status=lastResponseMeta.status;
    if(!error.request_id) error.request_id=lastResponseMeta.request_id;
    throw error;
  }
  const prompt=String(result.finalOutput || '').trim();
  if(!prompt){const error=new Error('Agent 未返回可用的提示词');error.code='empty_agent_output';Object.assign(error,lastResponseMeta);throw error;}
  const usage=result.state?.usage;
  return {prompt,usage:usage ? {...usage,callCount:usage.requests,hasTokenUsage:true} : null,
    webSearchUsed:result.rawResponses.some(response=>response.output.some(item=>
      item.type==='web_search_call' || item.type==='hosted_tool_call' && item.name==='web_search')),
    lastResponseId:result.lastResponseId};
}
window.MirraAgentsSDK={version:'0.22.0',runNativeAgent};
