/* Live backend/model; only synthetic conversation identities from the evaluator. */
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require('playwright');
(async()=>{
  const root=path.resolve(__dirname,'..'),report=JSON.parse(fs.readFileSync(process.argv[2]||path.join(root,'reports/ch07/demo-dialogue.json'),'utf8'));
  const seed=report.dialogues[0],base=process.env.CH07_BASE_URL||'http://localhost:8018';
  const browser=await chromium.launch({headless:true,executablePath:process.env.CH07_BROWSER_EXE||'C:/Program Files/Google/Chrome/Application/chrome.exe'});
  const context=await browser.newContext({viewport:{width:1280,height:900}}),errors=[];
  await context.addInitScript(({user,sid})=>{
    if(!localStorage.getItem('mewhelp-user-id'))localStorage.setItem('mewhelp-user-id',user);
    if(!sessionStorage.getItem('mewhelp-chat-session'))sessionStorage.setItem('mewhelp-chat-session',sid);
  },{user:seed.user_id,sid:seed.session_id});
  try{
    const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
    const url=`${base}/api/conversations/${seed.conversation_id}/messages?user_id=${seed.user_id}`;
    const original=await (await context.request.get(url)).json();
    assert(original.messages.filter(m=>m.role==='user').length>=21);
    await page.goto(base);await page.waitForFunction(n=>document.querySelectorAll('#chatArea .msg-row').length===n,original.messages.length);
    const texts=await page.locator('#chatArea .bubble').allTextContents();
    assert.deepEqual(texts,original.messages.map(m=>m.content));
    assert.match(await page.locator(`[data-conversation-id="${seed.conversation_id}"]`).textContent(),/已摘要/);
    async function send(text){await page.locator('#input').fill(text);await page.locator('#sendBtn').click();await page.waitForFunction(()=>!document.querySelector('#sendBtn').disabled,{},{timeout:180000});}
    await send('最开始那个订单号是多少？当时我有什么诉求？');
    const recall=await page.locator('#chatArea .msg-row.ai .bubble').last().textContent();
    assert.match(recall,/1001/);assert.match(recall,/签收/);
    await page.locator('#newChatBtn').click();await send('你好，这是新的合成浏览器验收会话。');
    await page.waitForFunction(()=>document.querySelectorAll('.conversation-item').length>=2);
    await page.locator(`[data-conversation-id="${seed.conversation_id}"]`).click();
    await page.waitForFunction(()=>document.querySelector('#chatArea').textContent.includes('最开始那个订单号是多少'));
    await page.reload();await page.waitForFunction(()=>document.querySelector('#chatArea').textContent.includes('最开始那个订单号是多少'));
    const tab=await context.newPage();await tab.goto(base);
    assert.equal(await tab.evaluate(()=>localStorage.getItem('mewhelp-user-id')),seed.user_id);await tab.close();
    await context.route('**/api/conversations?*',route=>route.fulfill({status:503,body:'synthetic sidebar outage'}));
    await send('谢谢，先聊到这里。');assert.match(await page.locator('#chatArea').textContent(),/谢谢，先聊到这里/);
    assert.deepEqual(errors,[]);
    await page.screenshot({path:path.join(root,'reports/ch07/browser-real-desktop.png'),fullPage:true});
    await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    await page.screenshot({path:path.join(root,'reports/ch07/browser-real-mobile.png'),fullPage:true});
    fs.writeFileSync(path.join(root,'reports/ch07/browser-e2e.json'),JSON.stringify({passed:true,provider:'real',base,user_id:seed.user_id,conversation_id:seed.conversation_id,raw_messages:original.messages.length,recall,checks:['raw history equality','continue summarized conversation','multiple conversations','new keeps old','switch and refresh','same browser grouping','summary mark','sidebar outage chat works','mobile no overflow'],errors},null,2));
    console.log('browser real: passed');
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
