(function () {
  'use strict';
  const followUp = document.getElementById('customer-message-approval');
  if (followUp && location.hash === '#customer-message-approval') followUp.open = true;
  const editor = document.getElementById('follow-up-body');
  if (!editor) return;
  const saved = editor.value;
  const notice = document.getElementById('follow-up-unsaved');
  const actions = new Set(['send_follow_up_sms', 'send_follow_up_email', 'send_follow_up_both', 'schedule_follow_up_sms']);
  editor.addEventListener('input', function () { notice.hidden = editor.value === saved; });
  document.addEventListener('submit', function (event) {
    const action = new FormData(event.target).get('action');
    if (actions.has(action) && editor.value !== saved) {
      event.preventDefault();
      event.stopImmediatePropagation();
      notice.hidden = false;
      editor.closest('details').open = true;
      editor.focus();
    }
  }, true);
}());
