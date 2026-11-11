// The platform controls every tool schema and every live tool authorization.
import {initialContext,toolBridge,fail,object,bounded,limitedSteps} from './runtime-memory.mjs';
export const inject=['tools'];
export async function apply(ctx) {
  const context=await initialContext();
  if(context.mode!=='agent'||!Array.isArray(context.tools)||context.tools.length<1||context.tools.length>12)fail();
  const allowed=new Set(context.tools.map(tool=>tool.name));
  if(allowed.size!==context.tools.length)fail();
  ctx.tools.guard(exec=>allowed.has(exec.name)?undefined:'The platform has not authorized this tool.');
  limitedSteps(ctx);
  let calls=0,totalBytes=0;
  for(const tool of context.tools){
    if(!object(tool)||typeof tool.name!=='string'||!/^[A-Za-z][A-Za-z0-9_.-]{0,63}$/.test(tool.name)||!object(tool.parameters))fail();
    ctx.tools.register({name:tool.name,description:tool.description,parameters:tool.parameters,
      output:{schema:{type:'string'},render:(_args,value)=>[{type:'text',text:value}]},
      async execute(args){
        if(++calls>16||!object(args))fail();bounded(args,65536);
        const value=await toolBridge(context.tool_bridge,tool.name,args);
        const text=JSON.stringify(bounded(value));totalBytes+=Buffer.byteLength(text,'utf8');if(totalBytes>1048576)fail();
        return text;
      },
    });
  }
}
