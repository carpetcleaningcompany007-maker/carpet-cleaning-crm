(() => {
  const dock = document.querySelector('.voice-dock'); if (!dock) return;
  const panel = dock.querySelector('[data-recorder-panel]'), status = dock.querySelector('[data-record-status]');
  const stop = dock.querySelector('[data-stop-recording]'), retry = dock.querySelector('[data-retry-recording]');
  const preview = dock.querySelector('[data-transcript-preview]'), transcript = dock.querySelector('[data-record-transcript]');
  const upload = dock.querySelector('[data-audio-upload]');
  let target, insertionTarget, stream, recorder, timer, ticker, recording, filename, busy = false, cancelled = false;
  const eligible = e => e && !e.disabled && !e.readOnly && !e.closest('.voice-dock') && e.matches('textarea,input[type=text],input[type=search],input:not([type])');
  document.addEventListener('focusin', e => { if (eligible(e.target)) target = e.target; });
  const defaultTarget = () => document.querySelector('[data-ai-notes]') || document.querySelector('.conversation-compose [name=body]') || document.querySelector('#voice-customer-query');
  const release = () => { clearTimeout(timer); clearInterval(ticker); if (stream) stream.getTracks().forEach(track => track.stop()); stream = null; stop.hidden = true; };
  async function transcribe() {
    if (!recording || busy) return;
    busy = true; retry.hidden = true; upload.disabled = true; status.textContent = 'Turning your recording into text…';
    const form = new FormData(); form.append('audio', recording, filename);
    const token = document.querySelector('meta[name=csrf-token]')?.content;
    try {
      const response = await fetch('/api/voice/transcribe', { method:'POST', headers:{'X-CSRF-Token':token || ''}, body:form });
      const result = await response.json(); if (!response.ok) throw Error(result.error || 'Could not transcribe this recording.');
      transcript.value = result.text; preview.hidden = false;
      status.textContent = 'Check your words, then insert them into your text box.';
    } catch (error) { status.textContent = error.message || 'Connection failed. Try again.'; retry.hidden = false; }
    finally { busy = false; upload.disabled = false; }
  }
  document.querySelectorAll('[data-record-voice]').forEach(button => button.addEventListener('click', async () => {
    panel.hidden = false;
    if (busy || (recorder && recorder.state === 'recording')) return;
    const specified = button.dataset.recordTarget && document.querySelector(button.dataset.recordTarget);
    insertionTarget = specified || (eligible(target) ? target : defaultTarget());
    if (insertionTarget) insertionTarget.closest('details')?.setAttribute('open','');
    preview.hidden = true; retry.hidden = true; cancelled = false;
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
      status.textContent = 'This browser cannot record here. Open the CRM in Chrome or Safari, or choose an audio recording below.'; return;
    }
    busy = true; status.textContent = 'Allow microphone access to start recording.';
    try {
      let expired = false, permissionTimer;
      const microphone = navigator.mediaDevices.getUserMedia({audio:true}).then(media => {
        if (expired || cancelled) { media.getTracks().forEach(track => track.stop()); throw Error('Microphone request cancelled.'); }
        return media;
      });
      try {
        stream = await Promise.race([microphone, new Promise((_, reject) => {
          permissionTimer = setTimeout(() => { expired = true; reject(Error('Microphone permission timed out.')); },15000);
        })]);
      } finally { clearTimeout(permissionTimer); }
      if (cancelled) { release(); return; }
      const mime = ['audio/webm;codecs=opus','audio/mp4','audio/webm'].find(type => MediaRecorder.isTypeSupported(type));
      recorder = new MediaRecorder(stream, mime ? {mimeType:mime} : undefined);
      const chunks = []; let size = 0;
      recorder.ondataavailable = e => { if (e.data.size) { chunks.push(e.data); size += e.data.size; if (size > 7*1024*1024 && recorder.state === 'recording') recorder.stop(); } };
      recorder.onerror = () => { cancelled = true; release(); status.textContent = 'Recording stopped unexpectedly. Please try again.'; };
      recorder.onstop = () => {
        release(); if (cancelled) return;
        const type = recorder.mimeType || mime || 'audio/webm';
        recording = new Blob(chunks,{type}); filename = type.includes('mp4') ? 'recording.mp4' : 'recording.webm';
        transcribe();
      };
      recorder.start(1000); stop.hidden = false; const start = Date.now();
      status.textContent = 'Recording… 0 seconds. Speak now, then press Stop.';
      ticker = setInterval(() => { status.textContent = `Recording… ${Math.floor((Date.now()-start)/1000)} seconds. Speak now, then press Stop.`; },1000);
      timer = setTimeout(() => { if (recorder.state === 'recording') recorder.stop(); },90000);
    } catch (error) { release(); status.textContent = error.name === 'NotAllowedError' ? 'Microphone access was blocked. Allow it in your browser, open the CRM in Chrome or Safari, or choose a recording below.' : 'No microphone could be opened. Check your microphone or choose an audio recording below.'; }
    finally { busy = false; }
  }));
  stop.addEventListener('click', () => { if (recorder?.state === 'recording') recorder.stop(); });
  retry.addEventListener('click',transcribe);
  upload.addEventListener('change', () => {
    if (!upload.files[0] || busy || recorder?.state === 'recording') return;
    insertionTarget = eligible(target) ? target : defaultTarget(); recording = upload.files[0]; filename = recording.name; preview.hidden = true; transcribe();
  });
  dock.querySelector('[data-insert-transcript]').addEventListener('click', () => {
    if (!eligible(insertionTarget) || !insertionTarget.isConnected) insertionTarget = target;
    if (!eligible(insertionTarget) || !insertionTarget.isConnected) { status.textContent = 'Choose a text box first, then press Insert text.'; return; }
    const text = transcript.value.trim(); if (!text) return;
    insertionTarget.value = insertionTarget.name === 'q' || insertionTarget.type === 'search' ? text.replace(/[.!?]+$/, '') : insertionTarget.value + (insertionTarget.value ? ' ' : '') + text;
    insertionTarget.dispatchEvent(new Event('input',{bubbles:true})); insertionTarget.focus(); panel.hidden = true;
  });
  dock.querySelector('[data-copy-transcript]').addEventListener('click', async () => { try { await navigator.clipboard.writeText(transcript.value); status.textContent = 'Text copied.'; } catch (_) { transcript.focus(); transcript.select(); status.textContent = 'Text selected. Copy it from here.'; } });
  const cancel = () => { cancelled = true; if (recorder?.state === 'recording') recorder.stop(); release(); panel.hidden = true; };
  dock.querySelector('[data-close-recorder]').addEventListener('click', cancel);
  window.addEventListener('pagehide',cancel);
})();
