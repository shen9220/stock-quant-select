/* One server ledger: user-confirmed price-difference percentages and dated money snapshots. */
const V3 = (() => {
  const models = [
    {id:'original',name:'V1 原始',program:'stock',tier:0},
    {id:'main',name:'V1 主板',program:'stock',board:'主板',tier:1},
    {id:'cyb',name:'V1 创业板',program:'stock',board:'创业板',tier:1},
    {id:'under',name:'水下',program:'stock_updata',tier:2},
    {id:'low',name:'V1 低价',program:'slow',tier:2},
    {id:'stable',name:'V2 稳定版',program:'stock_v2',path:'B',tier:2},
    {id:'advance',name:'V1 一进二',program:'stock_v2',path:'A',tier:3},
    {id:'low2',name:'V2 低价',program:'slow_v2',tier:3},
    {id:'up',name:'Up',program:'up',tier:3}
  ];
  const tiers=['第一档 · 核心主线','第二档 · 备选方案','第三档 · 只做观察','第四档 · 暂时放弃'];
  const now=beijingMonth(), year=Number(now.slice(0,4));
  const state={model:'original',rank:1,mode:'D1',view:'trade',month:now,from:'',to:'',page:1,per:50,sort:'date',dir:-1,yearFrom:year,yearTo:year,poolDate:'',keyword:'',compareFrom:now,compareTo:now};
  let raw=[], rows=[], comparison=[], seq=0, poolDays=[], editorContext=null;
  const nextDates=new Map();
  let picker=null, navigationKey='';
  const $=id=>document.getElementById(id);
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const num=v=>v===null||v===undefined||v===''||!Number.isFinite(Number(v))?null:Number(v);
  const sum=(list,key)=>{const a=list.map(r=>num(r[key])).filter(v=>v!==null);return a.length?a.reduce((a,b)=>a+b,0):null;};
  const color=v=>num(v)>0?'red':num(v)<0?'green':'';
  const money=v=>num(v)===null?'—':Number(v).toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2});
  const pct=v=>num(v)===null?'—':`${v>0?'+':''}${(Math.sign(Number(v))*Math.round((Math.abs(Number(v))+1e-10)*100)/100).toFixed(2)}%`;
  const fmt=(v,isPct=false)=>`<span class="${color(v)}">${isPct?pct(v):money(v)}</span>`;
  function assetNote(marks=[],fallback=''){
    const dates=[...new Set(marks.filter(m=>m.stale&&m.date).map(m=>m.date))];
    return dates.length?'收盘估值 · '+dates.join(' / '):fallback;
  }
  function assetMoney(value,marks=[]){
    const stale=marks.filter(m=>m.stale&&m.date);
    const detail=stale.map(m=>`${m.stock_code} · ${m.date} 收盘 ${money(m.price)}${m.shares?' × '+m.shares+'股':''}`).join('；');
    return money(value)+(stale.length?`<div class="stock-sub asset-note" title="${esc(detail)}">${esc(assetNote(stale))}</div>`:'');
  }
  const masked=()=>typeof window!=='undefined'&&window.__PAGES_MODE__&&!window.__PAGES_FULL__;
  const model=()=>models.find(m=>m.id===state.model);
  const placeholder=t=>!!(t.is_board_placeholder||t.is_rank_placeholder||!String(t.stock_code||'').trim());
  const noBuy=t=>placeholder(t)||num(t.buy_price)===null||Number(t.buy_price)<=0||/买不进|没买|不可买/.test(t.skip_reason||'');
  const capitalBase=date=>date<'2026-01-01'?null:date<'2026-11-01'?100000:40000;
  const period=date=>date<'2026-01-01'?'unknown':date<'2026-11-01'?'2026-first':'2026-second';
  function calculate(payload,mode){
    if(!payload||Array.isArray(payload)||payload.mode!==mode||payload.version!=='doc-delta-v1')throw new Error('缺少当前模式的模板资金账本，请刷新页面');
    const list=payload.rows||[];
    Object.defineProperty(list,'ledger',{value:payload,configurable:true});
    return list;
  }
  function params(m,rank=state.rank){
    const q=new URLSearchParams({program:m.program});
    if(m.board){q.set('board',m.board);q.set('board_rank',rank);}
    else if(m.path){q.set('path',m.path);q.set('path_rank',rank);}
    else q.set('rank',rank);
    return q;
  }
  function cutoffDay(list=rows){
    const month=state.to||state.month,[y,m]=month.split('-').map(Number);
    const end=`${month}-${String(new Date(y,m,0).getDate()).padStart(2,'0')}`;
    const today=list.ledger?.as_of?.slice(0,10)||beijingToday();return end<today?end:today;
  }
  function projectRow(source,day,currentDay=null){
    const r={...source};if(!r.executed)return r;
    let historical=(r.history||[]).filter(h=>h.date<=day).at(-1);
    if(day===currentDay&&historical?.date!==day)historical=null;
    if(historical){const {date:historyDate,...stateAtDate}=historical;Object.assign(r,stateAtDate);}
    const events=(source.events||[]).filter(e=>e.date<=day);r.events=events;
    for(const [slot,whole,batch,profit] of [['am','ra','amHalf','aProfit'],['pm','rp','pmHalf','pProfit']]){
      const e=events.find(e=>e.slot===slot);r[slot]=e?.price??null;r[whole]=e?.whole_pnl??null;
      r[batch]=e?.batch_change??null;r[profit]=e?.profit??null;r[slot+'_sell_date']=e?.date??null;
      if(!e&&historical)r[slot+'_status']=r.remainingShares?'截至期末未结算':'不适用';
    }
    r.saleAmount=events.reduce((n,e)=>n+Number(e.proceeds),0);
    r.realized_profit_total=events.reduce((n,e)=>n+Number(e.profit),0);
    r.batchTotal=events.reduce((n,e)=>n+Number(e.batch_change),0);
    r.profit=r.display_profit;return r;
  }
  function scoped(list){
    let a=list.filter(r=>{const month=String(r.date).slice(0,7);return state.from?month>=state.from&&month<=state.to:month===state.month;});
    if(state.keyword){const k=state.keyword.toLowerCase();a=a.filter(r=>(String(r.stock_code)+' '+String(r.stock_name)).toLowerCase().includes(k));}
    const day=cutoffDay(list);return a.map(r=>projectRow(r,day,list.ledger?.as_of?.slice(0,10)));
  }
  function totals(list){return Object.fromEntries(['ra','rp','morningBatchPct','afternoonBatchPct','amHalf','pmHalf','batchTotal','batch','profit','aProfit','pProfit'].map(k=>[k,sum(list,k)]));}
  function card(label,value,note,accent='#527af1'){return `<div class="card" style="--accent:${accent}"><div class="label">${label}</div><div class="value">${value}</div><div class="tiny">${note}</div></div>`;}
  function modeTabs(){return `<div class="tabs mode-tabs" aria-label="卖出模式">${['D1','D2'].map(m=>`<button class="tab ${state.mode===m?'on':''}" onclick="V3.set('mode','${m}')">${m}</button>`).join('')}</div>`;}
  function selectAnalysisMode(mode){set('mode',mode);document.querySelector('.analysis-tabs [aria-selected="true"]')?.focus({preventScroll:true});}
  function analysisModeTabs(){return `<section class="analysis-mode-bar"><div class="analysis-mode-label"><strong>统计模式</strong><span>D1 / D2 独立查看</span></div><div class="tabs mode-tabs analysis-tabs" role="tablist" aria-label="统计模式切换">${['D1','D2'].map((m,i)=>`<button type="button" role="tab" tabindex="${state.mode===m?0:-1}" class="tab ${state.mode===m?'on':''}" aria-selected="${state.mode===m}" aria-controls="v3App" aria-label="${m}" onclick="V3.selectAnalysisMode('${m}')" onkeydown="if(['ArrowLeft','ArrowRight','Home','End'].includes(event.key)){event.preventDefault();V3.selectAnalysisMode(event.key==='Home'?'D1':event.key==='End'?'D2':'${m==='D1'?'D2':'D1'}');}"><b>${m}</b><span>${i===0?'D0 买入 · D1 卖出':'D1 买入 · D2 卖出'}</span></button>`).join('')}</div><span class="analysis-current" aria-live="polite">当前查看 ${state.mode}</span></section>`;}
  function rankTabs(){return `<div class="rank-switch" aria-label="名次对照"><span>对照</span><div class="tabs medal-tabs">${[1,2].map(r=>`<button class="tab ${state.rank===r?'on':''}" aria-label="第${r===1?'一':'二'}名" aria-pressed="${state.rank===r}" onclick="V3.set('rank',${r})">${r===1?'🥇':'🥈'} 第${r===1?'一':'二'}名</button>`).join('')}</div></div>`;}
  function selectModel(){return `<select class="field" aria-label="模型" onchange="V3.set('model',this.value)">${models.map(m=>`<option value="${m.id}" ${m.id===state.model?'selected':''}>${m.name}</option>`).join('')}</select>`;}
  function hero(title,desc,pill){return `<section class="hero"><div><h2>${title}</h2><p>${desc}</p></div><span class="pill">${pill}</span></section>`;}
  function notice(){return '';}

  const sessionColumns={trade:{capital:[5,6,7,8,9],morning:[10,11,12,13],afternoon:[14,15,16,17],batch:[18,19]},history:{morning:[2,3],afternoon:[4,5],batch:[6,7,8,9]},compare:{morning:[4,5],afternoon:[6,7],batch:[8,9,10,11]}};
  function sessionClass(index){const groups=sessionColumns[state.view];if(!groups)return '';for(const [group,indexes] of Object.entries(groups)){if(indexes.includes(index))return `session-cell session-${group}${index===indexes[0]?' session-start':''}${index===indexes.at(-1)?' session-end':''}`;}return '';}
  function tableLegend(){return ''; }
  function table(headers,body,summary='',mobile=false){return tableLegend()+`<div class="tablewrap ${mobile?'desktoponly':''}" tabindex="0" aria-label="交易数据表，可横向滚动"><table><thead><tr>${headers.map((h,i)=>`<th class="${sessionClass(i)}" ${state.view==='trade'&&columns[i]?`onclick="V3.sort(${i})" tabindex="0" onkeydown="if(event.key==='Enter')V3.sort(${i})"`:''}>${h}</th>`).join('')}</tr>${summary?`<tr class="summary">${summary}</tr>`:''}</thead><tbody>${body||`<tr><td colspan="${headers.length}" class="empty-state">该范围暂无记录</td></tr>`}</tbody></table></div>`;}
  function cell(value,index){return `<td class="${sessionClass(index)}">${value}</td>`;}
  const columns=['date','score','stock_name','open_price','board','capital','buyAmount','buyBudget','positionAmount','cashAmount','am','ra','amHalf','aProfit','pm','rp','pmHalf','pProfit','batchTotal','profit','saleAmount','ending'];
  function stock(t){const c=esc(t.stock_code||'—'),n=esc(t.stock_name||'空占位');const url=baiduFinanceUrl(t.stock_code);return `<span class="code">${c}</span><span class="stock">${url?`<a class="name-link" href="${esc(url)}" target="_blank" rel="noopener noreferrer">${n}</a>`:n}</span>`;}
  function price(t,key){
    if(masked())return '<span class="chip">已脱敏</span>';
    if(key==='open_price')return `<span title="${esc(t.open_price_date||t.signal_date||t.date)} · D0 开盘价${num(t.open_price)===null?'，行情缺失':''}">${money(t.open_price)}</span>`;
    if(placeholder(t))return '<span class="chip">空占位</span>';
    if(!t.executed)return `<span class="chip" title="${esc(t.status)}">${key==='buy'&&t.buy?money(t.buy):'未买入'}</span>`;
    const prefix=state.mode==='D2'?'d2_':'',suffix=key==='am'?'935':'1335';
    if(t[key]===null){const label=t.remainingShares===0?'该时点未结算':t[key+'_status']==='未安排'?'尚未卖出':t[key+'_status']||'持仓中 / 缺价';return `<span class="muted" title="${esc(label)}${key==='pm'?'；不以13:15替代':''}">—<span class="stock-sub">${esc(label)}</span></span>`;}
    const sold=key==='buy'?'':t[key+'_sell_date']||'';
    const time=key==='am'?'9:35':'13:35';
    return `<span title="${key==='buy'?'买入日开盘价':esc(`${sold?sold+' ':''}${time} 实际模拟成交价`)}">${money(t[key])}</span>${sold?`<div class="stock-sub">${esc(sold)}</div>`:''}`;
  }
  function scoreValue(t){const label=String(t.score_label??'').trim(),value=t.score;return model().program==='stock_v2'&&label?label:(value!==null&&value!==undefined&&String(value).trim()!==''?value:(label?label.split(/[（(]/)[0]:'—'));}
  function score(t){const value=scoreValue(t);return value==='—'?'<span class="muted" title="没有对应的原始评分记录">—</span>':esc(value);}
  function operations(t){if(raw.filter(r=>Number(r.id)===Number(t.id)).length>1)return '<span class="muted" title="原始记录ID重复，旧写接口无法唯一定位；已禁止误改其他日期">ID重复</span>';return window.__PAGES_MODE__?'—':`<button class="smallbtn" onclick="V3.edit(${Number(t.id)})">编辑</button>`;}
  function latestBuy(list,today=beijingToday()){return list.filter(r=>!placeholder(r)&&r.date<=today&&(r.executed||r.date===today)).sort((a,b)=>b.date.localeCompare(a.date)||Number(b.id)-Number(a.id))[0];}
  function boardTag(board){return `<span class="board-label ${board==='主板'?'board-main':board==='创业板'?'board-growth':'board-neutral'}">${esc(board||'—')}</span>`;}
  function nameLink(t){const url=baiduFinanceUrl(t.stock_code);return url?`<a class="name-link" href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(t.stock_name||'—')}</a>`:esc(t.stock_name||'—');}
  function periodControls(target){const from=target==='history'?String(state.yearFrom):state.compareFrom,to=target==='history'?String(state.yearTo):state.compareTo;return `<div class="filters">${target==='compare'?`<button class="month-trigger" aria-label="对比月份" onclick="V3.openPicker('single','from','compare')">▦ ${from===to?from:'选择月份'}</button><button class="month-trigger" onclick="V3.openGrid('year','compare')">选择年份 ▾</button>`:''}<button class="month-trigger" aria-label="${target}开始" onclick="V3.openPicker('range','from','${target}')">起 ${from} ⌄</button><span class="range-dash">—</span><button class="month-trigger" aria-label="${target}结束" onclick="V3.openPicker('range','to','${target}')">止 ${to} ⌄</button></div>`;}
  function latestCard(latest){return `<div class="card latest-card"><div class="label">${latest&&!latest.executed?'今日选股':'最近买入标的'}</div><div class="value">${masked()?'登录后查看':latest?nameLink(latest):'暂无买入记录'}</div><div class="latest-meta">${masked()?'<span class="muted">已脱敏</span>':latest?`${boardTag(latest.board)}<span class="buy-date">· ${esc(latest.date)}</span>${!latest.executed?'<span class="muted"> · 成交待确认</span>':''}`:'<span class="muted">筛选范围内暂无有效记录</span>'}</div></div>`;}
  function maintenance(){return window.__PAGES_MODE__?'':`<div class="workspace-maintenance" id="v3Maintenance" role="group" aria-label="数据维护与导出"><button type="button" class="maintenance-tool" title="导出当前筛选的交易明细 CSV" onclick="V3.exportCSV()">导出 CSV</button><button type="button" class="maintenance-tool" title="执行收盘补全" onclick="runCloseJob()">收盘补全</button><button type="button" class="maintenance-tool" title="补全当前模型的价格空值" onclick="refreshPrices(false)">补全空值</button><button type="button" class="maintenance-tool maintenance-force" title="强制重拉当前模型的价格" onclick="refreshPrices(true)">强制重拉</button></div>`;}
  function monthControls(){return `<div class="month-controls"><div class="month-quick"><button class="btn" onclick="V3.month(-1)" aria-label="上一月">‹</button><button class="month-trigger" aria-label="月份" aria-haspopup="dialog" onclick="V3.openPicker('single')"><span class="calendar-icon">▦</span><span>${state.month.replace('-',' 年 ')} 月</span><span class="picker-chevron">⌄</span></button><button class="btn" onclick="V3.month(1)" aria-label="下一月">›</button><button class="btn" onclick="V3.month(0)">当月</button></div><div class="range-triggers"><button class="month-trigger range-trigger" aria-label="开始月份" aria-haspopup="dialog" onclick="V3.openPicker('range','from')"><span class="range-caption">起始年月</span><span>${state.from?state.from.replace('-',' / '):'选择起始月份'}</span><span class="picker-chevron">⌄</span></button><span class="range-dash">—</span><button class="month-trigger range-trigger" aria-label="结束月份" aria-haspopup="dialog" onclick="V3.openPicker('range','to')"><span class="range-caption">截止年月</span><span>${state.to?state.to.replace('-',' / '):'选择截止月份'}</span><span class="picker-chevron">⌄</span></button></div>${state.from?'<button class="btn" onclick="V3.clearRange()">清除区间</button>':''}</div>`;}
  function trade(){
    const a=scoped(rows),t=totals(a),latest=latestBuy(a),from=state.from||state.month,to=state.to||state.month;
    const stats=periodStats(rows,from,to),current=assetSnapshot(rows,to),range=from===to?from:`${from} — ${to}`;
    let html=`<div class="headrow"><h3>交易概览 <span class="hint">· ${range}</span></h3><div class="actions">${modeTabs()}${rankTabs()}</div></div>`;
    if(rows.ledger?.market_status==='offline')html+=`<p class="hint" role="status">选股同步至 ${esc(rows.ledger.as_of?.replace('T',' '))} · 行情库暂不可用，金额保留至 ${esc(rows.ledger.valuation_as_of||'最近有效快照')}，新成交待确认。</p>`;
    html+='<div class="grid overview-cards">';
    html+=latestCard(latest);
    html+=card('期末总资产',money(current?.equity),assetNote(current?.valuation_marks,`截至 ${current?.date||cutoffDay()} · 现金＋持仓`),'#d88d32');
    html+=card('期末可用现金',money(current?.cash_available),`账户现金 ${money(current?.cash)} · 锁定 ${money(current?.cash_locked)}`);
    html+=card('本期资产盈亏',fmt(stats.assetPnl),'已实现利润＋浮动利润变化','#a06be1');
    html+=card('本期已实现利润',fmt(stats.profit),'按本期结算日期汇总','#e58d39');
    html+=card('9:35 分批利润',fmt(stats.aProfit),'本期早盘已实现利润','#d95770');
    html+=card('13:35 分批利润',fmt(stats.pProfit),'本期午盘已实现利润','#19a78e')+'</div>';
    html+=`<section class="panel"><div class="paneltop"><div><h3>逐笔交易明细 · ${state.mode}</h3><div class="hint">按买入日筛选 · 交易结果截至 ${cutoffDay()} · 金额单位：元</div></div><div class="filters detail-filters"><input class="field trade-search" aria-label="搜索代码或名称" placeholder="搜索代码 / 名称" value="${esc(state.keyword)}" onchange="V3.set('keyword',this.value)">${monthControls()}${!window.__PAGES_MODE__?'<button class="btn primary" onclick="V3.edit()">＋ 新增</button>':''}</div></div>`;
    const multi=state.from&&state.from!==state.to,per=multi?state.per:Math.max(1,a.length),pages=Math.max(1,Math.ceil(a.length/per));state.page=Math.min(state.page,pages);
    const sorted=[...a].sort((a,b)=>{const x=a[state.sort],y=b[state.sort];if(x==null)return 1;if(y==null)return -1;return state.dir*(typeof x==='number'?x-y:String(x).localeCompare(String(y)));});
    const visible=sorted.slice((state.page-1)*per,state.page*per);
    const heads=['日期','分值','代码 / 名称','开盘价','板块','期初总资产','买入前可用现金','半仓额度','买入持仓金额','额度余款','9:35 卖价','9:35 整批盈亏','9:35 分批盈亏','9:35 分批利润','13:35 卖价','13:35 整批盈亏','13:35 分批盈亏','13:35 分批利润','分批盈亏合计','本笔利润','累计回款','买入日收盘总资产','操作'];
    const sumrow=heads.map((_,i)=>cell(i===0?'所选买入交易汇总':({11:fmt(t.ra),12:fmt(t.amHalf),13:fmt(t.aProfit),15:fmt(t.rp),16:fmt(t.pmHalf),17:fmt(t.pProfit),18:fmt(t.batchTotal),19:fmt(t.profit)})[i]||'—',i)).join('');
    const status=r=>`<div class="stock-sub trade-note" title="${esc(r.status)} · 信号D0 ${esc(r.signal_date)} · 计划卖出 ${esc(r.planned_sell_date||'—')} · 初始${r.shares}股 / 剩余${r.remainingShares}股 · 剩余持仓 ${money(r.remaining_position_value)} · 已实现 ${money(r.realized_profit_total)} · 浮动 ${money(r.unrealized_profit)}">${esc(r.status)}${r.fund_id?' · '+r.fund_id:''}${r.substituted_from?' · 停牌替补':''}</div>`;
    const values=r=>[`${esc(r.date)}<div class="muted">${weekdayOf(r.date)}</div>`,score(r),`<div class="trade-identity" title="${esc(r.stock_code)} ${esc(r.stock_name)}">${stock(r)}${status(r)}</div>`,price(r,'open_price'),boardTag(r.board),assetMoney(r.capital,r.capital_marks),money(r.buyAmount),money(r.buyBudget),money(r.initial_buy_cost),`<span title="仅为本次半仓额度未用部分，不是账户现金">${money(r.budget_remainder)}</span>`,price(r,'am'),fmt(r.ra),fmt(r.amHalf),fmt(r.aProfit),price(r,'pm'),fmt(r.rp),fmt(r.pmHalf),fmt(r.pProfit),fmt(r.batchTotal),`${fmt(r.display_profit)}<div class="stock-sub">${esc(r.profit_kind||'')}</div>`,money(r.saleAmount),money(r.buy_day_close_equity),operations(r)];
    html+=table(heads,visible.map(r=>'<tr>'+values(r).map(cell).join('')+'</tr>').join(''),sumrow,true);
    html+=`<div class="mobilecards">${visible.map(r=>`<article class="mobiletrade"><div class="mtop"><div>${stock(r)}</div>${boardTag(r.board)}</div><div class="hint">${esc(r.date)} · ${score(r)}分${status(r)}</div><div class="mgrid">${values(r).slice(3,-1).map((v,i)=>`<div class="${i>=7&&i<=10?'metric-morning':i>=11&&i<=14?'metric-afternoon':i>=15&&i<=16?'metric-batch':i>=2&&i<=6?'metric-capital':''}">${heads[i+3]}<b>${v}</b></div>`).join('')}<div>${operations(r)}</div></div></article>`).join('')||'<div class="empty-state">该范围暂无记录</div>'}</div>`;
    html+=`<div class="bottom"><span class="notice">共 ${a.length} 条 · 第 ${state.page} / ${pages} 页</span><div class="filters">${[['首页',1],['上一页',state.page-1],['下一页',state.page+1],['末页',pages]].map(([label,n],i)=>`<button class="btn" ${i<2?state.page<=1?'disabled':'':state.page>=pages?'disabled':''} onclick="V3.page(${n})">${label}</button>`).join('')}</div></div><div class="footnote">按确认的模板规则：价差0.97作为收益率0.97%，金额再除100。整批盈亏为分析金额；分批盈亏按前日持仓分摊计算；分批利润为回款减初始成本。额度余款不含回款，不是账户现金。初始买入成本固定保留，本笔利润标明浮动或已实现。账户概览按结算期间统计，列表汇总针对所选买入交易；搜索只影响列表。买入日收盘资产与本笔后续交易结果分组取值。</div></section>`;
    return html;
  }
  function assetSnapshot(list,month,before=false){
    if(masked()||month<'2026-01'||month>now)return null;
    const boundary=month+'-01',p=period(boundary),snapshots=list.ledger?.snapshots||[];
    if(before&&(month==='2026-01'||month==='2026-11'))return {equity:capitalBase(boundary),valuation_marks:[]};
    const selected=snapshots.filter(r=>r.period===p&&(before?r.date<boundary:r.date.slice(0,7)<=month));
    return selected.at(-1)??{equity:capitalBase(boundary),valuation_marks:[]};
  }
  function snapshot(list,month,before=false){return assetSnapshot(list,month,before)?.equity??null;}
  function monthOpening(list,month){return snapshot(list,month,true);}
  function monthClosing(list,month){return snapshot(list,month);}
  function periodStats(list,from,to){
    const months=list.ledger?.monthly_stats||{},selected=Object.keys(months).filter(m=>m>=from&&m<=to&&m<=now).sort().map(m=>months[m]);
    const t=totals(selected),start=from>now?null:months[from],closing=to>now?now:to,end=from>now?null:months[closing];
    const completeSum=key=>selected.length&&selected.every(r=>num(r[key])!==null)?selected.reduce((n,r)=>n+Number(r[key]),0):null;
    const pnl=completeSum('assetPnl'),samePeriod=period(from+'-01')===period(closing+'-01');
    return {...t,opening:start?.opening??null,ending:end?.ending??null,
      openingMarks:start?.openingMarks||[],endingMarks:end?.endingMarks||[],count:selected.reduce((n,r)=>n+(r.count||0),0),
      otherProfit:sum(selected,'otherProfit'),floatingChange:completeSum('floatingChange'),assetPnl:pnl,
      accountReturnPct:samePeriod&&pnl!==null&&num(start?.opening)?pnl/start.opening*100:null,
      cash:end?.cash??null,cash_available:end?.cash_available??null,cash_locked:end?.cash_locked??null,position_value:end?.position_value??null};
  }
  function monthly(list,fromYear,toYear){
    const months=[];for(let y=fromYear;y<=toYear;y++)for(let m=1;m<=12;m++)months.push(`${y}-${String(m).padStart(2,'0')}`);
    return months.sort((a,b)=>{const af=a>now,bf=b>now;return af!==bf?(af?1:-1):b.localeCompare(a);}).map(month=>({month,...periodStats(list,month,month)}));
  }
  function history(){
    const items=monthly(rows,state.yearFrom,state.yearTo),t=periodStats(rows,state.yearFrom+'-01',state.yearTo+'-12');
    const effective=items.filter(r=>r.count),wins=effective.filter(r=>num(r.profit)>0).length,loss=effective.filter(r=>num(r.profit)<0).length;
    let html=analysisModeTabs()+`<div class="headrow"><h3>月度统计概览</h3><div class="actions">${rankTabs()}</div></div><div class="grid g5">`+card('盈利 / 亏损月份',`${wins} / ${loss}`,'按已实现利润统计')+card('资产盈亏',fmt(t.assetPnl),'已实现＋浮动变化','#d95770')+card('已实现利润',fmt(t.profit),'按实际结算日汇总','#de9237')+card('期末总资产',money(t.ending),'现金＋模板持仓价值')+card('有效交易月份',effective.length,'有实际结算事件','#1caa9d')+'</div>';
    html+=`<section class="panel"><div class="paneltop"><h3>历史月度明细 · 金额单位：元</h3>${periodControls('history')}</div>`;
    const values=r=>[r.month,assetMoney(r.opening,r.openingMarks),fmt(r.amHalf),fmt(r.aProfit),fmt(r.pmHalf),fmt(r.pProfit),fmt(r.otherProfit),fmt(r.profit),fmt(r.floatingChange),fmt(r.assetPnl),money(r.cash),money(r.position_value),assetMoney(r.ending,r.endingMarks)];
    html+=table(['月份','月初总资产','9:35 分批盈亏','9:35 已实现利润','13:35 分批盈亏','13:35 已实现利润','其他结算利润','已实现利润合计','浮动利润变化','资产盈亏','月末账户现金','月末持仓金额','月末总资产'],items.map(r=>`<tr class="month-row" tabindex="0" onclick="V3.jump('${r.month}')" onkeydown="if(event.key==='Enter')V3.jump('${r.month}')">`+values(r).map(cell).join('')+'</tr>').join(''),['全部汇总','—',fmt(t.amHalf),fmt(t.aProfit),fmt(t.pmHalf),fmt(t.pProfit),fmt(t.otherProfit),fmt(t.profit),fmt(t.floatingChange),fmt(t.assetPnl),'—','—','—'].map(cell).join(''));
    return html+'<div class="footnote">月末总资产＝月初总资产＋已实现利润＋浮动利润变化。现金和持仓属于余额，不逐行相加；跨资金重置账期分段承接。月表与模型对比直接使用同一期间统计。</div></section>';
  }
  function modelJump(m,rank=1){return m?`<button class="model-link" onclick="V3.chooseRank('${m.id}',${rank})">${m.name} · 第${rank===1?'一':'二'}名</button>`:'—';}
  function compare(){
    const data=comparison.map(({m,rank,list,loading,error})=>({m,rank,loading,error,...(list?periodStats(list,state.compareFrom,state.compareTo):{}),p:list?num(periodStats(list,state.compareFrom,state.compareTo).profit):null})).sort((a,b)=>a.p===null&&b.p===null?models.indexOf(a.m)-models.indexOf(b.m)||a.rank-b.rank:a.p===null?1:b.p===null?-1:b.p-a.p);
    const max=Math.max(...data.map(r=>Math.abs(r.p||0)),1),valid=data.filter(r=>r.p!==null);
    const loaded=comparison.filter(r=>r.list).length,pending=comparison.filter(r=>r.loading).length,failed=comparison.filter(r=>r.error).length;
    let html=analysisModeTabs()+`<div class="comparison-progress" role="status">${pending?`正在汇总模型数据：${loaded} / 18`:`已载入 ${loaded} / 18 条模型名次线`}${failed?` · ${failed} 条加载失败，可单独重试`:''}</div>`+'<div class="grid g4">'+card('最佳模型',modelJump(valid[0]?.m,valid[0]?.rank),money(valid[0]?.p),'#df9441')+card('最弱模型',modelJump(valid.at(-1)?.m,valid.at(-1)?.rank),money(valid.at(-1)?.p),'#7b84bd')+card('统计范围',esc(state.compareFrom===state.compareTo?state.compareFrom:`${state.compareFrom} — ${state.compareTo}`),'与对应模型历史月表一致')+card('资金口径','模板收益模型','价差数值作为百分数，按确认规则结算','#1caa9d')+'</div>';
    html+=`<section class="panel compare-panel"><div class="paneltop"><h3>全模型已实现利润排名 · 金额单位：元</h3><div class="actions">${periodControls('compare')}</div></div>`;
    html+=table(['排名 / 模型','名次','所属档位','期初总资产','9:35 分批盈亏','9:35 已实现利润','13:35 分批盈亏','13:35 已实现利润','其他结算利润','已实现利润合计','浮动利润变化','资产盈亏','期末总资产','利润相对规模'],data.map((r,i)=>`<tr class="compare-row" tabindex="0" title="查看 ${r.m.name} · 第${r.rank===1?'一':'二'}名 · ${state.mode} 明细" onclick="V3.chooseRank('${r.m.id}',${r.rank})" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();V3.chooseRank('${r.m.id}',${r.rank});}">`+[`${i+1}. <b>${r.m.name}</b> ${r.loading?'<span class="chip">加载中…</span>':r.error?`<button class="smallbtn" title="${esc(r.error)}" onclick="event.stopPropagation();V3.retryComparison('${r.m.id}',${r.rank})">重试</button>`:''} ${valid.length&&r===valid[0]?'<span class="chip best-tag">最好</span>':valid.length&&r===valid.at(-1)?'<span class="chip worst-tag">最弱</span>':''}`,`<span class="rank-medal">${r.rank===1?'🥇 第一名':'🥈 第二名'}</span>`,tiers[r.m.tier],assetMoney(r.opening,r.openingMarks),fmt(r.amHalf),fmt(r.aProfit),fmt(r.pmHalf),fmt(r.pProfit),fmt(r.otherProfit),fmt(r.p),fmt(r.floatingChange),fmt(r.assetPnl),assetMoney(r.ending,r.endingMarks),`<div class="comparebar ${r.p>0?'positive':r.p<0?'negative':'neutral'}"><i style="width:${Math.abs(r.p||0)/max*100}%"></i></div>`].map(cell).join('')+'</tr>').join(''));
    return html+'<div class="footnote">按同一模型、名次、D1/D2及期间比较，已实现利润与月表逐项一致。资产盈亏另包含浮动利润变化；期初和期末资产不求和。缺失估值保持空值。</div></section>';
  }
  function pool(){
    const a=raw.filter(t=>String(t.date)===state.poolDate);
    const date=state.poolDate||beijingToday(),[y,m,d]=date.split('-');
    let html=`<section class="panel pool-panel"><div class="paneltop"><div><h3>首次涨停池</h3><div class="hint">涨停日 ${esc(date)} · 买入日 ${esc(nextDates.get(date)||a[0]?.d1||'—')} · 各板块均检查D0−2，剔除连板</div></div><div class="filters">${[['year',y+' 年'],['month',m+' 月'],['day',d+' 日']].map(([level,label])=>`<button class="month-trigger" onclick="V3.openGrid('${level}','pool')">${label} ⌄</button>`).join('')}<button class="btn" onclick="V3.latestPool()">最近已完成交易日</button></div></div>`;
    html+=table(['涨停日','买入日','名称','板块','概念板块','行业板块','涨跌幅','换手率','连板','开盘竞价量（股）','收盘量比','来源'],a.map(r=>'<tr>'+[esc(r.date),esc(r.d1||r.buy_date||nextDates.get(date)||'—'),nameLink(r),boardTag(r.board),esc(r.concept_board||'—'),esc(r.industry_board||'—'),fmt(r.d0_pct,true),pct(r.turnover),esc(r.lianban??'—'),r.auction_volume==null?'—':money(r.auction_volume),r.close_volume_ratio==null?'—':money(r.close_volume_ratio),esc(r.src||r.source||'—')].map(cell).join('')+'</tr>').join(''));
    return html+`<div class="footnote">共 ${a.length} 条。按买入日前一交易日的涨停池查阅，保留各板块的首次涨停，D0−2已涨停或校验资料不足时不入池。竞价量必须对应涨停日开盘，量比必须对应该日收盘；缺失时为 —，不使用实时行情代替。${esc(state.poolNote||'')}</div></section>`;
  }
  function navIcon(name){
    const paths={trade:'<path d="M4 17l5-5 4 3 7-10"/><path d="M15 5h5v5"/>',history:'<rect x="4" y="5" width="16" height="15" rx="2"/><path d="M8 3v4m8-4v4M4 11h16m-12 4h2m4 0h2"/>',compare:'<path d="M5 19V9m7 10V4m7 15v-7M3 20h18"/>',pool:'<rect x="4" y="4" width="6" height="6" rx="1"/><rect x="14" y="4" width="6" height="6" rx="1"/><rect x="4" y="14" width="6" height="6" rx="1"/><rect x="14" y="14" width="6" height="6" rx="1"/>',theme:'<path d="M12 3a9 9 0 1 0 9 9c-5 2-11-4-9-9Z"/>',refresh:'<path d="M20 7v5h-5M4 17v-5h5"/><path d="M6 7a7 7 0 0 1 12-1l2 3M4 15l2 3a7 7 0 0 0 12-1"/>'};
    return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name]||paths.trade}</svg>`;
  }
  function menu(open){
    const header=$('v3Header'),trigger=$('v3ModelToggle');if(!header||!trigger)return;
    header.classList.toggle('models-open',!!open);trigger.setAttribute('aria-expanded',String(!!open));
  }
  function renderNavigation(){
    const contextual=state.view==='trade'||state.view==='history';
    $('v3ModelBar').hidden=!contextual;if(!contextual)menu(false);
    const key=state.view+'|'+state.model;
    if(key!==navigationKey){
      $('v3TradeNav').innerHTML=tiers.map((tier,i)=>{
        const [level,label]=tier.split(' · ');
        return `<section class="model-group ${model().tier===i?'current-group':''}" data-tier="${i}" aria-label="${esc(tier)}"><button type="button" class="tier-caption tier-tab" data-tier="${i}" aria-label="${esc(tier)}，选择该档模型" aria-pressed="${model().tier===i}" title="${esc(tier)} · 点击进入该档模型" onclick="V3.selectTier(${i})"><span class="tier-level">${level}</span><span class="tier-name">${label}</span></button><div class="model-links">${models.filter(m=>m.tier===i).map(m=>`<button type="button" class="model-nav ${m.id===state.model?'active':''}" data-model="${m.id}" aria-pressed="${m.id===state.model}" onclick="V3.selectNavigationModel('${m.id}')">${m.name}</button>`).join('')}</div></section>`;
      }).join('');
      navigationKey=key;
    }
    ['trade','history','compare','pool'].forEach(view=>{const button=$('v3nav'+view),active=state.view===view;button.classList.toggle('active',active);if(active)button.setAttribute('aria-current','page');else button.removeAttribute('aria-current');});
    $('v3ModelLabel').textContent=model().name;
    $('v3ModelTier').textContent=tiers[model().tier].split(' · ')[0];
    const tools=$('v3Maintenance');if(tools)tools.hidden=state.view!=='trade'||!!window.__PAGES_MODE__;
    $('v3Header').classList.toggle('has-maintenance',state.view==='trade'&&!window.__PAGES_MODE__);
  }
  function selectTier(tier){
    const selected=model().tier===tier?model():models.find(m=>m.tier===tier);if(!selected)return;
    if(selected.id===state.model){$('v3TradeNav').querySelector(`[data-model="${selected.id}"]`)?.focus();return;}
    return selectNavigationModel(selected.id);
  }
  async function selectNavigationModel(id){
    if(!models.some(m=>m.id===id))return;
    state.model=id;state.rank=1;state.page=1;if(state.view!=='history')state.view='trade';sync();menu(false);
    window.scrollTo({top:0,behavior:'auto'});const loaded=await load();if(!loaded)return;
    const target=window.matchMedia('(max-width:1080px)').matches?$('v3ModelToggle'):$('v3TradeNav').querySelector(`[data-model="${id}"]`);
    target?.focus({preventScroll:true});
  }
  function render(){
    const m=model();
    renderNavigation();
    $('v3Title').textContent=state.view==='trade'?`${m.name} · ${state.mode} 交易明细`:state.view==='history'?`历史月表 · ${m.name} · ${state.mode}`:state.view==='compare'?`模型对比 · 四档全模型 · ${state.mode}`:'涨停池 · 首次涨停查阅';
    $('v3Sub').textContent=state.view==='trade'?(state.mode==='D1'?'D0−1 涨停 · D0 买入 · D1 分批卖出（9:35 / 13:35）':'D0−1 涨停 · D0 不买 · D1 买入 · D2 分批卖出（9:35 / 13:35）'):state.view==='pool'?'最近已完成交易日 · 仅查阅':`${tiers[m.tier]} / ${state.view==='compare'?'第一名 / 第二名统一比较':state.rank===1?'第一名':'第二名'} / ${state.mode}`;
    $('v3Sub').title=$('v3Sub').textContent;
    $('v3Title').title=$('v3Title').textContent;
    $('v3App').innerHTML=state.view==='trade'?trade():state.view==='history'?history():state.view==='compare'?compare():pool();
    $('v3Auth').textContent=window.__PAGES_MODE__?(window.__PAGES_FULL__?'退出':'登录'):'● 本机数据';
    $('v3Auth').disabled=!window.__PAGES_MODE__;
    $('v3Auth').hidden=!window.__PAGES_MODE__;
  }
  async function load(force=false){
    const ticket=++seq; $('v3Status').textContent='正在加载真实记录…';
    // Clear previous context immediately so old model contents cannot masquerade as new.
    raw=[];rows=[];comparison=state.view==='compare'?models.flatMap(m=>[1,2].map(rank=>({m,rank,list:null,loading:true,error:null}))):[];render();
    try{
      if(window.__PAGES_MODE__&&!window.__PAGES_SNAPSHOT__)await __pagesBoot();
      if(state.view==='compare'){await Promise.allSettled(comparison.map(entry=>loadComparisonEntry(entry,ticket,state.mode,force)));if(ticket!==seq)return false;}
      else if(state.view==='pool'){
        const response=window.__PAGES_MODE__?{items:await api('/api/trades?program=zt_pool')}:await api('/api/v3/pool'+(state.poolDate?'?date='+state.poolDate:''));if(ticket!==seq)return false;raw=response.items||[];state.poolNote=response.note||'';if(response.enriching)setTimeout(()=>refreshPoolExtras(response.date,ticket),2500);if(response.date)state.poolDate=response.date;
        poolDays=[...new Set(raw.map(r=>r.date))].filter(d=>d<beijingToday()||(d===beijingToday()&&Number(new Intl.DateTimeFormat('en-GB',{timeZone:'Asia/Shanghai',hour:'2-digit',hour12:false}).format(new Date()))>=16)).sort().reverse();
        if(!state.poolDate)state.poolDate=poolDays[0]||beijingToday();
        await loadPoolDate();
      }else{
        const data=await api(`/api/v3/ledger?model=${state.model}&rank=${state.rank}&mode=${state.mode}${force?'&refresh=1':''}`);
        if(ticket!==seq)return false;
        rows=calculate(data,state.mode);raw=rows;trades=rows.map(r=>({...r,date:r.signal_date||r.date}));
        if(data.marks_refreshing)setTimeout(()=>refreshLedgerMarks(ticket,state.mode),3000);
      }
      if(ticket!==seq)return false;
      $('v3Status').textContent='数据已加载';
      render(); return true;
    }catch(e){if(ticket!==seq)return false;$('v3Status').textContent='加载失败';$('v3App').innerHTML=`<section class="panel empty-state"><p class="error">${esc(e.message||'数据加载失败')}</p><button class="btn" onclick="V3.load()">重试</button></section>`;return false;}
  }
  async function refreshLedgerMarks(ticket,mode){
    if(ticket!==seq||state.view==='compare'||state.view==='pool')return;
    try{
      const payload=await api(`/api/v3/ledger?model=${state.model}&rank=${state.rank}&mode=${mode}`);
      if(ticket!==seq)return;rows=calculate(payload,mode);raw=rows;trades=rows.map(r=>({...r,date:r.signal_date||r.date}));render();
      if(payload.marks_refreshing)setTimeout(()=>refreshLedgerMarks(ticket,mode),3000);
    }catch(error){}
  }
  async function loadComparisonEntry(entry,ticket,mode,force=false){
    try{
      const payload=await api(`/api/v3/ledger-summary?model=${entry.m.id}&rank=${entry.rank}&mode=${mode}${force?'&refresh=1':''}`);
      if(ticket!==seq)return;
      entry.list=calculate(payload,mode);entry.error=null;
      if(payload.marks_refreshing)setTimeout(()=>{if(ticket===seq)loadComparisonEntry(entry,ticket,mode);},3000);
    }catch(error){if(ticket!==seq)return;entry.error=error.message||'加载失败';entry.list=null;}
    finally{if(ticket===seq){entry.loading=false;render();}}
  }
  async function retryComparison(id,rank){
    const entry=comparison.find(r=>r.m.id===id&&r.rank===rank);if(!entry||entry.loading)return;
    entry.loading=true;entry.error=null;render();await loadComparisonEntry(entry,seq,state.mode);
  }
  async function refreshPoolExtras(day,ticket){if(seq!==ticket||state.view!=='pool'||state.poolDate!==day)return;try{const data=await api('/api/v3/pool?date='+day);if(seq!==ticket)return;raw=data.items||[];state.poolNote=data.note||'';render();if(data.enriching)setTimeout(()=>refreshPoolExtras(day,ticket),2500);}catch(e){}}
  async function loadPoolDate(){if(window.__PAGES_MODE__||nextDates.has(state.poolDate))return;const day=state.poolDate;try{const data=await api('/api/v3/next-trading-day?date='+day);nextDates.set(day,data.next_date);}catch(e){nextDates.set(day,null);}}
  function sync(){const m=model();currentProgram=m.program;currentBoard=m.board||'';currentPath=m.path||'';currentRank=state.rank;currentMonth=state.month;viewMode='trades';$('monthFilter').innerHTML=`<option value="${state.month}">${state.month}</option>`;$('keyword').value=state.keyword;}
  function set(k,v){if(!v&&['month','model','mode'].includes(k))return;state[k]=v;state.page=1;if(k==='month'){state.from='';state.to='';}if(k==='model')state.rank=1;sync();if(['model','rank','mode'].includes(k))load();else render();}
  function init(){
    for(const field of FIELDS){if(field.key==='sell_price_1315'){field.key='sell_price_1335';field.label='D1 13:35 卖价';}}
    const root=document.createElement('div');root.id='v3Root';root.innerHTML=`
      <header class="workspace-header" id="v3Header">
        <div class="workspace-top">
          <div class="workspace-heading">
            <button type="button" class="workspace-mark" title="交易数据记录 · 返回交易明细" aria-label="交易数据记录，返回交易明细" onclick="V3.view('trade')">${navIcon('trade')}</button>
            <div class="workspace-titles"><div class="title" id="v3Title" role="heading" aria-level="1"></div><div class="subtitle" id="v3Sub"></div></div>
          </div>
          <nav class="workspace-primary" aria-label="页面导航">
            ${[['trade','交易明细'],['history','历史月表'],['compare','模型对比'],['pool','涨停池']].map(([view,label])=>`<button type="button" id="v3nav${view}" class="primary-nav" onclick="V3.view('${view}')">${navIcon(view)}<span>${label}</span></button>`).join('')}
          </nav>
          <div class="workspace-actions"><div class="workspace-common-tools">
            <button type="button" class="btn workspace-tool" onclick="V3.theme()" aria-label="切换亮暗主题" title="切换亮暗主题">${navIcon('theme')}<span>主题</span></button>
            <button type="button" class="btn" id="v3Auth" onclick="__pagesToggleAuth()"></button>
            <button type="button" class="btn workspace-tool" onclick="V3.load(true)" aria-label="刷新数据" title="刷新数据">${navIcon('refresh')}<span>刷新</span></button></div>${maintenance()}
          </div>
        </div>
        <div class="workspace-modelbar" id="v3ModelBar">
          <button type="button" id="v3ModelToggle" class="mobile-model-toggle" aria-expanded="false" aria-controls="v3TradeNav" onclick="V3.menu(this.getAttribute('aria-expanded')!=='true')"><span class="model-toggle-label"><span class="model-toggle-caption">当前模型</span><b id="v3ModelLabel"></b><span class="model-toggle-tier" id="v3ModelTier"></span></span><span class="model-toggle-action">切换模型 <span class="model-chevron" aria-hidden="true">⌄</span></span></button>
          <nav id="v3TradeNav" class="workspace-models" aria-label="按档位选择交易模型"></nav>
        </div>
      </header>
      <main class="main"><div class="content"><span id="v3Status" hidden aria-hidden="true">准备加载…</span><div id="v3App"></div></div></main>`;
    const headerResize=new ResizeObserver(entries=>{const height=entries[0]?.target.getBoundingClientRect().height||0;root.style.setProperty('--workspace-header-height',height+'px');});
    headerResize.observe(root.querySelector('#v3Header'));
    document.addEventListener('click',event=>{if(!event.target.closest?.('#v3ModelBar'))menu(false);});
    document.addEventListener('keydown',event=>{if(event.key==='Escape'&&!picker&&$('v3Header')?.classList.contains('models-open')){menu(false);$('v3ModelToggle').focus();}});
    const dialog=document.createElement('div');dialog.id='v3MonthPicker';dialog.className='month-dialog-backdrop';dialog.hidden=true;dialog.addEventListener('click',e=>{if(e.target===dialog)closePicker();});root.appendChild(dialog);
    document.addEventListener('keydown',e=>{if(!picker)return;if(e.key==='Escape')closePicker();else if(e.key==='Tab'){const focusable=[...dialog.querySelectorAll('button:not(:disabled)')],first=focusable[0],last=focusable.at(-1);if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus();}}});
    document.body.appendChild(root);$('authCancel').onclick=()=>{if(!window.__pagesLoginLock)$('authModal').classList.remove('show');};sync();render();
  }
  function openPicker(kind,focus='from',target='trade'){
    menu(false);
    const from=target==='history'?String(state.yearFrom)+'-01':target==='compare'?state.compareFrom:state.from||state.month,to=target==='history'?String(state.yearTo)+'-12':target==='compare'?state.compareTo:state.to||state.month;
    picker={kind,target,from,to,leftYear:Number(from.slice(0,4)),rightYear:Number(to.slice(0,4)),focus,returnFocus:document.activeElement};
    if(target==='history')picker.kind='years';
    renderPicker();$('v3MonthPicker').querySelector('[data-selected="true"]')?.focus();
  }
  function closePicker(){const focus=picker?.returnFocus;picker=null;$('v3MonthPicker').hidden=true;focus?.focus();}
  function renderPicker(){
    if(picker.kind==='grid'||picker.kind==='years')return renderGridPicker();
    const panel=$('v3MonthPicker');panel.hidden=false;
    const grid=(side,title,value,y)=>`<section class="month-picker-column" aria-label="${title}"><div class="month-picker-label">${title}<strong>${value.replace('-',' / ')}</strong></div><div class="month-picker-year"><button class="picker-arrow" aria-label="${title}上一年" onclick="V3.pickerYear('${side}',-1)">‹</button><b>${y} 年</b><button class="picker-arrow" aria-label="${title}下一年" onclick="V3.pickerYear('${side}',1)">›</button></div><div class="month-grid">${Array.from({length:12},(_,i)=>{const ym=`${y}-${String(i+1).padStart(2,'0')}`,selected=ym===value,inRange=picker.kind==='range'&&ym>picker.from&&ym<picker.to;return `<button class="month-cell ${selected?'selected':''} ${inRange?'in-range':''}" data-selected="${selected}" aria-label="${title} ${y}年${i+1}月" aria-pressed="${selected}" onclick="V3.pickMonth('${side}','${ym}')">${i+1}<span>月</span></button>`;}).join('')}</div></section>`;
    panel.innerHTML=`<div class="month-picker-dialog ${picker.kind==='single'?'single-picker':''}" role="dialog" aria-modal="true" aria-label="${picker.kind==='single'?'选择月份':'选择起止年月'}"><div class="month-picker-top"><div><h3>${picker.kind==='single'?'选择月份':'选择起止年月'}</h3><p>${picker.kind==='single'?'选择后立即更新交易明细':'左侧选择起始年月，右侧选择截止年月'}</p></div><button class="picker-arrow" aria-label="关闭月份选择" onclick="V3.closePicker()">×</button></div><div class="month-picker-panels">${grid('from',picker.kind==='single'?'交易月份':'起始年月',picker.from,picker.leftYear)}${picker.kind==='range'?grid('to','截止年月',picker.to,picker.rightYear):''}</div><div class="month-picker-bottom"><span class="picker-error" role="status">${picker.from>picker.to&&picker.kind==='range'?'起始年月不能晚于截止年月':''}</span><button class="btn" onclick="V3.closePicker()">取消</button>${picker.kind==='range'?`<button class="btn primary" ${picker.from>picker.to?'disabled':''} onclick="V3.confirmPicker()">确认范围</button>`:''}</div></div>`;
  }
  function pickMonth(side,value){picker[side]=value;if(picker.kind==='single'){const target=picker.target;closePicker();if(target==='compare'){state.compareFrom=value;state.compareTo=value;render();}else set('month',value);}else renderPicker();}
  function pickerYear(side,delta){picker[side==='from'?'leftYear':'rightYear']+=delta;renderPicker();}
  function confirmPicker(){if(!picker||picker.from>picker.to)return;const {from,to,target}=picker;closePicker();if(target==='history'){state.yearFrom=Number(from.slice(0,4));state.yearTo=Number(to.slice(0,4));}else if(target==='compare'){state.compareFrom=from;state.compareTo=to;}else{state.from=from;state.to=to;}state.page=1;sync();render();}
  function openGrid(level,target){menu(false);const date=target==='pool'?state.poolDate||beijingToday():state.compareFrom;picker={kind:'grid',target,level,date,gridYear:Number(date.slice(0,4)),returnFocus:document.activeElement};renderPicker();}
  function gridMove(side,delta){if(picker.kind==='years')picker[side==='from'?'leftYear':'rightYear']+=delta*9;else if(picker.level==='day'){const [y,m]=picker.date.split('-').map(Number),next=new Date(y,m-1+delta,1);picker.gridYear=next.getFullYear();picker.date=`${picker.gridYear}-${String(next.getMonth()+1).padStart(2,'0')}-01`;}else{picker.gridYear+=delta*(picker.level==='year'?9:1);}renderPicker();}
  async function gridPick(side,value){
    if(picker.kind==='years'){picker[side]=value+(side==='from'?'-01':'-12');renderPicker();return;}
    const {target,level,date}=picker;let [y,m,d]=date.split('-').map(Number);
    if(target==='compare'){state.compareFrom=value+'-01';state.compareTo=value+'-12';closePicker();render();return;}
    if(level==='year')y=Number(value);else if(level==='month'){y=picker.gridYear;m=Number(value);}else{y=picker.gridYear;d=Number(value);}
    d=Math.min(d,new Date(y,m,0).getDate());state.poolDate=`${y}-${String(m).padStart(2,'0')}-${String(d).padStart(2,'0')}`;closePicker();await load();
  }
  function renderGridPicker(){
    const panel=$('v3MonthPicker');panel.hidden=false;const paired=picker.kind==='years';
    const column=(side,y)=>{const level=paired?'year':picker.level,date=picker.date||picker[side],month=Number(date.slice(5,7))||1;const values=level==='year'?Array.from({length:9},(_,i)=>y-4+i):Array.from({length:level==='month'?12:new Date(y,month,0).getDate()},(_,i)=>i+1);const selected=level==='year'?Number(date.slice(0,4)):level==='month'?month:Number(date.slice(8,10));const offset=level==='day'?(new Date(y,month-1,1).getDay()+6)%7:0;return `<section class="month-picker-column"><div class="month-picker-label">${paired?side==='from'?'开始年份':'结束年份':level==='year'?'选择年份':level==='month'?'选择月份':'选择日期'}</div><div class="month-picker-year"><button class="picker-arrow" onclick="V3.gridMove('${side}',-1)">‹</button><b>${level==='day'?y+' 年 '+month+' 月':y+' 年'}</b><button class="picker-arrow" onclick="V3.gridMove('${side}',1)">›</button></div><div class="month-grid ${level==='day'?'day-grid':''}">${level==='day'?['一','二','三','四','五','六','日'].map(x=>'<span class="weekday-cell">'+x+'</span>').join('')+'<span></span>'.repeat(offset):''}${values.map(v=>`<button class="month-cell ${v===selected?'selected':''}" onclick="V3.gridPick('${side}','${v}')">${v}${level==='month'?'<span>月</span>':''}</button>`).join('')}</div></section>`;};
    panel.innerHTML=`<div class="month-picker-dialog ${paired?'':'single-picker'}" role="dialog" aria-modal="true" aria-label="${paired?'选择起止年份':'选择日期'}"><div class="month-picker-top"><h3>${paired?'选择起止年份':'选择日期'}</h3><button class="picker-arrow" onclick="V3.closePicker()">×</button></div><div class="month-picker-panels">${column('from',paired?picker.leftYear:picker.gridYear)}${paired?column('to',picker.rightYear):''}</div><div class="month-picker-bottom"><span class="picker-error">${paired&&picker.from>picker.to?'开始年份不能晚于结束年份':''}</span><button class="btn" onclick="V3.closePicker()">取消</button>${paired?`<button class="btn primary" ${picker.from>picker.to?'disabled':''} onclick="V3.confirmPicker()">确认范围</button>`:''}</div></div>`;
  }
  function edit(id){
    if(window.__PAGES_MODE__)return;
    sync();editorContext={...state};
    if(id===undefined)openCreate();else openEdit(id);
    const form=$('form');
    const dateLabel=form.querySelector('[name="date"]')?.closest('label');if(dateLabel?.firstChild?.nodeType===3)dateLabel.firstChild.textContent='原始信号 D0 日期（D2 实际买入顺延一天）';
    document.getElementById('modalTitle').textContent=(id===undefined?'新增交易':'编辑交易')+' · '+model().name+' · '+state.mode;
    form.insertAdjacentHTML('afterbegin',`<div class="noticebox" style="grid-column:1/-1">${model().name} · 第${state.rank===1?'一':'二'}名 · ${state.mode}。这里维护原D0信号记录；新账本按真实交易日重新取价、确定股数，旧D2开盘重买字段不用于计算。</div>`);
    if(state.mode==='D2'){
      const t=raw.find(t=>Number(t.id)===Number(id))||{};
      ['d2_sell_price_935','d2_sell_price_1335'].forEach((key,i)=>{
        if(!FIELDS.some(f=>f.key===key))FIELDS.push({key,label:i?'D2 13:35 卖价':'D2 9:35 卖价',type:'number'});
        form.querySelector(`[name="${key}"]`)?.closest('label')?.remove();
        form.insertAdjacentHTML('beforeend',`<label>${i?'D2 13:35 卖价':'D2 9:35 卖价'}<input name="${key}" type="number" min="0" step="0.01" value="${t[key]??''}"></label>`);
      });
      ['buy_price','d2_buy_price','d2_sell_price_935','d2_sell_price_1335','sell_price_935','sell_price_1315','sell_price_1335','sell_date','is_second_board'].forEach(key=>form.querySelector(`[name="${key}"]`)?.closest('label')?.setAttribute('hidden',''));
    }else{
      form.querySelectorAll('[name^="d2_"]').forEach(el=>el.closest('label').hidden=true);
    }
    if(id!==undefined)form.insertAdjacentHTML('beforeend',`<div style="grid-column:1/-1"><button type="button" class="smallbtn" style="color:#c33" onclick="removeTrade(${Number(id)}).then(()=>closeModal())">删除该交易（含 D1 / D2，需确认）</button></div>`);
    document.querySelector('#form [name="rank"]')?.closest('label')?.setAttribute('hidden','');
  }
  return {init,load,selectTier,selectNavigationModel,retryComparison,calculate,totals,projectRow,cutoffDay,selectAnalysisMode,capitalBase,monthOpening,monthClosing,periodStats,monthly,state,models,latestBuy,openPicker,closePicker,pickMonth,pickerYear,confirmPicker,openGrid,gridPick,gridMove,
    async chooseRank(id,rank){if(state.view==='compare'){state.month=state.compareFrom;state.from=state.compareFrom===state.compareTo?'':state.compareFrom;state.to=state.compareFrom===state.compareTo?'':state.compareTo;state.keyword='';}state.model=id;state.rank=rank;state.view='trade';state.page=1;state.sort='date';state.dir=-1;sync();this.menu(false);window.scrollTo({top:0,behavior:'auto'});await load();const title=$('v3Title');title.setAttribute('tabindex','-1');title.focus({preventScroll:true});},
    set,choose(id){state.model=id;state.rank=1;state.view='trade';state.page=1;sync();this.menu(false);load();},
    view(v){state.view=v;state.page=1;sync();this.menu(false);window.scrollTo({top:0,behavior:'auto'});load();},
    theme(){const skin=document.documentElement.getAttribute('data-skin')==='night'?'day':'night';document.documentElement.setAttribute('data-skin',skin);localStorage.setItem('trade-ui-skin',skin);},
    menu,
    month(n){let [y,m]=state.month.split('-').map(Number);const d=new Date(y,m-1+n,1);set('month',n===0?now:`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}`);},
    range(k,v){let from=k==='from'?v:state.from,to=k==='to'?v:state.to;if(from&&to&&from>to){toast('开始月份不能晚于结束月份');render();return;}state.from=from||to;state.to=to||from;state.page=1;render();},
    clearRange(){state.from='';state.to='';state.page=1;render();},
    page(n){state.page=Math.max(1,n);render();},
    sort(i){const k=columns[i];if(!k)return;state.dir=state.sort===k?-state.dir:-1;state.sort=k;state.page=1;render();},
    years(k,v){if(k==='yearFrom'&&v>state.yearTo||k==='yearTo'&&v<state.yearFrom){toast('开始年份不能晚于结束年份');render();return;}state[k]=v;render();},
    thisYear(){state.yearFrom=year;state.yearTo=year;render();},
    jump(m){state.view='trade';state.month=m;state.from='';state.to='';state.page=1;sync();render();},
    compareRange(k,v){if(!v)return;if(k==='compareFrom'&&v>state.compareTo||k==='compareTo'&&v<state.compareFrom){toast('开始月份不能晚于结束月份');render();return;}state[k]=v;render();},
    async poolSelect(){const y=+$('v3poolY').value,m=+$('v3poolM').value,d=Math.min(+$('v3poolD').value,new Date(y,m,0).getDate());state.poolDate=`${y}-${String(m).padStart(2,'0')}-${String(d).padStart(2,'0')}`;render();await loadPoolDate();render();},
    async latestPool(){state.poolDate='';await load();},
    edit,editorContext:()=>editorContext,
    exportCSV(){
      if(window.__PAGES_MODE__)return;
      const headers=['日期','分值','代码','名称','开盘价','板块','期初总资产','买入前可用现金','半仓额度','买入持仓金额','额度余款','9:35卖价','9:35整批盈亏(元)','9:35分批盈亏(元)','9:35分批利润(元)','13:35卖价','13:35整批盈亏(元)','13:35分批盈亏(元)','13:35分批利润(元)','分批盈亏合计(元)','本笔利润(元)','累计回款','买入日收盘总资产','利润状态','已实现利润','浮动利润','剩余数量','剩余持仓金额','计算版本','截止日期','9:35实际卖出日','13:35实际卖出日','状态'];
      const number=v=>num(v)===null?'':Number(v).toFixed(4);
      const textIndexes=[0,1,2,3,5,23,28,29,30,31,32];
      const lines=[headers,...scoped(rows).map(r=>[r.date,scoreValue(r)==='—'?'':scoreValue(r),r.stock_code,r.stock_name,r.open_price,r.board,r.capital,r.buyAmount,r.buyBudget,r.initial_buy_cost,r.budget_remainder,r.am,r.ra,r.amHalf,r.aProfit,r.pm,r.rp,r.pmHalf,r.pProfit,r.batchTotal,r.display_profit,r.saleAmount,r.buy_day_close_equity,r.profit_kind,r.realized_profit_total,r.unrealized_profit,r.remainingShares,r.remaining_position_value,r.calc_version,cutoffDay(),r.am_sell_date,r.pm_sell_date,r.status].map((v,i)=>textIndexes.includes(i)?v:i===10?num(v)===null?'':String(v):number(v)))];
      const csv='\uFEFF'+lines.map(line=>line.map(v=>'"'+String(v??'').replace(/"/g,'""')+'"').join(',')).join('\r\n');
      const url=URL.createObjectURL(new Blob([csv],{type:'text/csv;charset=utf-8'})),link=document.createElement('a');link.href=url;link.download=`${model().name}_第${state.rank}名_${state.mode}_${state.from||state.month}_${state.to||state.month}_1335.csv`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    }

  };
})();
// Keep legacy save/refresh/auth callbacks connected to the new visible view.
loadAll=()=>V3.load();
refreshView=()=>V3.load();
const v3OriginalFormData=formData;
formData=function(){const data=v3OriginalFormData();const c=V3.editorContext();if(c){const m=V3.models.find(m=>m.id===c.model);data.program=m.program;if(editingId===null)data.rank=(m.board||m.path)?null:c.rank;else delete data.rank;if(editingId===null){if(m.board){data.board=m.board;data.board_rank=c.rank;}if(m.path){data.path=m.path;data.path_rank=c.rank;}}if(c.mode==='D2'){delete data.sell_price_935;delete data.sell_price_1315;delete data.sell_price_1335;delete data.sell_date;delete data.is_second_board;}else{delete data.d2_sell_price_935;delete data.d2_sell_price_1315;delete data.d2_sell_price_1335;}}return data;};
const v3OriginalSave=saveTrade;
saveTrade=async function(){await v3OriginalSave();};
V3.init();
