(function(){
  'use strict';
  document.querySelectorAll('.flash').forEach(function(node){if(/failed|not sent|couldn.t|could not|unable/i.test(node.textContent)){node.classList.add('delivery-notice-error');node.setAttribute('role','alert');}});
  let pending=null;
  const approved=new WeakSet();
  const dialog=document.createElement('dialog');
  dialog.className='message-confirm-dialog';
  dialog.setAttribute('aria-labelledby','message-confirm-title');
  dialog.innerHTML='<div class="message-confirm-symbol" aria-hidden="true">✉</div><h2 id="message-confirm-title">Send this message?</h2><p data-confirm-copy></p><div class="message-confirm-actions"><button type="button" data-no>No, cancel</button><button type="button" data-yes>Yes, send message</button></div>';
  document.body.appendChild(dialog);
  const notice=document.createElement('div');notice.className='message-result-toast';notice.setAttribute('role','status');notice.hidden=true;document.body.appendChild(notice);
  function cancel(){
    const previous=pending;pending=null;dialog.close();
    if(previous){notice.textContent='Thank you, your '+previous.kind+' has been cancelled. Nothing was sent.';notice.hidden=false;previous.button?.focus();}
  }
  dialog.querySelector('[data-no]').addEventListener('click',cancel);
  dialog.addEventListener('cancel',function(event){event.preventDefault();cancel();});
  dialog.querySelector('[data-yes]').addEventListener('click',function(){
    if(!pending)return;
    const item=pending;pending=null;dialog.close();approved.add(item.form);
    item.form.requestSubmit(item.button||undefined);
  });
  document.addEventListener('submit',function(event){
    const form=event.target,button=event.submitter;
    if(approved.has(form)){approved.delete(form);return;}
    const method=button?.getAttribute('formmethod')||form.method;
    if((method||'get').toLowerCase()!=='post')return;
    const path=new URL(button?.getAttribute('formaction')||form.action||location.href,location.href).pathname;
    const data=new FormData(form);if(button?.name)data.set(button.name,button.value);
    const action=data.get('action');
    const sending=/\/(?:send-contact-form|send-message-template|send-review-sms|send-booking-confirmation|send-late-notice|send-customer|approve-send-email|send_reminder|batch-send)$/.test(path)
      || /^\/customers\/\d+\/conversation$/.test(path) || /^\/sms-threads\/\d+\/reply$/.test(path)
      || (/^\/today-run\/job\/\d+\/action$/.test(path)&&['coming','reminder','finished','review'].includes(action));
    if(!sending || data.get('save_only')==='1' || data.get('preview')==='1' || data.get('test_mode')==='1' || data.get('use_test_details')==='1' || data.get('channel')==='test_email')return;
    const channel=String(data.get('channel')||'').toLowerCase();
    const sms=data.get('send_sms')==='1'||['sms','text','both'].includes(channel)||/send-review-sms|sms-threads/.test(path);
    const email=data.get('send_email')==='1'||['email','both'].includes(channel);
    const kind=sms&&email?'email and text message':sms?'text message':email?'email':'message';
    event.preventDefault();event.stopImmediatePropagation();
    if(pending)return;
    notice.hidden=true;pending={form,button,kind};
    dialog.querySelector('[data-confirm-copy]').textContent='You are about to send a real '+kind+' to '+(path.includes('batch-send')?'the selected customers':'this customer')+'. Do you wish to continue?';
    dialog.showModal();dialog.querySelector('[data-no]').focus();
  },true);
})();
