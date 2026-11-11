import {Fragment, type ReactNode} from 'react';
import './markdown.css';

/** Business documents only: raw HTML and remote images are never executed. */
function safeHref(value:string):string|null {
  const href=value.trim();
  if(!href||/[\u0000-\u0020\u007f\\]/.test(href))return null;
  if(href.startsWith('/')&&!href.startsWith('//'))return href;
  if(href.startsWith('#'))return href;
  try{const url=new URL(href);return ['https:','http:'].includes(url.protocol)?url.href:null}catch{return null}
}

function inline(text:string,key='i',depth=0):ReactNode {
  if(depth>5)return text;
  const expression=/(`[^`\n]+`|\*\*[^*\n]+\*\*|__[^_\n]+__|\[[^\]\n]+\]\([^\s)]+\)|\*[^*\n]+\*)/g;
  const parts:ReactNode[]=[];let last=0,match:RegExpExecArray|null;
  while((match=expression.exec(text))){
    if(match.index>last)parts.push(text.slice(last,match.index));
    const token=match[0],id=key+'-'+match.index;
    if(token.startsWith('`'))parts.push(<code key={id}>{token.slice(1,-1)}</code>);
    else if(token.startsWith('**')||token.startsWith('__'))parts.push(<strong key={id}>{inline(token.slice(2,-2),id,depth+1)}</strong>);
    else if(token.startsWith('*'))parts.push(<em key={id}>{inline(token.slice(1,-1),id,depth+1)}</em>);
    else{const link=token.match(/^\[([^\]]+)\]\((.+)\)$/)!,href=safeHref(link[2]);parts.push(href?<a key={id} href={href} {...(href.startsWith('http')?{target:'_blank',rel:'noopener noreferrer'}:{})}>{link[1]}</a>:<Fragment key={id}>{link[1]}</Fragment>)}
    last=expression.lastIndex;
  }
  if(last<text.length)parts.push(text.slice(last));
  return parts;
}

const cells=(line:string)=>line.trim().replace(/^\|/,'').replace(/\|$/,'').split('|').map(x=>x.trim());
const tableDivider=(line:string)=>/^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(line);
const blockStart=(line:string)=>/^\s*(#{1,6}\s|```|~~~|[-*+]\s|\d+[.)]\s|>\s?|([-*_])(?:\s*\2){2,}\s*$)/.test(line);

export default function Markdown({content,className=''}:{content:string;className?:string}) {
  const lines=(content||'').replace(/\r\n?/g,'\n').split('\n'),blocks:ReactNode[]=[];
  for(let i=0;i<lines.length;){
    const line=lines[i];if(!line.trim()){i++;continue}
    const key='b'+i,heading=line.match(/^\s*(#{1,6})\s+(.+)$/),fence=line.match(/^\s*(```|~~~)(.*)$/);
    if(fence){const end=fence[1],code:string[]=[];i++;while(i<lines.length&&!lines[i].trim().startsWith(end))code.push(lines[i++]);if(i<lines.length)i++;blocks.push(<pre key={key}><code>{code.join('\n')}</code></pre>);continue}
    if(heading){const children=inline(heading[2].replace(/\s+#+\s*$/,''),key);switch(heading[1].length){case 1:blocks.push(<h2 key={key}>{children}</h2>);break;case 2:blocks.push(<h3 key={key}>{children}</h3>);break;case 3:blocks.push(<h4 key={key}>{children}</h4>);break;default:blocks.push(<h5 key={key}>{children}</h5>)}i++;continue}
    if(/^\s*([-*_])(?:\s*\1){2,}\s*$/.test(line)){blocks.push(<hr key={key}/>);i++;continue}
    if(i+1<lines.length&&line.includes('|')&&tableDivider(lines[i+1])){const headers=cells(line),rows:string[][]=[];i+=2;while(i<lines.length&&lines[i].trim()&&lines[i].includes('|'))rows.push(cells(lines[i++]));blocks.push(<div className="markdown-table" key={key}><table><thead><tr>{headers.map((v,j)=><th key={j}>{inline(v,key+'h'+j)}</th>)}</tr></thead><tbody>{rows.map((row,j)=><tr key={j}>{headers.map((_,k)=><td key={k}>{inline(row[k]||'',key+j+'c'+k)}</td>)}</tr>)}</tbody></table></div>);continue}
    if(/^\s*>/.test(line)){const quote:string[]=[];while(i<lines.length&&/^\s*>/.test(lines[i]))quote.push(lines[i++].replace(/^\s*>\s?/,''));blocks.push(<blockquote key={key}><Markdown content={quote.join('\n')}/></blockquote>);continue}
    const list=line.match(/^\s*([-*+]|\d+[.)])\s+(.+)$/);
    if(list){const ordered=/^\d/.test(list[1]),items:ReactNode[]=[];while(i<lines.length){const item=lines[i].match(/^\s*([-*+]|\d+[.)])\s+(.+)$/);if(!item||/^\d/.test(item[1])!==ordered)break;const checked=item[2].match(/^\[([ xX])\]\s+(.*)$/);items.push(<li key={i}>{checked?<><span className="markdown-check" aria-label={checked[1].trim()?'已完成':'未完成'}>{checked[1].trim()?'☑':'☐'}</span> {inline(checked[2],key+i)}</>:inline(item[2],key+i)}</li>);i++}blocks.push(ordered?<ol key={key}>{items}</ol>:<ul key={key}>{items}</ul>);continue}
    const paragraph:string[]=[line];i++;while(i<lines.length&&lines[i].trim()&&!blockStart(lines[i])&&!(i+1<lines.length&&tableDivider(lines[i+1])))paragraph.push(lines[i++]);blocks.push(<p key={key}>{paragraph.map((part,j)=><Fragment key={j}>{j>0&&<br/>}{inline(part,key+j)}</Fragment>)}</p>);
  }
  return <div className={'business-markdown '+className}>{blocks}</div>;
}
