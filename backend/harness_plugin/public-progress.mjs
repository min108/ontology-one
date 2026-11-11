// Fixed observer of the official public stream seam; never a model-selected tool.
import {request as httpRequest} from 'node:http';

export function apply(ctx) {
  const endpoint=process.env.ONTOLOGY_PUBLIC_PROGRESS_URL,token=process.env.ONTOLOGY_PUBLIC_PROGRESS_TOKEN;
  const expected=process.env.ONTOLOGY_PUBLIC_PROGRESS_SESSION;
  delete process.env.ONTOLOGY_PUBLIC_PROGRESS_URL;
  delete process.env.ONTOLOGY_PUBLIC_PROGRESS_TOKEN;
  delete process.env.ONTOLOGY_PUBLIC_PROGRESS_SESSION;
  if(typeof endpoint!=='string'||!/^http:\/\/127\.0\.0\.1:[0-9]{1,5}\/public-progress$/.test(endpoint)
     ||typeof token!=='string'||!/^[A-Za-z0-9_-]{43}$/.test(token)||typeof expected!=='string')throw new Error('Public progress channel unavailable.');
  const url=new URL(endpoint);
  let pending=Promise.resolve(),text='',message='',timer=null;
  const send=value=>{
    const payload=JSON.stringify(value);
    pending=pending.then(()=>new Promise(resolve=>{
      const req=httpRequest({hostname:'127.0.0.1',port:url.port,path:url.pathname,method:'POST',agent:false,
        headers:{Authorization:'Bearer '+token,'Content-Type':'application/json','Content-Length':Buffer.byteLength(payload)}},res=>{res.resume();res.on('end',resolve);res.on('error',resolve)});
      req.on('error',resolve);req.setTimeout(1500,()=>{req.destroy();resolve()});req.end(payload);
    }));
  };
  const flush=()=>{
    if(timer){clearTimeout(timer);timer=null}
    if(text){
      // Keep full Unicode characters together and bound even a coalesced provider
      // chunk before delivering it through the authenticated memory channel.
      const characters=Array.from(text);text='';
      for(let index=0;index<characters.length;index+=1024)
        send({type:'answer_delta',message_id:message,text:characters.slice(index,index+1024).join('')});
    }
  };
  ctx.on('agent/assistant-stream',({agent,frame})=>{
    if(String(agent.session.id)!==expected||!frame||typeof frame.attemptId!=='string')return;
    if(frame.type==='start'){
      flush();message=frame.attemptId;send({type:'answer_start',message_id:message});
    }else if(frame.type==='chunk'&&frame.chunk?.type==='text-delta'&&typeof frame.chunk.text==='string'){
      if(message!==frame.attemptId){flush();message=frame.attemptId;send({type:'answer_start',message_id:message})}
      // Reasoning, tool input and raw tool result chunks never enter this channel.
      text+=frame.chunk.text;
      if(Buffer.byteLength(text)>4096)flush();else if(!timer)timer=setTimeout(flush,25);
    }else if(frame.type==='end'){
      flush();send({type:'answer_end',message_id:frame.attemptId});
    }
  });
}
