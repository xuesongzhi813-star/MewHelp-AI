/* Real static page + synthetic HTTP contract. No project dependency installation. */
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const {chromium} = require('playwright');

(async () => {
  const browser = await chromium.launch({headless:true, executablePath:process.env.CH07_BROWSER_EXE || 'C:/Program Files/Google/Chrome/Application/chrome.exe'});
  const context = await browser.newContext({viewport:{width:1280,height:900}});
  const root = path.resolve(__dirname, '..');
  const reports = path.join(root,'reports/ch07'); fs.mkdirSync(reports,{recursive:true});
  let next=1, sidebarFailure=false, historyFailure=false, slow=false, historyDelay=false;
  const items=[], histories=new Map(), requests=[], errors=[], actionStates=new Map(), ticketRequests=[];
  await context.route('http://localhost:8977/**', async route => {
    const req=route.request(), url=new URL(req.url());
    if(url.pathname==='/health') return route.fulfill({json:{status:'ok'}});
    if(url.pathname==='/api/chat/actions/state'){
      const id=Number(/:c:(\d+)$/.exec(url.searchParams.get('session_id')||'')?.[1]);
      return route.fulfill({json:{actions:actionStates.get(id)||[],selection:null}});
    }
    if(url.pathname==='/api/chat/actions/ticket'){
      const body=req.postDataJSON();ticketRequests.push(body);
      assert.equal(body.session_id,items.find(i=>i.id===1).session_id);
      assert.equal(body.action_id,'ticket-restore');assert.equal(body.confirmed,true);
      Object.assign(actionStates.get(1).find(a=>a.action_id===body.action_id),{status:'completed',result:'合成工单已创建'});
      return route.fulfill({json:{action_id:body.action_id,status:'completed',result:'合成工单已创建'}});
    }
    if(url.pathname==='/api/conversations') return route.fulfill(sidebarFailure ? {status:500,body:'synthetic failure'}:{json:{items:[...items].reverse()}});
    const match=/^\/api\/conversations\/(\d+)\/messages$/.exec(url.pathname);
    if(match){
      const item=items.find(i=>i.id===Number(match[1]));
      if(historyDelay) await new Promise(resolve=>setTimeout(resolve,1500));
      return route.fulfill(historyFailure ? {status:500,body:'synthetic history failure'} : {json:{conversation_id:item.id,session_id:item.session_id,messages:histories.get(item.id)}});
    }
    if(url.pathname==='/chat'){
      const body=req.postDataJSON(); requests.push(body);
      let item=items.find(i=>i.session_id===body.session_id);
      if(!item){
        const id=next++; item={id,session_id:`u:${body.user_id}:c:${id}`,preview:body.message,has_summary:id===1};
        items.push(item); histories.set(id,[]);
      }
      const answer='完整原文答复'.repeat(70);
      histories.get(item.id).push({id:histories.get(item.id).length+1,role:'user',content:body.message},{id:histories.get(item.id).length+2,role:'assistant',content:answer});
      if(slow) await new Promise(resolve=>setTimeout(resolve,800));
      return route.fulfill({contentType:'text/event-stream',body:[
        {type:'conversation',user_id:body.user_id,conversation_id:item.id,session_id:item.session_id},
        {type:'token',content:answer},{type:'actions',items:actionStates.get(item.id)||[]},{type:'done'}].map(e=>'data: '+JSON.stringify(e)+'\n\n').join('')});
    }
    const file=url.pathname==='/'?'app/static/index.html':'app'+url.pathname;
    const target=path.join(root,file);
    if(!fs.existsSync(target)) return route.fulfill({status:404,body:'not found'});
    return route.fulfill({contentType:target.endsWith('.js')?'text/javascript':'text/html',body:fs.readFileSync(target)});
  });
  try {
    const page=await context.newPage(); page.on('pageerror',e=>errors.push(e.message));
    await page.goto('http://localhost:8977/');
    const user=await page.evaluate(()=>localStorage.getItem('mewhelp-user-id'));
    assert.match(user,/^[a-f0-9]{32}$/);
    async function send(text){
      await page.locator('#input').fill(text); await page.locator('#sendBtn').click();
      await page.waitForFunction(()=>!document.querySelector('#sendBtn').disabled);
    }
    await send('<img src=x onerror="window.hacked=true"> 首问A');
    await page.waitForFunction(()=>document.querySelectorAll('.conversation-item').length===1);
    await page.locator('#newChatBtn').click(); await send('首问B');
    await page.waitForFunction(()=>document.querySelectorAll('.conversation-item').length===2);
    assert.equal(requests[0].user_id,requests[1].user_id);
    await page.locator('[data-conversation-id="1"]').click();
    await page.waitForFunction(()=>document.querySelector('#chatArea').textContent.includes('首问A'));
    assert.equal(await page.locator('.msg-row.ai .bubble').first().textContent(),'完整原文答复'.repeat(70));
    assert.equal(await page.evaluate(()=>window.hacked),undefined);
    assert.equal(await page.locator('#chatArea img').count(),0);
    await send('旧会话继续'); assert.equal(requests.at(-1).conversation_id,1);
    await page.reload(); await page.waitForFunction(()=>document.querySelector('#chatArea').textContent.includes('旧会话继续'));
    const second=await context.newPage(); await second.goto('http://localhost:8977/');
    assert.equal(await second.evaluate(()=>localStorage.getItem('mewhelp-user-id')),user);
    await second.waitForFunction(()=>document.querySelectorAll('.conversation-item').length===2);
    await second.close();
    historyFailure=true; await page.locator('[data-conversation-id="2"]').click();
    await page.waitForTimeout(100); assert.match(await page.locator('#chatArea').textContent(),/旧会话继续/); historyFailure=false;
    sidebarFailure=true; await send('侧栏失败仍可聊');
    assert.match(await page.locator('#chatArea').textContent(),/侧栏失败仍可聊/); sidebarFailure=false;
    slow=true; await page.locator('#input').fill('迟到旧流'); await page.locator('#sendBtn').click();
    await page.locator('[data-conversation-id="2"]').click();
    await page.waitForFunction(()=>document.querySelector('#chatArea').textContent.includes('首问B'));
    await page.waitForTimeout(1000);
    assert.doesNotMatch(await page.locator('#chatArea').textContent(),/迟到旧流/); slow=false;
    slow=true; historyDelay=true;
    await page.locator('#input').fill('元数据先到，历史后到'); await page.locator('#sendBtn').click();
    await page.locator('[data-conversation-id="1"]').click();
    await page.waitForTimeout(2200);
    assert.match(await page.locator('#chatArea').textContent(),/首问A/);
    slow=false; historyDelay=false;
    actionStates.set(1,[
      {action_id:'ticket-restore',kind:'create_ticket',status:'available',description:'合成测试工单',ticket_type:'咨询'},
      {action_id:'handoff-restore',kind:'handoff',status:'available'},
      {action_id:'ticket-completed',kind:'create_ticket',status:'completed',result:'既有工单'},
      {action_id:'ticket-uncertain',kind:'create_ticket',status:'uncertain'}
    ]);
    await send('合成普通动作建议');
    assert.equal(await page.locator('[data-action-id="ticket-restore"]').count(),1);
    await page.locator('[data-conversation-id="2"]').click();
    await page.waitForFunction(()=>document.querySelector('#chatArea').textContent.includes('首问B'));
    await page.locator('[data-conversation-id="1"]').click();
    await page.waitForFunction(()=>document.querySelector('#chatArea').textContent.includes('合成普通动作建议'));
    await page.waitForTimeout(200);
    assert.equal(await page.locator('[data-action-id="ticket-restore"]').count(),1,'ordinary action must restore after switching');
    assert(await page.locator('[data-action-id="handoff-restore"]').isEnabled());
    assert(await page.locator('[data-action-id="ticket-completed"]').isDisabled());
    assert(await page.locator('[data-action-id="ticket-uncertain"]').isDisabled());
    await page.locator('[data-action-id="ticket-restore"]').click();
    await page.locator('.ticket-confirm button').filter({hasText:'取消'}).click();assert.equal(ticketRequests.length,0);
    await page.reload();await page.waitForSelector('[data-action-id="ticket-restore"]');
    await page.locator('[data-action-id="ticket-restore"]').click();
    await page.locator('.ticket-confirm button').filter({hasText:'确认创建'}).click();
    await page.waitForFunction(()=>document.querySelector('[data-action-id="ticket-restore"]').disabled);
    assert.equal(ticketRequests.length,1);
    await page.reload();await page.waitForSelector('[data-action-id="ticket-restore"]');
    assert(await page.locator('[data-action-id="ticket-restore"]').isDisabled());
    assert.equal(await page.locator('[data-action-id="ticket-restore"]').textContent(),'工单已创建');
    await page.screenshot({path:path.join(reports,'browser-desktop.png'),fullPage:true});
    await page.setViewportSize({width:390,height:844});
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    await page.screenshot({path:path.join(reports,'browser-mobile.png'),fullPage:true});
    assert.deepEqual(errors,[]);
    fs.writeFileSync(path.join(reports,'browser-synthetic.json'),JSON.stringify({status:'passed',runner:'Playwright + Chromium, real static assets, synthetic HTTP',checks:['two conversations','raw long history','continue old','refresh','same browser new tab','summary mark','sidebar 500','history 500 preserves chat','old stream cancelled','HTML inert','metadata/history race','ordinary actions restore','completed and uncertain disabled','cancel no write','restore confirm same action/session once','390px no overflow'],requests:requests.length,ticket_requests:ticketRequests.length,errors},null,2));
    console.log('browser synthetic: passed');
  } finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exitCode=1;});
