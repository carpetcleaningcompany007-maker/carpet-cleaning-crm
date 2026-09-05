(function(){
  'use strict';
  if(!('serviceWorker' in navigator) || !(location.protocol==='https:' || location.hostname==='localhost' || location.hostname==='127.0.0.1')) return;
  navigator.serviceWorker.register('/service-worker.js',{scope:'/'}).catch(function(){});

  function base64UrlToUint8Array(value){
    var padding='='.repeat((4-value.length%4)%4);
    var raw=atob((value+padding).replace(/-/g,'+').replace(/_/g,'/'));
    return Uint8Array.from(Array.prototype.map.call(raw,function(c){return c.charCodeAt(0)}));
  }
  function csrf(){var node=document.querySelector('meta[name="csrf-token"]');return node?node.content:''}
  function prefs(){var out={};document.querySelectorAll('[data-push-category]').forEach(function(input){out[input.dataset.pushCategory]=input.checked});return out}
  function setStatus(message,kind){document.querySelectorAll('[data-push-status]').forEach(function(node){node.textContent=message;node.dataset.kind=kind||''})}
  async function currentSubscription(){var registration=await navigator.serviceWorker.ready;return registration.pushManager.getSubscription()}
  async function post(url,method,body){return fetch(url,{method:method,headers:{'Content-Type':'application/json','X-CSRF-Token':csrf()},body:JSON.stringify(body),credentials:'same-origin'})}

  document.addEventListener('click',async function(event){
    var enable=event.target.closest('[data-enable-push]');
    var disable=event.target.closest('[data-disable-push]');
    if(!enable&&!disable)return;
    event.preventDefault();
    if(disable){
      try{var old=await currentSubscription();if(old){await post('/api/push/subscriptions','DELETE',{subscription:old.toJSON()});await old.unsubscribe()}setStatus('Notifications are off on this device.','off')}catch(e){setStatus('Could not turn notifications off. Please try again.','error')}return;
    }
    enable.disabled=true;
    try{
      var standalone=window.matchMedia('(display-mode: standalone)').matches||window.navigator.standalone===true;
      if(/iPhone|iPad|iPod/.test(navigator.userAgent)&&!standalone){setStatus('First add the CRM to your Home Screen, then open that installed app and enable notifications.','install');return}
      var config=await fetch('/api/push/config',{credentials:'same-origin'}).then(function(r){return r.json()});
      if(!config.configured){setStatus('Phone alerts are ready, but the secure push keys still need adding in Render.','setup');return}
      var permission=await Notification.requestPermission();
      if(permission!=='granted'){setStatus('Notifications were not enabled. You can allow them later in your phone settings.','off');return}
      var registration=await navigator.serviceWorker.ready;
      var subscription=await registration.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:base64UrlToUint8Array(config.publicKey)});
      var response=await post('/api/push/subscriptions','POST',{subscription:subscription.toJSON(),preferences:prefs()});
      if(!response.ok)throw new Error('save failed');
      setStatus('Phone alerts are enabled on this device.','on');
    }catch(e){setStatus('Could not enable notifications. Please try again from the installed app.','error')}finally{enable.disabled=false}
  });
  document.addEventListener('change',async function(event){
    if(!event.target.matches('[data-push-category]'))return;
    try{var subscription=await currentSubscription();if(subscription)await post('/api/push/preferences','POST',{endpoint:subscription.endpoint,preferences:prefs()})}catch(e){setStatus('Preference could not be saved. Please try again.','error')}
  });
  if(document.querySelector('[data-push-status]'))currentSubscription().then(function(subscription){if(subscription)setStatus('Phone alerts are enabled on this device.','on')}).catch(function(){});
  if(navigator.setAppBadge){var badge=document.querySelector('.notification-badge');var count=badge?parseInt(badge.textContent,10)||0:0;if(count)navigator.setAppBadge(count);else if(navigator.clearAppBadge)navigator.clearAppBadge()}
})();
