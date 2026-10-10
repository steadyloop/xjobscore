(() => {
  "use strict";
  const payload = window.CLOSURE_RESEARCH_DATA;
  if (!payload) { document.querySelector("main").textContent = "Data unavailable."; return; }
  const $ = id => document.getElementById(id);
  const ns = "http://www.w3.org/2000/svg";
  const parse = value => value ? new Date(value.replace(" ", "T")).getTime() : null;
  const jobs = payload.jobs.map(row => ({ ...row, time: parse(row.closed_at), posted: parse(row.posted_at),
    haystack: [row.company_name,row.title,row.role_family,row.location_text].filter(Boolean).join(" ").toLocaleLowerCase() }));
  const now = parse(payload.meta.generated_at);
  const first = Math.min(...jobs.map(row => row.time));
  const state = { company: "", role: "", q: "", range: null, period: "7", granularity: "hour", breakdown: "company", sort: "closed_at", direction: -1, page: 0 };
  const size = 8;
  let results = [], bins = [], plot = {}, dragging = null, renderTimer;
  const count = n => n.toLocaleString();
  const date = time => time == null ? "—" : new Date(time).toLocaleDateString(undefined,
    { month:"short",day:"numeric",...(new Date(time).getFullYear() !== new Date(now).getFullYear() ? {year:"numeric"} : {}) });
  const fullDate = time => time == null ? "—" : new Date(time).toLocaleString(undefined,{month:"short",day:"numeric",year:"numeric",hour:"numeric",minute:"2-digit",timeZoneName:"short"});
  const closedDate = time => `${date(time)}, ${new Date(time).toLocaleTimeString(undefined,{hour:"numeric",minute:"2-digit"})}`;
  const hourLabel = time => new Date(time).toLocaleTimeString(undefined,{hour:"2-digit",minute:"2-digit",hourCycle:"h23"});
  function node(tag, attrs={}, ...children) {
    const element = document.createElement(tag);
    for (const [key,value] of Object.entries(attrs)) {
      if (key.startsWith("on")) element.addEventListener(key.slice(2),value);
      else if (value != null) element.setAttribute(key,String(value));
    }
    for (const child of children.flat()) if (child != null) element.append(child instanceof Node ? child : document.createTextNode(child));
    return element;
  }
  function svg(tag, attrs={}, text) {
    const element=document.createElementNS(ns,tag);
    for (const [key,value] of Object.entries(attrs)) element.setAttribute(key,value);
    if (text != null) element.textContent=text;
    return element;
  }
  function periodBounds() {
    const start=new Date(now); start.setHours(0,0,0,0);
    if (state.period !== "all") start.setDate(start.getDate()-Number(state.period)+1);
    return [state.period === "all" ? first : Math.max(first,start.getTime()),now];
  }
  function matches(ignoreRange=false, ignoreDimension=null) {
    const [from,to]=state.range && !ignoreRange ? state.range : periodBounds();
    return jobs.filter(row => row.time >= from && row.time <= to && (ignoreDimension==="company" || !state.company || String(row.company_id) === state.company)
      && (ignoreDimension==="role" || !state.role || row.role_family === state.role) && (!state.q || row.haystack.includes(state.q)));
  }
  function floor(time) {
    const d=new Date(time);
    if (state.granularity === "day") d.setHours(0,0,0,0);
    else d.setMinutes(0,0,0);
    return d.getTime();
  }
  function next(time) {
    if (state.granularity === "hour") return time+3600000;
    const d=new Date(time); d.setDate(d.getDate()+1); return d.getTime();
  }
  function intervalLabel(start,end) {
    if (state.granularity === "day" && end <= next(start)) return date(start);
    return `${date(start)} · ${new Date(start).toLocaleTimeString(undefined,{hour:"numeric",minute:"2-digit"})}–${new Date(end).toLocaleTimeString(undefined,{hour:"numeric",minute:"2-digit"})}`;
  }
  function renderChart() {
    const [from,to]=periodBounds();
    bins=[];
    for (let time=floor(from);time<=to;time=next(time)) bins.push({start:time,end:next(time),count:0});
    const lookup=new Map(bins.map((bin,i)=>[bin.start,i]));
    for (const row of matches(true)) {
      const i=lookup.get(floor(row.time)); if (i != null) bins[i].count++;
    }
    const width=$("timeline").clientWidth, height=$("timeline").clientHeight;
    const left=42, right=8, top=15, bottom=40;
    const w=width-left-right,h=height-top-bottom;
    const max=Math.max(1,...bins.map(bin=>bin.count));
    const magnitude=10**Math.floor(Math.log10(max));
    const ceiling=Math.ceil(max/magnitude)*magnitude;
    const step=w/Math.max(1,bins.length);
    plot={left,right,top,bottom,width,height,w,h,step};
    const chart=$("chart"); chart.setAttribute("viewBox",`0 0 ${width} ${height}`);
    chart.replaceChildren();
    for (let i=0;i<=4;i++) {
      const y=top+h-h*i/4;
      chart.append(svg("line",{x1:left,x2:width-right,y1:y,y2:y,class:"chart-grid"}),
        svg("text",{x:left-12,y:y+3,"text-anchor":"end",class:"chart-tick"},new Intl.NumberFormat(undefined,{notation:"compact",maximumFractionDigits:1}).format(ceiling*i/4)));
    }
    bins.forEach((bin,i)=>{
      const selected=state.range && bin.start<state.range[1] && bin.end>state.range[0];
      const bar=svg("rect",{x:left+i*step+Math.min(2,step*.14),y:top+h-h*bin.count/ceiling,
        width:Math.max(.5,step-Math.min(4,step*.28)),height:h*bin.count/ceiling,
        rx:Math.min(1.5,step*.05),class:`chart-bar${state.range ? selected ? " selected" : " dim" : ""}`,
        tabindex:bin.count ? 0 : -1,role:"button","aria-label":`${intervalLabel(bin.start,bin.end)}: ${count(bin.count)} closures`});
      bar.addEventListener("keydown",event=>{
        if (event.key==="Enter" || event.key===" ") { event.preventDefault(); setRange(bin.start,bin.end); }
      });
      bar.addEventListener("focus",()=>showTooltip(i,left+i*step));
      bar.addEventListener("blur",hideTooltip);
      chart.append(bar);
    });
    const tickIndices=[];
    const spacing=width<500 ? 110 : 130;
    for (let i=0;i<bins.length;i++) {
      const d=new Date(bins[i].start);
      const major=state.granularity==="day" || d.getHours()===0;
      if ((major || i===0) && (!tickIndices.length || (i-tickIndices.at(-1))*step>spacing)) tickIndices.push(i);
    }
    if (tickIndices.length<2 && bins.length>1) tickIndices.push(bins.length-1);
    if (state.granularity==="hour") {
      const every=step*6>85 ? 6 : step*12>75 ? 12 : 24;
      bins.forEach((bin,i)=>{
        if (new Date(bin.start).getHours()%every===0) chart.append(svg("text",{x:left+(i+.5)*step,y:height-22,"text-anchor":"middle",class:"chart-tick"},hourLabel(bin.start)));
      });
    }
    tickIndices.forEach(i=>chart.append(svg("text",{x:Math.min(width-34,Math.max(left+20,left+(i+.5)*step)),y:height-5,"text-anchor":"middle",class:"chart-tick"},date(bins[i].start))));
    chart.append(svg("line",{id:"hover-line",x1:0,x2:0,y1:top,y2:top+h,class:"chart-hover",visibility:"hidden"}));
    chart.append(svg("rect",{id:"brush",x:0,y:top,width:0,height:h,class:"chart-brush"}));
    if (!matches(true).length) chart.append(svg("text",{x:left+w/2,y:top+h/2,"text-anchor":"middle",class:"chart-tick"},"No closures in this selection"));
  }
  function showTooltip(i,x) {
    const bin=bins[i]; if (!bin) return;
    $("tooltip").replaceChildren(node("span",{},intervalLabel(bin.start,bin.end)),node("strong",{},`${count(bin.count)} closures`));
    $("tooltip").hidden=false;
    $("tooltip").style.left=`${Math.max(0,Math.min(x+12,plot.width-$("tooltip").offsetWidth))}px`;
    const line=$("hover-line"); line.setAttribute("x1",plot.left+(i+.5)*plot.step);line.setAttribute("x2",plot.left+(i+.5)*plot.step);line.setAttribute("visibility","visible");
  }
  function hideTooltip() { $("tooltip").hidden=true; $("hover-line")?.setAttribute("visibility","hidden"); }
  function indexAt(event) {
    const x=event.clientX-$("chart").getBoundingClientRect().left;
    return Math.max(0,Math.min(bins.length-1,Math.floor((x-plot.left)/plot.step)));
  }
  function setRange(from,to) { state.range=[Math.max(from,periodBounds()[0]),Math.min(to-1,now)];state.page=0;hideTooltip();render(); }
  $("chart").addEventListener("pointerdown",event=>{
    if (event.button!==0 || !bins.length) return;
    dragging=indexAt(event);$("chart").setPointerCapture(event.pointerId);
  });
  $("chart").addEventListener("pointermove",event=>{
    const i=indexAt(event);showTooltip(i,event.clientX-$("chart").getBoundingClientRect().left);
    if (dragging!=null) {
      const a=Math.min(dragging,i),b=Math.max(dragging,i),brush=$("brush");
      brush.setAttribute("x",plot.left+a*plot.step);brush.setAttribute("width",(b-a+1)*plot.step);
    }
  });
  $("chart").addEventListener("pointerup",event=>{
    if (dragging==null) return;
    const i=indexAt(event),a=Math.min(dragging,i),b=Math.max(dragging,i);dragging=null;
    setRange(bins[a].start,bins[b].end);
  });
  $("chart").addEventListener("pointerleave",()=>{ if (dragging==null) hideTooltip(); });
  $("chart").addEventListener("pointercancel",()=>{ dragging=null;$("brush").setAttribute("width",0);hideTooltip(); });

  function renderRanking() {
    const grouping=state.breakdown==="company" ? "company_name" : "role_family";
    const groups=new Map();
    for (const row of matches(false,state.breakdown)) {
      const key=state.breakdown==="company" ? String(row.company_id) : row.role_family;
      if (!groups.has(key)) groups.set(key,{key,label:row[grouping],count:0});
      groups.get(key).count++;
    }
    const ranked=[...groups.values()].sort((a,b)=>b.count-a.count || a.label.localeCompare(b.label));
    const max=ranked[0]?.count || 1;
    $("ranking").replaceChildren(...ranked.map(item=>node("button",{class:"rank",type:"button",title:`${item.label}: ${count(item.count)} closures`,
      "aria-pressed":String(item.key===(state.breakdown==="company" ? state.company : state.role)),
      onclick:()=>{
        if (state.breakdown==="company") { state.company=item.key;state.breakdown="role"; }
        else { state.role=item.key; }
        state.page=0;render();
      }},node("div",{class:"rank-caption"},node("span",{class:"rank-name"},item.label),node("span",{class:"rank-count"},count(item.count))),
      node("div",{class:"rank-track"},node("div",{class:"rank-fill",style:`width:${item.count/max*100}%`})) )));
    if (!ranked.length) $("ranking").append(node("div",{class:"empty"},"No results"));
    $("companies-tab").setAttribute("aria-pressed",String(state.breakdown==="company"));
    $("roles-tab").setAttribute("aria-pressed",String(state.breakdown==="role"));
  }
  function sortedRows() {
    return [...results].sort((a,b)=>{
      const key=state.sort;
      const aa=key==="closed_at" ? a.time : key==="posted_at" ? a.posted : a[key];
      const bb=key==="closed_at" ? b.time : key==="posted_at" ? b.posted : b[key];
      if (aa==null && bb==null) return b.id-a.id;
      if (aa==null) return 1;if (bb==null) return -1;
      return (typeof aa==="number" ? aa-bb : aa.localeCompare(bb))*state.direction || b.id-a.id;
    });
  }
  function renderTable() {
    const ordered=sortedRows();
    const maxPage=Math.max(0,Math.ceil(ordered.length/size)-1);state.page=Math.min(state.page,maxPage);
    const rows=ordered.slice(state.page*size,(state.page+1)*size);
    $("jobs").replaceChildren(...rows.map(row=>node("tr",{onclick:()=>openJob(row)},
      node("td",{},node("span",{class:"company-name",title:row.company_name},row.company_name)),
      node("td",{},node("button",{class:"role-title",type:"button",title:row.title},row.title),node("span",{class:"role-family"},row.role_family)),
      node("td",{},node("span",{class:"location",title:row.location_text || "Location unavailable"},row.location_text || "—")),
      node("td",{class:"table-date",title:fullDate(row.posted)},date(row.posted)),
      node("td",{class:"table-date closed-date",title:fullDate(row.time)},closedDate(row.time)))));
    $("job-count").textContent=count(results.length);
    $("empty").hidden=rows.length>0;
    $("page-label").textContent=ordered.length ? `${count(state.page*size+1)}–${count(state.page*size+rows.length)} of ${count(ordered.length)}` : "0 jobs";
    $("page-number").textContent=`${state.page+1} / ${maxPage+1}`;
    $("previous").disabled=state.page===0;$("next").disabled=state.page===maxPage;
    document.querySelectorAll("[data-sort]").forEach(button=>{
      const selected=button.dataset.sort===state.sort;
      button.classList.toggle("sorted",selected);
      button.querySelector("span")?.remove();
      if (selected) button.append(node("span",{},state.direction===-1 ? "↓" : "↑"));
      button.closest("th").setAttribute("aria-sort",selected ? state.direction===-1 ? "descending" : "ascending" : "none");
    });
  }
  function openJob(row) {
    $("detail-company").textContent=row.company_name;$("detail-title").textContent=row.title;
    $("detail-fields").replaceChildren(...[["Role",row.role_family],["Location",row.location_text || "—"],["Posted",fullDate(row.posted)],["Closed",fullDate(row.time)],["Source",row.source]].flatMap(([label,value])=>[node("dt",{},label),node("dd",{},value)]));
    const href=row.source_url || row.apply_url;
    try { $("detail-link").hidden=!["http:","https:"].includes(new URL(href).protocol); } catch { $("detail-link").hidden=true; }
    if (!$("detail-link").hidden) $("detail-link").href=href;
    $("detail-description").textContent=row.description_excerpt || "";
    $("job-detail").showModal();
  }
  function render() {
    results=matches();
    $("company").value=state.company;$("role").value=state.role;
    $("reset").hidden=!(state.company || state.role || state.q || state.range);
    $("range-filter").hidden=!state.range;
    if (state.range) $("range-filter").replaceChildren(node("span",{title:`${fullDate(state.range[0])} – ${fullDate(state.range[1])}`},`${date(state.range[0])} · ${hourLabel(state.range[0])}–${date(state.range[0])!==date(state.range[1]) ? date(state.range[1])+" · " : ""}${hourLabel(state.range[1]+1)}`),node("button",{type:"button","aria-label":"Clear selected time range",onclick:()=>{state.range=null;state.page=0;render();}},"×"));
    const [from,to]=periodBounds();$("date-range").textContent=`${date(from)}–${date(to)}, ${new Date(to).getFullYear()}`;
    renderChart();renderRanking();renderTable();
  }
  const companies=new Map(jobs.map(row=>[String(row.company_id),row.company_name]));
  for (const [id,name] of [...companies].sort((a,b)=>a[1].localeCompare(b[1]))) $("company").append(node("option",{value:id},name));
  for (const role of [...new Set(jobs.map(row=>row.role_family))].sort()) $("role").append(node("option",{value:role},role));
  $("company").addEventListener("change",event=>{ state.company=event.target.value;state.breakdown=state.company ? "role" : "company";state.page=0;render(); });
  $("role").addEventListener("change",event=>{ state.role=event.target.value;state.breakdown=state.company ? "role" : "company";state.page=0;render(); });
  $("period").addEventListener("change",event=>{ state.period=event.target.value;state.range=null;state.page=0;render(); });
  $("reset").addEventListener("click",()=>{ Object.assign(state,{company:"",role:"",q:"",range:null,page:0,breakdown:"company"});$("search").value="";render(); });
  $("search").addEventListener("input",event=>{clearTimeout(renderTimer);renderTimer=setTimeout(()=>{state.q=event.target.value.trim().toLocaleLowerCase();state.page=0;render();},180);});
  $("companies-tab").addEventListener("click",()=>{state.breakdown="company";renderRanking();});
  $("roles-tab").addEventListener("click",()=>{state.breakdown="role";renderRanking();});
  document.querySelectorAll("[data-value]").forEach(button=>button.addEventListener("click",()=>{
    state.granularity=button.dataset.value;document.querySelectorAll("[data-value]").forEach(b=>b.setAttribute("aria-pressed",String(b===button)));hideTooltip();renderChart();
  }));
  document.querySelectorAll("[data-sort]").forEach(button=>button.addEventListener("click",()=>{
    if (state.sort===button.dataset.sort) state.direction*=-1;
    else { state.sort=button.dataset.sort;state.direction=state.sort.endsWith("_at") ? -1 : 1; }
    state.page=0;renderTable();
  }));
  $("previous").addEventListener("click",()=>{state.page--;renderTable();});$("next").addEventListener("click",()=>{state.page++;renderTable();});
  $("close-detail").addEventListener("click",()=>$("job-detail").close());
  $("job-detail").addEventListener("click",event=>{if(event.target===$("job-detail")){const r=event.target.getBoundingClientRect();if(event.clientX<r.left || event.clientX>r.right || event.clientY<r.top || event.clientY>r.bottom) event.target.close();}});
  $("export").addEventListener("click",()=>{
    const columns=["company_name","title","role_family","location_text","source","posted_at","closed_at","source_url"];
    const quote=value=>{ const text=String(value ?? "");return '"'+(/^[=+\-@\t\r]/.test(text) ? "'" : "")+text.replaceAll('"','""')+'"'; };
    const csv=[columns.join(","),...sortedRows().map(row=>columns.map(key=>quote(row[key])).join(","))].join("\r\n");
    const href=URL.createObjectURL(new Blob(["\ufeff",csv],{type:"text/csv;charset=utf-8;"}));
    const link=node("a",{href,download:"closed-jobs.csv"});link.click();setTimeout(()=>URL.revokeObjectURL(href),1000);
  });
  new ResizeObserver(()=>renderChart()).observe($("timeline"));
  render();
})();
