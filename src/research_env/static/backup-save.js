// Native Save As where supported; the ordinary browser download remains available.
function enableBackupSave() {
  const button=document.getElementById('backup-save-as');
  if (!button || !window.showSaveFilePicker || button.dataset.bound) return;
  button.hidden=false; button.dataset.bound='true';
  button.addEventListener('click',async()=>{
    const status=document.getElementById('backup-save-status');
    let output;
    try {
      const handle=await window.showSaveFilePicker({suggestedName:button.dataset.filename,types:[{description:'Sicherung (ZIP)',accept:{'application/zip':['.zip']}}]});
      const response=await fetch(button.dataset.url);
      if (!response.ok) throw new Error('Sicherung ist nicht verfügbar. Seite erneut öffnen.');
      output=await handle.createWritable();
      await response.body.pipeTo(output); output=null;
      status.textContent='ZIP vollständig gespeichert: '+handle.name+'. Bestätige den tatsächlichen Speicherort unten.';
    } catch (error) {
      if (output) await output.abort();
      status.textContent=error.name==='AbortError'?'Speichern abgebrochen.':error.message;
    }
  });
}
enableBackupSave();
document.addEventListener('htmx:afterSwap',enableBackupSave);
