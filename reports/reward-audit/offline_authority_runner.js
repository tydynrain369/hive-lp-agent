// OFFLINE MOCK ONLY. No networking, signing, or broadcasting.
const fs=require('fs'), vm=require('vm'), BN=require('./bignumber-9.1.2.js');
const input=JSON.parse(fs.readFileSync(0,'utf8'));
(async()=>{
 const outputs=[];
 for(const fixture of input){
  const events=[],pending=[],errors=[];
  const api={BigNumber:BN,hiveBlockTimestamp:new Date(fixture.at_ms).toISOString().slice(0,19),
   assert:(ok,msg)=>{if(!ok)errors.push(msg);return ok;},emit:(event,data)=>events.push({event,data}),
   db:{findOneInTable:async(c,t,q)=>t==='pools'?{totalShares:fixture.poolShares??'100'}:{precision:fixture.precisions[q.symbol]},
    findInTable:async(c,t,q,limit,offset)=>fixture.positions.filter(p=>!q.account.$nin.includes(p.account)&&Number(p.timeFactor)<=q.timeFactor.$lte).sort((a,b)=>a._id-b._id).slice(offset,offset+limit),
    update:async()=>{},insert:async(t,v)=>pending.push(v)}};
  const context=vm.createContext({api, fixture, events, pending, errors, payRecipient:async()=>true});
  for(const name of ['getEffectiveShares','getPoolRecipients','runDistribution'])
    vm.runInContext(fs.readFileSync(__dirname+'/'+name+'.js','utf8'),context);
  await vm.runInContext('runDistribution(fixture.batch,fixture.params)',context);
  outputs.push({name:fixture.name,events,pending,errors});
 }
 console.log(JSON.stringify({bigNumberConfig:BN.config(),outputs}));
})().catch(e=>{console.error(e);process.exitCode=1});
