// Display-only clock and completion announcement. Never starts a run or a model call.
(() => {
  let previous = null;
  let expanded = [];
  const originalTitle = document.title;
  function refresh() {
    const root = document.getElementById('progress');
    if (!root) return;
    document.querySelectorAll('.pipeline-controls').forEach(element => { element.hidden = root.dataset.pipelineEnded === 'true'; });
    root.querySelectorAll('[data-elapsed-start]').forEach(element => {
      const start = Date.parse(element.dataset.elapsedStart);
      if (!Number.isFinite(start)) return;
      const seconds = Math.max(0, Math.floor((Date.now() - start) / 1000));
      const hours = Math.floor(seconds / 3600);
      const minutes = Math.floor(seconds / 60) % 60;
      element.textContent = hours ? `${hours} h ${String(minutes).padStart(2, '0')} min` : minutes ? `${minutes} min ${String(seconds % 60).padStart(2, '0')} s` : `${seconds} s`;
    });
    if (previous?.id === root.dataset.runId && !previous.finished && root.dataset.finished === 'true') {
      // Persistent on-page notice and tab title; no permission dialog or sound.
      document.title = `${root.dataset.heading} · ${originalTitle}`;
    }
    previous = {id: root.dataset.runId, finished: root.dataset.finished === 'true'};
  }
  document.addEventListener('htmx:beforeSwap', event => {
    if (!['progress', 'results-progress'].includes(event.detail.target?.id)) return;
    expanded = [...event.detail.target.querySelectorAll('details[open]')].map(detail => detail.querySelector('summary')?.textContent);
  });
  document.addEventListener('htmx:afterSwap', () => {
    document.querySelectorAll('#progress details, #results-progress details').forEach(detail => {
      if (expanded.includes(detail.querySelector('summary')?.textContent)) detail.open = true;
    });
    refresh();
  });
  const configuration = document.querySelector('form[action$="/test-configurations"]');
  function selectionPreview() {
    if (!configuration) return;
    const fields = configuration.elements;
    configuration.querySelector('.context-preview-link').href = `${configuration.getAttribute('action').replace(/\/test-configurations$/, '')}/settings/context?module=${encodeURIComponent(fields.module.value)}&context=${encodeURIComponent(fields.context.value)}`;
    for (const [role, field] of [['P', 'producer'], ['V', 'verifier']]) {
      const choice = fields['model_' + fields[field].value.toLowerCase()];
      configuration.querySelector(`[data-role-preview="${role}"]`).textContent = `${role === 'P' ? 'Produzent P' : 'Verifikation V'}: ${choice.selectedOptions[0]?.textContent || 'Noch kein Modell'} (Paket ${fields[field].value})`;
    }
  }
  configuration?.addEventListener('change', selectionPreview);
  selectionPreview();
  document.addEventListener('click', async event => {
    const button = event.target.closest('[data-copy-from]');
    if (!button) return;
    const source = document.getElementById(button.dataset.copyFrom);
    try { await navigator.clipboard.writeText(source.value); button.textContent = 'Pfad kopiert'; }
    catch { source.focus(); source.select(); button.textContent = 'Pfad markieren und kopieren'; }
  });
  refresh();
  setInterval(refresh, 1000);
})();

// Local form feedback; all scientific and permission checks also run server-side.
document.addEventListener('click', event => {
  const opener = event.target.closest('[data-dialog-open]');
  if (opener) document.getElementById(opener.dataset.dialogOpen)?.showModal();
  if (event.target.closest('[data-dialog-close]')) event.target.closest('dialog')?.close();
});
document.addEventListener('input', event => {
  const field = event.target;
  if (field.dataset.exact !== undefined) field.setCustomValidity(field.value === field.dataset.exact ? '' : 'Bitte den Namen exakt wie angegeben eingeben.');
});
document.addEventListener('submit', event => {
  const form = event.target;
  if (form.matches('[data-review-form]')) {
    const fields=form.elements, completed=event.submitter?.value === 'completed';
    let message='', focus=null;
    const missing=(name,text) => {if (!message && !fields[name]?.value.trim()) {message=text;focus=fields[name];}};
    missing('reason','Bitte beschreibe deine Beobachtung – auch beim Speichern eines Entwurfs.');
    missing('person','Bitte trage deinen Namen ein.');
    if (completed && fields.verdict.value==='open' && !message) {message='Wähle Erfüllt oder Nicht erfüllt. Wenn du noch unsicher bist, speichere einen Entwurf.';focus=form.querySelector('[name=verdict]');}
    if (completed) missing('code_artifact_id','Wähle die Datei, die deine Bewertung belegt.');
    if (fields.code_artifact_id.value) {
      missing('lines','Trage die Zeile oder den Zeilenbereich der gewählten Datei ein (z. B. 12-18).');
      if (!message && !/^[1-9][0-9]*(?:-[1-9][0-9]*)?$/.test(fields.lines.value)) {message='Zeilen bitte als positive Nummer oder Bereich eingeben, z. B. 12-18.';focus=fields.lines;}
      const range=fields.lines.value.split('-').map(Number);
      if (!message && range.at(-1)<range[0]) {message='Das Ende des Zeilenbereichs liegt vor dem Anfang.';focus=fields.lines;}
      const lastLine=Number(fields.code_artifact_id.selectedOptions[0]?.dataset.lineCount);
      if (!message && range.at(-1)>lastLine) {message=`Die gewählte Datei hat nur ${lastLine} Zeilen. Bitte die Belegstelle korrigieren.`;focus=fields.lines;}
    }
    if (completed && fields.criterion.value==='T4') missing('measurement_id','Ordne für T4 den unabhängigen Integrationsbefund zu. Fehlt er noch, speichere einen Entwurf.');
    const banner=form.querySelector('.form-error');
    banner.hidden=!message;banner.textContent=message;
    if (message) {event.preventDefault();focus?.focus();return;}
    if (!form.reportValidity()) {event.preventDefault();return;}
  }
  if (form.dataset.confirm && !window.confirm(form.dataset.confirm)) event.preventDefault();
});

const researchForm=document.querySelector('form[action$="/research-configurations"]');
function matchingResearchChoices() {
  if (!researchForm) return;
  for (const select of researchForm.querySelectorAll('select[name^="condition_"]')) {
    for (const option of select.querySelectorAll('option[data-model-a]')) {
      const matches=option.dataset.modelA===researchForm.elements.model_a.value && option.dataset.modelB===researchForm.elements.model_b.value;
      option.hidden=!matches;option.disabled=!matches;
      if (!matches && option.selected) select.value='';
    }
  }
}
researchForm?.addEventListener('change',matchingResearchChoices);
matchingResearchChoices();

// A display-only count; saving and validating the scope always happens on the server.
document.querySelectorAll('.scope-form').forEach(form => {
  const core = form.elements.r_c, extra = form.elements.r_e, output = form.querySelector('output');
  function updateScopeCount() {
    const c = Number(core.value), e = Number(extra.value);
    const positive = Number.isSafeInteger(c) && Number.isSafeInteger(e) && c > 0 && e > 0;
    extra.setCustomValidity(positive && e > c ? 'Die Zusatzvergleiche dürfen höchstens so oft wiederholt werden wie der Kontextvergleich.' : '');
    output.textContent = positive && e <= c ? `${6 * c + 6 * e} geplante Hauptläufe · Vorschau, noch nicht gespeichert` : 'Gib positive ganze Wiederholungszahlen ein; r_E darf höchstens r_C sein.';
  }
  core.addEventListener('input', updateScopeCount);
  extra.addEventListener('input', updateScopeCount);
});
