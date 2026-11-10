// Fixed trusted plugin. Skill files are values in a bounded memory map, never modules.
import {readFileSync} from 'node:fs';
import {request as httpRequest} from 'node:http';

export const inject = ['tools'];
const PORTABLE = new Set(['applicability','inputs','outputs','steps','entrypoint','test_cases']);
const ALLOWED = new Set(['read_skill_file','propose_skill_files']);
const PARAMETERS = JSON.parse(readFileSync(new URL('./authoring.schema.json', import.meta.url), 'utf8'));
const SCHEMA_KEYS = Object.keys(PARAMETERS.properties.manifest_patch.properties.inputs.properties);
const SCHEMA_TYPES = PARAMETERS.properties.manifest_patch.properties.inputs.properties.type.enum;
const utf8 = value => Buffer.byteLength(value, 'utf8');
const fail = (message='Skill authoring arguments exceed the permitted contract.') => {throw new Error(message);};
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value)
  && Object.getPrototypeOf(value) !== null;
const own = (obj,key) => Object.prototype.hasOwnProperty.call(obj,key);
const pathOK = value => typeof value==='string' && value.length<=240 && !/[\\\x00-\x1f\x7f:]/.test(value)
  && !value.startsWith('/') && value.split('/').length<=8
  && value.split('/').every(part=>part!==''&&part!=='.'&&part!=='..');
function checkFiles(files) {
  if(!object(files)||Object.keys(files).length>64||!own(files,'SKILL.md'))fail();
  let total=0;
  for(const [path,content] of Object.entries(files)) {
    if(!pathOK(path)||typeof content!=='string'||content.includes('\0')||utf8(content)>262144)fail();
    total+=utf8(content);
  }
  if(total>2097152)fail();
}
function keys(value,allowed) {if(!object(value)||Object.keys(value).some(key=>!allowed.includes(key)))fail();}
function boundedJSON(value, maxBytes, maxDepth=12, maxNodes=1500) {
  let nodes=0;
  function visit(item,depth) {
    nodes++;if(nodes>maxNodes||depth>maxDepth)fail('JSON value exceeds the bounded schema or example profile.');
    if(typeof item==='number'&&!Number.isFinite(item))fail();
    if(Array.isArray(item))for(const child of item)visit(child,depth+1);
    else if(object(item))for(const child of Object.values(item))visit(child,depth+1);
  }
  visit(value,0);if(utf8(JSON.stringify(value))>maxBytes)fail('JSON value exceeds the allowed byte limit.');
}
function checkSchema(value) {
  boundedJSON(value,32768);
  function visit(schema,depth=0) {
    keys(schema,SCHEMA_KEYS);
    if(depth>8||!SCHEMA_TYPES.includes(schema.type))fail('Every schema node needs one supported string type; schema depth is at most 8.');
    if(own(schema,'properties')) {
      if(schema.type!=='object'||!object(schema.properties)||Object.keys(schema.properties).length>100)fail();
      for(const [name,child] of Object.entries(schema.properties)) {
        if(name.length<1||name.length>200)fail();visit(child,depth+1);
      }
    }
    if(own(schema,'required')&&(schema.type!=='object'||!Array.isArray(schema.required)||schema.required.length>100||
      schema.required.some(name=>typeof name!=='string'||!own(schema.properties??{},name))||new Set(schema.required).size!==schema.required.length))fail('required must list unique, declared object properties.');
    if(own(schema,'additionalProperties')&&(schema.type!=='object'||typeof schema.additionalProperties!=='boolean'))fail('additionalProperties must be boolean and belongs to object schemas.');
    if(schema.type==='array'&&!own(schema,'items'))fail('Array schemas require an items schema.');
    if(own(schema,'items')) {if(schema.type!=='array')fail();visit(schema.items,depth+1);}
    for(const key of ['minLength','maxLength','minItems','maxItems'])if(own(schema,key)&&(!Number.isInteger(schema[key])||schema[key]<0||schema[key]>(key.endsWith('Items')?100:10000)))fail();
    for(const key of ['minimum','maximum'])if(own(schema,key)&&(typeof schema[key]!=='number'||!Number.isFinite(schema[key])))fail();
    for(const [low,high] of [['minimum','maximum'],['minLength','maxLength'],['minItems','maxItems']])if(own(schema,low)&&own(schema,high)&&schema[low]>schema[high])fail();
    for(const key of ['title','description'])if(own(schema,key)&&(typeof schema[key]!=='string'||schema[key].length>2000))fail();
    if(own(schema,'enum')&&(!Array.isArray(schema.enum)||schema.enum.length<1||schema.enum.length>100))fail();
  }
  visit(value);
}
function checkPortable(value) {
  if(own(value,'applicability')&&(typeof value.applicability!=='string'||value.applicability.length>4000))fail();
  for(const key of ['inputs','outputs'])if(own(value,key))checkSchema(value[key]);
  if(own(value,'steps')) {
    if(!Array.isArray(value.steps)||value.steps.length>30)fail('steps must contain at most 30 {title,instruction} objects.');
    for(const step of value.steps) {
      keys(step,['title','instruction']);
      for(const [key,max] of [['title',200],['instruction',4000]])if(typeof step[key]!=='string'||!step[key].trim()||step[key].length>max)fail('Each step requires nonempty title and instruction strings.');
    }
  }
  if(own(value,'entrypoint')&&value.entrypoint!==null&&(!pathOK(value.entrypoint)||!value.entrypoint.startsWith('scripts/')||!value.entrypoint.endsWith('.py')))fail('entrypoint must be a safe scripts/*.py path or null.');
  if(own(value,'test_cases')) {
    if(!Array.isArray(value.test_cases)||value.test_cases.length>10)fail('test_cases must contain at most 10 {name,input,expected_output} objects.');
    const names=new Set();
    for(const item of value.test_cases) {
      keys(item,['name','input','expected_output']);
      if(typeof item.name!=='string'||item.name.length<1||item.name.length>100||names.has(item.name)||!own(item,'input')||!own(item,'expected_output'))fail('Each test case requires a unique name, input, and expected_output.');
      names.add(item.name);boundedJSON(item.input,65536,16,10000);boundedJSON(item.expected_output,65536,16,10000);
    }
  }
}

