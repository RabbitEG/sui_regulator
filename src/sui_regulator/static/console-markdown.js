'use strict';
window.YanMarkdown = (() => {
  const escape = value => String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const parser = window.markdownit({html:false,breaks:true,linkify:false,typographer:false});
  const safeURL = url => /^https?:\/\//i.test(url);
  parser.validateLink = safeURL;
  parser.renderer.rules.image = (tokens,index) => {
    const token=tokens[index],url=token.attrGet('src'),label=escape(token.content || 'image');
    // Remote images are explicit links, never background network requests.
    return safeURL(url || '') ? `<a href="${escape(url)}">[${label}]</a>` : `[${label}]`;
  };
  const cache=new Map(); let bytes=0;
  function render(value){
    const text=String(value ?? ''),key=YanI18n.language+'\0'+text;
    if(cache.has(key)) return cache.get(key);
    if(text.length>64000) return `<p class="note">${YanI18n.tr('长消息以原文显示，避免渲染阻塞。','Long message shown as source to keep the reader responsive.')}</p><pre class="markdown-source">${escape(text)}</pre>`;
    const div=document.createElement('div');div.append(formatHTML(parser.render(text)));const html=div.innerHTML;
    cache.set(key,html);bytes+=key.length+html.length;
    while(cache.size>64 || bytes>1000000){const first=cache.keys().next().value;bytes-=first.length+cache.get(first).length;cache.delete(first);}
    return html;
  }
  function formatHTML(html){
    const fragment=DOMPurify.sanitize(html,{
      ALLOWED_TAGS:['p','br','hr','h1','h2','h3','h4','h5','h6','strong','em','s','blockquote','ul','ol','li','pre','code','table','thead','tbody','tr','th','td','a'],
      ALLOWED_ATTR:['href','title','start'],ALLOW_DATA_ATTR:false,ALLOW_ARIA_ATTR:false,RETURN_DOM_FRAGMENT:true,
    });
    fragment.querySelectorAll('a').forEach(link=>{
      if(!safeURL(link.getAttribute('href') || '')) {link.replaceWith(document.createTextNode(link.textContent));return;}
      link.target='_blank';link.rel='noopener noreferrer';link.referrerPolicy='no-referrer';
    });
    fragment.querySelectorAll('pre').forEach(pre=>{
      const wrap=document.createElement('div');wrap.className='code-block';
      const bar=document.createElement('div');bar.className='code-bar';
      const label=document.createElement('span');label.textContent=YanI18n.tr('代码','Code');
      const copy=document.createElement('button');copy.type='button';copy.dataset.copyCode='';copy.className='text-button';copy.textContent=YanI18n.tr('复制代码','Copy code');
      bar.append(label,copy);pre.replaceWith(wrap);wrap.append(bar,pre);pre.tabIndex=0;
    });
    fragment.querySelectorAll('table').forEach(table=>{
      const wrap=document.createElement('div');wrap.className='markdown-table';wrap.tabIndex=0;
      table.replaceWith(wrap);wrap.append(table);
    });
    return fragment;
  }
  async function renderInto(element,text,current=()=>true){
    // Parse the assembled message once: storage boundaries must not break fences,
    // lists, tables or reference links. Yield between complete Markdown blocks.
    const env={},tokens=parser.parse(String(text ?? ''),env);
    let batch=[],depth=0,blocks=0;
    for(let index=0;index<tokens.length;index++){
      if(!current()) return;
      const token=tokens[index];batch.push(token);depth+=token.nesting;
      if(depth===0) blocks++;
      if(depth===0 && (blocks>=24 || index===tokens.length-1)){
        const chunk=document.createElement('div');chunk.className='markdown-chunk';
        chunk.append(formatHTML(parser.renderer.render(batch,parser.options,env)));
        element.append(chunk);batch=[];blocks=0;
        await new Promise(resolve=>requestAnimationFrame(resolve));
      }
    }
  }
  return {render,renderInto};
})();
