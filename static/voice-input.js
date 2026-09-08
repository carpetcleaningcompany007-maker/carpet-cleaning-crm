(() => {
  document.querySelectorAll('[data-voice-target]').forEach(button => {
    const input = document.querySelector(button.dataset.voiceTarget);
    const hint = document.querySelector('[data-voice-hint]');
    const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!Recognition) { button.disabled = true; hint.textContent = 'Tap the search box and use your phone keyboard microphone to say a name, town or postcode.'; return; }
    let active;
    button.addEventListener('click', () => {
      if (active) { active.stop(); return; }
      active = new Recognition(); active.lang = 'en-GB'; active.interimResults = false;
      active.onresult = event => {
        input.value = event.results[0][0].transcript.trim().replace(/[.!?]+$/, '');
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.focus(); hint.textContent = 'Check the name or place, then press Find customer to see matching customers.';
      };
      active.onerror = () => { hint.textContent = 'Allow microphone access, or use your phone keyboard microphone in the search box.'; };
      active.onend = () => { active = null; button.textContent = 'Find customer by voice'; };
      try { active.start(); button.textContent = 'Stop listening'; hint.textContent = 'Listening for a name, town or postcode…'; }
      catch (_) { active = null; hint.textContent = 'Use your keyboard microphone to dictate into the search box.'; }
    });
    window.addEventListener('pagehide', () => { if (active) active.stop(); });
  });
})();
