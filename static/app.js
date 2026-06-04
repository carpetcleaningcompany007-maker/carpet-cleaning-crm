(function() {
  const zones = document.querySelectorAll(".dropzone");
  let dragged = null;
  document.querySelectorAll(".job-card").forEach(card => {
    card.addEventListener("dragstart", () => dragged = card);
  });
  zones.forEach(zone => {
    zone.addEventListener("dragover", e => e.preventDefault());
    zone.addEventListener("drop", async e => {
      e.preventDefault();
      if (!dragged) return;
      zone.appendChild(dragged);
      const form = new FormData();
      form.append("job_date", zone.dataset.date || "");
      await fetch(`/jobs/${dragged.dataset.jobId}/move_date`, { method: "POST", body: form });
      dragged = null;
    });
  });

  if (!window.CALC_PRICING) return;
  const pricing = window.CALC_PRICING;
  const qty = {};
  const areaRows = [];
  const resGrid = document.getElementById("resGrid");
  const areaList = document.getElementById("areaList");
  const calcLines = document.getElementById("calcLines");
  const subtotalLabel = document.getElementById("subtotalLabel");
  const vatLabel = document.getElementById("vatLabel");
  const totalLabel = document.getElementById("totalLabel");
  const includeVat = document.getElementById("includeVat");
  const payloadJson = document.getElementById("payloadJson");

  const fmt = n => "£" + (Math.round((Number(n)||0) * 100) / 100).toFixed(2);
  const getBands = method => {
    if (method === "rotary") return pricing.rotaryBands;
    if (method === "hybrid") return pricing.hybridBands;
    if (method === "hardfloor") return pricing.hardfloorBands;
    return pricing.hweBands;
  };
  const methodLabel = method => ({rotary:"Rotary bonnet", hybrid:"Hybrid Deep Clean", hwe:"Hot water extraction", hardfloor:"Hard floor cleaning"}[method] || method);
  const pickBand = (sqm, bands) => bands.find(b => sqm >= b.min && sqm <= b.max) || bands[bands.length - 1];

  function renderDomestic() {
    resGrid.innerHTML = pricing.domestic.map(item => `
      <div class="res-item">
        <h4>${item.name}</h4>
        <p>${item.desc}</p>
        <div class="row-actions">
          <span class="pill">${fmt(item.price)} each</span>
          <input type="number" min="0" step="1" value="${qty[item.id] || 0}" data-domestic-id="${item.id}">
        </div>
      </div>
    `).join("");
    resGrid.querySelectorAll("[data-domestic-id]").forEach(el => {
      el.addEventListener("input", () => {
        qty[el.dataset.domesticId] = Math.max(0, Number(el.value) || 0);
        renderReview();
      });
    });
  }

  function renderAreas() {
    areaList.innerHTML = areaRows.map((r, i) => `
      <div class="timeline-item">
        <strong>${r.name}</strong>
        <p>${methodLabel(r.method)} · ${r.sqm} m²</p>
        <button class="btn danger" type="button" data-remove-area="${i}">Remove</button>
      </div>
    `).join("");
    areaList.querySelectorAll("[data-remove-area]").forEach(btn => {
      btn.addEventListener("click", () => {
        areaRows.splice(Number(btn.dataset.removeArea), 1);
        renderAreas();
        renderReview();
      });
    });
  }

  function calculate() {
    const lines = [];
    let subtotal = 0;
    pricing.domestic.forEach(item => {
      const q = Number(qty[item.id] || 0);
      if (!q) return;
      const total = q * item.price;
      subtotal += total;
      lines.push({item_name:item.name, method:item.name, quantity:q, unit_price:item.price, line_total:total, group_name:item.group});
    });
    areaRows.forEach(r => {
      const band = pickBand(Number(r.sqm || 0), getBands(r.method));
      const unit = Number(band.rate || 0);
      const total = Number(r.sqm || 0) * unit;
      subtotal += total;
      lines.push({item_name:r.name, method:methodLabel(r.method), quantity:Number(r.sqm||0), unit_price:unit, line_total:total, group_name:"Commercial Work"});
    });
    const hotelQty = Math.max(0, Number((document.getElementById("hotelQty") || {}).value || 0));
    const hotelMethod = (document.getElementById("hotelMethod") || {}).value || "rotary";
    if (hotelQty > 0) {
      const rate = Number(pricing.hotelRooms[hotelMethod] || 0);
      const total = hotelQty * rate;
      subtotal += total;
      lines.push({item_name:"Hotel rooms", method:methodLabel(hotelMethod), quantity:hotelQty, unit_price:rate, line_total:total, group_name:"Commercial Work"});
    }
    const extraDesc = (document.getElementById("extraDesc") || {}).value || "";
    const extraPrice = Math.max(0, Number((document.getElementById("extraPrice") || {}).value || 0));
    if (extraPrice > 0) {
      subtotal += extraPrice;
      lines.push({item_name:extraDesc || "Extra", method:"Manual extra", quantity:1, unit_price:extraPrice, line_total:extraPrice, group_name:"Commercial Work"});
    }
    const vat = includeVat && includeVat.checked ? subtotal * 0.20 : 0;
    const rawTotal = subtotal + vat;
    const total = rawTotal < 100 ? 100 : rawTotal;
    return {lines, subtotal, vat, total, include_vat:!!(includeVat && includeVat.checked)};
  }

  function renderReview() {
    const result = calculate();
    calcLines.innerHTML = result.lines.map(line => `
      <div class="timeline-item">
        <strong>${line.item_name}</strong>
        <p>${line.method} · ${line.quantity} × ${fmt(line.unit_price)}</p>
        <strong>${fmt(line.line_total)}</strong>
      </div>
    `).join("") || '<div class="timeline-item"><p>No items selected yet.</p></div>';
    subtotalLabel.textContent = fmt(result.subtotal);
    vatLabel.textContent = fmt(result.vat);
    totalLabel.textContent = fmt(result.total);
    payloadJson.value = JSON.stringify(result);
  }

  const addAreaBtn = document.getElementById("addAreaBtn");
  if (addAreaBtn) {
    addAreaBtn.addEventListener("click", () => {
      const type = document.getElementById("areaType").value;
      const custom = document.getElementById("customAreaName").value.trim();
      const sqm = Number(document.getElementById("areaSqm").value || 0);
      const method = document.getElementById("areaMethod").value;
      if (sqm <= 0) return;
      areaRows.push({name: type === "Custom" ? (custom || "Custom Area") : type, sqm, method});
      document.getElementById("areaSqm").value = "";
      document.getElementById("customAreaName").value = "";
      renderAreas();
      renderReview();
    });
  }

  ["hotelQty","hotelMethod","extraDesc","extraPrice"].forEach(id => {
    const el = document.getElementById(id);
    if (el) {
      el.addEventListener("input", renderReview);
      el.addEventListener("change", renderReview);
    }
  });
  if (includeVat) includeVat.addEventListener("change", renderReview);

  renderDomestic();
  renderAreas();
  renderReview();
})();


document.addEventListener('DOMContentLoaded', function(){
  const accentColor = document.getElementById('accentColorPicker');
  if(accentColor){
    accentColor.addEventListener('input', function(){
      document.documentElement.style.setProperty('--accent', this.value);
    });
  }

  const toggle = document.querySelector('[data-sidebar-toggle]');
  const close = document.querySelector('[data-sidebar-close]');
  const sidebar = document.getElementById('mainSidebar');
  function setSidebar(open){
    document.body.classList.toggle('sidebar-open', !!open);
    if(toggle) toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    if(sidebar) sidebar.setAttribute('aria-hidden', open ? 'false' : 'true');
  }
  if(toggle){
    toggle.addEventListener('click', function(){
      setSidebar(!document.body.classList.contains('sidebar-open'));
    });
  }
  if(close){ close.addEventListener('click', function(){ setSidebar(false); }); }
  document.addEventListener('keydown', function(e){
    if(e.key === 'Escape') setSidebar(false);
  });
  document.querySelectorAll('.sidebar .nav-link').forEach(function(link){
    link.addEventListener('click', function(){ setSidebar(false); });
  });
});
