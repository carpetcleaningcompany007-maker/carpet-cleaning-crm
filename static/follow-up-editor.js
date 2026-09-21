(function () {
  'use strict';
  const steps = Array.from(document.querySelectorAll('[data-enquiry-step]'));
  const firstContactSent = document.querySelector('[data-first-contact-sent]')?.dataset.firstContactSent !== 'no';
  function showStep(number, scroll) {
    if (!firstContactSent && number !== '1') number = '1';
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
  const firstBody = document.getElementById('first-body');
  const firstSaved = firstBody?.value;
  function openFirstPanel(id) {
    document.querySelectorAll('.first-control-panel').forEach(panel => { panel.hidden = panel.id !== id; });
    document.querySelectorAll('[data-first-panel]').forEach(button => {
      button.setAttribute('aria-expanded', String(button.dataset.firstPanel === id));
    });
  }
  document.querySelectorAll('[data-first-panel]').forEach(button => {
    button.addEventListener('click', () => {
      const panelId = button.getAttribute('aria-expanded') === 'true' ? '' : button.dataset.firstPanel;
      openFirstPanel(panelId);
      if (panelId) document.getElementById(panelId)?.scrollIntoView({behavior:'smooth',block:'center'});
    });
  });
  document.addEventListener('submit', event => {
    if (!firstBody || firstBody.value === firstSaved || !event.target.action.endsWith('/first-text')) return;
    const action = new FormData(event.target).get('action');
    if (['send_now','schedule'].includes(action)) {
      event.preventDefault(); event.stopImmediatePropagation();
      openFirstPanel('first-edit');
      document.getElementById('first-unsaved').hidden = false;
      firstBody.focus();
    }
  }, true);
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
