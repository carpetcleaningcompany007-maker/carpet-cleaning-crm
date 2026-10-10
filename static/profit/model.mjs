export const num=v=>Math.max(0,Number(v)||0);
export const defaults=()=>({id:crypto.randomUUID(),name:'First two weeks',start:'',length:2,unit:'Weeks',enquiries:20,source:'Totals',booked:6,average:140,hours:3,expenses:20,adMode:'Total',adAmount:300,adsOn:true,adsProvider:'Jason',adsFee:300,adsBasis:'Monthly',phoneOn:true,phoneProvider:'Jason',phoneFee:500,phoneBasis:'Monthly',bookingFee:0,extras:[],jobs:[]});
export function calculate(p){
 const days=num(p.length)*(p.unit==='Weeks'?7:p.unit==='Months'?28:1),months=days/28;
 const eligible=(p.jobs||[]).filter(j=>['Booked','Completed'].includes(j.status));
 const booked=p.source==='Jobs'?eligible.length:num(p.booked);
 const incomplete=p.source==='Jobs'&&eligible.some(j=>j.price===''||j.price==null);
 const revenue=p.source==='Jobs'?eligible.reduce((s,j)=>s+num(j.price),0):booked*num(p.average);
 const hours=p.source==='Jobs'?eligible.reduce((s,j)=>s+(j.hours===''||j.hours==null?num(p.hours):num(j.hours)),0):booked*num(p.hours);
 const ads=num(p.adAmount)*(p.adMode==='Daily'?days:p.adMode==='Per lead'?num(p.enquiries):1);
 const expense=booked*num(p.expenses), management=p.adsOn?num(p.adsFee)*(p.adsBasis==='Monthly'?months:1):0;
 const phone=p.phoneOn?num(p.phoneFee)*(p.phoneBasis==='Monthly'?months:1):0,booking=booked*num(p.bookingFee);
 const extra=(p.extras||[]).reduce((s,e)=>s+num(e.amount),0);
 const costs=ads+expense+management+phone+booking+extra,profit=revenue-costs;
 return {days,booked,revenue,hours,ads,expense,management,phone,booking,extra,costs,profit,incomplete,average:booked?revenue/booked:0,perJob:booked?profit/booked:null,perHour:hours?profit/hours:null,costPerJob:booked?costs/booked:null,leadCost:num(p.enquiries)?ads/num(p.enquiries):null,adPerJob:booked?ads/booked:null,rate:num(p.enquiries)?booked/num(p.enquiries):null,margin:revenue?profit/revenue:null};
}
