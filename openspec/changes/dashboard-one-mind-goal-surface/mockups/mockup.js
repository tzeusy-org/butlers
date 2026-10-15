const before = document.querySelector('.before');
document.querySelector('[data-before]').onclick = () => {
  const open = before.dataset.open === 'no';
  before.dataset.open = open ? 'yes' : 'no';
  document.querySelector('[data-before]').setAttribute('aria-expanded', String(open));
  if (open) before.scrollIntoView({behavior: 'auto', block: 'start'});
};
const themeKey = 'd19-synthetic-theme-v1';
const savedTheme = sessionStorage.getItem(themeKey);
if (savedTheme === 'light' || savedTheme === 'dark') document.documentElement.dataset.theme = savedTheme;
document.querySelector('button[data-theme]').onclick = () => {
  const theme = document.documentElement.dataset.theme === 'light' ? 'dark' : 'light';
  document.documentElement.dataset.theme = theme;
  sessionStorage.setItem(themeKey, theme);
};
const palette = document.querySelector('dialog');
document.querySelector('[data-command]').onclick = () => palette.showModal();
document.querySelector('[data-close]').onclick = () => palette.close();
document.addEventListener('keydown', event => {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
    event.preventDefault();
    palette.open ? palette.close() : palette.showModal();
  }
});
document.querySelectorAll('[data-demo]').forEach(button => {
  button.onclick = () => {
    document.querySelector('[role=status]').textContent = button.dataset.demo;
    button.disabled = true;
  };
});
let decision = null;
const choices = document.querySelectorAll('[data-decision]');
const states = {
  approve: ['Approved · dispatch unconfirmed', 'Design simulation: this exact call is approved. Dispatch and delivery remain unconfirmed.'],
  reject: ['Rejected', 'Design simulation: this effect is rejected. Its original lineage remains readable.'],
  defer: ['Pending · deferred', 'Design simulation: this effect remains pending under its deferred presentation generation. No dispatch or delivery is claimed.']
};
choices.forEach(button => {
  button.onclick = () => {
    if (decision !== null) return;
    decision = button.dataset.decision;
    const [label, notice] = states[decision];
    document.querySelectorAll('[data-effect-state]').forEach(node => {
      node.textContent = label;
      node.classList.toggle('amber', decision !== 'reject');
    });
    choices.forEach(choice => { choice.disabled = true; });
    document.querySelector('[role=status]').textContent = notice + ' Reload this synthetic artifact to inspect another decision.';
  };
});

