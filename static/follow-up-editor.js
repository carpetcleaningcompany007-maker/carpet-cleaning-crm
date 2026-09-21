(function () {
  'use strict';
  const steps = Array.from(document.querySelectorAll('[data-enquiry-step]'));
  function showStep(number, scroll) {
    steps.forEach(step => { step.hidden = step.dataset.enquiryStep !== number; });
    document.querySelectorAll('.enquiry-step-nav [data-show-step]').forEach(button => {
      if (button.dataset.showStep === number) button.setAttribute('aria-current', 'step');
      else button.removeAttribute('aria-current');
    });
    if (scroll) document.querySelector('.enquiry-step-nav')?.scrollIntoView({behavior:'smooth',block:'start'});
  }
  document.querySelectorAll('[data-show-step]').forEach(button => {
    button.addEventListener('click', () => showStep(button.dataset.showStep, true));
  });
  if (steps.length) showStep(location.hash === '#customer-message-approval' ? '2' : location.hash === '#edit-intake-details' ? '3' : '1', false);
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
