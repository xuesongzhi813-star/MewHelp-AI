/* Real Chrome + shipped page; synthetic HTTP, no business records. */
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require('playwright');
(async()=>{
  const root=path.resolve(__dirname,'..'),out=path.join(root,'reports/ch08/browser-synthetic');fs.mkdirSync(out,{recursive:true});
  const user='8'.repeat(32),sid=`u:${user}:c:1`,other=`u:${user}:c:2`;
  const browser=await chromium.launch({headless:true,executablePath:process.env.CH08_BROWSER_EXE||'C:/Program Files/Google/Chrome/Application/chrome.exe'});
  const context=await browser.newContext({viewport:{width:1280,height:900}});
  await context.addInitScript(({user,sid})=>{if(!localStorage.getItem('mewhelp-user-id'))localStorage.setItem('mewhelp-user-id',user);if(!sessionStorage.getItem('mewhelp-chat-session'))sessionStorage.setItem('mewhelp-chat-session',sid);},{user,sid});
  let preview={type:'tool_confirmation',preview_id:'p1',status:'available',ticket_type:'售后',description:'包裹未收到 <img src=x onerror="window.hacked=true">'},mode='normal',writes=0;
  const requests=[],errors=[],checks=[];
  await context.route('http://localhost:8978/**',async route=>{
    const req=route.request(),url=new URL(req.url());
    if(url.pathname==='/health')return route.fulfill({json:{status:'ok'}});
    if(url.pathname==='/api/conversations')return route.fulfill({json:{items:[{id:1,session_id:sid,preview:'合成待确认',has_summary:false},{id:2,session_id:other,preview:'第二会话',has_summary:false}]}});
    const match=/^\/api\/conversations\/(\d+)\/messages$/.exec(url.pathname);
    if(match)return route.fulfill({json:{conversation_id:Number(match[1]),session_id:match[1]==='1'?sid:other,messages:[{id:1,role:'user',content:match[1]==='1'?'包裹未收到，帮我建工单':'第二会话原文'}]}});
    if(url.pathname==='/api/chat/actions/state'){
      const legacy=mode==='legacy-rejection'?[{action_id:'old-ticket',kind:'create_ticket',status:'available',description:'完整服务器文本',ticket_type:'投诉'}]:
        mode==='refund-rejection'?[{action_id:'old-refund',kind:'refund_application',status:'available',order_id:'1001',description:'退货申请',ticket_type:'售后',reasons:['尺码问题']}]:[];
      return route.fulfill({json:{selection:null,actions:legacy,ticket_confirmation:legacy.length?null:url.searchParams.get('session_id')===sid?preview:null}});
    }
    if(url.pathname==='/api/chat/actions/ticket')return route.fulfill({status:422,json:{detail:'参数不合法，未创建工单。'}});
    if(url.pathname==='/api/chat/actions/refund')return route.fulfill({status:422,json:{detail:'参数不合法，未提交申请。'}});
    if(url.pathname==='/api/chat/actions/tool-confirmation'){
      const data=req.postDataJSON();requests.push(data);assert.equal(data.session_id,sid);assert.deepEqual(Object.keys(data).sort(),['decision','preview_id','session_id']);
      if(mode==='failure')return route.abort('failed');
      if(mode==='slow')await new Promise(r=>setTimeout(r,800));
      if(data.decision==='confirm')writes++;
      preview={...preview,status:data.decision==='confirm'?'completed':'cancelled',result:data.decision==='confirm'?'工单号 TK-BROWSER-001':'已取消，未提交'};
      const events=[{type:'token',content:'迟到答复 工单号 TK-BROWSER-001'},{type:'tool_confirmation_result',preview_id:data.preview_id,status:preview.status,result:preview.result},{type:'done'}];
      return route.fulfill({contentType:'text/event-stream',body:events.map(e=>'data: '+JSON.stringify(e)+'\r\n\r\n').join('')});
    }
    const file=path.join(root,url.pathname==='/'?'app/static/index.html':'app'+url.pathname);
    if(fs.existsSync(file)&&fs.statSync(file).isFile())return route.fulfill({path:file});
    return route.fulfill({status:404,body:'synthetic missing'});
  });
  const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
  async function reload(){await page.goto('http://localhost:8978');await page.locator('.tool-confirmation').waitFor({state:'visible'});}
  try{
    await reload();assert((await page.locator('.tool-confirmation').textContent()).includes('<img'));
    assert.equal(await page.evaluate(()=>window.hacked),undefined);assert.equal(await page.locator('.tool-confirmation img').count(),0);checks.push('untrusted description rendered as text');
    await page.getByRole('button',{name:'确认提交',exact:true}).evaluate(b=>{b.click();b.click();});
    await page.waitForFunction(()=>document.querySelector('.tool-confirmation .status').textContent.includes('TK-BROWSER-001'));
    assert.equal(writes,1);assert.equal(requests.length,1);checks.push('double click creates one request');
    await reload();assert(await page.getByRole('button',{name:'确认提交',exact:true}).isDisabled());checks.push('refresh completed state');
    preview={...preview,preview_id:'p2',status:'available',result:''};await reload();await page.getByRole('button',{name:'取消',exact:true}).click();
    await page.waitForFunction(()=>document.querySelector('.tool-confirmation .status').textContent.includes('已取消'));assert.equal(writes,1);checks.push('cancel zero writes');
    preview={...preview,preview_id:'p3',status:'available',result:''};mode='failure';await reload();await page.getByRole('button',{name:'确认提交',exact:true}).click();
    await page.waitForFunction(()=>document.querySelector('.tool-confirmation .status').textContent.includes('待核实'));assert(await page.getByRole('button',{name:'取消',exact:true}).isDisabled());checks.push('network failure no automatic retry');
    mode='slow';preview={...preview,preview_id:'p4',status:'available',result:''};await reload();await page.getByRole('button',{name:'确认提交',exact:true}).click();
    await page.locator('[data-conversation-id="2"]').click();await page.waitForTimeout(1200);
    assert(!(await page.locator('#chatArea').textContent()).includes('迟到答复'));assert((await page.locator('#chatArea').textContent()).includes('第二会话原文'));checks.push('switch session ignores late stream');
    await page.locator('[data-conversation-id="1"]').click();await page.locator('.tool-confirmation').waitFor({state:'visible'});
    await page.screenshot({path:path.join(out,'desktop.png'),fullPage:true});await page.setViewportSize({width:390,height:844});
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:path.join(out,'mobile.png'),fullPage:true});checks.push('390px no overflow');
    mode='legacy-rejection';await page.goto('http://localhost:8978');await page.getByRole('button',{name:'建工单',exact:true}).click();
    await page.getByRole('button',{name:'确认创建',exact:true}).click();
    await page.waitForFunction(()=>document.querySelector('.chat-action-status').textContent!=='正在创建工单…');
    let refusal=await page.locator('.chat-action-status').textContent();
    assert(refusal.includes('未创建工单')&&!refusal.includes('待核实'));checks.push('known legacy validation refusal stays rejected');
    mode='refund-rejection';await page.goto('http://localhost:8978');await page.getByRole('button',{name:'提交演示退款申请',exact:true}).click();
    await page.getByRole('combobox').selectOption('尺码问题');await page.getByRole('button',{name:'确认提交',exact:true}).click();
    await page.waitForFunction(()=>document.querySelector('.refund-note').textContent.includes('参数不合法'));
    refusal=await page.locator('.refund-note').textContent();assert(refusal.includes('未提交')&&!refusal.includes('核实'));checks.push('known refund validation refusal stays rejected');
    assert.deepEqual(errors,[]);fs.writeFileSync(path.join(out,'result.json'),JSON.stringify({passed:true,provider:'synthetic HTTP + real Chrome',checks,requests,writes,errors},null,2));
    console.log('ch08 browser synthetic: '+checks.length+' passed');
  }finally{await browser.close();}
})().catch(e=>{console.error(e.message);process.exitCode=1;});
