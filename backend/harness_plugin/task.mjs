// Bounded text/JSON assistance; submitting a result is its only tool.
import {initialContext,fail,object,bounded,limitedSteps} from './runtime-memory.mjs';
export const inject=['tools'];
export async function apply(ctx) {
  const context=await initialContext();
  if(context.mode!=='task'||typeof context.json_mode!=='boolean')fail();
  ctx.tools.guard(exec=>exec.name==='submit_task_result'?undefined:'This task only permits submitting a result.');
  limitedSteps(ctx);
  let submitted=0;
  ctx.tools.register({
    name:'submit_task_result',description:'Return the complete final requested text or JSON value. This only submits an in-memory answer and performs no business action.',
    parameters:{type:'object',properties:{text:{type:'string',minLength:1,maxLength:262144}},required:['text'],additionalProperties:false},
    output:{schema:{type:'string'},render:(_args,value)=>[{type:'text',text:value}]},
    async execute(args){
      if(!object(args)||Object.keys(args).join(',')!=='text'||typeof args.text!=='string'||!args.text.trim()||++submitted>8)fail();
      bounded(args);if(context.json_mode){try{JSON.parse(args.text);}catch{fail();}}
      return JSON.stringify({ontology_task_result:'1.0',text:args.text});
    },
  });
}