async function initialContext() {
  if(!process.env.ONTOLOGY_HARNESS_CONTEXT_URL) {
    return JSON.parse(readFileSync(process.env.ONTOLOGY_HARNESS_CONTEXT,'utf8'));
  }
  const endpoint=process.env.ONTOLOGY_HARNESS_CONTEXT_URL,token=process.env.ONTOLOGY_HARNESS_CONTEXT_TOKEN;
  // The worker chooses a one-use loopback endpoint. No caller-controlled URL,
  // proxy, redirect, filesystem fallback, or model tool can reach this channel.
  if(!/^http:\/\/127\.0\.0\.1:[0-9]{1,5}\/context$/.test(endpoint)||typeof token!=='string'||!/^[A-Za-z0-9_-]{43}$/.test(token))fail('Invalid private context channel.');
  try {
    return await new Promise((resolve,reject)=>{
      let settled=false,bytes=0;
      const chunks=[];
      const rejectSafe=()=>{if(!settled){settled=true;reject(new Error('Private context channel unavailable.'));}};
      const url=new URL(endpoint);
      const req=httpRequest({hostname:'127.0.0.1',port:url.port,path:'/context',method:'GET',agent:false,
        headers:{Authorization:'Bearer '+token}},res=>{
        if(res.statusCode!==200||res.headers['content-type']!=='application/json'){res.destroy();rejectSafe();return;}
        res.on('data',chunk=>{
          bytes+=chunk.length;
          if(bytes>1048576){res.destroy();req.destroy();rejectSafe();return;}
          chunks.push(chunk);
        });
        res.on('error',rejectSafe);
        res.on('end',()=>{
          if(settled)return;
          try{const value=JSON.parse(Buffer.concat(chunks).toString('utf8'));settled=true;resolve(value);}
          catch{rejectSafe();}
        });
      });
      const deadline=setTimeout(()=>{req.destroy();rejectSafe();},5000);
      req.on('error',rejectSafe);
      req.on('close',()=>{clearTimeout(deadline);if(!settled)rejectSafe();});
      req.end();
    });
  } finally {
    delete process.env.ONTOLOGY_HARNESS_CONTEXT_URL;
    delete process.env.ONTOLOGY_HARNESS_CONTEXT_TOKEN;
  }
}

