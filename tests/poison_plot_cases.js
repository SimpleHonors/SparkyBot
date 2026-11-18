/* Synthetic offline geometry/availability cases for the production JS span.
 * Invoke runPoisonPlotCases(span, finiteMetricSource) in Chromium.
 * Both arguments are extracted unchanged from the production viewer literal.
 * Whole-report navigation and the real tooltip handlers are tested separately.
 */
function runPoisonPlotCases(span, finiteMetricSource) {
  function assert(ok, message) { if (!ok) throw new Error(message); }
  assert(typeof finiteMetricSource==='string' && finiteMetricSource.indexOf('function finiteMetric(')>=0,'production finiteMetric source required');
  var render = new Function('model', `
    var refreshCss='';
    ${finiteMetricSource}
    function fmt(v){return v==null?'—':String(Math.round(v*100)/100);}
    function esc(v){return String(v==null?'':v).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;');}
    function drillAttrs(){return '';}
    function tablePlayer(r){return '<span class="table-player"><b>'+esc(r.name)+'</b></span>';}
    function poisonTable(){return model.poison.length?'<table><tr><td>Secondary</td></tr></table>':'';}
    ${span}
    return {chart:poisonBubbleChart(),details:poisonDetailsTable()};
  `);
  function row(overrides){return Object.assign({account:'Synthetic.1',name:'Synthetic',prof:'Scourge',apps:10,output:0.2,apps_per_min:5,output_available:true},overrides);}
  var cases = [
    {id:'empty-input',rows:[],points:0,missing:0},
    {id:'observed-zero',rows:[row({output:0})],points:1,missing:0},
    {id:'negative-measured-metadata',rows:[row({output:-0.5})],points:0,missing:1},
    {id:'negative-tiny-measured-metadata',rows:[row({output:-0.000001})],points:0,missing:1},
    {id:'negative-legacy',rows:[row({output:-0.5,output_available:undefined})],points:0,missing:1},
    {id:'negative-unavailable-metadata',rows:[row({output:-0.5,output_available:false})],points:0,missing:1},
    {id:'negative-with-positive-peer',rows:[row({output:-0.5}),row({account:'Synthetic.2',name:'Positive peer'})],points:1,missing:1,ys:[0.2]},
    {id:'invalid-output-metadata',rows:[null,undefined,'',NaN,Infinity,-Infinity].map(function(v,i){return row({account:'Invalid.'+i,output:v});}),points:0,missing:6},
    {id:'invalid-output-legacy',rows:[null,undefined,'',NaN,Infinity,-Infinity].map(function(v,i){return row({account:'Invalid.'+i,output:v,output_available:undefined});}),points:0,missing:6},
    {id:'positive-with-and-without-metadata',rows:[row({output:0.25}),row({account:'Synthetic.2',output:0.25,output_available:undefined})],points:2,missing:0,ys:[0.25,0.25]},
    {id:'source-unavailable',rows:[row({output:0,output_available:false})],points:0,missing:1},
    {id:'legacy-positive-and-ambiguous-zero',rows:[row({output_available:undefined}),row({account:'Synthetic.2',name:'Legacy Zero',output:0,output_available:undefined})],points:1,missing:1},
    {id:'tiny-positive',rows:[row({output:0.000001,apps:1,apps_per_min:0.000001})],points:1,missing:0},
    {id:'single-axis-and-both-zero',rows:[row({apps:0}),row({name:'Both zero',apps:0,output:0})],points:1,missing:0},
    {id:'duplicate-names-distinct-identities',rows:[row({name:'Duplicate',account:'Synthetic.A'}),row({name:'Duplicate',account:'Synthetic.B',prof:'Reaper'})],points:2,missing:0},
    {id:'missing-size',rows:[row({apps_per_min:null})],points:1,missing:0}
  ];
  return cases.map(function(test){
    var originals=test.rows.map(function(r){return {output:r.output,available:r.output_available};});
    var result=render({poison:test.rows}), doc=new DOMParser().parseFromString(result.chart,'text/html');
    var circles=Array.from(doc.querySelectorAll('circle[data-bubble]'));
    var missing=doc.querySelectorAll('tr[data-identity]');
    assert(circles.length===test.points,test.id+': point count');
    assert(missing.length===test.missing,test.id+': missing row count');
    test.rows.forEach(function(r,i){assert(Object.is(r.output,originals[i].output)&&r.output_available===originals[i].available,test.id+': input values and availability unchanged');});
    circles.forEach(function(c){assert(Number(c.closest('g').dataset.y)>=0,test.id+': no negative measured coordinate');});
    missing.forEach(function(r){assert(r.dataset.y===''&&r.querySelectorAll('td')[1].textContent==='—',test.id+': invalid output stays table-only/unavailable');});
    if(test.ys)assert(JSON.stringify(circles.map(function(c){return Number(c.closest('g').dataset.y);}).sort())===JSON.stringify(test.ys),test.id+': measured values preserved');
    if(test.id==='negative-with-positive-peer')assert(Number(circles[0].getAttribute('cy'))===50,test.id+': positive peer keeps true coordinate');
    if(test.id==='empty-input')assert(result.chart===''&&result.details==='',test.id+': no empty chart/disclosure');
    if(test.id==='observed-zero')assert(Number(circles[0].closest('g').dataset.y)===0&&Number(circles[0].getAttribute('cy'))===410,test.id+': true zero coordinate');
    if(test.id==='tiny-positive'){
      assert(circles[0].closest('g').dataset.y==='0.000001',test.id+': original numeric value');
      assert(Number(circles[0].getAttribute('cy'))===50,test.id+': real scaled coordinate');
      assert(circles[0].dataset.tooltip.indexOf('1e-6')>=0,test.id+': tiny positive is not displayed as zero');
    }
    if(test.id==='duplicate-names-distinct-identities')assert(new Set(circles.map(function(c){return c.closest('g').dataset.identity;})).size===2,test.id+': account/profession identity');
    if(test.id==='missing-size')assert(circles[0].hasAttribute('stroke-dasharray'),test.id+': distinct missing-size marker');
    return {id:test.id,pass:true,points:circles.length,unavailable:missing.length};
  });
}
