'use strict';
const http=require('node:http'),https=require('node:https');
const cache=new Map();let active=0;
function download(url){return new Promise((resolve,reject)=>{const transport=url.startsWith('https:')?https:http;let request=transport.get(url,{headers:{'User-Agent':'BOAT-BALANCE/1.0','Accept':'application/octet-stream'}},response=>{if(response.statusCode!==200){response.resume();resolve({status:response.statusCode});return}let parts=[],size=0;response.on('data',part=>{size+=part.length;if(size>2000000){request.destroy(new Error('file too large'));return}parts.push(part)});response.on('end',()=>resolve({status:200,body:Buffer.concat(parts)}));response.on('error',reject)});request.setTimeout(10000,()=>request.destroy(new Error('timeout')));let timer=setTimeout(()=>request.destroy(new Error('total timeout')),12000);request.on('close',()=>clearTimeout(timer));request.on('error',reject)})}

module.exports={download};