export async function apply(ctx) {
  // The worker owns this random tmpfs path; no model or client chooses it.
  const initial=await initialContext();
  let files=Object.fromEntries(Object.entries(initial.files));
  let manifest=JSON.parse(JSON.stringify(initial.manifest));
  let steps=0,readBytes=0,proposals=0;
  checkFiles(files);
  ctx.tools.guard(exec=>ALLOWED.has(exec.name)?undefined:'Skill development does not authorize this tool.');
  ctx.on('agent/pre-step',async (_payload,next)=>{
    steps++;
    if(steps>8)throw new Error('Skill authoring model-step limit reached.');
    return next();
  });
  const output={schema:{type:'string'},render:(_args,value)=>[{type:'text',text:value}]};
  ctx.tools.register({
    name:'read_skill_file',description:'Read one authorized skill-package file from memory. Paths refer only to this draft, never to the host filesystem.',
    parameters:{type:'object',properties:{path:{type:'string'}},required:['path'],additionalProperties:false},output,
    async execute(args){
      keys(args,['path']);if(!pathOK(args.path)||!own(files,args.path))fail();
      const content=files[args.path];readBytes+=utf8(content);if(readBytes>524288)fail();
      return content;
    },
  });
  ctx.tools.register({
    name:'propose_skill_files',
    description:'Propose draft changes in memory for human review; this does not save, execute or publish. Read relevant files first, preserve the SKILL.md package name and YAML frontmatter, and submit changed files only. Use only synthetic examples. Never invent business definitions, thresholds, units, object identifiers or join keys. If these are unclear, or a request exceeds your authority, preserve all files and manifest fields and submit empty changes/delete_paths/manifest_patch with the unresolved questions or refusal boundary in summary. Never change tenant dependencies, tool grants, review state or publication. A step is {title,instruction}; a test case is {name,input,expected_output}. Follow the bounded reference-free JSON Schema profile described in inputs/outputs. Python entrypoints read JSON stdin and write JSON stdout using only the standard library.',
    parameters:PARAMETERS,output,
    async execute(args){
      keys(args,['summary','changes','delete_paths','manifest_patch']);
      if(typeof args.summary!=='string'||args.summary.length>2000||!Array.isArray(args.changes)||args.changes.length>32||!Array.isArray(args.delete_paths)||args.delete_paths.length>32||!object(args.manifest_patch)||Object.keys(args.manifest_patch).some(key=>!PORTABLE.has(key))||utf8(JSON.stringify(args))>262144)fail();
      checkPortable(args.manifest_patch);
      const next=Object.fromEntries(Object.entries(files)),seen=new Set();
      for(const change of args.changes){
        keys(change,['path','content']);if(!pathOK(change.path)||seen.has(change.path)||typeof change.content!=='string')fail();
        seen.add(change.path);Object.defineProperty(next,change.path,{value:change.content,enumerable:true,writable:true,configurable:true});
      }
      for(const path of args.delete_paths){if(!pathOK(path)||path==='SKILL.md'||seen.has(path)||!own(next,path))fail();seen.add(path);delete next[path];}
      checkFiles(next);
      const nextManifest={...manifest,...args.manifest_patch};
      if(utf8(JSON.stringify(nextManifest))>131072)fail();
      proposals++;if(proposals>8)fail();files=next;manifest=nextManifest;
      // A bounded accepted patch is returned through native SDK tool-result events.
      // The worker reconstructs the final proposal; no output file or host mutation.
      return JSON.stringify({ontology_skill_proposal:'1.0',summary:args.summary,changes:args.changes,delete_paths:args.delete_paths,manifest_patch:args.manifest_patch});
    },
  });
}
