/* Real page/API/model. seed waits; resume verifies pending after service restart. */
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require('playwright');
(async()=>{
  const root=path.resolve(__dirname,'..'),out=path.join(root,'reports/ch08/browser-real');fs.mkdirSync(out,{recursive:true});
  const phase=process.argv[2]||'seed',target=path.join(out,'state.json'),base='http://127.0.0.1:8020';
  const state=phase==='resume'?JSON.parse(fs.readFileSync(target,'utf8')):{user:require('node:crypto').randomBytes(16).toString('hex'),checks:[],provider:'real Chrome/API/model/MySQL'};
  const browser=await chromium.launch({headless:true,executablePath:process.env.CH08_BROWSER_EXE||'C:/Program Files/Google/Chrome/Application/chrome.exe'});
  const context=await browser.newContext({viewport:{width:1280,height:900}});
  await context.addInitScript(({user,session})=>{localStorage.setItem('mewhelp-user-id',user);if(session)sessionStorage.setItem('mewhelp-chat-session',session);},{user:state.user,session:state.session});
  const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  const save=()=>fs.writeFileSync(target,JSON.stringify({...state,errors},null,2));
  async function send(text){await page.locator('#input').fill(text);await page.locator('#sendBtn').click();await page.waitForFunction(()=>!document.querySelector('#sendBtn').disabled,{},{timeout:180000});assert.equal(await page.locator('.bubble.error').count(),0);}
  try{
    await page.goto(base);
    if(phase==='seed'){
      await send('请帮我建一个售后工单');assert.equal(await page.locator('.tool-confirmation:not([hidden])').count(),0);
      state.session=await page.evaluate(()=>MewHelpConversations.currentRequestFields().session_id);
      state.checks.push('missing description asks before preview');
      await send('问题描述：订单1001显示签收，但我没有收到包裹。');
      await page.locator('.tool-confirmation:not([hidden])').waitFor({state:'visible',timeout:120000});
      state.preview=(await (await context.request.get(base+'/api/chat/actions/state?session_id='+encodeURIComponent(state.session))).json()).ticket_confirmation.preview_id;
      state.checks.push('follow-up description produces pending preview');save();
      await page.screenshot({path:path.join(out,'pending-before-restart.png'),fullPage:true});
    }else{
      await page.locator('.tool-confirmation:not([hidden])').waitFor({state:'visible',timeout:30000});
      const restored=await (await context.request.get(base+'/api/chat/actions/state?session_id='+encodeURIComponent(state.session))).json();assert.equal(restored.ticket_confirmation.preview_id,state.preview);
      state.checks.push('pending survives backend restart and browser refresh');
      await page.getByRole('button',{name:'取消',exact:true}).click();
      await page.waitForFunction(()=>document.querySelector('.tool-confirmation .status').textContent.includes('取消'),{},{timeout:180000});state.cancelSession=state.session;state.checks.push('cancel via browser');
      await page.locator('#newChatBtn').click();await send('浏览器合成验收：订单1002少一件衬衫，请帮我建售后工单');
      await page.locator('.tool-confirmation:not([hidden])').waitFor({state:'visible'});
      state.confirmSession=await page.evaluate(()=>MewHelpConversations.currentRequestFields().session_id);
      const confirmState=await (await context.request.get(base+'/api/chat/actions/state?session_id='+encodeURIComponent(state.confirmSession))).json();state.confirmPreview=confirmState.ticket_confirmation.preview_id;
      await page.getByRole('button',{name:'确认提交',exact:true}).evaluate(b=>{b.click();b.click();});
      await page.waitForFunction(()=>document.querySelector('.tool-confirmation .status').textContent.match(/T\d{12,}/),{},{timeout:180000});
      const repeat=await context.request.post(base+'/api/chat/actions/tool-confirmation',{data:{session_id:state.confirmSession,preview_id:state.confirmPreview,decision:'confirm'}});assert.equal(repeat.status(),409);state.checks.push('confirm and duplicate rejected');
      await page.reload();await page.locator('.tool-confirmation:not([hidden])').waitFor({state:'visible'});assert(await page.getByRole('button',{name:'确认提交',exact:true}).isDisabled());state.checks.push('refresh completed result');
      await page.screenshot({path:path.join(out,'completed-desktop.png'),fullPage:true});await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:path.join(out,'completed-mobile.png'),fullPage:true});
      state.passed=true;save();
    }
    assert.deepEqual(errors,[]);console.log('real ch08 browser '+phase+': passed');
  }catch(e){state.failure=e.message;save();throw e;}finally{await browser.close();}
})().catch(e=>{console.error(e.message);process.exitCode=1;});
