(() => {
  const form = document.querySelector('.ai-invoice-form'); if (!form) return;
  const lines = form.querySelector('[data-invoice-lines]');
  const total = form.querySelector('[data-invoice-total]');
  const update = () => {
    let subtotal = 0, complete = true;
    lines.querySelectorAll('.ai-invoice-line').forEach(row => {
      const qty = row.querySelector('[name=quantity]').value, price = row.querySelector('[name=unit_price]').value;
      if (!qty || !price || Number(qty) <= 0 || Number(price) < 0) complete = false;
      subtotal += Math.round(Number(qty) * Number(price) * 100);
    });
    const vat = form.querySelector('[name=vat]').value;
    total.textContent = complete && vat !== '' && Number(vat) >= 0 ? `Subtotal £${(subtotal/100).toFixed(2)} · Total including VAT £${((subtotal+Math.round(Number(vat)*100))/100).toFixed(2)}` : 'Complete the quantities, prices and VAT to see the total.';
  };
  form.addEventListener('input', update);
  form.addEventListener('click', event => {
    if (event.target.matches('[data-remove-line]') && lines.children.length > 1) { event.target.closest('.ai-invoice-line').remove(); update(); }
  });
  form.querySelector('[data-add-invoice-line]').addEventListener('click', () => {
    if (lines.children.length >= 20) return;
    const row = lines.firstElementChild.cloneNode(true);
    row.querySelectorAll('input').forEach(input => input.value = ''); lines.append(row); update();
  }); update();
})();
