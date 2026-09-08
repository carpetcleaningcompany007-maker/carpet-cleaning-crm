(() => {
  const panel = document.querySelector('[data-writing-assistance]');
  if (!panel) return;
  const form = panel.closest('form');
  const body = form.querySelector('[name=body]');
  const subject = form.querySelector('[name=subject]');
  const channel = form.querySelector('[name=channel]');
  const status = panel.querySelector('[data-writing-status]');
  const preview = panel.querySelector('[data-writing-preview]');
  const previewBody = panel.querySelector('[data-writing-body]');
  const previewSubject = panel.querySelector('[data-writing-subject]');
  const dictate = panel.querySelector('[data-dictate]');
  let recognition = null, source = null, pending = false;
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Recognition) {
    dictate.disabled = true;
    status.textContent = 'Use the microphone on your phone keyboard to dictate into Message, then choose an AI writing tool.';
  }
  dictate.addEventListener('click', () => {
    if (recognition) { recognition.stop(); return; }
    recognition = new Recognition();
    recognition.lang = 'en-GB'; recognition.interimResults = false; recognition.continuous = true;
    recognition.onresult = event => {
      for (let i = event.resultIndex; i < event.results.length; i++) {
        if (event.results[i].isFinal) body.value += (body.value ? ' ' : '') + event.results[i][0].transcript;
      }
      body.dispatchEvent(new Event('input', { bubbles: true }));
    };
    recognition.onerror = () => { status.textContent = 'Dictation could not continue. Allow microphone access, or use your phone keyboard microphone.'; };
    recognition.onend = () => { recognition = null; dictate.textContent = 'Start dictating'; };
    try { recognition.start(); dictate.textContent = 'Stop dictating'; status.textContent = 'Listening. Stop when finished, then check or improve the wording.'; }
    catch (_) { recognition = null; status.textContent = 'Dictation is unavailable here. Use your keyboard microphone.'; }
  });
  panel.querySelectorAll('[data-writing-action]').forEach(button => button.addEventListener('click', async () => {
    if (pending) return;
    if (!body.value.trim()) { status.textContent = 'Type or dictate your message first.'; body.focus(); return; }
    if (recognition) recognition.stop();
    source = { body: body.value, subject: subject.value, channel: channel.value };
    pending = true;
    panel.querySelectorAll('[data-writing-action]').forEach(el => el.disabled = true);
    preview.hidden = true; status.textContent = 'AI is checking your wording… Your original stays in the editor.';
    const data = new FormData();
    for (const [key, value] of Object.entries(source)) data.append(key, value);
    data.append('_csrf_token', form.querySelector('[name=_csrf_token]').value);
    data.append('writing_action', button.dataset.writingAction);
    try {
      const response = await fetch(panel.dataset.endpoint, { method: 'POST', body: data });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'AI could not finish. Please try again.');
      previewBody.value = result.body; previewSubject.value = result.subject;
      panel.querySelector('[data-writing-subject-label]').hidden = source.channel !== 'Email';
      panel.querySelector('[data-writing-feedback]').textContent = [result.feedback, result.warning].filter(Boolean).join(' ');
      preview.hidden = false; status.textContent = 'Check and edit the suggestion, then choose Use this draft.';
    } catch (error) { status.textContent = error.message || 'AI could not finish. Your original message is unchanged.'; }
    finally { pending = false; panel.querySelectorAll('[data-writing-action]').forEach(el => el.disabled = false); }
  }));
  panel.querySelector('[data-use-writing]').addEventListener('click', () => {
    if (!source || body.value !== source.body || subject.value !== source.subject || channel.value !== source.channel) {
      status.textContent = 'Your original or channel changed while AI was working. Check it and run the writing tool again.'; return;
    }
    body.value = previewBody.value;
    if (channel.value === 'Email') subject.value = previewSubject.value;
    body.dispatchEvent(new Event('input', { bubbles: true }));
    preview.hidden = true; status.textContent = 'Draft added to the editor. Check it, then press Send message when ready.';
  });
  panel.querySelector('[data-dismiss-writing]').addEventListener('click', () => { preview.hidden = true; status.textContent = 'Original message kept.'; });
  form.addEventListener('submit', () => { if (recognition) recognition.stop(); });
  window.addEventListener('pagehide', () => { if (recognition) recognition.stop(); });
})();
