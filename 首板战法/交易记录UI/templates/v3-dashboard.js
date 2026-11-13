/* One server ledger: user-confirmed price-difference percentages and dated money snapshots. */
const V3 = (() => {
  const models = [
    {id:'original',name:'V1 原始',program:'stock',tier:0},
    {id:'llm',name:'DeepSeek',program:'llm_input',tier:0},
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
  let picker=null, navigationKey='', nodeTimer=null;
  const $=id=>document.getElementById(id);
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const num=v=>v===null||v===undefined||v===''||!Number.isFinite(Number(v))?null:Number(v);
  const sum=(list,key)=>{const a=list.map(r=>num(r[key])).filter(v=>v!==null);return a.length?a.reduce((a,b)=>a+b,0):null;};
  const color=v=>num(v)>0?'red':num(v)<0?'green':'';
  function bgClass(v){
    const n=num(v);
    if(n===null) return '';
    if(n<=-100) return 'pnl-bg-g0';
    if(n<=-60) return 'pnl-bg-g1';
    if(n<=-30) return 'pnl-bg-g2';
    if(n<0) return 'pnl-bg-g3';
    if(n<=30) return 'pnl-bg-r0';
    if(n<=60) return 'pnl-bg-r1';
    if(n<=100) return 'pnl-bg-r2';
    return 'pnl-bg-r3';
  }
  const money=v=>(typeof masked==='function'&&masked())||num(v)===null?'—':Number(v).toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2});
  const pct=v=>(typeof masked==='function'&&masked())||num(v)===null?'—':`${v>0?'+':''}${(Math.sign(Number(v))*Math.round((Math.abs(Number(v))+1e-10)*100)/100).toFixed(2)}%`;
  const unsignedPct=v=>num(v)===null?'—':`${(Math.round((Math.abs(Number(v))+1e-10)*100)/100).toFixed(2)}%`;
  const fmt=(v,isPct=false)=>`<span class="${color(v)}">${isPct?pct(v):money(v)}</span>`;
  function assetNote(marks=[],fallback=''){
    const dates=[...new Set(marks.filter(m=>m.stale&&m.date).map(m=>m.date))];
    return dates.length?'收盘估值 · '+dates.join(' / '):fallback;
  }
  function assetMoney(value,marks=[]){
    if(typeof masked==='function'&&masked()) return '—';
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
  function projectReturns(r,asOf){
    const cost=num(r.initial_buy_cost),events=r.events||[];
    for(const key of ['amWholeReturnPct','amBatchReturnPct','amHalfReturnPct','pmWholeReturnPct','pmBatchReturnPct','pmHalfReturnPct','batchReturnPct','batchPnlProfit'])r[key]=null;
    if(!r.executed||!cost)return r;
    const ratio=value=>num(value)===null?null:Number(value)/cost*100;
    for(const slot of ['am','pm']){
      const chosen=events.filter(e=>e.slot===slot);
      const raw=e=>num(e.raw)??ratio(e.whole_pnl);
      const values={WholeReturnPct:chosen.map(raw),BatchReturnPct:chosen.map(e=>ratio(e.batch_change)),HalfReturnPct:chosen.map(e=>num(r.shares)&&raw(e)!==null?raw(e)*e.shares/r.shares:null)};
      for(const [suffix,a] of Object.entries(values))r[slot+suffix]=a.length&&a.every(v=>v!==null)?a.reduce((n,v)=>n+v,0):null;
    }
    r.batchPnlProfit=sum(events,'batch_change')??(events.length?null:0);r.batchReturnPct=ratio(r.batchPnlProfit);
    r.periodAssetReturnPct=ratio(r.periodAssetProfit);
    return r;
  }
  function projectRow(source,day,currentDay=null,asOf=null,ledger=null){
    const r={...source};if(!r.executed)return {...r,positionState:'SKIPPED',holdingShares:null,availableShares:null,positionValue:null,positionWeight:null,positionDayProfit:null,positionDayReturnPct:null,positionDayContributionPct:null};
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
    if(num(r.shares)!==null)r.remainingShares=r.shares-events.reduce((n,e)=>n+Number(e.shares||0),0);
    if(num(r.shares)>0&&r.remainingShares===0){r.unrealized_profit=0;r.display_profit=r.realized_profit_total;r.profit_kind='已实现';}
    r.periodAssetProfit=day===currentDay&&num(source.periodAssetProfit)!==null?source.periodAssetProfit:r.display_profit;
    r.profit=r.periodAssetProfit;projectReturns(r,asOf||day+'T23:59');return projectPositions(r,source,day,ledger);
  }
  function projectPositions(r,source,day,ledger){
    const entries=Object.entries(source.v2_days||{}).filter(([d])=>d<=day).sort(([a],[b])=>a.localeCompare(b));
    const last=entries.at(-1);if(!last)return r;
    Object.assign(r,last[1]);
    const daily=ledger?.daily_stats?.[last[0]];
    if(daily){r.positionDayOpening=daily.opening_equity;r.positionDayEnding=daily.ending_equity;r.positionDayCash=daily.cash;r.positionDayStatus=daily.status;r.positionValuedAt=daily.valued_at;r.positionCloseEquity=['closed','reference_close'].includes(daily.status)?daily.ending_equity:null;}
    return r;
  }
  function scoped(list){
    let a=list.filter(r=>{const month=String(r.date).slice(0,7);return state.from?month>=state.from&&month<=state.to:month===state.month;});
    if(state.keyword){const k=state.keyword.toLowerCase();a=a.filter(r=>(String(r.stock_code)+' '+String(r.stock_name)).toLowerCase().includes(k));}
    const day=cutoffDay(list);return a.map(r=>projectRow(r,day,list.ledger?.as_of?.slice(0,10),day<list.ledger?.as_of?.slice(0,10)?day+'T23:59':list.ledger?.as_of,list.ledger));
  }
  function totals(list){return Object.fromEntries(['ra','rp','morningBatchPct','afternoonBatchPct','amHalf','pmHalf','batchTotal','batch','profit','aProfit','pProfit','amWholeReturnPct','amBatchReturnPct','amHalfReturnPct','pmWholeReturnPct','pmBatchReturnPct','pmHalfReturnPct','batchReturnPct','batchPnlProfit','amSellProfit','pmSellProfit','sessionProfit','sessionCost','soldProfit','soldCost','amSoldShares','pmSoldShares','amSellBase','pmSellBase'].map(k=>[k,sum(list,k)]));}
  function card(label,value,note,accent='#527af1',returnPct){return `<div class="card" style="--accent:${accent}"><div class="label">${label}${fieldNote(label,label)}</div><div class="value">${value}</div>${returnPct!==undefined?`<div class="card-return" title="${esc(note)}"><span>收益率${fieldNote(label,"收益率")}</span>${fmt(returnPct,true)}</div>`:''}<div class="tiny">${note}</div></div>`;}
  function modeTabs(){return `<div class="tabs mode-tabs" aria-label="卖出模式">${['D1','D2'].map(m=>`<button class="tab ${state.mode===m?'on':''}" onclick="V3.set('mode','${m}')">${m}</button>`).join('')}</div>`;}
  function selectAnalysisMode(mode){set('mode',mode);document.querySelector('.analysis-tabs [aria-selected="true"]')?.focus({preventScroll:true});}
  function analysisModeTabs(){return `<section class="analysis-mode-bar"><div class="analysis-mode-label"><strong>统计模式</strong><span>D1 / D2 独立查看</span></div><div class="tabs mode-tabs analysis-tabs" role="tablist" aria-label="统计模式切换">${['D1','D2'].map((m,i)=>`<button type="button" role="tab" tabindex="${state.mode===m?0:-1}" class="tab ${state.mode===m?'on':''}" aria-selected="${state.mode===m}" aria-controls="v3App" aria-label="${m}" onclick="V3.selectAnalysisMode('${m}')" onkeydown="if(['ArrowLeft','ArrowRight','Home','End'].includes(event.key)){event.preventDefault();V3.selectAnalysisMode(event.key==='Home'?'D1':event.key==='End'?'D2':'${m==='D1'?'D2':'D1'}');}"><b>${m}</b><span>${i===0?'D0 买入 · D1 卖出':'D1 买入 · D2 卖出'}</span></button>`).join('')}</div><span class="analysis-current" aria-live="polite">当前查看 ${state.mode}</span></section>`;}
  function rankTabs(){return `<div class="rank-switch" aria-label="名次对照"><span>对照</span><div class="tabs medal-tabs">${[1,2].map(r=>`<button class="tab ${state.rank===r?'on':''}" aria-label="第${r===1?'一':'二'}名" aria-pressed="${state.rank===r}" onclick="V3.set('rank',${r})">${r===1?'🥇':'🥈'} 第${r===1?'一':'二'}名</button>`).join('')}</div></div>`;}
  function selectModel(){return `<select class="field" aria-label="模型" onchange="V3.set('model',this.value)">${models.map(m=>`<option value="${m.id}" ${m.id===state.model?'selected':''}>${m.name}</option>`).join('')}</select>`;}
  function hero(title,desc,pill){return `<section class="hero"><div><h2>${title}</h2><p>${desc}</p></div><span class="pill">${pill}</span></section>`;}
  function notice(){return '';}

  const sessionColumns={trade:{identity:[1],capital:[2],close:[3],holding:[4],morning:[5],afternoon:[6]},history:{morning:[2,3,4],afternoon:[5,6,7],batch:[8,9,10,11,12],close:[13,14,15]},compare:{morning:[4,5,6],afternoon:[7,8,9],batch:[10,11,12,13,14],close:[15]}};
  function sessionClass(index){const groups=sessionColumns[state.view];if(!groups)return '';for(const [group,indexes] of Object.entries(groups)){if(indexes.includes(index))return `session-cell session-${group}${index===indexes[0]?' session-start':''}${index===indexes.at(-1)?' session-end':''}`;}return '';}
  function tableLegend(){return ''; }
  function table(headers,body,summary='',mobile=false){
    const compound=state.view==='trade';
    const headings=`<tr${compound?' class="detail-column-headings"':''}>${headers.map((h,i)=>{const sortable=compound&&columns[i],active=sortable&&state.sort===columns[i];return `<th scope="col" class="${sessionClass(i)}" ${sortable?`title="按${esc(detailSortLabels[i])}排序" aria-sort="${active?(state.dir>0?'ascending':'descending'):'none'}" onclick="V3.sort(${i})" tabindex="0" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();V3.sort(${i});}"`:''}>${h}${sortable?`<span class="sort-indicator ${active?'is-sorted':''}" aria-hidden="true">${active?(state.dir>0?'↑':'↓'):'↕'}</span>`:''}</th>`;}).join('')}</tr>`;
    const summaryRow=summary?(compound?`<tr class="detail-summary-row"><td colspan="${headers.length}">${summary}</td></tr>`:`<tr class="summary">${summary}</tr>`):'';
    return tableLegend()+`<div class="tablewrap ${compound?'compound-tablewrap ':''}${mobile?'desktoponly':''}" tabindex="0" aria-label="交易数据表，可横向滚动"><table${compound?' class="compound-table"':''}>${compound?'<colgroup>'+headers.map(()=>'<col>').join('')+'</colgroup>':''}<thead>${compound?summaryRow+headings:headings+summaryRow}</thead><tbody>${body||`<tr><td colspan="${headers.length}" class="empty-state">该范围暂无记录</td></tr>`}</tbody></table></div>`;
  }
  function cell(value,index,extra){
    const klass=typeof extra==='string'?extra:'';
    return `<td class="${sessionClass(index)} ${klass}">${value}</td>`;
  }
  const columns=['date','stock_name','capital','positionDayProfit','positionWeight','am','pm',null];
  const detailSortLabels=['买入日期','股票名称','期初总资产','单票当天收益利润','个股仓位','9:35卖价','13:35卖价'];
  const detailHeaders=['日期信息','基础与买入','资金快照','当日收益与收盘','当日持仓信息','9:35 卖出','13:35 卖出','操作'];
  const fieldHelp = {
  "date": "本笔交易的实际买入日期。D1 按信号日买入，D2 顺延至下一交易日买入；不把原始信号日与实际买入日混用。列表按买入日期筛选。",
  "stock_name": "本笔交易对应的股票名称与证券代码，取当前模型、名次及实际标的记录；如发生停牌替补，展示最终标的。名称链接可打开股票行情页。红色名称仅作界面强调，不表示上涨或盈利。",
  "score": "对应原始信号日、股票和模型路径的选股评分。D2 仍取原始信号日评分。真实 0 分保留；找不到对应评分时显示 —，不借用其他股票或日期的分数。",
  "board": "该股票记录中的板块信息，用于识别标的所属板块。不参与本页资金和收益计算；未取得信息时显示 —。",
  "buy": "本笔实际买入日的开盘价，也是实际买入成本价。D2 使用顺延买入日开盘价，不使用 D0 开盘价替代。单位：元；未成交或缺价时按交易状态显示。",
  "shares": "本笔实际买入数量，界面沿用规范的“手”单位。买入持仓金额＝买入开盘价×买入手数，直接相乘，不再额外乘 100。数量按既定整手规则和真实现金约束确定；未买入为 0。",
  "initial_buy_cost": "本笔初始买入成本＝实际买入开盘价×实际买入手数。单位：元。卖出后仍保留原始买入成本，不变成剩余成本或卖出回款；原“累计买入支付”重复展示已删除。",
  "capital": "本笔买入日的组合期初总资产，取前一交易日收盘总资产；独立本金账期首日取初始本金。包含现金、持仓估值及停牌冻结持仓。单位：元；日内不因卖出而重复累加。",
  "buyAmount": "本笔买入前可用且已解锁的现金，包含此前剩余现金与已满足解锁规则的卖出回款。不包括未卖持仓、停牌冻结持仓及尚未解锁的回款。单位：元。",
  "buyBudget": "按已确认口径：可买额度＝（期初总资产－停牌冻结持仓）×50%。实际买入同时受已解锁现金和整手规则限制。它是买入额度，不等于账户全部现金。此处沿用历史规范与样例，覆盖 V2 第4.2节的冲突表述。",
  "dayBuyCost": "本笔在买入日实际支付的买入持仓金额＝买入开盘价×买入手数。仅记录该笔新买入成本，不等于账户全部持仓估值；未买入为 0。单位：元。",
  "buyDayCash": "买入日快照的全部账户剩余现金＝总资产－所有持仓金额和利润。包含账户此前和当日剩余现金；与当日可买额度、单笔额度余款不同。此次仅更名为“当日剩余可用现金”，取值时点和计算不变。单位：元。",
  "mergedSellPrice": "合并卖出均价＝Σ实际卖出价格×对应实际卖出手数÷总卖出手数。只按已经发生的卖出计算，仅展示成交均价，不用来重新计算利润。没有实际卖出时显示 —。单位：元。",
  "sessionReturnPct": "早午盘盈亏总收益率＝（9:35实际卖出利润＋13:35实际卖出利润）÷早午已卖批次成本合计×100%。部分卖出时分母只含已卖成本，不是整笔买入成本；无已卖成本时显示 —。",
  "sessionProfit": "早午盘盈亏总利润＝9:35实际卖出利润＋13:35实际卖出利润。仅统计已发生的早午卖出批次，不包括未卖持仓浮盈，也不把其他时点结算混入。单位：元。",
  "positionDayProfit": "单票当日增量＝当日卖出回款＋当前剩余持仓金额和利润－前日收盘持仓金额和利润－当日买入支付。盘中用截至快照时点已确认的估值，收盘后按收盘值。停牌冻结日为 0；已退出股票后续交易日为 0；缺估值留空。单位：元。",
  "positionDayContributionPct": "单票当天收益贡献率＝单票当天收益利润÷同日组合期初总资产×100%。分母为组合总资产，不是单票买入成本。按同一交易日汇总各票贡献可分解组合当日收益率。",
  "positionDayOpening": "所展示收益日期对应的组合期初总资产＝前一交易日收盘总资产，独立账期首日取初始本金。同日多笔股票共享此值，不逐行累加；与左侧买入日资金快照可能对应不同日期。单位：元。",
  "positionCloseEquity": "所展示收益日的组合收盘总资产＝该日期初总资产＋组合当天收益利润＝现金＋全部持仓金额和利润。仅收盘后显示，盘中留空；收盘行情待补时按页面参考状态识别。同日多笔股票共享此值。单位：元。",
  "cumulativeProfit": "沿用原“累计盈亏金额”：累计卖出回款＋当前剩余持仓金额和利润－初始买入持仓金额。单位：元。按上次确认仅更名为“单票累计卖出盈亏利润”；仍包含剩余持仓浮动盈亏，并非只算已经卖出的利润。全部退出后即为该笔最终盈亏。",
  "cumulativeReturnPct": "沿用原“累计盈亏比例”：单票累计盈亏金额÷初始买入持仓金额×100%。此次仅更名，仍含剩余持仓估值带来的浮动盈亏；分母不是组合总资产。买入成本缺失或为 0 时不计算。",
  "holdingShares": "左侧为当前剩余持仓手数＝初始买入手数－累计已卖手数；右侧为当前可用卖出手数。当天新买入可用为 0，T+1 后以尚未卖出的剩余数量为准，不重复扣已卖数量；停牌冻结可用为 0。",
  "costCurrent": "左侧成本为实际买入开盘价，右侧为按实际卖出手数加权的合并卖出均价。分时成交价分别见 9:35、13:35 卖出列；合并价只作展示，没有卖出时为空。单位：元。",
  "positionDayReturnPct": "单票当日盈亏收益率＝单票当天收益利润÷该票前日收盘持仓金额和利润×100%。当日新买入、前日持仓为 0 时，以当日买入支付为分母。它衡量该票当日变化，与除以组合资产的“贡献率”不同。",
  "positionValue": "截至所选收益日期，剩余持仓的成本及累计浮动盈亏之和。已卖出部分不重复计入；全部卖出为 0；停牌保留冻结估值。单位：元。缺少有效估值时留空，行情时点以页面标注为准。",
  "positionWeight": "个股仓位＝该票剩余持仓金额和利润÷同日组合总资产×100%。只计算仍持有的仓位，已卖出为 0；未买入不计算。百分比仅表达占比，不代表收益率。",
  "positionState": "截至所选收益日期的持仓状态：持仓中、停牌冻结、已卖出或未买入。状态色仅用于区分状态，不参与金额计算。当天买入与停牌冻结影响可卖数量。",
  "positionDate": "持仓和收益字段对应的实际交易日：取筛选截止日期及以前最近一个已有账本快照的交易日。周末或节假日不会虚构新收益；状态同时标明盘中参考、已收盘或行情待补。",
  "status": "本笔实际执行状态与资金批次标识，用于说明已买入、待报价、停牌替补或未买入原因。缺数据不视为已成交；状态本身不作为金额相加。",
  "am": "9:35 实际卖出成交价，使用对应实际卖出交易日的分钟行情。停牌、未卖出或缺有效报价时留空，不用其他时刻价格冒充；实际卖出日期标在价格下方。单位：元。",
  "amSoldShares": "9:35 已确认实际卖出的数量。早盘拆分按卖出日开盘价相对买入开盘价的涨跌：≥0卖约2/3，<0卖约1/3，按100单位取整；午盘卖剩余数量。阻塞或尚未成交的数量不算已卖。",
  "amMarketChangePct": "9:35 卖出盈亏率＝（实际卖出价÷卖出日前收盘价－1）×100%。记录卖出时股票的行情涨跌幅，不参与利润、回款或仓位拆分计算。缺前收盘价时显示 —。例：59.43÷62.69－1≈−5.20%。",
  "amSellReturnPct": "9:35 卖出收益率＝该批真实卖出利润÷卖出日组合期初总资产×100%。表示这笔卖出对组合总资金的贡献；与相对前收价的“卖出盈亏率”是两个口径。跨日合计分母按实际卖出日期去重。",
  "amSellProfit": "9:35 卖出利润＝该批卖出回款－该批分摊的初始买入成本。回款按已确认模板：分摊前日持仓金额×[1＋（卖价－买入价）÷100]；分摊成本＝初始买入成本×该批手数÷初始手数。仅计已成交批次，包含此前分摊浮盈亏。单位：元。",
  "pm": "13:35 实际卖出成交价，使用对应实际卖出交易日的分钟行情。停牌、未卖出或缺有效报价时留空，不用其他时刻价格冒充；实际卖出日期标在价格下方。单位：元。",
  "pmSoldShares": "13:35 已确认实际卖出的数量。早盘拆分按卖出日开盘价相对买入开盘价的涨跌：≥0卖约2/3，<0卖约1/3，按100单位取整；午盘卖剩余数量。阻塞或尚未成交的数量不算已卖。",
  "pmMarketChangePct": "13:35 卖出盈亏率＝（实际卖出价÷卖出日前收盘价－1）×100%。记录卖出时股票的行情涨跌幅，不参与利润、回款或仓位拆分计算。缺前收盘价时显示 —。例：59.43÷62.69－1≈−5.20%。",
  "pmSellReturnPct": "13:35 卖出收益率＝该批真实卖出利润÷卖出日组合期初总资产×100%。表示这笔卖出对组合总资金的贡献；与相对前收价的“卖出盈亏率”是两个口径。跨日合计分母按实际卖出日期去重。",
  "pmSellProfit": "13:35 卖出利润＝该批卖出回款－该批分摊的初始买入成本。回款按已确认模板：分摊前日持仓金额×[1＋（卖价－买入价）÷100]；分摊成本＝初始买入成本×该批手数÷初始手数。仅计已成交批次，包含此前分摊浮盈亏。单位：元。",
  "期末总资产": "组合资产＝账户现金＋所有持仓金额和利润（含停牌冻结）。收盘后展示当日收盘快照；盘中按规范展示前一交易日收盘快照，日期会标注。单位：元。",
  "期末可用现金": "与期末总资产取同一快照的账户现金＝总资产－所有持仓金额和利润。不是买入额度，也不是单笔预算余款。单位：元。",
  "总市值": "同一资产快照中所有剩余持仓金额和利润之和，包含浮动盈亏及停牌冻结持仓。现金不计入市值。单位：元。",
  "当天累计收益利润": "当前筛选期间的组合资产增量，按当前独立本金账期统计；收益率＝期间利润÷期间期初组合总资产×100%。不重复累加每日已累计的数值，跨本金重置不混账。单位：元。",
  "当天收益利润": "所选截止日最近实际交易日的组合当天收益＝当前组合资产－该日期初组合资产；收益率＝当天收益÷期初组合资产×100%。盘中使用已确认估值，收盘后结算；日期和时点见摘要。",
  "9:35 利润": "当前筛选期间实际卖出日落在期间内的9:35批次利润之和。附收益率按对应卖出日组合期初资产去重合计作为分母；不含未卖浮盈或午盘卖出。单位：元。",
  "13:35 利润": "当前筛选期间实际卖出日落在期间内的13:35批次利润之和。附收益率按对应卖出日组合期初资产去重合计作为分母；不含未卖浮盈或早盘卖出。单位：元。",
  "latest": "当前筛选范围内最近的有效买入标的；当天推荐已落盘但尚未确认成交时显示选股和待确认状态，不据此虚构买入金额。"
};
  function fieldNote(key,label){return fieldHelp[key]?`<button type="button" class="field-note" data-key="${esc(key)}" data-label="${esc(label)}" aria-label="${esc(label)}字段说明" aria-haspopup="dialog" onclick="event.stopPropagation();V3.explain(this.dataset.key,this.dataset.label)"><svg viewBox="0 0 20 20" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" aria-hidden="true" focusable="false"><circle cx="10" cy="10" r="7.5"/><path d="M10 5.8v4.8"/><circle cx="10" cy="13.7" r=".8" fill="currentColor" stroke="none"/></svg></button>`:'';}
  function explain(key,label){
    if(!fieldHelp[key])return;
    let dialog=document.getElementById('v3FieldHelp');
    if(!dialog){dialog=document.createElement('dialog');dialog.id='v3FieldHelp';dialog.className='field-help-dialog';document.getElementById('v3Root').appendChild(dialog);dialog.addEventListener('click',e=>{if(e.target===dialog){const r=dialog.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)dialog.close();}});}
    dialog.setAttribute('aria-labelledby','v3FieldHelpTitle');
    dialog.innerHTML=`<div class="field-help-top"><h3 id="v3FieldHelpTitle">${esc(label)}</h3><button type="button" class="btn" autofocus aria-label="关闭字段说明" onclick="this.closest('dialog').close()">关闭</button></div><p>${esc(fieldHelp[key])}</p><div class="field-help-source">依据：V2 字段设计规范、当天收益补充规范、买卖取数口径规范及最新确认的字段调整。金额为元，收益比例为百分数；— 表示缺失或不适用，不等于 0。</div>`;
    dialog.showModal();
  }
  function detailMetrics(items){return `<dl class="detail-metrics">${items.map(([label,value,key='',primary=false,title='',tone=''])=>`<div class="detail-metric${primary?' metric-primary':''}"${key?` data-field="${esc(key)}"`:''}${title?` title="${esc(title)}"`:''}${tone?` data-tone="${tone}"`:''}><dt>${esc(label)}${fieldNote(key,label)}</dt><dd>${value}</dd></div>`).join('')}</dl>`;}
  function detailSummary(t,count){
    const items=[['9:35 卖出利润','amSellProfit','morning'],['13:35 卖出利润','pmSellProfit','afternoon'],['早午盘盈亏总利润','sessionProfit','batch']];
    return `<div class="detail-summary-strip" role="group" aria-label="所选汇总 · ${count} 笔买入交易"><dl class="detail-summary-metrics">${items.map(([label,key,group])=>`<div class="detail-summary-metric session-${group}" data-summary="${key}" data-tone="${color(t[key])}"><dt>${label}${fieldNote(key==='amSellProfit'?'9:35 利润':key==='pmSellProfit'?'13:35 利润':key,label)}<span class="summary-unit">元</span></dt><dd>${fmt(t[key])}</dd></div>`).join('')}</dl></div>`;
  }
  const quantity=v=>num(v)===null?'—':Number(v).toLocaleString('zh-CN')+' 手';
  const positionState=r=>({HOLDING:'持仓中',FROZEN_SUSPENDED:'停牌冻结',SOLD:'已卖出',SKIPPED:'未买入'}[r.positionState]||(!r.executed?'未买入':'待确认'));
  const positionMark=r=>r.positionState==='HOLDING'?'holding':r.positionState==='SOLD'?'sold':r.positionState==='FROZEN_SUSPENDED'?'frozen':'skipped';
  function detailValues(r,status){
    const sale=(slot,label)=>detailMetrics([
      [label+'卖价',price(r,slot),slot],
      [label+'卖出手数',quantity(r[slot+'SoldShares']),slot+'SoldShares'],
      [label+'卖出盈亏率',fmt(r[slot+'MarketChangePct'],true),slot+'MarketChangePct',false,'卖出时股价相对卖出日前收盘价的涨跌幅，仅记录行情，不参与利润计算',color(r[slot+'MarketChangePct'])],
      [label+'卖出收益率',fmt(r[slot+'SellReturnPct'],true),slot+'SellReturnPct',false,'真实卖出利润 ÷ 卖出日组合期初总资产 × 100%',color(r[slot+'SellProfit'])],
      [label+'卖出利润',fmt(r[slot+'SellProfit']),slot+'SellProfit',true,'已卖回款减实际卖出手数对应成本，单位：元',color(r[slot+'SellProfit'])]
    ]);
    return [
      `<time class="detail-date" datetime="${esc(r.date)}">${esc(r.date)}</time>${fieldNote("date","买入日")}<div class="stock-sub">买入日 · ${state.mode}</div>`,
      `<div class="detail-trade-block"><div class="trade-identity">${stock(r)}${fieldNote("stock_name","股票代码与名称")}</div>${detailMetrics([['分值',score(r),'score'],['板块',boardTag(r.board),'board'],['买入开盘价',price(r,'buy'),'buy'],['买入手数',quantity(r.executed?r.shares:0),'shares'],['买入持仓金额',money(r.initial_buy_cost),'initial_buy_cost']])}${status}</div>`,
      detailMetrics([['期初总资产',assetMoney(r.capital,r.capital_marks),'capital',true],['当日未持仓总资产',money(r.buyAmount),'buyAmount',false,'买入前可用且已解锁现金，不含持仓或锁定回款'],['当日可买资金',money(r.buyBudget),'buyBudget',true,'已确认：（期初总资产－停牌冻结持仓）×50%，实际买入另受可用现金限制'],['当日持仓资金',money(r.executed?r.initial_buy_cost:0),'dayBuyCost'],['当日剩余可用现金',money(r.buyDayCash),'buyDayCash',false,'买入日快照的全部账户现金＝总资产－所有持仓金额和利润']]),
      `<div class="position-asof">${esc(r.positionDate||cutoffDay())} · ${esc(dayStatus(r.positionDayStatus))}${fieldNote("positionDate","收益日期与状态")}</div>`+detailMetrics([['合并卖出均价',money(r.mergedSellPrice),'mergedSellPrice',false,'按实际卖出手数加权，仅作价格展示'],['早午盘盈亏总收益率',fmt(r.sessionReturnPct,true),'sessionReturnPct',false,'早午盘实际利润 ÷ 已卖批次成本 ×100%'],['早午盘盈亏总利润',fmt(r.sessionProfit),'sessionProfit',true],['单票当天收益利润',fmt(r.positionDayProfit),'positionDayProfit',true],['单票当天收益贡献率',fmt(r.positionDayContributionPct,true),'positionDayContributionPct',false,'单票当日增量 ÷ 组合期初总资产 ×100%'],['组合期初总资产',money(r.positionDayOpening),'positionDayOpening'],['收盘总资产',money(r.positionCloseEquity),'positionCloseEquity',true,'收盘后确认，盘中留空；组合当天收益在上方摘要统一展示']]),
      `<div class="position-asof"><span class="position-state ${positionMark(r)}">${positionState(r)}</span>${fieldNote("positionState","持仓状态")}<span>${esc(r.positionDate||cutoffDay())}${fieldNote("positionDate","持仓日期")}</span></div>`+detailMetrics([['单票累计卖出盈亏利润',fmt(r.cumulativeProfit),'cumulativeProfit',true],['单票累计卖出盈亏收益',fmt(r.cumulativeReturnPct,true),'cumulativeReturnPct'],['持仓 / 可用',`${quantity(r.holdingShares)} / ${quantity(r.availableShares)}`,'holdingShares',false,'当日买入可卖0；T+1为尚未卖出的剩余手数；停牌冻结为0'],['成本 / 合并现价',`${money(r.buy)} / ${money(r.mergedSellPrice)}`,'costCurrent',false,'成本为实际买入价；分时现价见9:35与13:35卖出列，合并价仅按真实卖出手数加权'],['单票当日盈亏收益率',fmt(r.positionDayReturnPct,true),'positionDayReturnPct',false,'当日单票利润 ÷ 前日该票持仓金额和利润；新买入以当日买入支付为分母'],['持仓金额和利润',money(r.positionValue),'positionValue'],['个股仓位',`<span class="weight-plain">${unsignedPct(r.positionWeight)}</span>`,'positionWeight',false,'只计算当前还持有的仓位。没持有为 0。只展示百分比，不加正负号，不上红绿底色']]),
      sale('am','9:35 '),sale('pm','13:35 '),
      `<div class="detail-actions">${operations(r)}</div>`
    ];
  }
  function stock(t){if(masked())return '<span class="code">******</span><span class="stock">***</span>';const c=esc(t.stock_code||'—'),n=esc(t.stock_name||'空占位');const url=baiduFinanceUrl(t.stock_code);return `<span class="code">${c}</span><span class="stock">${url?`<a class="name-link" href="${esc(url)}" target="_blank" rel="noopener noreferrer">${n}</a>`:n}</span>`;}
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
  function score(t){if(masked())return '<span class="muted">***</span>';const value=scoreValue(t);return value==='—'?'<span class="muted" title="没有对应的原始评分记录">—</span>':esc(value);}
  function operations(t){if(raw.filter(r=>Number(r.id)===Number(t.id)).length>1)return '<span class="muted" title="原始记录ID重复，旧写接口无法唯一定位；已禁止误改其他日期">ID重复</span>';return window.__PAGES_MODE__?'—':`<button class="smallbtn" onclick="V3.edit(${Number(t.id)})">编辑</button>`;}
  function latestBuy(list,today=beijingToday()){return list.filter(r=>!placeholder(r)&&r.date<=today&&(r.executed||r.date===today)).sort((a,b)=>b.date.localeCompare(a.date)||Number(b.id)-Number(a.id))[0];}
  function boardTag(board){if(masked())return '<span class="board-label board-neutral">***</span>';return `<span class="board-label ${board==='主板'?'board-main':board==='创业板'?'board-growth':'board-neutral'}">${esc(board||'—')}</span>`;}
  function nameLink(t){const url=baiduFinanceUrl(t.stock_code);return url?`<a class="name-link" href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(t.stock_name||'—')}</a>`:esc(t.stock_name||'—');}
  function periodControls(target){const from=target==='history'?String(state.yearFrom):state.compareFrom,to=target==='history'?String(state.yearTo):state.compareTo;return `<div class="filters">${target==='compare'?`<button class="month-trigger" aria-label="对比月份" onclick="V3.openPicker('single','from','compare')">▦ ${from===to?from:'选择月份'}</button><button class="month-trigger" onclick="V3.openGrid('year','compare')">选择年份 ▾</button>`:''}<button class="month-trigger" aria-label="${target}开始" onclick="V3.openPicker('range','from','${target}')">起 ${from} ⌄</button><span class="range-dash">—</span><button class="month-trigger" aria-label="${target}结束" onclick="V3.openPicker('range','to','${target}')">止 ${to} ⌄</button></div>`;}
  function latestCard(latest){return `<div class="card latest-card"><div class="label">${latest&&!latest.executed?'今日选股':'最近买入标的'}${fieldNote('latest','最近标的')}</div><div class="value">${masked()?'登录后查看':latest?nameLink(latest):'暂无买入记录'}</div><div class="latest-meta">${masked()?'<span class="muted">已脱敏</span>':latest?`${boardTag(latest.board)}<span class="buy-date">· ${esc(latest.date)}</span>${!latest.executed?'<span class="muted"> · 成交待确认</span>':''}`:'<span class="muted">筛选范围内暂无有效记录</span>'}</div></div>`;}
  function maintenance(){return window.__PAGES_MODE__?'':`<div class="workspace-maintenance" id="v3Maintenance" role="group" aria-label="数据维护与导出"><button type="button" class="maintenance-tool" title="导出当前筛选的交易明细 CSV" onclick="V3.exportCSV()">导出 CSV</button><button type="button" class="maintenance-tool" title="执行收盘补全" onclick="runCloseJob()">收盘补全</button><button type="button" class="maintenance-tool" title="补全当前模型的价格空值" onclick="refreshPrices(false)">补全空值</button><button type="button" class="maintenance-tool maintenance-force" title="强制重拉当前模型的价格" onclick="refreshPrices(true)">强制重拉</button></div>`;}
  function monthControls(){return `<div class="month-controls"><div class="month-quick"><button class="btn" onclick="V3.month(-1)" aria-label="上一月">‹</button><button class="month-trigger" aria-label="月份" aria-haspopup="dialog" onclick="V3.openPicker('single')"><span class="calendar-icon">▦</span><span>${state.month.replace('-',' 年 ')} 月</span><span class="picker-chevron">⌄</span></button><button class="btn" onclick="V3.month(1)" aria-label="下一月">›</button><button class="btn" onclick="V3.month(0)">当月</button></div><div class="range-triggers"><button class="month-trigger range-trigger" aria-label="开始月份" aria-haspopup="dialog" onclick="V3.openPicker('range','from')"><span class="range-caption">起始年月</span><span>${state.from?state.from.replace('-',' / '):'选择起始月份'}</span><span class="picker-chevron">⌄</span></button><span class="range-dash">—</span><button class="month-trigger range-trigger" aria-label="结束月份" aria-haspopup="dialog" onclick="V3.openPicker('range','to')"><span class="range-caption">截止年月</span><span>${state.to?state.to.replace('-',' / '):'选择截止月份'}</span><span class="picker-chevron">⌄</span></button></div>${state.from?'<button class="btn" onclick="V3.clearRange()">清除区间</button>':''}</div>`;}
  const dayStatus=status=>({closed:'已收盘',reference_close:'收盘参考 · 行情待补',intraday:'盘中参考',missing_valuation:'估值待补',unavailable:'暂无快照'}[status]||'暂无快照');
  const dayQuoteNote=marks=>marks?.map(m=>`${m.stock_code} ${m.date||'待补'} ${m.time||'收盘'}${m.frozen?' · 停牌冻结':''}`).join('；')||'无持仓';
  function dayStats(list,day){return Object.values(list.ledger?.daily_stats||{}).filter(d=>d.date<=day&&d.period===period(day)).sort((a,b)=>a.date.localeCompare(b.date)).at(-1)||null;}
  function dailyPerformance(list,day){
    const d=dayStats(list,day);if(!d)return '';const month=periodStats(list,d.date.slice(0,7),d.date.slice(0,7));
    return `<section class="daily-performance" aria-label="账户当天与累计收益"><div class="daily-performance-date"><b>${esc(d.date)}</b><span class="daily-status">${dayStatus(d.status)} · ${esc(d.valued_at?.slice(11)||'—')}</span></div><div class="daily-performance-item" data-day-metric="daily"><span>当天收益利润${fieldNote("当天收益利润","当天收益利润")}</span><strong>${fmt(d.daily_profit)}</strong><b>${fmt(d.daily_return_pct,true)}</b></div><div class="daily-performance-item" data-day-metric="cumulative"><span>累计当天收益利润${fieldNote("当天累计收益利润","累计当天收益利润")}</span><strong>${fmt(month.assetPnl)}</strong><b>${fmt(month.accountReturnPct,true)}</b></div><div class="daily-performance-basis" title="${esc(dayQuoteNote(d.valuation_marks))}">当日期初${fieldNote("positionDayOpening","当日期初总资产")} ${money(d.opening_equity)} · 本月期初${fieldNote("当天累计收益利润","本月期初总资产")} ${money(month.opening)}${assetNote(d.valuation_marks)?'<br>'+esc(assetNote(d.valuation_marks)):''}</div></section>`;
  }
  function trade(){
    const a=scoped(rows),t=totals(a),latest=latestBuy(a),from=state.from||state.month,to=state.to||state.month;
    const stats=periodStats(rows,from,to),range=from===to?from:`${from} — ${to}`;
    let html=`<div class="headrow"><h3>交易概览 <span class="hint">· ${range}</span></h3></div>`;
    if(rows.ledger?.market_status==='offline')html+=`<p class="hint" role="status">选股同步至 ${esc(rows.ledger.as_of?.replace('T',' '))} · 行情库暂不可用，金额保留至 ${esc(rows.ledger.valuation_as_of||'最近有效快照')}，新成交待确认。</p>`;
    html+='<div class="grid overview-cards">';
    html+=latestCard(latest);
    const closing=closingSnapshot(rows,cutoffDay()),daily=dayStats(rows,cutoffDay());
    html+=card('期末总资产',money(closing?.equity),`收盘快照 ${closing?.date||'初始本金'} · 现金＋全部持仓`,'#d88d32');
    html+=card('期末可用现金',money(closing?.cash),'与总资产同一收盘快照 · 含已卖回款');
    html+=card('总市值',money(closing?.position_value),'所有持仓金额和利润 · 含停牌冻结','#527af1');
    html+=card('当天累计收益利润',fmt(stats.assetPnl),`${range} · 每日组合收益合计`,'#a06be1',stats.accountReturnPct);
    html+=card('当天收益利润',fmt(daily?.daily_profit),`${daily?.date||'—'} · ${dayStatus(daily?.status)}`,'#e58d39',daily?.daily_return_pct);
    html+=card('9:35 利润',fmt(stats.amSellProfit),'本期真实卖出 · 收益率分母为对应日组合期初资产','#d95770',stats.amSellReturnPct);
    html+=card('13:35 利润',fmt(stats.pmSellProfit),'本期真实卖出 · 收益率分母为对应日组合期初资产','#19a78e',stats.pmSellReturnPct)+'</div>';
    html+=dailyPerformance(rows,cutoffDay());
    html+=`<section class="panel"><div class="paneltop detail-paneltop"><div class="detail-heading"><h3>逐笔交易明细 · V2 · ${state.mode}</h3><div class="hint">按买入日筛选 · 持仓与当天收益截至 ${cutoffDay()} · 手数直接乘价格 · 金额：元</div></div><div class="filters detail-filters"><div class="detail-filter-search"><input class="field trade-search" aria-label="搜索代码或名称" placeholder="搜索代码 / 名称" value="${esc(state.keyword)}" onchange="V3.set('keyword',this.value)">${!window.__PAGES_MODE__?'<button class="btn primary" onclick="V3.edit()">＋ 新增</button>':''}</div><div class="actions detail-switches" role="group" aria-label="交易模式与名次">${modeTabs()}${rankTabs()}</div><div class="detail-filter-dates">${monthControls()}</div></div></div>`;
    const multi=state.from&&state.from!==state.to,per=multi?state.per:Math.max(1,a.length),pages=Math.max(1,Math.ceil(a.length/per));state.page=Math.min(state.page,pages);
    const sorted=[...a].sort((a,b)=>{const x=a[state.sort],y=b[state.sort];if(x==null)return 1;if(y==null)return -1;return state.dir*(typeof x==='number'?x-y:String(x).localeCompare(String(y)));});
    const visible=sorted.slice((state.page-1)*per,state.page*per);
    const heads=detailHeaders;
    const sumrow=detailSummary(t,a.length);
    const status=r=>`<div class="stock-sub trade-note" data-unbought="${!r.executed}" title="${esc(r.status)} · 信号D0 ${esc(r.signal_date)} · 计划卖出 ${esc(r.planned_sell_date||'—')} · 初始${r.shares}手 / 剩余${r.remainingShares}手 · 剩余持仓 ${money(r.remaining_position_value)} · 已实现 ${money(r.realized_profit_total)} · 浮动 ${money(r.unrealized_profit)}">${esc(r.status)}${r.fund_id?' · '+r.fund_id:''}${r.substituted_from?' · 停牌替补':''}${fieldNote("status","交易状态")}</div>`;
    const values=r=>detailValues(r,status(r));
    html+=table(heads,visible.map(r=>'<tr>'+values(r).map(cell).join('')+'</tr>').join(''),sumrow,true);
    html+=`<div class="detail-mobile-summary">${sumrow}</div>`;
    html+=`<div class="mobilecards detail-mobilecards">${visible.map(r=>{const grouped=values(r);return `<article class="mobiletrade detail-mobiletrade"><div class="detail-mobile-date">${grouped[0]}</div><div class="detail-mobile-groups">${grouped.slice(1,7).map((value,i)=>`<section class="detail-mobile-group ${sessionClass(i+1)}"><h4>${heads[i+1]}</h4>${value}</section>`).join('')}</div><div class="detail-mobile-actions"><span>操作</span>${grouped[7]}</div></article>`;}).join('')||'<div class="empty-state">该范围暂无记录</div>'}</div>`;
    html+=`<div class="bottom"><span class="notice">共 ${a.length} 条 · 第 ${state.page} / ${pages} 页</span><div class="filters">${[['首页',1],['上一页',state.page-1],['下一页',state.page+1],['末页',pages]].map(([label,n],i)=>`<button class="btn" ${i<2?state.page<=1?'disabled':'':state.page>=pages?'disabled':''} onclick="V3.page(${n})">${label}</button>`).join('')}</div></div><div class="footnote">V2：资金快照对应买入日，持仓与当天收益对应所选截止日。卖出利润＝实际回款－分摊成本；价格收益率沿用价差百分数。单票当天收益扣除昨日持仓，组合收益在上方统一展示。可买资金按非冻结总资产50%，实际买入受现金限制；额度余款不是现金。自动更新：早盘推荐落盘、10:00、14:00、15:00。时点之间只读本机账本。盘中期末总资产、现金、总市值统一显示前一收盘快照。</div></section>`;
    return html;
  }
  function closingSnapshot(list,day){
    if(masked())return null;
    const p=period(day),snap=(list.ledger?.snapshots||[]).filter(s=>s.period===p&&s.date<=day&&s.time==='close').at(-1);
    return snap?{...snap,equity:'reference_equity'in snap?snap.reference_equity:snap.equity,position_value:'reference_position_value'in snap?snap.reference_position_value:snap.position_value}:{equity:capitalBase(day),cash:capitalBase(day),position_value:0,date:null};
  }
  function assetSnapshot(list,month,before=false){
    if(masked()||month<'2026-01'||month>now)return null;
    const boundary=month+'-01',p=period(boundary),snapshots=list.ledger?.snapshots||[];
    if(before&&(month==='2026-01'||month==='2026-11'))return {equity:capitalBase(boundary),valuation_marks:[]};
    const selected=snapshots.filter(r=>r.period===p&&(before?r.date<boundary:r.date.slice(0,7)<=month)),s=selected.at(-1);
    return s?{...s,equity:'reference_equity'in s?s.reference_equity:s.equity,position_value:'reference_position_value'in s?s.reference_position_value:s.position_value,valuation_marks:s.reference_marks??s.valuation_marks}:{equity:capitalBase(boundary),valuation_marks:[]};
  }
  function snapshot(list,month,before=false){return assetSnapshot(list,month,before)?.equity??null;}
  function monthOpening(list,month){return snapshot(list,month,true);}
  function monthClosing(list,month){return snapshot(list,month);}
  function periodStats(list,from,to){
    const months=list.ledger?.monthly_stats||{},selected=Object.keys(months).filter(m=>m>=from&&m<=to&&m<=now).sort().map(m=>months[m]);
    const t=totals(selected),start=from>now?null:months[from],closing=to>now?now:to,end=from>now?null:months[closing];
    const completeSum=key=>selected.length&&selected.every(r=>num(r[key])!==null)?selected.reduce((n,r)=>n+Number(r[key]),0):null;
    const pnl=completeSum('assetPnl'),samePeriod=period(from+'-01')===period(closing+'-01');
    const sellRate=slot=>{const base=sum(selected,slot+'SellBase'),profit=sum(selected,slot+'SellProfit');return base>0&&profit!==null?profit/base*100:null;};
    const rate=value=>samePeriod&&num(value)!==null&&num(start?.opening)?value/start.opening*100:null;
    return {...t,amSellReturnPct:sellRate('am'),pmSellReturnPct:sellRate('pm'),sessionReturnPct:num(t.sessionCost)>0?t.sessionProfit/t.sessionCost*100:null,opening:start?.opening??null,ending:end?.ending??null,
      openingMarks:start?.openingMarks||[],endingMarks:end?.endingMarks||[],count:selected.reduce((n,r)=>n+(r.count||0),0),
      otherProfit:sum(selected,'otherProfit'),floatingChange:completeSum('floatingChange'),assetPnl:pnl,
      accountReturnPct:samePeriod&&pnl!==null&&num(start?.opening)?pnl/start.opening*100:null,
      realizedReturnPct:rate(t.profit),amProfitReturnPct:rate(t.aProfit),pmProfitReturnPct:rate(t.pProfit),
      cash:end?.cash??null,cash_available:end?.cash_available??null,cash_locked:end?.cash_locked??null,position_value:end?.position_value??null};
  }
  function monthly(list,fromYear,toYear){
    const months=[];for(let y=fromYear;y<=toYear;y++)for(let m=1;m<=12;m++)months.push(`${y}-${String(m).padStart(2,'0')}`);
    return months.sort((a,b)=>{const af=a>now,bf=b>now;return af!==bf?(af?1:-1):b.localeCompare(a);}).map(month=>({month,...periodStats(list,month,month)}));
  }
  const periodOutcomes=[['9:35卖出手数','amSoldShares',false],['9:35卖出收益率（%）','amSellReturnPct',true],['9:35卖出利润（元）','amSellProfit',false],['13:35卖出手数','pmSoldShares',false],['13:35卖出收益率（%）','pmSellReturnPct',true],['13:35卖出利润（元）','pmSellProfit',false],['早午盘盈亏总收益率（%）','sessionReturnPct',true],['早午盘盈亏总利润（元）','sessionProfit',false],['已卖总利润（元）','profit',false],['持仓浮动利润变化（元）','floatingChange',false],['累计当天收益利润（元）','assetPnl',false]];
  const periodValues=r=>periodOutcomes.map(([,key,isPct])=>fmt(r[key],isPct));
  function history(){
    const items=monthly(rows,state.yearFrom,state.yearTo),t=periodStats(rows,state.yearFrom+'-01',state.yearTo+'-12');
    const effective=items.filter(r=>r.count),wins=effective.filter(r=>num(r.profit)>0).length,loss=effective.filter(r=>num(r.profit)<0).length;
    let html=analysisModeTabs()+`<div class="headrow"><h3>月度统计概览</h3><div class="actions">${rankTabs()}</div></div><div class="grid g5">`+card('盈利 / 亏损月份',`${wins} / ${loss}`,'按已实现利润统计')+card('本期资产利润',fmt(t.assetPnl),'已实现＋浮动变化','#d95770')+card('已实现利润',fmt(t.profit),'按实际结算日汇总','#de9237')+card('期末总资产',money(t.ending),'现金＋模板持仓价值')+card('有效交易月份',effective.length,'有实际结算事件','#1caa9d')+'</div>';
    html+=`<section class="panel"><div class="paneltop"><h3>历史月度明细 · 盈亏：% · 利润与资产：元</h3>${periodControls('history')}</div>`;
    const values=r=>[r.month,assetMoney(r.opening,r.openingMarks),...periodValues(r),money(r.cash),money(r.position_value),assetMoney(r.ending,r.endingMarks)];
    html+=table(['月份','月初总资产（元）',...periodOutcomes.map(([label])=>label),'月末账户现金（元）','月末持仓金额（元）','月末总资产（元）'],items.map(r=>`<tr class="month-row" tabindex="0" onclick="V3.jump('${r.month}')" onkeydown="if(event.key==='Enter')V3.jump('${r.month}')">`+values(r).map(cell).join('')+'</tr>').join(''),['全部汇总','—',...periodValues(t),'—','—','—'].map(cell).join(''));
    return html+'<div class="footnote">月末总资产＝月初总资产＋已实现利润＋浮动利润变化。卖出利润按真实卖出日期归月，卖出收益率＝实际卖出利润÷对应日组合期初资产合计。同一天多笔卖出只计一次组合期初资产。现金和持仓余额不相加；月表与模型对比使用同一期间统计。</div></section>';
  }
  function modelJump(m,rank=1){return m?`<button class="model-link" onclick="V3.chooseRank('${m.id}',${rank})">${m.name} · 第${rank===1?'一':'二'}名</button>`:'—';}
  function compare(){
    const data=comparison.map(({m,rank,list,loading,error})=>({m,rank,loading,error,...(list?periodStats(list,state.compareFrom,state.compareTo):{}),p:list?num(periodStats(list,state.compareFrom,state.compareTo).profit):null})).sort((a,b)=>a.p===null&&b.p===null?models.indexOf(a.m)-models.indexOf(b.m)||a.rank-b.rank:a.p===null?1:b.p===null?-1:b.p-a.p);
    const max=Math.max(...data.map(r=>Math.abs(r.p||0)),1),valid=data.filter(r=>r.p!==null);
    const loaded=comparison.filter(r=>r.list).length,pending=comparison.filter(r=>r.loading).length,failed=comparison.filter(r=>r.error).length;
    const lines=models.length*2;let html=analysisModeTabs()+`<div class="comparison-progress" role="status">${pending?`正在汇总模型数据：${loaded} / ${lines}`:`已载入 ${loaded} / ${lines} 条模型名次线`}${failed?` · ${failed} 条加载失败，可单独重试`:''}</div>`+'<div class="grid g4">'+card('最佳模型',modelJump(valid[0]?.m,valid[0]?.rank),money(valid[0]?.p),'#df9441')+card('最弱模型',modelJump(valid.at(-1)?.m,valid.at(-1)?.rank),money(valid.at(-1)?.p),'#7b84bd')+card('统计范围',esc(state.compareFrom===state.compareTo?state.compareFrom:`${state.compareFrom} — ${state.compareTo}`),'与对应模型历史月表一致')+card('资金口径','模板收益模型','价差数值作为百分数，按确认规则结算','#1caa9d')+'</div>';
    html+=`<section class="panel compare-panel"><div class="paneltop"><h3>全模型已实现利润排名 · 盈亏：% · 利润与资产：元</h3><div class="actions">${periodControls('compare')}</div></div>`;
    html+=table(['排名 / 模型','名次','所属档位','期初总资产（元）',...periodOutcomes.map(([label])=>label),'期末总资产（元）','利润相对规模'],data.map((r,i)=>`<tr class="compare-row" tabindex="0" title="查看 ${r.m.name} · 第${r.rank===1?'一':'二'}名 · ${state.mode} 明细" onclick="V3.chooseRank('${r.m.id}',${r.rank})" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();V3.chooseRank('${r.m.id}',${r.rank});}">`+[`${i+1}. <b>${r.m.name}</b> ${r.loading?'<span class="chip">加载中…</span>':r.error?`<button class="smallbtn" title="${esc(r.error)}" onclick="event.stopPropagation();V3.retryComparison('${r.m.id}',${r.rank})">重试</button>`:''} ${valid.length&&r===valid[0]?'<span class="chip best-tag">最好</span>':valid.length&&r===valid.at(-1)?'<span class="chip worst-tag">最弱</span>':''}`,`<span class="rank-medal">${r.rank===1?'🥇 第一名':'🥈 第二名'}</span>`,tiers[r.m.tier],assetMoney(r.opening,r.openingMarks),...periodValues(r),assetMoney(r.ending,r.endingMarks),`<div class="comparebar ${r.p>0?'positive':r.p<0?'negative':'neutral'}"><i style="width:${Math.abs(r.p||0)/max*100}%"></i></div>`].map(cell).join('')+'</tr>').join(''));
    return html+'<div class="footnote">同一模型、名次、D1/D2及期间的百分比和金额与历史月表逐项一致，按已实现利润金额排名。卖出收益率分母为对应日组合期初资产合计，早午盘成本收益率分母为已卖成本。本期资产利润另含浮动利润变化。期初和期末资产不求和，缺失估值保持空值。</div></section>';
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
    ['trade','history','compare','pool'].forEach(view=>{const button=$('v3nav'+view);if(!button)return;const active=state.view===view;button.classList.toggle('active',active);if(active)button.setAttribute('aria-current','page');else button.removeAttribute('aria-current');});
    const modelLabel=$('v3ModelLabel'),modelTier=$('v3ModelTier'),header=$('v3Header');
    if(modelLabel)modelLabel.textContent=model().name;
    if(modelTier)modelTier.textContent=tiers[model().tier].split(' · ')[0];
    const tools=$('v3Maintenance');if(tools)tools.hidden=state.view!=='trade'||!!window.__PAGES_MODE__;
    if(header)header.classList.toggle('has-maintenance',state.view==='trade'&&!window.__PAGES_MODE__);
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
    if(!$('v3App')||!$('v3Title'))return;
    const m=model();
    renderNavigation();
    $('v3Title').textContent=state.view==='trade'?`${m.name} · ${state.mode} 交易明细`:state.view==='history'?`历史月表 · ${m.name} · ${state.mode}`:state.view==='compare'?`模型对比 · 四档全模型 · ${state.mode}`:'涨停池 · 首次涨停查阅';
    $('v3Sub').textContent=state.view==='trade'?(state.mode==='D1'?'D0−1 涨停 · D0 买入 · D1 卖出（9:35 / 13:35）':'D0−1 涨停 · D0 不买 · D1 买入 · D2 卖出（9:35 / 13:35）'):state.view==='pool'?'最近已完成交易日 · 仅查阅':`${tiers[m.tier]} / ${state.view==='compare'?'第一名 / 第二名统一比较':state.rank===1?'第一名':'第二名'} / ${state.mode}`;
    $('v3Sub').title=$('v3Sub').textContent;
    $('v3Title').title=$('v3Title').textContent;
    $('v3App').innerHTML=state.view==='trade'?trade():state.view==='history'?history():state.view==='compare'?compare():pool();
    $('v3Auth').textContent=window.__PAGES_MODE__?(window.__PAGES_FULL__?'退出':'登录'):'● 本机数据';
    $('v3Auth').disabled=!window.__PAGES_MODE__;
    $('v3Auth').hidden=!window.__PAGES_MODE__;
  }
  async function load(force=false){
    const ticket=++seq; if($('v3Status'))$('v3Status').textContent='正在加载真实记录…';
    if (typeof setLoading === 'function') setLoading(true, '数据加载中，请稍候');
    // Clear previous context immediately so old model contents cannot masquerade as new.
    raw=[];rows=[];comparison=state.view==='compare'?models.flatMap(m=>[1,2].map(rank=>({m,rank,list:null,loading:true,error:null}))):[];
    try{
      render();
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
      if($('v3Status'))$('v3Status').textContent='数据已加载';
      render(); scheduleNodeRefresh(); return true;
    }catch(e){if(ticket!==seq)return false;if($('v3Status'))$('v3Status').textContent='加载失败';if($('v3App'))$('v3App').innerHTML=`<section class="panel empty-state"><p class="error">${esc(e.message||'数据加载失败')}</p><button class="btn" onclick="V3.load()">重试</button></section>`;document.getElementById('pagesBootMask')?.classList.add('done');return false;}
    finally { if (typeof markPageDataReady === 'function') markPageDataReady(); if (typeof setLoading === 'function') setLoading(false); }
  }
  function scheduleNodeRefresh(){
    clearTimeout(nodeTimer);if(window.__PAGES_MODE__)return;
    const parts=new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(new Date()).split(' ');
    const clock=parts[1],next=['09:30','10:00','14:00','15:05'].find(t=>t+':00'>clock);
    const target=next?Date.parse(parts[0]+'T'+next+':02+08:00'):Date.parse(parts[0]+'T09:30:02+08:00')+86400000;
    // Morning ingestion can complete after 09:30. Reads reuse the node cache
    // until the source signature changes; this never polls live prices.
    const delay=Math.max(1000,target-Date.now());
    nodeTimer=setTimeout(()=>load(),clock>='09:30:00'&&clock<'10:00:00'?Math.min(delay,30000):delay);
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
  function useAfternoonField(legacy){
    for(const field of FIELDS){
      if(legacy && field.key==='sell_price_1335'){field.key='sell_price_1315';field.label='D1 午盘卖出价';}
      if(!legacy && field.key==='sell_price_1315'){field.key='sell_price_1335';field.label='D1 13:35 卖价';}
    }
  }
  function showLegacy(){
    document.documentElement.dataset.ui='legacy';
    const mask=document.getElementById('loadingMask');
    if(mask)mask.classList.remove('show');
    useAfternoonField(true);
    if(typeof window.__legacyLoadAll==='function')window.__legacyLoadAll();
  }
  function showNew(){
    document.documentElement.dataset.ui='v3';
    useAfternoonField(false);
    load();
  }
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
            ${(window.__PAGES_MODE__?[['trade','交易明细'],['history','历史月表']]:[['trade','交易明细'],['history','历史月表'],['compare','模型对比'],['pool','涨停池']]).map(([view,label])=>`<button type="button" id="v3nav${view}" class="primary-nav" onclick="V3.view('${view}')">${navIcon(view)}<span>${label}</span></button>`).join('')}
          </nav>
          <div class="workspace-actions"><div class="workspace-common-tools">
            <button type="button" class="btn workspace-tool" onclick="V3.theme()" aria-label="切换亮暗主题" title="切换亮暗主题">${navIcon('theme')}<span>主题</span></button>
            <button type="button" class="btn" id="v3Auth" onclick="__pagesToggleAuth()"></button>
            <button type="button" class="btn workspace-tool" onclick="V3.load(true)" aria-label="刷新数据" title="刷新数据">${navIcon('refresh')}<span>刷新</span></button></div>${maintenance()}
            <button type="button" class="version-switch" onclick="V3.showLegacy()" aria-label="切换到老版本界面" title="老版本用原来的计算方式，和新版本分开">老版本</button>
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
    form.insertAdjacentHTML('afterbegin',`<div class="noticebox" style="grid-column:1/-1">${model().name} · 第${state.rank===1?'一':'二'}名 · ${state.mode}。这里维护原D0信号记录；新账本按真实交易日重新取价、确定手数，旧D2开盘重买字段不用于计算。</div>`);
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
  return {explain,init,load,showLegacy,showNew,selectTier,selectNavigationModel,retryComparison,calculate,totals,projectRow,projectReturns,projectPositions,closingSnapshot,dayStats,cutoffDay,selectAnalysisMode,capitalBase,monthOpening,monthClosing,periodStats,monthly,state,models,latestBuy,openPicker,closePicker,pickMonth,pickerYear,confirmPicker,openGrid,gridPick,gridMove,
    async chooseRank(id,rank){if(state.view==='compare'){state.month=state.compareFrom;state.from=state.compareFrom===state.compareTo?'':state.compareFrom;state.to=state.compareFrom===state.compareTo?'':state.compareTo;state.keyword='';}state.model=id;state.rank=rank;state.view='trade';state.page=1;state.sort='date';state.dir=-1;sync();this.menu(false);window.scrollTo({top:0,behavior:'auto'});await load();const title=$('v3Title');title.setAttribute('tabindex','-1');title.focus({preventScroll:true});},
    set,choose(id){state.model=id;state.rank=1;state.view='trade';state.page=1;sync();this.menu(false);load();},
    view(v){if(window.__PAGES_MODE__&&v!=='trade'&&v!=='history')v='trade';state.view=v;state.page=1;sync();this.menu(false);window.scrollTo({top:0,behavior:'auto'});load();},
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
      const fields=[["买入日", "date", "text"], ["分值", "score", "text"], ["板块", "board", "text"], ["9:35实际卖出日", "am_sell_date", "text"], ["13:35实际卖出日", "pm_sell_date", "text"], ["代码", "stock_code", "text"], ["名称", "stock_name", "text"], ["买入开盘价", "buy", ""], ["买入手数", "shares", ""], ["买入持仓金额(元)", "initial_buy_cost", ""], ["期初总资产(元)", "capital", ""], ["当日未持仓总资产(元)", "buyAmount", ""], ["当日可买资金(元)", "buyBudget", ""], ["当日持仓资金(元)", "initial_buy_cost", ""], ["当日剩余可用现金(元)", "buyDayCash", ""], ["合并卖出均价", "mergedSellPrice", ""], ["早午盘盈亏总收益率(%)", "sessionReturnPct", "pct"], ["早午盘盈亏总利润(元)", "sessionProfit", ""], ["持仓与收益日期", "positionDate", "text"], ["单票当天收益利润(元)", "positionDayProfit", ""], ["单票当天收益贡献率(%)", "positionDayContributionPct", "pct"], ["单票当日盈亏收益率(%)", "positionDayReturnPct", "pct"], ["组合期初总资产(元)", "positionDayOpening", ""], ["收盘总资产(元)", "positionCloseEquity", ""], ["单票累计卖出盈亏利润(元)", "cumulativeProfit", ""], ["单票累计卖出盈亏收益(%)", "cumulativeReturnPct", "pct"], ["持仓手数", "holdingShares", ""], ["可用卖出手数", "availableShares", ""], ["持仓金额和利润(元)", "positionValue", ""], ["个股仓位(%)", "positionWeight", "pct"], ["持仓状态", "positionState", "text"], ["估值时点", "positionValuedAt", "text"], ["9:35卖价", "am", ""], ["9:35卖出手数", "amSoldShares", ""], ["9:35卖出盈亏率(%)", "amMarketChangePct", "pct"], ["9:35卖出收益率(%)", "amSellReturnPct", "pct"], ["9:35卖出利润(元)", "amSellProfit", ""], ["13:35卖价", "pm", ""], ["13:35卖出手数", "pmSoldShares", ""], ["13:35卖出盈亏率(%)", "pmMarketChangePct", "pct"], ["13:35卖出收益率(%)", "pmSellReturnPct", "pct"], ["13:35卖出利润(元)", "pmSellProfit", ""], ["计算版本", "calc_version", "text"], ["状态", "status", "text"]];
      const lines=[fields.map(([label])=>label),...scoped(rows).map(r=>fields.map(([,key,type])=>{const v=typeof key==='function'?key(r):r[key];return type==='text'?v:num(v)===null?'':type==='exact'?String(v):Number(v).toFixed(type==='pct'?8:4);} ))];
      const csv='\uFEFF'+lines.map(line=>line.map(v=>'"'+String(v??'').replace(/"/g,'""')+'"').join(',')).join('\r\n');
      const url=URL.createObjectURL(new Blob([csv],{type:'text/csv;charset=utf-8'})),link=document.createElement('a');link.href=url;link.download=`${model().name}_第${state.rank}名_${state.mode}_${state.from||state.month}_${state.to||state.month}_1335.csv`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    }

  };
})();
// Keep legacy save/refresh/auth callbacks connected to the new visible view.
window.__legacyLoadAll=loadAll;
window.__legacyRefreshView=refreshView;
loadAll=()=>document.documentElement.dataset.ui==='legacy'?window.__legacyLoadAll():V3.load();
refreshView=()=>document.documentElement.dataset.ui==='legacy'?window.__legacyRefreshView():V3.load();
const v3OriginalFormData=formData;
formData=function(){if(document.documentElement.dataset.ui==='legacy')return v3OriginalFormData();const data=v3OriginalFormData();const c=V3.editorContext();if(c){const m=V3.models.find(m=>m.id===c.model);data.program=m.program;if(editingId===null)data.rank=(m.board||m.path)?null:c.rank;else delete data.rank;if(editingId===null){if(m.board){data.board=m.board;data.board_rank=c.rank;}if(m.path){data.path=m.path;data.path_rank=c.rank;}}if(c.mode==='D2'){delete data.sell_price_935;delete data.sell_price_1315;delete data.sell_price_1335;delete data.sell_date;delete data.is_second_board;}else{delete data.d2_sell_price_935;delete data.d2_sell_price_1315;delete data.d2_sell_price_1335;}}return data;};
const v3OriginalSave=saveTrade;
saveTrade=async function(){await v3OriginalSave();};
V3.init();
