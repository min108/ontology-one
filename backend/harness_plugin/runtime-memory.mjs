// Fixed memory channel; no host-file fallback and no proxy or redirects.
import {request as httpRequest} from 'node:http';

const bytes = value => Buffer.byteLength(value, 'utf8');
export const fail = () => {throw new Error('Controlled runtime contract rejected this operation.');};
export const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
export const bounded = (value, limit=262144) => {
  if(bytes(JSON.stringify(value))>limit)fail();
  return value;
};
function endpoint(value,path) {
  if(typeof value!=='string'||!new RegExp('^http://127\\.0\\.0\\.1:[0-9]{1,5}'+path+'$').test(value))fail();
  const url=new URL(value);if(Number(url.port)<1||Number(url.port)>65535)fail();return url;
}
function request(url,token,method,body,maximum) {
  if(typeof token!=='string'||!/^[A-Za-z0-9_-]{43}$/.test(token))fail();
  return new Promise((resolve,reject)=>{
    let settled=false,size=0;
    const chunks=[],rejectSafe=()=>{if(!settled){settled=true;reject(new Error('Controlled memory channel unavailable.'));}};
    const payload=body===undefined?undefined:JSON.stringify(bounded(body,65536));
    const headers={Authorization:'Bearer '+token};
    if(payload!==undefined){headers['Content-Type']='application/json';headers['Content-Length']=bytes(payload);}
    const req=httpRequest({hostname:'127.0.0.1',port:url.port,path:url.pathname,method,agent:false,headers},res=>{
      if(res.statusCode!==200||res.headers['content-type']!=='application/json'){res.destroy();rejectSafe();return;}
      res.on('data',chunk=>{size+=chunk.length;if(size>maximum){res.destroy();req.destroy();rejectSafe();return;}chunks.push(chunk);});
      res.on('error',rejectSafe);
      res.on('end',()=>{if(settled)return;try{const result=JSON.parse(Buffer.concat(chunks).toString('utf8'));settled=true;resolve(result);}catch{rejectSafe();}});
    });
    const timer=setTimeout(()=>{req.destroy();rejectSafe();},15000);
    req.on('error',rejectSafe);req.on('close',()=>{clearTimeout(timer);if(!settled)rejectSafe();});req.end(payload);
  });
}
export async function initialContext() {
  const url=process.env.ONTOLOGY_HARNESS_CONTEXT_URL,token=process.env.ONTOLOGY_HARNESS_CONTEXT_TOKEN;
  try{return await request(endpoint(url,'/context'),token,'GET',undefined,1048576);}
  finally{delete process.env.ONTOLOGY_HARNESS_CONTEXT_URL;delete process.env.ONTOLOGY_HARNESS_CONTEXT_TOKEN;}
}
export function toolBridge(bridge,name,args) {
  if(!object(bridge)||Object.keys(bridge).sort().join(',')!=='token,url')fail();
  return request(endpoint(bridge.url,'/tools'),bridge.token,'POST',{name,arguments:args},262144);
}
export function limitedSteps(ctx) {
  let steps=0;
  ctx.on('agent/pre-step',async(_payload,next)=>{if(++steps>8)fail();return next();});
}