// Synthetic browser-tab state only: these files never call a product service.
const holdKey = 'd19-synthetic-hold-v1';
const initialHold = {held: true, state: 'confirmed', reason: 'Focus time', until: '', since: '09:00 UTC', admissions: 4, deliveries: 2};
let heldFixture;
try { heldFixture = JSON.parse(sessionStorage.getItem(holdKey)) || {...initialHold}; }
catch { heldFixture = {...initialHold}; }
function renderHold() {
  const banner = document.querySelector('[data-hold-banner]');
  const label = banner.querySelector('[data-hold-state]');
  const facts = banner.querySelector('[data-hold-facts]');
  banner.hidden = !heldFixture.held && heldFixture.state === 'confirmed';
  label.textContent = heldFixture.state === 'unavailable' ? 'Hold status unavailable' : heldFixture.state === 'engage-unknown' ? 'Hold request unconfirmed' : heldFixture.state === 'release-unknown' ? `Release unconfirmed · last confirmed ${heldFixture.held ? 'Held' : 'unheld'}` : 'Held';
  const uncertain = heldFixture.state !== 'confirmed';
  facts.textContent = `Last confirmed ${heldFixture.held ? 'held' : 'unheld'} · Since ${heldFixture.since} · By owner via dashboard · Reason: ${heldFixture.reason} · Until ${heldFixture.until || 'manual release'} · Deferred admissions: ${uncertain ? 'unavailable' : heldFixture.admissions} · Deliveries: ${uncertain ? 'unavailable' : heldFixture.deliveries} · Observed 09:10 UTC · Synthetic fixture`;
  sessionStorage.setItem(holdKey, JSON.stringify(heldFixture));
}
renderHold();
const holdForm = document.querySelector('[data-hold-form]');
if (holdForm) {
  const action = holdForm.querySelector('[data-hold-action]');
  const reason = holdForm.elements.reason;
  const until = holdForm.elements.until;
  const submit = holdForm.querySelector('[data-hold-submit]');
  action.addEventListener('change', () => {
    const release = action.value === 'release';
    reason.disabled = release; until.disabled = release;
    submit.querySelector('.button-face').textContent = action.selectedOptions[0].textContent;
    holdForm.querySelector('[data-hold-explanation]').textContent = release ? 'Release resumes deferred work through the owning durable path; unheld is distinct from release progress.' : action.value === 'hold-stop' ? 'Engage Hold first, then Stop every nonterminal goal. Stop cancels parked effects and cannot undo dispatched work.' : 'Hold does not stop a running goal or undo any effect.';
  });
  holdForm.addEventListener('submit', event => {
    event.preventDefault();
    const fixture = document.querySelector('[data-hold-fixture]').value;
    const receipt = document.querySelector('[data-hold-receipt]');
    if (action.value !== 'release' && until.value && Date.parse(until.value + 'Z') <= Date.parse('2026-10-15T09:10:00Z')) {
      receipt.textContent = 'Synthetic refusal: until must be after the displayed fixture time, 09:10 UTC. No command submitted.';
      until.focus(); return;
    }
    if (fixture === 'unavailable') {
      heldFixture.state = 'unavailable';
      receipt.textContent = 'Design simulation: status unavailable. Last-confirmed state is retained; no all-clear or zero count is claimed. Reconcile status before another command.';
    } else if (action.value === 'release') {
      if (fixture === 'release-unknown') {
        heldFixture.state = 'release-unknown';
        receipt.textContent = 'Design simulation: Release unconfirmed. Last-confirmed Hold remains visible. Reconcile the same command identity; deferred work is not claimed released.';
      } else {
        heldFixture.held = false; heldFixture.state = 'confirmed';
        receipt.textContent = 'Design simulation: Release receipt confirmed; unheld. Deferred admissions and channel digests still have their own progress/receipts. No backend work ran.';
      }
    } else if (fixture === 'engage-unknown') {
      heldFixture.state = 'engage-unknown';
      receipt.textContent = 'Design simulation: engagement unconfirmed. Stop-all was not requested. Reconcile actual status and the same command identity; no success is claimed.';
    } else {
      const wasHeld = heldFixture.held;
      heldFixture.held = true; heldFixture.state = 'confirmed';
      if (!wasHeld) { heldFixture.reason = reason.value; heldFixture.until = until.value ? until.value + ' UTC' : ''; heldFixture.since = '09:10 UTC'; }
      receipt.textContent = action.value === 'hold-stop' ? fixture === 'stop-partial' ? 'Design simulation: engagement receipt confirmed. Stop-all partial: g-example-104 stopped, parked effect cancelled; g-example-105 Stop unconfirmed, dispatched effect unknown; terminal g-example-92 skipped. Held remains active. No undo or all-stopped claim.' : 'Design simulation: engagement receipt confirmed. Separate Stop-all receipt: nonterminal fixture goal stopped; parked effect cancelled, dispatched effect unknown; terminal goal skipped. No committed effect undone.' : 'Design simulation: Hold receipt confirmed. Existing running goals continue; owner replies and approvals remain eligible. No Stop-all was requested.';
    }
    renderHold();
    document.querySelector('[role=status]').textContent = receipt.textContent;
  });
}
const researchFixture = document.querySelector('[data-research-fixture]');
if (researchFixture) researchFixture.addEventListener('change', () => {
  const mode = researchFixture.value;
  document.querySelector('[data-research-record]').hidden = mode === 'empty' || mode === 'unavailable';
  document.querySelector('[data-research-coverage]').textContent = ({complete: '1 recorded call · Complete fixture population · Observed 09:15 UTC', empty: 'No recorded research calls · Complete ledger read · Observed 09:15 UTC', unavailable: 'Research ledger unavailable · Count unknown · Retry the owning read; no zero is inferred', partial: '1 recorded call on this page · More available · Cursor/total coverage partial', stale: 'Last-known call · Observed 09:15 UTC · Current source unavailable; not current freshness'})[mode];
});
const researchCall = document.querySelector('details#research-example-1');
if (researchCall) {
  const openResearchCall = hash => {
    if (hash !== '#research-example-1' || researchCall.closest('[hidden]')) return;
    researchCall.open = true;
    researchCall.querySelector('summary').focus();
  };
  document.querySelectorAll('a[href="#research-example-1"]').forEach(link => {
    link.addEventListener('click', event => {
      if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      if (location.hash === link.hash) event.preventDefault();
      openResearchCall(link.hash);
    });
  });
  window.addEventListener('hashchange', () => openResearchCall(location.hash));
  window.addEventListener('pageshow', () => requestAnimationFrame(() => openResearchCall(location.hash)));
  openResearchCall(location.hash);
}
