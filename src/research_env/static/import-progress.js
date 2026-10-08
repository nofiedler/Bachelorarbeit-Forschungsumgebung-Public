// Displays local upload/validation progress; never starts research or model calls.
(() => {
  const previousMeters = new Map(), animations = new WeakMap();
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  function updateMeter(meter, data) {
    const fill = meter.querySelector('.import-meter-fill');
    const measured = Number.isFinite(data.percent);
    meter.setAttribute('aria-label', data.stage);
    meter.setAttribute('aria-valuemin', '0'); meter.setAttribute('aria-valuemax', '100');
    meter.classList.toggle('is-indeterminate', !measured);
    animations.get(meter)?.cancel();
    if (!measured) {
      meter.removeAttribute('aria-valuenow');
      previousMeters.delete(meter.id);
      return;
    }
    const value = Math.max(0, Math.min(100, data.percent));
    meter.setAttribute('aria-valuenow', String(value));
    const previous = previousMeters.get(meter.id);
    const from = previous?.stage === data.stage ? previous.value : value;
    animations.set(meter, fill.animate([
      {transform:`scaleX(${from / 100})`}, {transform:`scaleX(${value / 100})`}
    ], {duration:reducedMotion.matches ? 0 : 650, easing:'cubic-bezier(.22,.61,.36,1)', fill:'forwards'}));
    previousMeters.set(meter.id, {value, stage:data.stage});
  }
  function refreshMeters() {
    document.querySelectorAll('[data-import-meter]').forEach(meter => updateMeter(meter, {
      stage:meter.dataset.stage,
      percent:meter.dataset.percent === '' ? null : Number(meter.dataset.percent)
    }));
  }
  refreshMeters();
  document.addEventListener('htmx:afterSwap', refreshMeters);
  const format = new Intl.NumberFormat('de-DE');
  const bytes = value => value >= 1024 ** 2 ? `${(value / 1024 ** 2).toLocaleString('de-DE', {maximumFractionDigits:1})} MiB` : `${format.format(value)} Bytes`;
  const eta = seconds => !Number.isFinite(seconds) ? 'Restzeit wird geschätzt, sobald genügend Fortschritt messbar ist.' : seconds < 60 ? 'Voraussichtlich noch weniger als eine Minute für diesen Schritt.' : `Noch ungefähr ${Math.max(1, Math.round(seconds * .8 / 60))}–${Math.max(2, Math.round(seconds * 1.3 / 60))} Minuten für diesen Schritt.`;
  document.querySelectorAll('form[data-import-form]').forEach(form => {
    if (!window.XMLHttpRequest || !window.crypto?.randomUUID) return; // Native form remains usable.
    const panel = document.createElement('section');
    panel.className = 'import-progress'; panel.hidden = true; panel.setAttribute('aria-live', 'polite'); panel.setAttribute('aria-label', 'Importfortschritt');
    const heading = document.createElement('h2'), bar = document.createElement('div'), count = document.createElement('p'), estimate = document.createElement('p'), note = document.createElement('p');
    bar.className = 'import-meter'; bar.id = 'upload-meter-' + crypto.randomUUID(); bar.setAttribute('role','progressbar');
    const fill = document.createElement('span'); fill.className = 'import-meter-fill'; bar.append(fill);
    count.className = 'import-progress-count'; note.className = 'hint';
    const waitingNote = 'Die Anzeige gilt für den aktuellen Schritt. Anschließend werden Inhalt und Prüfsummen geprüft. Bitte lasse diese Seite bis zur Weiterleitung geöffnet.';
    note.textContent = waitingNote;
    panel.append(heading, bar, count, estimate, note); form.after(panel);
    const show = data => {
      heading.textContent = data.stage; updateMeter(bar, data);
      count.textContent = data.percent == null ? '' : `${data.percent} % dieses Schritts · ${data.unit === 'Bytes' ? `${bytes(data.done)} von ${bytes(data.total)}` : `${format.format(data.done)} von ${format.format(data.total)} ${data.unit}`}`;
      estimate.textContent = data.eta || eta(data.remaining_seconds);
    };
    let busy = false;
    form.addEventListener('submit', event => {
      if (event.defaultPrevented) return;
      event.preventDefault();
      if (busy || !form.reportValidity()) return;
      busy = true; panel.hidden = false; panel.classList.remove('error'); bar.hidden = false; count.hidden = false; note.textContent = waitingNote;
      const data = new FormData(form), buttons = [...form.querySelectorAll('button, input[type=submit]')];
      buttons.forEach(button => button.disabled = true);
      const xhr = new XMLHttpRequest(), token = crypto.randomUUID(), start = performance.now();
      let polling = null, pending = false, stopped = false;
      const stop = () => { stopped = true; if (polling) clearInterval(polling); };
      const fail = message => { stop(); busy = false; buttons.forEach(button => button.disabled = false); show({stage:'Import nicht abgeschlossen', eta:message}); bar.hidden = true; count.hidden = true; panel.classList.add('error'); note.textContent = 'Du kannst die Datei prüfen und anschließend erneut auswählen. Vorhandene Importe bleiben erhalten.'; };
      const action = new URL(form.action, location.href);
      const poll = async () => {
        if (pending || stopped) return;
        pending = true;
        try {
          const response = await fetch(`${action.pathname}/progress/${token}`, {credentials:'same-origin',cache:'no-store'});
          if (response.ok && !stopped) show(await response.json());
        } catch { /* The original upload response remains authoritative. */ }
        finally { pending = false; }
      };
      show({stage:'Datei hochladen'});
      xhr.open('POST', action.href);
      if (form.hasAttribute('data-server-progress')) xhr.setRequestHeader('X-Import-Progress', token);
      xhr.upload.addEventListener('progress', event => {
        const elapsed = (performance.now() - start) / 1000;
        show({stage:'Datei hochladen', done:event.loaded, total:event.total, unit:'Bytes', percent:event.lengthComputable ? Math.floor(event.loaded / event.total * 100) : null,
          remaining_seconds:event.lengthComputable && elapsed >= 2 && event.loaded ? (event.total - event.loaded) * elapsed / event.loaded : null});
      });
      xhr.upload.addEventListener('load', () => {
        show({stage:'Upload vollständig – Import wird geprüft', eta:'Die Datei ist übertragen. Jetzt werden Inhalt und Prüfsummen geprüft.'});
        if (form.hasAttribute('data-server-progress')) { poll(); polling = setInterval(poll, 1500); }
      });
      xhr.addEventListener('load', () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          stop();
          const destination = new URL(xhr.getResponseHeader('HX-Redirect') || xhr.responseURL, location.href);
          if (destination.origin !== location.origin) return fail('Unerwartetes Weiterleitungsziel. Bitte öffne die Importübersicht.');
          show({stage:'Import übernommen – Ansicht wird geöffnet', eta:'Die nächste Ansicht wird geladen.'});
          location.assign(destination.href);
        } else {
          const document = new DOMParser().parseFromString(xhr.responseText, 'text/html');
          let message = document.querySelector('.error p')?.textContent?.trim();
          if (message === 'File is not a zip file') message = 'Die ausgewählte Datei ist kein gültiges ZIP-Archiv.';
          fail(message || `Der Import konnte nicht abgeschlossen werden (HTTP ${xhr.status}). Bitte prüfe die Datei und versuche es erneut.`);
        }
      });
      xhr.addEventListener('error', () => fail('Die Verbindung wurde unterbrochen. Prüfe zuerst die Importübersicht, bevor du die Datei erneut überträgst.'));
      xhr.addEventListener('abort', () => fail('Die Übertragung wurde abgebrochen.'));
      xhr.send(data);
    });
  });
})();
